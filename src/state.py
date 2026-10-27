"""Versioned, topic-isolated state with atomic persistence."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import tempfile


class StateError(ValueError):
    """State cannot be read or safely changed."""


def empty_state() -> dict:
    return {"schema_version": 1, "topics": {}}


def validate_state(state: dict) -> None:
    if (not isinstance(state, dict) or state.get("schema_version") != 1
            or not isinstance(state.get("topics"), dict)):
        raise StateError("Unsupported or malformed state")
    for topic in state["topics"].values():
        if not isinstance(topic, dict) or not isinstance(topic.get("posts"), dict) or not isinstance(topic.get("briefings"), dict):
            raise StateError("Malformed topic state")


def load_state(path: str | Path) -> dict:
    path = Path(path)
    if not path.exists():
        return empty_state()
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise StateError(f"Cannot read valid state from {path}; original left untouched") from exc
    validate_state(state)
    return state


def save_state(path: str | Path, state: dict, *, dry_run: bool = False) -> None:
    if dry_run:
        return
    path = Path(path)
    if path.exists():
        load_state(path)  # Refuse to overwrite corrupted existing state.
    validate_state(state)
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=f".{path.name}.", delete=False) as stream:
            temp_name = stream.name
            json.dump(state, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
        temp_name = None
    finally:
        if temp_name is not None:
            Path(temp_name).unlink(missing_ok=True)


def ensure_topic(state: dict, topic_id: str) -> dict:
    return state["topics"].setdefault(topic_id, {"posts": {}, "briefings": {}})


def record_post(state: dict, topic_id: str, post, *, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    posts = ensure_topic(state, topic_id)["posts"]
    return posts.setdefault(post.id, {
        "id": post.id, "source_url": post.url, "first_seen_at": now.isoformat(),
        "last_attempt_at": None, "status": "pending", "retry_count": 0,
        "alert": {"delivered": False, "message_id": None},
        "last_error": None, "title": post.title,
    })


def record_briefing(state: dict, topic_id: str, briefing_id: str,
                    window_start: datetime, window_end: datetime, post_ids: list[str],
                    *, audio_required: bool = True) -> dict:
    return ensure_topic(state, topic_id)["briefings"].setdefault(briefing_id, {
        "id": briefing_id, "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(), "post_ids": list(post_ids),
        "text": {"delivered": False, "message_id": None},
        "audio": {"delivered": False, "message_id": None},
        "audio_required": audio_required,
        "last_attempt_at": None, "retry_count": 0, "last_error": None,
    })


def record_delivery(state: dict, topic_id: str, item_id: str, *, mode: str,
                    success: bool, message_id: int | None = None,
                    error: str | None = None, now: datetime | None = None,
                    part: str = "text") -> None:
    if mode not in {"alert", "briefing"} or (mode == "briefing" and part not in {"text", "audio"}):
        raise ValueError("unsupported delivery mode or part")
    if success and message_id is None:
        raise ValueError("successful delivery requires message_id")
    now = now or datetime.now(timezone.utc)
    topic = ensure_topic(state, topic_id)
    item = topic["posts"][item_id] if mode == "alert" else topic["briefings"][item_id]
    item["last_attempt_at"] = now.isoformat()
    item["retry_count"] += 1
    item["alert" if mode == "alert" else part].pop("uncertain", None)
    if success:
        item["alert" if mode == "alert" else part] = {"delivered": True, "message_id": message_id}
        item["last_error"] = None
        if mode == "alert":
            item["status"] = "delivered"
    else:
        item["last_error"] = error or "delivery failed"


def mark_uncertain(state: dict, topic_id: str, item_id: str, *, mode: str,
                   part: str = "text") -> None:
    topic = ensure_topic(state, topic_id)
    item = topic["posts"][item_id] if mode == "alert" else topic["briefings"][item_id]
    item["alert" if mode == "alert" else part]["uncertain"] = True
    item["last_error"] = "delivery outcome unknown; manual reconciliation required"


def cleanup_state(state: dict, *, now: datetime | None = None, retention_days: int = 90) -> dict:
    """Return a pruned copy; retain pending posts and incomplete briefings."""
    if retention_days < 1:
        raise ValueError("retention_days must be positive")
    now = now or datetime.now(timezone.utc)
    result = deepcopy(state)
    cutoff = now - timedelta(days=retention_days)
    for topic in result["topics"].values():
        topic["posts"] = {key: value for key, value in topic["posts"].items()
                          if not value["alert"]["delivered"] or datetime.fromisoformat(value["first_seen_at"]) >= cutoff}
        topic["briefings"] = {key: value for key, value in topic["briefings"].items()
                              if not (value["text"]["delivered"] and
                                      (not value.get("audio_required", True) or value["audio"]["delivered"]))
                              or datetime.fromisoformat(value["window_end"]) >= cutoff}
    return result
