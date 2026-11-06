"""Read-only Phase 7 provider probe. Never sends Telegram or writes state."""

from datetime import datetime, timedelta, timezone
import hashlib
import os
from pathlib import Path
import sys

from src.brain import Analysis, BrainError, SourceNotice, analyze_topic
from src.config import load_config
from src.scraper import fetch_topic
from src.state import load_state


def main() -> int:
    config = load_config()
    topics = {topic.id: topic for topic in config.topics}
    if (set(topics) != {"ai_tech", "gold_price"} or not topics["ai_tech"].enabled
            or topics["gold_price"].enabled or config.features.audio_enabled
            or config.alert.generate_audio or config.briefing.generate_audio):
        raise RuntimeError("Read-only probe requires AI-only text configuration")
    if "edge_tts" in sys.modules:
        raise RuntimeError("Audio module loaded unexpectedly")
    state_path = Path("seen_posts.json")
    original_hash = hashlib.sha256(state_path.read_bytes()).hexdigest()
    topic = topics["ai_tech"]
    now = datetime.now(timezone.utc)
    posts, errors = fetch_topic(topic, now=now, max_age=timedelta(days=2),
                               timeout=config.app.request_timeout_seconds, max_retries=0)
    print(f"probe_feeds={len(topic.rss_urls)} feed_errors={len(errors)} recent_posts={len(posts)} gold_processed=false audio_called=false")
    if errors or not posts:
        raise RuntimeError("RSS probe did not produce a clean AI candidate set")
    preferred = next((post for post in posts if "Gemini Omni 1.2 Flash" in post.title
                      and "blog.google" in post.title), None)
    selected = preferred or next((post for post in posts if "Hugging Face" in post.title), posts[0])
    existing = load_state(state_path)["topics"].get("ai_tech", {}).get("posts", {})
    pending = [post for post in posts if not existing.get(post.id, {}).get("alert", {}).get("delivered")
               and not existing.get(post.id, {}).get("alert", {}).get("uncertain")
               and existing.get(post.id, {}).get("status") != "processed"]
    batch = pending[:config.ai.batch_size]
    if not batch:
        raise RuntimeError("No pending AI articles for batch probe")
    try:
        results = analyze_topic(topic, [selected], api_key=os.environ["GEMINI_API_KEY"],
                                model=config.app.default_model,
                                threshold=config.alert.relevance_threshold,
                                max_retries=1, timeout_seconds=config.app.request_timeout_seconds,
                                max_output_tokens=config.ai.max_output_tokens,
                                max_output_retries=config.ai.max_output_retries,
                                source_only_fallback=config.ai.source_only_fallback)
        batch_results = analyze_topic(topic, batch, api_key=os.environ["GEMINI_API_KEY"],
                                      model=config.app.default_model,
                                      threshold=config.alert.relevance_threshold,
                                      max_retries=1, timeout_seconds=config.app.request_timeout_seconds,
                                      max_output_tokens=config.ai.max_output_tokens,
                                      max_output_retries=config.ai.max_output_retries,
                                      source_only_fallback=config.ai.source_only_fallback)
    except BrainError as exc:
        print(f"probe_gemini=FAIL model={config.app.default_model} error_category={exc.error_category} "
              f"attempt={exc.attempt} finish_reason={exc.finish_reason} token_usage={exc.token_usage}")
        return 1
    finally:
        if hashlib.sha256(state_path.read_bytes()).hexdigest() != original_hash:
            raise RuntimeError("Read-only probe unexpectedly changed state")
    valid = len(results) == 1 and isinstance(results[0], (Analysis, SourceNotice))
    usage = results.token_usage
    print(f"probe_gemini={'PASS' if valid else 'FAIL'} model={config.app.default_model} "
          f"finish_reason={results.finish_reason} attempt={results.attempt} "
          f"input_tokens={usage.get('input_tokens')} output_tokens={usage.get('output_tokens')} "
          f"total_tokens={usage.get('total_tokens')} fallback={bool(valid and isinstance(results[0], SourceNotice))} "
          f"state_unchanged=true")
    batch_valid = all(isinstance(item, (Analysis, SourceNotice)) for item in batch_results)
    print(f"probe_batch={'PASS' if batch_valid else 'FAIL'} batch_size={len(batch)} "
          f"accepted={len(batch_results)} finish_reason={batch_results.finish_reason} "
          f"attempt={batch_results.attempt} token_usage={batch_results.token_usage} "
          f"state_unchanged=true")
    return 0 if valid and batch_valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
