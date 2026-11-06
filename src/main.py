"""Sequential topic pipeline for alert and briefing modes."""

import argparse
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import logging
import os
from pathlib import Path

from src.brain import Analysis, SourceNotice, analyze_topic
from src.config import ConfigError, load_config, validate_live_config
from src.editorial import render_news
from src.notifier import TelegramNotifier, deliver
from src.scraper import fetch_topic
from src.state import (cleanup_state, ensure_topic, load_state, mark_processed,
                       mark_uncertain, record_briefing, record_delivery, record_post,
                       save_state)


LOG = logging.getLogger(__name__)


def _window(now: datetime, hours: int) -> tuple[datetime, datetime]:
    epoch_hours = int(now.timestamp()) // (hours * 3600)
    start = datetime.fromtimestamp(epoch_hours * hours * 3600, timezone.utc)
    return start, start + timedelta(hours=hours)


def _text(analysis) -> str:
    if isinstance(analysis, (Analysis, SourceNotice)):
        return render_news(analysis)
    return analysis.summary_th + "\n" + "\n".join(map(str, analysis.source_urls))


def _save(state_path: Path, state: dict, dry_run: bool) -> None:
    save_state(state_path, state, dry_run=dry_run)


def run(config, *, mode: str, state_path: str | Path = "seen_posts.json",
        dry_run: bool = False, fetch=fetch_topic, analyze=analyze_topic,
        notifier=None, now: datetime | None = None) -> int:
    """Return 0 on clean processing, 1 for topic errors, 2 for config/state errors."""
    if mode not in {"alert", "briefing"}:
        raise ValueError("mode must be alert or briefing")
    if config.features.audio_enabled:
        raise ValueError("audio_enabled=true is not supported in this text-only release")
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
            topic_failures = failures
            hours = config.briefing.reporting_hours
            start, end = _window(now, hours)
            current = state["topics"].get(topic.id, {"posts": {}, "briefings": {}})
            alert_checkpoint = None
            if mode == "alert":
                alert_checkpoint = datetime.fromisoformat(
                    current.get("alert_checkpoint_at", (now - timedelta(days=2)).isoformat()))
                if not dry_run and "alert_checkpoint_at" not in current:
                    ensure_topic(state, topic.id)["alert_checkpoint_at"] = alert_checkpoint.isoformat()
                    _save(state_path, state, dry_run)
                lookback = min(config.alert.catchup_hours,
                               max(48, (now - alert_checkpoint).total_seconds() / 3600))
                if lookback >= config.alert.catchup_hours and now - alert_checkpoint > timedelta(hours=lookback):
                    LOG.warning("topic=%s catchup_window_capped_hours=%d", topic.id, config.alert.catchup_hours)
            existing_briefing_id = f"{topic.id}:briefing:{start.isoformat()}"
            existing_briefing = current["briefings"].get(existing_briefing_id)
            # Preserve existing delivered text and historical audio state; never retry audio.
            if mode == "briefing" and existing_briefing and existing_briefing["text"]["delivered"]:
                continue
            posts, errors = fetch(topic, timeout=config.app.request_timeout_seconds,
                                  max_retries=config.app.max_retries,
                                  max_age=timedelta(hours=hours if mode == "briefing" else lookback),
                                  now=now)
            if errors:
                LOG.warning("topic=%s feed_errors=%d", topic.id, len(errors))
                failures += len(errors)
            if mode == "alert":
                posts = [p for p in posts if not current["posts"].get(p.id, {}).get("alert", {}).get("delivered")
                         and not current["posts"].get(p.id, {}).get("alert", {}).get("uncertain")
                         and current["posts"].get(p.id, {}).get("status") != "processed"]
                pending_posts = posts
                older = [p for p in posts if p.published_at < now - timedelta(days=2)]
                fresh = [p for p in posts if p.published_at >= now - timedelta(days=2)]
                older_limit = min(config.alert.catchup_posts_per_run, config.app.max_posts_per_topic)
                posts = (older[:older_limit] + fresh[:config.app.max_posts_per_topic - min(len(older), older_limit)])
                LOG.info("topic=%s catchup_candidates=%d catchup_selected=%d",
                         topic.id, len(older), min(len(older), older_limit))
            else:
                posts = [p for p in posts if start <= p.published_at < end]
                if existing_briefing and (existing_briefing["text"]["delivered"]
                                          or existing_briefing["text"].get("uncertain")):
                    continue
                posts = posts[:config.app.max_posts_per_topic]
            if not posts:
                if mode == "alert" and not dry_run and failures == topic_failures:
                    ensure_topic(state, topic.id)["alert_checkpoint_at"] = now.isoformat()
                    _save(state_path, state, dry_run)
                continue
            if dry_run:
                LOG.info("topic=%s mode=%s dry_run_candidates=%d", topic.id, mode, len(posts))
                continue
            results = []
            batch_failed = False
            for offset in range(0, len(posts), config.ai.batch_size):
                batch = posts[offset:offset + config.ai.batch_size]
                try:
                    batch_results = analyze(topic, batch, threshold=config.alert.relevance_threshold,
                                            model=config.app.default_model, max_retries=config.app.max_retries,
                                            timeout_seconds=config.app.request_timeout_seconds,
                                            max_output_tokens=config.ai.max_output_tokens,
                                            max_output_retries=config.ai.max_output_retries,
                                            source_only_fallback=config.ai.source_only_fallback,
                                            api_key=os.environ.get("GEMINI_API_KEY"))
                    results.extend(batch_results)
                    if mode == "alert":
                        irrelevant_sources = getattr(batch_results, "irrelevant_sources", ())
                        for post in batch:
                            if post.url in irrelevant_sources:
                                mark_processed(state, topic.id, post, now=now)
                        if any(post.url in irrelevant_sources for post in batch):
                            _save(state_path, state, dry_run)
                    if getattr(batch_results, "rejected_count", 0):
                        failures += 1
                        batch_failed = True
                        LOG.error("topic=%s mode=%s batch_start=%d quarantined=%d",
                                  topic.id, mode, offset, batch_results.rejected_count)
                except Exception as exc:
                    failures += 1
                    batch_failed = True
                    LOG.error("topic=%s mode=%s batch_start=%d batch_size=%d failed=%s",
                              topic.id, mode, offset, len(batch), type(exc).__name__)
            # A delivered briefing closes its time window; do not close an incomplete one.
            if mode == "briefing" and batch_failed:
                continue
            if not results and mode == "briefing":
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
                    if not outcome.text_delivered:
                        LOG.error("topic=%s mode=%s delivery_failed=1 uncertain=%s", topic.id, mode,
                                  bool(outcome.text_uncertain or (outcome.text_error and outcome.text_message_ids)))
                    if outcome.text_uncertain or (outcome.text_error and outcome.text_message_ids):
                        mark_uncertain(state, topic.id, post.id, mode="alert")
                    else:
                        record_delivery(state, topic.id, post.id, mode="alert",
                                        success=outcome.text_delivered,
                                        message_id=outcome.text_message_ids[-1] if outcome.text_delivered else None,
                                        error=outcome.text_error, now=now)
                    _save(state_path, state, dry_run)
                    failures += not outcome.text_delivered
                if failures == topic_failures and all(
                    state["topics"][topic.id]["posts"].get(p.id, {}).get("status") == "processed"
                    or state["topics"][topic.id]["posts"].get(p.id, {}).get("alert", {}).get("delivered")
                    for p in pending_posts
                ):
                    ensure_topic(state, topic.id)["alert_checkpoint_at"] = now.isoformat()
                    _save(state_path, state, dry_run)
            else:
                briefing_id = existing_briefing_id
                text = "\n\n".join(_text(item) for item in results)
                if dry_run:
                    continue
                briefing = record_briefing(state, topic.id, briefing_id, start, end,
                                           [p.id for p in posts],
                                           audio_required=False)
                briefing["summary_text"] = text
                mark_uncertain(state, topic.id, briefing_id, mode="briefing", part="text")
                _save(state_path, state, dry_run)
                outcome = deliver(notifier, topic.telegram.message_thread_id, text, audio=False)
                if not outcome.text_delivered:
                    LOG.error("topic=%s mode=%s delivery_failed=1 uncertain=%s", topic.id, mode,
                              bool(outcome.text_uncertain or (outcome.text_error and outcome.text_message_ids)))
                if outcome.text_uncertain or (outcome.text_error and outcome.text_message_ids):
                    mark_uncertain(state, topic.id, briefing_id, mode="briefing", part="text")
                else:
                    record_delivery(state, topic.id, briefing_id, mode="briefing", part="text",
                                    success=outcome.text_delivered,
                                    message_id=outcome.text_message_ids[-1] if outcome.text_delivered else None,
                                    error=outcome.text_error, now=now)
                _save(state_path, state, dry_run)
                failures += not outcome.text_delivered
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
    # HTTP request URLs can contain Telegram bot credentials.
    logging.getLogger("httpx").setLevel(logging.WARNING)
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
