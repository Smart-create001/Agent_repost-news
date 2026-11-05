from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx
import json
import pytest

from src.scraper import fetch_topic, parse_feed, parse_x_profile


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


def test_feeds_are_interleaved_before_pipeline_limit():
    def handler(request):
        prefix = request.url.path.strip("/")
        items = "".join(f"<item><title>{prefix}{i}</title><link>https://news.example.org/{prefix}{i}</link></item>" for i in range(25))
        return httpx.Response(200, text=f'<rss version="2.0"><channel><title>x</title>{items}</channel></rss>')
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        posts, errors = fetch_topic(SimpleNamespace(rss_urls=["https://feeds.example.org/first", "https://feeds.example.org/second"]), client=client, now=NOW)
    assert not errors
    assert [post.title for post in posts[:4]] == ["first0", "second0", "first1", "second1"]
    assert sum(post.title.startswith("second") for post in posts[:20]) == 10


def x_page(tweets):
    entries = [{"content": {"content": {"__typename": "TimelineTweet", "tweet_results": {"result": tweet}}}} for tweet in tweets]
    timeline = {"id": "ProfileOriginalsProduct-Timeline", "instructions": [{"__typename": "TimelineAddEntries", "entries": entries}]}
    payload = {"data": {"user_result_by_screen_name": {"result": {"profile_user_originals_timeline": {"timeline": timeline}}}}}
    return '<script>kind:"Result.Ok",value:' + json.dumps(json.dumps(payload)) + '</script>'


def x_tweet(identifier, author, text):
    return {"__typename": "Tweet", "rest_id": identifier, "core": {"user_results": {"result": {"core": {"screen_name": author}}}},
            "details": {"created_at_ms": int(NOW.timestamp() * 1000), "full_text": text}}


def test_x_profile_extracts_only_account_posts_with_stable_ids():
    page = x_page([x_tweet("123", "thsottiaux", "AI product update"), x_tweet("124", "other", "other post"), x_tweet("123", "thsottiaux", "duplicate")])
    posts = parse_x_profile(page, "thsottiaux", now=NOW, max_age=timedelta(days=2))
    assert len(posts) == 1
    assert posts[0].url == "https://x.com/thsottiaux/status/123"
    assert posts[0].published_at == NOW and posts[0].title == "AI product update"
    assert parse_x_profile(page, "thsottiaux", now=NOW + timedelta(days=3), max_age=timedelta(days=2)) == []


def test_x_missing_timeline_is_reported_and_rss_continues():
    with pytest.raises(ValueError, match="timeline unavailable"):
        parse_x_profile("<html>login</html>", "thsottiaux", now=NOW)
    def handler(request):
        return httpx.Response(200, text="<html>login</html>") if request.url.host == "x.com" else httpx.Response(200, content=(FIXTURES / "sample.atom").read_bytes())
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        posts, errors = fetch_topic(SimpleNamespace(x_handles=["thsottiaux"], rss_urls=[URL]), client=client, now=NOW)
    assert len(posts) == 1 and len(errors) == 1
