"""Phase 7 controlled recovery and safe Gemini error diagnostics."""

import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from google.genai import errors

from src.brain import AnalysisResults, BrainError, analyze_topic
from src.main import run
from src.notifier import DeliveryResult
from src.scraper import Post, fetch_topic
from src.state import load_state
from tests.test_brain import POST, result, topic
from tests.test_pipeline import settings
from tests.test_workflow import SCRIPT, git, setup_git


NOW = datetime(2026, 11, 6, 0, tzinfo=timezone.utc)


class FakeModels:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.calls = 0

    def generate_content(self, **kwargs):
        self.calls += 1
        reply = next(self.replies)
        if isinstance(reply, Exception):
            raise reply
        return reply


def response(text, *, finish="STOP", usage=None):
    return SimpleNamespace(text=text, candidates=[SimpleNamespace(finish_reason=finish)],
                           usage_metadata=usage)


def ai_client(*replies):
    return SimpleNamespace(models=FakeModels(replies))


def test_tc7_a01_max_tokens_category_and_safe_usage(caplog):
    usage = SimpleNamespace(prompt_token_count=120, candidates_token_count=8192,
                            total_token_count=8312)
    client = ai_client(response("partial", finish="MAX_TOKENS", usage=usage),
                       response("partial", finish="MAX_TOKENS", usage=usage))
    with pytest.raises(BrainError) as caught:
        analyze_topic(topic(), [POST], client=client, max_output_retries=1)
    err = caught.value
    assert (err.error_category, err.finish_reason, err.attempt) == ("MAX_TOKENS", "MAX_TOKENS", 2)
    assert err.token_usage == {"input_tokens": 120, "output_tokens": 8192, "total_tokens": 8312}
    assert "event=ai_output_truncation" in caplog.text
    assert "partial" not in caplog.text


def test_tc7_a02_invalid_json_is_not_delivered_and_is_classified(tmp_path, monkeypatch, caplog):
    post = make_post("invalid", NOW)
    client = ai_client(response("{invalid"))
    monkeypatch.setattr("src.main.deliver", lambda *a, **k: pytest.fail("Telegram must not be called"))
    path = tmp_path / "state.json"
    def analyze(_topic, posts, **kwargs):
        return analyze_topic(topic(), [POST], client=client)
    assert run(settings(False), mode="alert", state_path=path,
               fetch=lambda *a, **k: ([post], []), analyze=analyze,
               notifier=object(), now=NOW) == 1
    assert "error_category=JSON_DECODE_ERROR" in caplog.text
    assert "model=gemini-3.1-flash-lite" in caplog.text
    assert "batch_start=0 batch_size=1" in caplog.text
    assert load_state(path)["topics"]["ai_tech"]["alert_checkpoint_at"] == (NOW - timedelta(days=2)).isoformat()


def test_tc7_a03_rate_limit_bounded_retry_and_classification(monkeypatch):
    waits = []
    monkeypatch.setattr("src.brain.time.sleep", waits.append)
    limited = errors.ClientError(429, {}, httpx.Response(429, headers={"Retry-After": "7"}))
    client = ai_client(limited, limited)
    with pytest.raises(BrainError) as caught:
        analyze_topic(topic(), [POST], client=client, max_retries=1)
    assert caught.value.error_category == "RATE_LIMIT"
    assert caught.value.attempt == 2
    assert client.models.calls == 2 and waits == [7]


@pytest.mark.parametrize("reply,category", [
    (response(json.dumps([result(confidence=2)])), "SCHEMA_VALIDATION_ERROR"),
    (response(json.dumps([result(source_urls=["https://example.org/fabricated"])])), "SOURCE_GROUNDING_ERROR"),
    (TimeoutError(), "TIMEOUT"),
    (RuntimeError("unknown provider failure"), "UNKNOWN_ERROR"),
])
def test_error_categories_cover_schema_grounding_timeout_unknown(reply, category):
    with pytest.raises(BrainError) as caught:
        analyze_topic(topic(), [POST], client=ai_client(reply), max_retries=0)
    assert caught.value.error_category == category


def test_server_error_classified_without_response_text():
    server = errors.ServerError(503, {}, httpx.Response(503))
    with pytest.raises(BrainError) as caught:
        analyze_topic(topic(), [POST], client=ai_client(server), max_retries=0)
    assert caught.value.error_category == "SERVER_ERROR"


def make_post(identifier, published):
    return Post(id=identifier, url=f"https://news.example.org/{identifier}",
                title=identifier, published_at=published,
                source_url="https://feeds.example.org/ai", retrieved_at=published)


def live_like_fetch(posts):
    def fetch(_topic, **kwargs):
        now = kwargs["now"]
        age = kwargs["max_age"]
        expiry_age = kwargs.get("expiry_age")
        if expiry_age and kwargs.get("stats") is not None:
            kwargs["stats"]["expired_ids"] = {
                post.id for post in posts if post.published_at < now - expiry_age
            }
        return [post for post in posts if now - age <= post.published_at <= now], []
    return fetch


def selected_analyses(_topic, posts, **kwargs):
    return AnalysisResults([SimpleNamespace(summary_th=p.title, source_urls=[p.url]) for p in posts])


