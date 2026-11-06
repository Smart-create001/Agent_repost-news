"""Versioned, topic-isolated state with atomic persistence."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import tempfile
from urllib.parse import urlsplit

PENDING_CAPACITY = 600
PENDING_TITLE_LIMIT = 500
PENDING_DESCRIPTION_LIMIT = 3000


def _utc_timestamp(value: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.utcoffset() is None:
            raise ValueError("timezone required")
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError) as exc:
        raise StateError(f"Malformed {field}") from exc


def _http_url(value: str, field: str) -> None:
    if not isinstance(value, str) or len(value) > 4096:
        raise StateError(f"Malformed {field}")
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise StateError(f"Malformed {field}")


def _validate_pending(topic: dict) -> None:
    from src.scraper import canonical_url

    pending = topic.get("pending_posts", {})
    if not isinstance(pending, dict) or len(pending) > PENDING_CAPACITY:
        raise StateError("Malformed or oversized pending_posts")
    for post_id, item in pending.items():
        if not isinstance(post_id, str) or not post_id or not isinstance(item, dict) or item.get("id") != post_id:
            raise StateError("Malformed pending entry identity")
        _http_url(item.get("canonical_url"), "pending canonical_url")
        try:
            if canonical_url(item["canonical_url"]) != item["canonical_url"]:
                raise StateError("Noncanonical pending URL")
        except ValueError as exc:
            raise StateError("Malformed pending canonical URL") from exc
        _http_url(item.get("source_feed"), "pending source_feed")
        if not isinstance(item.get("title"), str) or not item["title"].strip() or len(item["title"]) > PENDING_TITLE_LIMIT:
            raise StateError("Malformed pending title")
        if not isinstance(item.get("description"), str) or len(item["description"]) > PENDING_DESCRIPTION_LIMIT:
            raise StateError("Malformed pending description")
        if type(item.get("date_inferred")) is not bool:
            raise StateError("Malformed pending date_inferred")
        _utc_timestamp(item.get("published_at"), "pending published_at")
        _utc_timestamp(item.get("discovered_at"), "pending discovered_at")
    metrics = topic.get("pending_metrics", {"overflow_total": 0, "expired_total": 0})
    if not isinstance(metrics, dict) or any(type(metrics.get(key)) is not int or metrics[key] < 0
                                             for key in ("overflow_total", "expired_total")):
        raise StateError("Malformed pending metrics")
    if type(metrics.get("overflow_unresolved", False)) is not bool:
        raise StateError("Malformed pending overflow flag")


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
        _validate_pending(topic)
        alert_checkpoint = topic.get("alert_checkpoint_at")
        if alert_checkpoint is not None:
            try:
                parsed = datetime.fromisoformat(alert_checkpoint)
                if parsed.utcoffset() is None:
                    raise ValueError("timezone required")
            except (TypeError, ValueError) as exc:
                raise StateError("Malformed alert checkpoint") from exc


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


def queue_post(state: dict, topic_id: str, post, *, now: datetime,
               capacity: int = PENDING_CAPACITY) -> str:
    """Capture a bounded article without changing its delivery history."""
    if capacity < 1 or capacity > PENDING_CAPACITY:
        raise ValueError("invalid pending capacity")
    topic = ensure_topic(state, topic_id)
    topic.setdefault("pending_metrics", {"overflow_total": 0, "expired_total": 0})
    history = topic["posts"].get(post.id, {})
    if (history.get("status") in {"processed", "expired"}
            or history.get("alert", {}).get("delivered")):
        return "settled"
    pending = topic.setdefault("pending_posts", {})
    if post.id in pending:
        return "existing"
    if len(pending) >= capacity:
        metrics = topic.setdefault("pending_metrics", {"overflow_total": 0, "expired_total": 0})
        metrics["overflow_total"] += 1
        metrics["overflow_unresolved"] = True
        return "overflow"
    item = {
        "id": post.id, "canonical_url": post.url,
        "title": post.title.strip()[:PENDING_TITLE_LIMIT],
        "description": post.description[:PENDING_DESCRIPTION_LIMIT],
        "published_at": post.published_at.astimezone(timezone.utc).isoformat(),
        "source_feed": post.source_url,
        "discovered_at": now.astimezone(timezone.utc).isoformat(),
        "date_inferred": post.date_inferred,
    }
    _validate_pending({"pending_posts": {post.id: item}})
    pending[post.id] = item
    return "added"


def pending_as_posts(state: dict, topic_id: str, *, now: datetime,
                     window: timedelta) -> tuple[list, int]:
    """Return replayable articles; expire old items with an auditable status."""
    from src.scraper import Post

    topic = ensure_topic(state, topic_id)
    pending = topic.setdefault("pending_posts", {})
    metrics = topic.setdefault("pending_metrics", {"overflow_total": 0, "expired_total": 0})
    posts = []
    expired = 0
    for post_id, item in list(pending.items()):
        history = topic["posts"].get(post_id, {})
        alert = history.get("alert", {})
        if history.get("status") == "processed" or alert.get("delivered"):
            del pending[post_id]
            continue
        if alert.get("uncertain"):
            continue  # Quarantined until manually reconciled.
        published = _utc_timestamp(item["published_at"], "pending published_at")
        if published < now - window:
            history = topic["posts"].setdefault(post_id, {
                "id": post_id, "source_url": item["canonical_url"],
                "first_seen_at": item["discovered_at"], "last_attempt_at": None,
                "status": "expired", "retry_count": 0,
                "alert": {"delivered": False, "message_id": None},
                "last_error": "pending recovery window expired", "title": item["title"],
            })
            history["status"] = "expired"
            del pending[post_id]
            expired += 1
            continue
        posts.append(Post(id=post_id, url=item["canonical_url"], title=item["title"],
                          description=item["description"], published_at=published,
                          source_url=item["source_feed"], date_inferred=item["date_inferred"],
                          retrieved_at=_utc_timestamp(item["discovered_at"], "pending discovered_at")))
    metrics["expired_total"] += expired
    return posts, expired


def record_post(state: dict, topic_id: str, post, *, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    posts = ensure_topic(state, topic_id)["posts"]
    return posts.setdefault(post.id, {
        "id": post.id, "source_url": post.url, "first_seen_at": now.isoformat(),
        "last_attempt_at": None, "status": "pending", "retry_count": 0,
        "alert": {"delivered": False, "message_id": None},
        "last_error": None, "title": post.title,
    })


def mark_processed(state: dict, topic_id: str, post, *, now: datetime | None = None) -> None:
    """Remember an article that a complete AI response did not select for delivery."""
    now = now or datetime.now(timezone.utc)
    item = record_post(state, topic_id, post, now=now)
    if item["alert"].get("delivered") or item["alert"].get("uncertain"):
        return
    item["status"] = "processed"
    item["last_attempt_at"] = now.isoformat()


def record_briefing(state: dict, topic_id: str, briefing_id: str,
                    window_start: datetime, window_end: datetime, post_ids: list[str],
                    *, audio_required: bool = True) -> dict:
    item = {
        "id": briefing_id, "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(), "post_ids": list(post_ids),
        "text": {"delivered": False, "message_id": None},
        "audio_required": audio_required,
        "last_attempt_at": None, "retry_count": 0, "last_error": None,
    }
    if audio_required:
        item["audio"] = {"delivered": False, "message_id": None}
    return ensure_topic(state, topic_id)["briefings"].setdefault(briefing_id, item)


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
