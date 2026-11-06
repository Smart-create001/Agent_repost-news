"""Fresh-runner verification always reads the explicitly expected Git object."""

from hashlib import sha256
import json

import pytest

from src.scraper import canonical_url
from src.state import StateError
from scripts.verify_pending_snapshot import verify_snapshot
from tests.test_workflow import git, setup_git


ROOT = __import__("pathlib").Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config.yaml"
PENDING_FIXTURE = ROOT / "tests/fixtures/pending_queue_321.json"


def commit_state(work, state):
    state_path = work / "seen_posts.json"
    state_path.write_text(json.dumps(state, separators=(",", ":")))
    git(work, "add", "seen_posts.json")
    git(work, "commit", "-m", "capture pending state")
    git(work, "push", "origin", "main")
    git(work, "fetch", "origin", "main")
    return git(work, "rev-parse", "HEAD"), sha256(state_path.read_bytes()).hexdigest()


def empty_state():
    return {"schema_version": 1, "topics": {
        "ai_tech": {"posts": {}, "briefings": {}, "pending_posts": {},
                    "pending_metrics": {"overflow_total": 0, "expired_total": 0,
                                        "overflow_unresolved": False}},
        "gold_price": {"posts": {}, "briefings": {}}}}


def test_expected_snapshot_hash_and_state_are_validated_from_git_object(tmp_path):
    work, _ = setup_git(tmp_path)
    state = empty_state()
    url = canonical_url("https://news.example.org/story?utm_source=rss")
    post_id = sha256(url.encode()).hexdigest()
    state["topics"]["ai_tech"]["pending_posts"][post_id] = {
        "id": post_id, "canonical_url": url, "title": "AI story",
        "description": "bounded source description", "published_at": "2026-11-06T00:00:00+00:00",
        "source_feed": "https://news.example.org/feed", "discovered_at": "2026-11-06T01:00:00+00:00",
        "date_inferred": False}
    commit, digest = commit_state(work, state)
    report = verify_snapshot(work, commit, digest, processing_outcome="skipped", config_path=CONFIG)
    assert report["state_schema"] == report["pending_queue"] == report["topic_isolation"] == "validated"
    assert report["topic_pending"] == {"ai_tech": 1, "gold_price": 0}
    assert report["main_state_matches_expected"] is True
    assert report["gemini_status"] == report["telegram_status"] == "not_invoked"
    assert report["live_delivery_status"] == "not_verified"


def test_hash_mismatch_fails_before_reporting_validated(tmp_path):
    work, _ = setup_git(tmp_path)
    commit, _ = commit_state(work, empty_state())
    with pytest.raises(StateError, match="SHA-256 mismatch"):
        verify_snapshot(work, commit, "0" * 64, processing_outcome="skipped", config_path=CONFIG)


@pytest.mark.parametrize("commit,digest", [("", ""), ("a" * 40, "")])
def test_missing_expected_commit_or_sha_fails_closed(tmp_path, commit, digest):
    work, _ = setup_git(tmp_path)
    with pytest.raises(StateError, match="required"):
        verify_snapshot(work, commit, digest, processing_outcome="skipped", config_path=CONFIG)


@pytest.mark.parametrize("commit,digest", [("", "a" * 64), ("f" * 40, "a" * 64)])
def test_missing_or_unavailable_expected_commit_fails(tmp_path, commit, digest):
    work, _ = setup_git(tmp_path)
    with pytest.raises(StateError):
        verify_snapshot(work, commit, digest, processing_outcome="skipped", config_path=CONFIG)


def test_fresh_runner_reads_expected_commit_when_main_has_advanced(tmp_path):
    work, remote = setup_git(tmp_path)
    expected, digest = commit_state(work, empty_state())
    clone = tmp_path / "fresh"
    git(tmp_path, "clone", "-b", "main", str(remote), str(clone))
    git(clone, "config", "user.name", "Test")
    git(clone, "config", "user.email", "test@example.org")
    newer_state = empty_state()
    newer_state["topics"]["ai_tech"]["pending_metrics"]["expired_total"] = 1
    commit_state(clone, newer_state)
    report = verify_snapshot(clone, expected, digest, processing_outcome="skipped", config_path=CONFIG)
    assert report["main_moved"] is True
    assert report["main_state_matches_expected"] is False
    assert report["expected_commit"] == expected


def test_immutable_321_item_fixture_verifies_from_expected_git_object(tmp_path):
    work, _ = setup_git(tmp_path)
    state_bytes = PENDING_FIXTURE.read_bytes()
    state = json.loads(state_bytes)
    assert len(state["topics"]["ai_tech"]["pending_posts"]) == 321
    assert not state["topics"]["gold_price"].get("pending_posts", {})
    state_path = work / "seen_posts.json"
    state_path.write_bytes(state_bytes)
    git(work, "add", "seen_posts.json")
    git(work, "commit", "-m", "add immutable pending fixture")
    git(work, "push", "origin", "main")
    git(work, "fetch", "origin", "main")
    expected = git(work, "rev-parse", "HEAD")
    digest = sha256(state_bytes).hexdigest()

    report = verify_snapshot(work, expected, digest, processing_outcome="skipped", config_path=CONFIG)
    assert report["ai_pending"] == 321
    assert report["state_schema"] == report["pending_queue"] == report["topic_isolation"] == "validated"
    assert report["main_state_matches_expected"] is True


def test_invalid_state_from_expected_commit_fails(tmp_path):
    work, _ = setup_git(tmp_path)
    path = work / "seen_posts.json"
    path.write_text('{"schema_version":99,"topics":{}}')
    git(work, "add", "seen_posts.json")
    git(work, "commit", "-m", "invalid state")
    git(work, "push", "origin", "main")
    git(work, "fetch", "origin", "main")
    commit = git(work, "rev-parse", "HEAD")
    digest = sha256(path.read_bytes()).hexdigest()
    with pytest.raises(StateError, match="Unsupported or malformed state"):
        verify_snapshot(work, commit, digest, processing_outcome="skipped", config_path=CONFIG)


def test_unskipped_processing_is_not_reported_as_non_invocation(tmp_path):
    work, _ = setup_git(tmp_path)
    commit, digest = commit_state(work, empty_state())
    with pytest.raises(StateError, match="not verified as skipped"):
        verify_snapshot(work, commit, digest, processing_outcome="success", config_path=CONFIG)
