from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.state import (StateError, cleanup_state, empty_state, load_state,
                       record_briefing, record_delivery, record_post, save_state)


NOW = datetime(2026, 10, 26, tzinfo=timezone.utc)
POST = SimpleNamespace(id="post-1", url="https://news.example.org/1", title="Story")


def test_tc2_04_same_url_across_topics():
    state = empty_state()
    record_post(state, "a", POST, now=NOW)
    record_post(state, "b", POST, now=NOW)
    record_delivery(state, "a", POST.id, mode="alert", success=True, message_id=7)
    assert state["topics"]["a"]["posts"][POST.id]["alert"]["delivered"]
    assert not state["topics"]["b"]["posts"][POST.id]["alert"]["delivered"]


def test_tc2_08_corrupt_state_refuses_overwrite(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{broken")
    with pytest.raises(StateError):
        load_state(path)
    with pytest.raises(StateError):
        save_state(path, empty_state())
    assert path.read_text() == "{broken"


def test_tc2_09_atomic_write_failure(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    save_state(path, empty_state())
    original = path.read_bytes()
    def fail(*args):
        raise OSError("simulated replace failure")
    monkeypatch.setattr("src.state.os.replace", fail)
    with pytest.raises(OSError):
        save_state(path, {"schema_version": 1, "topics": {"new": {"posts": {}, "briefings": {}}}})
    assert path.read_bytes() == original
    assert sorted(tmp_path.iterdir()) == [path]


def test_tc2_10_failed_delivery_and_retry_metadata():
    state = empty_state()
    item = record_post(state, "a", POST, now=NOW)
    record_delivery(state, "a", POST.id, mode="alert", success=False, error="network", now=NOW)
    assert not item["alert"]["delivered"] and item["retry_count"] == 1
    assert item["last_error"] == "network"
    with pytest.raises(ValueError):
        record_delivery(state, "a", POST.id, mode="alert", success=True)
    assert not item["alert"]["delivered"]


def test_tc2_11_alert_and_briefing_separate():
    state = empty_state()
    record_post(state, "a", POST, now=NOW)
    briefing = record_briefing(state, "a", "brief-1", NOW - timedelta(days=1), NOW, [POST.id])
    record_delivery(state, "a", POST.id, mode="alert", success=True, message_id=7)
    assert not briefing["text"]["delivered"] and not briefing["audio"]["delivered"]
    record_delivery(state, "a", "brief-1", mode="briefing", part="text", success=True, message_id=8)
    assert briefing["text"]["delivered"] and not briefing["audio"]["delivered"]


def test_tc2_12_reload_missing_dry_run_and_retention(tmp_path):
    path = tmp_path / "state.json"
    state = load_state(path)
    record_post(state, "a", POST, now=NOW - timedelta(days=100))
    save_state(path, state, dry_run=True)
    assert not path.exists()
    save_state(path, state)
    assert load_state(path) == state
    assert POST.id in cleanup_state(state, now=NOW)["topics"]["a"]["posts"]
    record_delivery(state, "a", POST.id, mode="alert", success=True, message_id=1)
    assert POST.id not in cleanup_state(state, now=NOW)["topics"]["a"]["posts"]
    assert POST.id in state["topics"]["a"]["posts"]