@pytest.mark.parametrize("gap_hours,article_hour", [(4, 1), (24, 1), (72, 1)])
def test_tc7_b01_to_b03_gap_recovery(tmp_path, monkeypatch, gap_hours, article_hour):
    cfg = settings(False)
    path = tmp_path / "state.json"
    sent = []
    monkeypatch.setattr("src.main.deliver", lambda *a, **k:
                        (sent.append(1) or DeliveryResult(text_message_ids=[len(sent)])))
    fetch = live_like_fetch([make_post("missed", NOW + timedelta(hours=article_hour))])
    assert run(cfg, mode="alert", state_path=path, fetch=fetch, now=NOW) == 0
    later = NOW + timedelta(hours=gap_hours)
    assert run(cfg, mode="alert", state_path=path, fetch=fetch, analyze=selected_analyses,
               notifier=object(), now=later) == 0
    state = load_state(path)["topics"]["ai_tech"]
    assert sent == [1]
    assert state["posts"]["missed"]["alert"]["delivered"]
    assert state["alert_checkpoint_at"] == later.isoformat()


def test_tc7_b04_backlog_over_three_resumes_without_duplicate(tmp_path, monkeypatch):
    cfg = settings(False)
    path = tmp_path / "state.json"
    posts = [make_post(f"old-{i}", NOW + timedelta(hours=i)) for i in range(1, 6)]
    fetch = live_like_fetch(posts)
    sent = []
    monkeypatch.setattr("src.main.deliver", lambda *a, **k:
                        (sent.append(1) or DeliveryResult(text_message_ids=[len(sent)])))
    assert run(cfg, mode="alert", state_path=path, fetch=fetch, now=NOW) == 0
    later = NOW + timedelta(days=3)
    args = dict(mode="alert", state_path=path, fetch=fetch, analyze=selected_analyses,
                notifier=object(), now=later)
    assert run(cfg, **args) == 0
    state = load_state(path)["topics"]["ai_tech"]
    assert len(sent) == 3
    assert state["alert_checkpoint_at"] == NOW.isoformat()
    assert all(post.id not in state["posts"] for post in posts[3:])
    assert run(cfg, **args) == 0
    state = load_state(path)["topics"]["ai_tech"]
    assert len(sent) == 5
    assert state["alert_checkpoint_at"] == later.isoformat()
    assert run(cfg, **args) == 0 and len(sent) == 5


def test_tc7_b05_gemini_failure_retries_pending_post(tmp_path, monkeypatch):
    cfg = settings(False)
    path = tmp_path / "state.json"
    post = make_post("missed", NOW + timedelta(hours=1))
    fetch = live_like_fetch([post])
    sent = []
    monkeypatch.setattr("src.main.deliver", lambda *a, **k:
                        (sent.append(1) or DeliveryResult(text_message_ids=[len(sent)])))
    assert run(cfg, mode="alert", state_path=path, fetch=fetch, now=NOW) == 0
    later = NOW + timedelta(days=3)
    attempts = []
    def analyze(_topic, posts, **kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            raise BrainError("simulated", error_category="SERVER_ERROR", attempt=1)
        return selected_analyses(_topic, posts)
    args = dict(mode="alert", state_path=path, fetch=fetch, analyze=analyze,
                notifier=object(), now=later)
    assert run(cfg, **args) == 1
    assert load_state(path)["topics"]["ai_tech"]["alert_checkpoint_at"] == NOW.isoformat()
    assert run(cfg, **args) == 0
    assert attempts == [1, 1] and sent == [1]


def test_tc7_b06_persisted_partial_delivery_retries_only_confirmed_failure(tmp_path, monkeypatch):
    cfg = settings(False)
    work, remote = setup_git(tmp_path)
    path = work / "seen_posts.json"
    posts = [make_post("first", NOW), make_post("second", NOW)]
    outcomes = iter([DeliveryResult(text_message_ids=[101]), DeliveryResult(text_error="rejected")])
    monkeypatch.setattr("src.main.deliver", lambda *a, **k: next(outcomes))
    args = dict(mode="alert", state_path=path, fetch=live_like_fetch(posts),
                analyze=selected_analyses, notifier=object(), now=NOW)
    assert run(cfg, **args) == 1
    subprocess.run(["bash", str(SCRIPT)], cwd=work, check=True, capture_output=True)
    clone = tmp_path / "next-run"
    git(tmp_path, "clone", "-b", "main", str(remote), str(clone))
    persisted = load_state(clone / "seen_posts.json")["topics"]["ai_tech"]["posts"]
    assert persisted["first"]["alert"]["delivered"]
    assert not persisted["second"]["alert"]["delivered"]
    retried = []
    monkeypatch.setattr("src.main.deliver", lambda *a, **k:
                        (retried.append(1) or DeliveryResult(text_message_ids=[102])))
    assert run(cfg, mode="alert", state_path=clone / "seen_posts.json",
               fetch=live_like_fetch(posts), analyze=selected_analyses,
               notifier=object(), now=NOW + timedelta(hours=1)) == 0
    assert retried == [1]
    assert load_state(clone / "seen_posts.json")["topics"]["ai_tech"]["posts"]["second"]["alert"]["delivered"]


def test_expired_feed_entries_are_counted_and_reported(tmp_path, caplog):
    cfg = settings(False)
    path = tmp_path / "state.json"
    old = make_post("older-than-seven-days", NOW - timedelta(days=8))
    rss = (f'<rss version="2.0"><channel><title>AI</title><item><title>Old</title>'
           f'<link>{old.url}</link><pubDate>Thu, 29 Oct 2026 00:00:00 GMT</pubDate>'
           f'</item></channel></rss>').encode()
    client = httpx.Client(transport=httpx.MockTransport(lambda _request: httpx.Response(200, content=rss)))
    try:
        fetch = lambda topic, **kwargs: fetch_topic(topic, client=client, **kwargs)
        assert run(cfg, mode="alert", state_path=path, fetch=fetch, now=NOW) == 0
    finally:
        client.close()
    assert "expired_feed_entries=1 expired_untracked=1" in caplog.text
