"""Sequential topic pipeline for alert and briefing modes."""

import argparse
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import logging
import os
from pathlib import Path

from src.brain import analyze_topic
from src.config import ConfigError, load_config, validate_live_config
from src.notifier import TelegramNotifier, deliver, deliver_audio_only
from src.scraper import fetch_topic
from src.state import (cleanup_state, load_state, mark_uncertain, record_briefing,
                       record_delivery, record_post, save_state)


LOG = logging.getLogger(__name__)


def _window(now: datetime, hours: int) -> tuple[datetime, datetime]:
    epoch_hours = int(now.timestamp()) // (hours * 3600)
    start = datetime.fromtimestamp(epoch_hours * hours * 3600, timezone.utc)
    return start, start + timedelta(hours=hours)


def _text(analysis) -> str:
    return analysis.summary_th + "\n" + "\n".join(map(str, analysis.source_urls))


def _save(state_path: Path, state: dict, dry_run: bool) -> None:
    save_state(state_path, state, dry_run=dry_run)


def run(config, *, mode: str, state_path: str | Path = "seen_posts.json",
        dry_run: bool = False, fetch=fetch_topic, analyze=analyze_topic,
        notifier=None, now: datetime | None = None) -> int:
    """Return 0 on clean processing, 1 for topic errors, 2 for config/state errors."""
    if mode not in {"alert", "briefing"}:
        raise ValueError("mode must be alert or briefing")
    if mode == "alert" and config.alert.generate_audio:
        raise ValueError("audio alerts require delivery state support; disable alert.generate_audio")
    now = now or datetime.now(timezone.utc)
    state_path = Path(state_path)
    state = load_state(state_path)
    initial_state = deepcopy(state)
    failures = 0
    for topic in config.topics:
        if not topic.enabled:
            continue
        try:
            hours = config.briefing.reporting_hours
            start, end = _window(now, hours)
            current = state["topics"].get(topic.id, {"posts": {}, "briefings": {}})
            existing_briefing_id = f"{topic.id}:briefing:{start.isoformat()}"
            existing_briefing = current["briefings"].get(existing_briefing_id)
            # Recover audio without re-sending previously confirmed text.
            if mode == "briefing" and existing_briefing and existing_briefing["text"]["delivered"]:
                if (config.briefing.generate_audio and not existing_briefing["audio"]["delivered"]
                        and not existing_briefing["audio"].get("uncertain")):
                    if not dry_run:
                        mark_uncertain(state, topic.id, existing_briefing_id, mode="briefing", part="audio")
                        _save(state_path, state, dry_run)
                        outcome = deliver_audio_only(notifier, topic.telegram.message_thread_id,
                                                     existing_briefing["summary_text"],
                                                     voice=config.tts.voice, rate=config.tts.rate)
                        if outcome.audio_uncertain:
                            mark_uncertain(state, topic.id, existing_briefing_id, mode="briefing", part="audio")
                        else:
                            record_delivery(state, topic.id, existing_briefing_id, mode="briefing",
                                            part="audio", success=outcome.audio_delivered,
                                            message_id=outcome.audio_message_id, error=outcome.audio_error)
                        _save(state_path, state, dry_run)
                        failures += not outcome.audio_delivered
                continue
            posts, errors = fetch(topic, timeout=config.app.request_timeout_seconds,
                                  max_retries=config.app.max_retries,
                                  max_age=timedelta(hours=hours) if mode == "briefing" else timedelta(days=2),
                                  now=now)
            if errors:
                LOG.warning("topic=%s feed_errors=%d", topic.id, len(errors))
                failures += len(errors)
            if mode == "alert":
                posts = [p for p in posts if not current["posts"].get(p.id, {}).get("alert", {}).get("delivered")
                         and not current["posts"].get(p.id, {}).get("alert", {}).get("uncertain")]
            else:
                posts = [p for p in posts if start <= p.published_at < end]
                if existing_briefing and (existing_briefing["text"]["delivered"]
                                          or existing_briefing["text"].get("uncertain")):
                    continue
            posts = posts[:config.app.max_posts_per_topic]
            if not posts:
                continue
            if dry_run:
                LOG.info("topic=%s mode=%s dry_run_candidates=%d", topic.id, mode, len(posts))
                continue
            results = analyze(topic, posts, threshold=config.alert.relevance_threshold,
                              model=config.app.default_model, max_retries=config.app.max_retries,
                              timeout_seconds=config.app.request_timeout_seconds,
                              api_key=os.environ.get("GEMINI_API_KEY"))
            if not results:
                continue
            if mode == "alert":
                for analysis in results:
                    source = str(analysis.source_urls[0])
                    post = next((p for p in posts if p.url == source), None)
                    if post is None:
                        raise ValueError("analysis source not found in topic posts")
                    if dry_run:
                        continue
                    record_post(state, topic.id, post, now=now)
                    mark_uncertain(state, topic.id, post.id, mode="alert")
                    _save(state_path, state, dry_run)
                    outcome = deliver(notifier, topic.telegram.message_thread_id, _text(analysis),
                                      audio=False,
                                      voice=config.tts.voice, rate=config.tts.rate)
                    if outcome.text_uncertain or (outcome.text_error and outcome.text_message_ids):
                        mark_uncertain(state, topic.id, post.id, mode="alert")
                    else:
                        record_delivery(state, topic.id, post.id, mode="alert",
                                        success=outcome.text_delivered,
                                        message_id=outcome.text_message_ids[-1] if outcome.text_delivered else None,
                                        error=outcome.text_error, now=now)
                    _save(state_path, state, dry_run)
                    failures += not outcome.text_delivered
            else:
                briefing_id = existing_briefing_id
                text = "\n\n".join(_text(item) for item in results)
                if dry_run:
                    continue
                briefing = record_briefing(state, topic.id, briefing_id, start, end,
                                           [p.id for p in posts],
                                           audio_required=config.briefing.generate_audio)
                briefing["summary_text"] = text
                mark_uncertain(state, topic.id, briefing_id, mode="briefing", part="text")
                _save(state_path, state, dry_run)
                outcome = deliver(notifier, topic.telegram.message_thread_id, text, audio=False)
                if outcome.text_uncertain or (outcome.text_error and outcome.text_message_ids):
                    mark_uncertain(state, topic.id, briefing_id, mode="briefing", part="text")
                else:
                    record_delivery(state, topic.id, briefing_id, mode="briefing", part="text",
                                    success=outcome.text_delivered,
                                    message_id=outcome.text_message_ids[-1] if outcome.text_delivered else None,
                                    error=outcome.text_error, now=now)
                _save(state_path, state, dry_run)
                audio_ok = True
                if outcome.text_delivered and config.briefing.generate_audio:
                    mark_uncertain(state, topic.id, briefing_id, mode="briefing", part="audio")
                    _save(state_path, state, dry_run)
                    audio_outcome = deliver_audio_only(notifier, topic.telegram.message_thread_id,
                                                       text, voice=config.tts.voice, rate=config.tts.rate)
                    audio_ok = audio_outcome.audio_delivered
                    if audio_outcome.audio_uncertain:
                        mark_uncertain(state, topic.id, briefing_id, mode="briefing", part="audio")
                    else:
                        record_delivery(state, topic.id, briefing_id, mode="briefing", part="audio",
                                        success=audio_ok, message_id=audio_outcome.audio_message_id,
                                        error=audio_outcome.audio_error, now=now)
                    _save(state_path, state, dry_run)
                failures += not outcome.text_delivered or not audio_ok
            LOG.info("topic=%s mode=%s processed=%d", topic.id, mode, len(posts))
        except Exception as exc:
            failures += 1
            LOG.error("topic=%s mode=%s failed=%s", topic.id, mode, type(exc).__name__)
    if not dry_run:
        cleaned = cleanup_state(state, now=now)
        if cleaned != state or (state != initial_state and not state_path.exists()):
            _save(state_path, cleaned, dry_run)
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Topic-isolated social listening agent")
    parser.add_argument("--mode", choices=("alert", "briefing"), required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--state", default="seen_posts.json")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    notifier = None
    try:
        config = load_config(args.config)
        if not args.dry_run:
            validate_live_config(config)
            notifier = TelegramNotifier(token=os.environ["TELEGRAM_BOT_TOKEN"],
                                        chat_id=os.environ["TELEGRAM_CHAT_ID"])
        return run(config, mode=args.mode, state_path=args.state,
                   dry_run=args.dry_run, notifier=notifier)
    except (ConfigError, ValueError) as exc:
        LOG.error("configuration or state error: %s", exc)
        return 2
    finally:
        if notifier:
            notifier.close()


if __name__ == "__main__":
    raise SystemExit(main())
