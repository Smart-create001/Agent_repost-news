"""Phase 7 pending queue recovery and Git durability gates."""

from copy import deepcopy
from datetime import timedelta
import json
import logging
import subprocess

import pytest

from src.brain import AnalysisResults, BrainError
from src.main import capture_pending, run
from src.notifier import DeliveryResult
from src.persistence import verify_remote_state
from src.state import StateError, empty_state, load_state, save_state
from tests.test_phase7_stability import NOW, live_like_fetch, make_post, selected_analyses
from tests.test_pipeline import settings
from tests.test_workflow import SCRIPT, git, setup_git


def persist(work):
    result = subprocess.run(["bash", str(SCRIPT)], cwd=work, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return git(work, "rev-parse", "HEAD")


def next_run(tmp_path, remote, name="next-run"):
    clone = tmp_path / name
    git(tmp_path, "clone", "-b", "main", str(remote), str(clone))
    git(clone, "config", "user.name", "Test")
    git(clone, "config", "user.email", "test@example.org")
    return clone


def prepare(tmp_path, posts):
    work, remote = setup_git(tmp_path)
    path = work / "seen_posts.json"
    assert capture_pending(settings(False), state_path=path,
                           fetch=live_like_fetch(posts), now=NOW) == 0
    return work, remote, path


def test_backward_compatible_migration_preserves_delivery_history(tmp_path):
    work, _ = setup_git(tmp_path)
    path = work / "seen_posts.json"
    legacy = empty_state()
    legacy["topics"]["ai_tech"] = {"posts": {"delivered": {
        "id": "delivered", "source_url": "https://news.example.org/delivered",
        "first_seen_at": NOW.isoformat(), "last_attempt_at": NOW.isoformat(),
        "status": "delivered", "retry_count": 1,
        "alert": {"delivered": True, "message_id": 42},
        "last_error": None, "title": "Delivered"}}, "briefings": {}}
    save_state(path, legacy)
    before = deepcopy(legacy["topics"]["ai_tech"]["posts"])
    assert capture_pending(settings(False), state_path=path,
                           fetch=live_like_fetch([make_post("new", NOW)]), now=NOW) == 0
    migrated = load_state(path)["topics"]["ai_tech"]
    assert migrated["posts"] == before
    assert "new" in migrated["pending_posts"]
    assert migrated["pending_posts"]["new"]["discovered_at"] == NOW.isoformat()


def test_runner_failure_before_push_cannot_process_or_advance_checkpoint(tmp_path, monkeypatch):
    work, remote, path = prepare(tmp_path, [make_post("new", NOW)])
    old_commit = git(work, "rev-parse", "HEAD")
    monkeypatch.setattr("src.main.deliver", lambda *a, **k: pytest.fail("no delivery before push"))
    with pytest.raises(StateError, match="differs"):
        run(settings(False), mode="alert", state_path=path, durable_pending=True,
            persisted_commit=old_commit, git_repo=work, analyze=selected_analyses,
            notifier=object(), now=NOW)
    clone = next_run(tmp_path, remote)
    assert load_state(clone / "seen_posts.json") == empty_state()
    assert "new" in load_state(path)["topics"]["ai_tech"]["pending_posts"]


def test_after_push_feed_eviction_resume_and_no_duplicate(tmp_path, monkeypatch):
    work, remote, path = prepare(tmp_path, [make_post("new", NOW)])
    captured_commit = persist(work)
    clone = next_run(tmp_path, remote)
    clone_path = clone / "seen_posts.json"
    calls = []
    monkeypatch.setattr("src.main.deliver", lambda *a, **k:
                        (calls.append(1) or DeliveryResult(text_message_ids=[101])))
    assert run(settings(False), mode="alert", state_path=clone_path,
               durable_pending=True, persisted_commit=captured_commit, git_repo=clone,
               fetch=lambda *a, **k: pytest.fail("feed disappeared"),
               analyze=selected_analyses, notifier=object(), now=NOW) == 0
    persist(clone)
    resumed = next_run(tmp_path, remote, "third-run")
    resumed_path = resumed / "seen_posts.json"
    state = load_state(resumed_path)["topics"]["ai_tech"]
    assert state["posts"]["new"]["alert"] == {"delivered": True, "message_id": 101}
    assert not state["pending_posts"]
    assert run(settings(False), mode="alert", state_path=resumed_path,
               durable_pending=True, persisted_commit=git(resumed, "rev-parse", "HEAD"),
               git_repo=resumed, analyze=selected_analyses, notifier=object(), now=NOW) == 0
    assert calls == [1]


def test_conflict_refuses_capture_push_and_preserves_remote_checkpoint(tmp_path):
    work, remote, path = prepare(tmp_path, [make_post("new", NOW)])
    other = next_run(tmp_path, remote, "other")
    changed = load_state(other / "seen_posts.json")
    changed["topics"]["other"] = {"posts": {}, "briefings": {},
                                    "alert_checkpoint_at": NOW.isoformat()}
    save_state(other / "seen_posts.json", changed)
    persist(other)
    result = subprocess.run(["bash", str(SCRIPT)], cwd=work, capture_output=True, text=True)
    assert result.returncode == 3
    saved = load_state(next_run(tmp_path, remote) / "seen_posts.json")
    assert "ai_tech" not in saved["topics"]
    assert "new" in load_state(path)["topics"]["ai_tech"]["pending_posts"]


def test_queue_capacity_300_reports_overflow_and_keeps_first_300(tmp_path, caplog):
    work, _ = setup_git(tmp_path)
    path = work / "seen_posts.json"
    posts = [make_post(f"post-{i}", NOW) for i in range(301)]
    with caplog.at_level(logging.INFO):
        assert capture_pending(settings(False), state_path=path,
                               fetch=live_like_fetch(posts), now=NOW) == 1
    state = load_state(path)["topics"]["ai_tech"]
    assert len(state["pending_posts"]) == 300
    assert state["pending_metrics"]["overflow_total"] == 1
    assert state["pending_metrics"]["overflow_unresolved"] is True
    assert "overflow=1" in caplog.text
    assert "alert_checkpoint_at" not in state


def test_unresolved_overflow_blocks_checkpoint_even_after_queue_drains(tmp_path):
    work, _, path = prepare(tmp_path, [make_post("one", NOW)])
    state = load_state(path)
    topic = state["topics"]["ai_tech"]
    topic["pending_posts"] = {}
    topic["pending_metrics"]["overflow_unresolved"] = True
    topic["pending_metrics"]["overflow_total"] = 1
    save_state(path, state)
    commit = persist(work)
    assert run(settings(False), mode="alert", state_path=path, durable_pending=True,
               persisted_commit=commit, git_repo=work, notifier=object(), now=NOW) == 0
    assert "alert_checkpoint_at" not in load_state(path)["topics"]["ai_tech"]


def test_corrupt_pending_entry_refuses_load_and_overwrite(tmp_path):
    path = tmp_path / "seen_posts.json"
    bad = {"schema_version": 1, "topics": {"ai_tech": {
        "posts": {}, "briefings": {}, "pending_posts": {"x": {
            "id": "x", "canonical_url": "https://example.org/x",
            "title": "X", "description": "Description", "published_at": "invalid",
            "source_feed": "https://example.org/feed", "discovered_at": NOW.isoformat(),
            "date_inferred": False}}}}}
    path.write_text(json.dumps(bad))
    with pytest.raises(StateError, match="published_at"):
        load_state(path)
    with pytest.raises(StateError):
        save_state(path, empty_state())
    assert json.loads(path.read_text()) == bad


def test_gemini_failure_preserves_queue_and_retry(tmp_path, monkeypatch):
    work, _, path = prepare(tmp_path, [make_post("new", NOW)])
    commit = persist(work)
    monkeypatch.setattr("src.main.deliver", lambda *a, **k:
                        DeliveryResult(text_message_ids=[77]))
    def fail(*a, **k):
        raise BrainError("simulated", error_category="SERVER_ERROR")
    assert run(settings(False), mode="alert", state_path=path, durable_pending=True,
               persisted_commit=commit, git_repo=work, analyze=fail,
               notifier=object(), now=NOW) == 1
    assert "new" in load_state(path)["topics"]["ai_tech"]["pending_posts"]
    assert run(settings(False), mode="alert", state_path=path, durable_pending=True,
               persisted_commit=commit, git_repo=work, analyze=selected_analyses,
               notifier=object(), now=NOW) == 0
    assert load_state(path)["topics"]["ai_tech"]["posts"]["new"]["alert"]["delivered"]


def test_telegram_failure_remains_pending_and_uncertain_quarantined(tmp_path, monkeypatch):
    work, remote, path = prepare(tmp_path, [make_post("failed", NOW), make_post("unknown", NOW)])
    commit = persist(work)
    outcomes = iter([DeliveryResult(text_error="rejected"),
                     DeliveryResult(text_uncertain=True, text_error="timeout")])
    monkeypatch.setattr("src.main.deliver", lambda *a, **k: next(outcomes))
    assert run(settings(False), mode="alert", state_path=path, durable_pending=True,
               persisted_commit=commit, git_repo=work, analyze=selected_analyses,
               notifier=object(), now=NOW) == 1
    persist(work)
    clone = next_run(tmp_path, remote)
    state = load_state(clone / "seen_posts.json")["topics"]["ai_tech"]
    assert set(state["pending_posts"]) == {"failed", "unknown"}
    assert not state["posts"]["failed"]["alert"]["delivered"]
    assert state["posts"]["unknown"]["alert"]["uncertain"]
    assert "alert_checkpoint_at" not in state
    attempts = []
    monkeypatch.setattr("src.main.deliver", lambda *a, **k:
                        (attempts.append(1) or DeliveryResult(text_message_ids=[88])))
    assert run(settings(False), mode="alert", state_path=clone / "seen_posts.json",
               durable_pending=True, persisted_commit=git(clone, "rev-parse", "HEAD"),
               git_repo=clone, analyze=selected_analyses, notifier=object(), now=NOW) == 0
    assert attempts == [1]
    remaining = load_state(clone / "seen_posts.json")["topics"]["ai_tech"]
    assert set(remaining["pending_posts"]) == {"unknown"}


def test_confirmed_irrelevant_is_recorded_without_reprocessing(tmp_path):
    work, _, path = prepare(tmp_path, [make_post("irrelevant", NOW)])
    commit = persist(work)
    analyzed = []
    def irrelevant(_topic, posts, **kw):
        analyzed.append(len(posts))
        return AnalysisResults([], irrelevant_sources=[posts[0].url])
    assert run(settings(False), mode="alert", state_path=path, durable_pending=True,
               persisted_commit=commit, git_repo=work, analyze=irrelevant,
               notifier=object(), now=NOW) == 0
    persist(work)
    assert load_state(path)["topics"]["ai_tech"]["posts"]["irrelevant"]["status"] == "processed"
    assert run(settings(False), mode="alert", state_path=path, durable_pending=True,
               persisted_commit=git(work, "rev-parse", "HEAD"), git_repo=work,
               analyze=irrelevant, notifier=object(), now=NOW) == 0
    assert analyzed == [1]


def test_expiry_metrics_and_disabled_topic_isolation(tmp_path):
    work, _, path = prepare(tmp_path, [make_post("old", NOW)])
    state = load_state(path)
    state["topics"]["gold_price"] = {"posts": {"keep": {
        "alert": {"delivered": True}, "first_seen_at": NOW.isoformat()}},
                                        "briefings": {}}
    save_state(path, state)
    commit = persist(work)
    assert run(settings(False), mode="alert", state_path=path, durable_pending=True,
               persisted_commit=commit, git_repo=work, analyze=selected_analyses,
               notifier=object(), now=NOW + timedelta(days=8)) == 0
    current = load_state(path)["topics"]
    assert current["gold_price"] == state["topics"]["gold_price"]
    assert current["ai_tech"]["pending_metrics"]["expired_total"] == 1
    assert current["ai_tech"]["posts"]["old"]["status"] == "expired"
    assert not current["ai_tech"]["pending_posts"]


def test_three_old_per_run_and_next_run_resume(tmp_path, monkeypatch):
    work, _, path = prepare(tmp_path, [make_post(f"old-{i}", NOW)
                                      for i in range(5)])
    commit = persist(work)
    sent = []
    monkeypatch.setattr("src.main.deliver", lambda *a, **k:
                        (sent.append(1) or DeliveryResult(text_message_ids=[len(sent)])))
    args = dict(mode="alert", state_path=path, durable_pending=True,
                git_repo=work, analyze=selected_analyses, notifier=object(),
                now=NOW + timedelta(days=3))
    assert run(settings(False), persisted_commit=commit, **args) == 0
    state = load_state(path)["topics"]["ai_tech"]
    assert len(sent) == 3 and len(state["pending_posts"]) == 2
    assert state.get("alert_checkpoint_at") is None
    commit = persist(work)
    assert run(settings(False), persisted_commit=commit, **args) == 0
    assert len(sent) == 5
    assert not load_state(path)["topics"]["ai_tech"]["pending_posts"]


def test_post_delivery_push_failure_leaves_remote_uncertain(tmp_path, monkeypatch):
    work, remote, path = prepare(tmp_path, [make_post("new", NOW)])
    commit = persist(work)
    from src import main
    real_persist = main.persist_remote_state
    calls = []
    def fail_second(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise StateError("simulated push failure")
        return real_persist(*args, **kwargs)
    monkeypatch.setattr(main, "persist_remote_state", fail_second)
    monkeypatch.setattr("src.main.deliver", lambda *a, **k:
                        DeliveryResult(text_message_ids=[123]))
    assert run(settings(False), mode="alert", state_path=path, durable_pending=True,
               persisted_commit=commit, git_repo=work, analyze=selected_analyses,
               notifier=object(), now=NOW) == 1
    clone = next_run(tmp_path, remote)
    remote_state = load_state(clone / "seen_posts.json")["topics"]["ai_tech"]
    assert remote_state["posts"]["new"]["alert"]["uncertain"]
    assert "new" in remote_state["pending_posts"]
    assert remote_state.get("alert_checkpoint_at") is None


def test_pre_delivery_push_failure_prevents_telegram(tmp_path, monkeypatch):
    work, remote, path = prepare(tmp_path, [make_post("new", NOW)])
    commit = persist(work)
    monkeypatch.setattr("src.main.persist_remote_state", lambda *a, **k:
                        (_ for _ in ()).throw(StateError("simulated push failure")))
    monkeypatch.setattr("src.main.deliver", lambda *a, **k:
                        pytest.fail("Telegram called without persisted intent"))
    assert run(settings(False), mode="alert", state_path=path, durable_pending=True,
               persisted_commit=commit, git_repo=work, analyze=selected_analyses,
               notifier=object(), now=NOW) == 1
    remote_state = load_state(next_run(tmp_path, remote) / "seen_posts.json")["topics"]["ai_tech"]
    assert "new" in remote_state["pending_posts"]
    assert "new" not in remote_state["posts"]
