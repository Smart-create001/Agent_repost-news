from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx

from src.scraper import fetch_topic, parse_feed


FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 10, 26, 12, tzinfo=timezone.utc)
URL = "https://feeds.example.org/rss"


def test_tc2_01_rss_and_tc2_03_duplicate_url():
    posts = parse_feed((FIXTURES / "sample.rss").read_bytes(), URL, now=NOW)
    assert len(posts) == 1
    assert posts[0].url == "https://news.example.org/story?x=1"
    assert posts[0].published_at == datetime(2026, 10, 26, 10, tzinfo=timezone.utc)


def test_tc2_02_atom():
    posts = parse_feed((FIXTURES / "sample.atom").read_bytes(), URL, now=NOW)
    assert len(posts) == 1 and posts[0].title == "Atom story"


def test_tc2_05_missing_date_and_stale_filter():
    data = b'<rss version="2.0"><channel><title>x</title><item><title>x</title><link>https://news.example.org/unknown</link></item></channel></rss>'
    post = parse_feed(data, URL, now=NOW)[0]
    assert post.date_inferred and post.published_at == NOW
    assert parse_feed((FIXTURES / "sample.atom").read_bytes(), URL,
                      now=NOW + timedelta(days=90), max_age=timedelta(days=1)) == []


def test_tc2_06_bad_feed_does_not_stop_next():
    def handler(request):
        content = b"not xml" if request.url.path == "/bad" else (FIXTURES / "sample.atom").read_bytes()
        return httpx.Response(200, content=content)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        posts, errors = fetch_topic(SimpleNamespace(rss_urls=["https://feeds.example.org/bad", URL]),
                                   client=client, now=NOW)
    assert len(posts) == 1 and len(errors) == 1


def test_tc2_07_timeout_is_bounded(monkeypatch, caplog):
    calls = []
    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("timeout")
    monkeypatch.setattr("src.scraper.time.sleep", lambda _: None)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        posts, errors = fetch_topic(SimpleNamespace(rss_urls=[URL]), client=client, max_retries=2)
    assert posts == [] and len(errors) == 1 and len(calls) == 3
    assert "after 3 attempts" in caplog.text


def test_rate_limit_waits_and_auth_failure_does_not_retry(monkeypatch):
    waits, calls = [], []
    monkeypatch.setattr("src.scraper.time.sleep", waits.append)
    def handler(request):
        calls.append(request)
        return httpx.Response(429, headers={"Retry-After": "6"}) if len(calls) == 1 else httpx.Response(403)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        _, errors = fetch_topic(SimpleNamespace(rss_urls=[URL]), client=client, max_retries=3)
    assert waits == [6] and len(calls) == 2 and len(errors) == 1
