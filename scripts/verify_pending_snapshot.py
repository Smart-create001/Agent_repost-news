"""Validate a captured queue from the expected Git commit on a fresh runner."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess

from src.config import load_config
from src.scraper import canonical_url
from src.state import StateError, validate_state


def _git(repo: str | Path, *args: str) -> bytes:
    try:
        return subprocess.run(["git", *args], cwd=repo, check=True,
                              capture_output=True, timeout=20).stdout
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise StateError("Expected snapshot Git object is unavailable") from exc


def verify_snapshot(repo: str | Path, expected_commit: str, expected_sha256: str,
                    *, processing_outcome: str,
                    config_path: str | Path = "config.yaml") -> dict:
    if not expected_commit or not expected_sha256:
        raise StateError("Expected commit and state hash are required")
    if processing_outcome != "skipped":
        raise StateError("Processing was not verified as skipped")
    resolved = _git(repo, "rev-parse", "--verify", f"{expected_commit}^{{commit}}").decode().strip()
    if resolved != expected_commit:
        raise StateError("Expected commit did not resolve exactly")
    raw = _git(repo, "show", f"{expected_commit}:seen_posts.json")
    actual_hash = hashlib.sha256(raw).hexdigest()
    if actual_hash != expected_sha256:
        raise StateError("Expected commit state SHA-256 mismatch")
    try:
        state = json.loads(raw)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise StateError("Expected commit contains invalid JSON state") from exc
    validate_state(state)
    config = load_config(config_path)
    configured = {topic.id: topic for topic in config.topics}
    active_topics = {topic.id for topic in config.topics if topic.enabled}
    pending_ids: dict[str, str] = {}
    for topic_id, topic_state in state["topics"].items():
        pending = topic_state.get("pending_posts", {})
        if pending and (topic_id not in configured or topic_id not in active_topics):
            raise StateError("Pending items belong to an unknown or disabled topic")
        for post_id, item in pending.items():
            expected_id = hashlib.sha256(canonical_url(item["canonical_url"]).encode()).hexdigest()
            if post_id != expected_id:
                raise StateError("Pending item ID does not match its canonical URL")
            owner = pending_ids.setdefault(post_id, topic_id)
            if owner != topic_id:
                raise StateError("Pending item is duplicated across topics")
    ai = state["topics"].get("ai_tech", {})
    main_head = _git(repo, "rev-parse", "refs/remotes/origin/main").decode().strip()
    main_state = _git(repo, "show", f"{main_head}:seen_posts.json")
    main_state_hash = hashlib.sha256(main_state).hexdigest()
    return {
        "expected_commit": expected_commit,
        "expected_sha256": expected_sha256,
        "actual_sha256": actual_hash,
        "state_schema": "validated",
        "pending_queue": "validated",
        "topic_isolation": "validated",
        "topic_pending": {topic_id: len(value.get("pending_posts", {}))
                          for topic_id, value in state["topics"].items()},
        "ai_pending": len(ai.get("pending_posts", {})),
        "main_head": main_head,
        "main_moved": main_head != expected_commit,
        "main_state_matches_expected": main_state_hash == expected_sha256,
        "gold_status": "not_invoked" if not any(topic.id == "gold_price" and topic.enabled for topic in config.topics) else "not_verified",
        "audio_status": "not_invoked" if not (config.features.audio_enabled or config.alert.generate_audio or config.briefing.generate_audio) else "not_verified",
        "gemini_status": "not_invoked",
        "telegram_status": "not_invoked",
        "live_delivery_status": "not_verified",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--processing-outcome", required=True)
    args = parser.parse_args()
    report = verify_snapshot(".", args.expected_commit, args.expected_sha256,
                             config_path=args.config,
                             processing_outcome=args.processing_outcome)
    for topic_id, count in sorted(report["topic_pending"].items()):
        print(f"topic={topic_id} pending={count}")
    print(f"state_schema={report['state_schema']} pending_queue={report['pending_queue']} topic_isolation={report['topic_isolation']}")
    print(f"expected_commit={report['expected_commit']} expected_sha256={report['expected_sha256']} actual_sha256={report['actual_sha256']}")
    print(f"main_head={report['main_head']} main_moved={str(report['main_moved']).lower()} main_state_matches_expected={str(report['main_state_matches_expected']).lower()}")
    print(f"gemini={report['gemini_status']} telegram={report['telegram_status']} audio={report['audio_status']} gold={report['gold_status']} live_delivery={report['live_delivery_status']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
