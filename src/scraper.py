"""Fetch and normalize RSS/Atom without mutating state."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from html.parser import HTMLParser
import logging
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import feedparser
import httpx

LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class Post:
    id: str
    url: str
    title: str
    published_at: datetime
    source_url: str
    date_inferred: bool = False
    description: str = ""
    retrieved_at: datetime | None = None


class _PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden += 1

    def handle_endtag(self, tag):
        if tag in {"script", "style"} and self.hidden:
            self.hidden -= 1
        self.parts.append(" ")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def _description(value):
    parser = _PlainText()
    parser.feed(str(value or "")[:12000])
    return " ".join(" ".join(parser.parts).split())[:3000]


def canonical_url(url: str) -> str:
    parts = urlsplit(url.strip())
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        raise ValueError("entry URL must be HTTP(S)")
    query = urlencode(sorted((k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                           if not k.lower().startswith("utm_") and k.lower() not in {"fbclid", "gclid"}))
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path or "/", query, ""))


def parse_feed(data: bytes, source_url: str, *, now: datetime | None = None,
               max_age: timedelta | None = None) -> list[Post]:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    parsed = feedparser.parse(data)
    if parsed.bozo or not parsed.version:
        raise ValueError("invalid RSS/Atom feed")
    posts: list[Post] = []
    seen: set[str] = set()
    for entry in parsed.entries:
        try:
            url = canonical_url(entry.get("link", ""))
        except ValueError:
            LOG.warning("Skipping feed entry without a valid URL")
            continue
        if url in seen:
            continue
        seen.add(url)
        stamp = entry.get("published_parsed") or entry.get("updated_parsed")
        published = datetime(*stamp[:6], tzinfo=timezone.utc) if stamp else now
        if max_age is not None and published < now - max_age:
            continue
        posts.append(Post(id=sha256(url.encode()).hexdigest(), url=url,
                          title=entry.get("title", "").strip(), published_at=published,
                          source_url=source_url, date_inferred=stamp is None,
                          description=_description(entry.get("summary", "")), retrieved_at=now))
    return posts


def fetch_topic(topic, *, timeout: float = 20, max_retries: int = 3,
                max_age: timedelta | None = None, client: httpx.Client | None = None,
                now: datetime | None = None) -> tuple[list[Post], list[str]]:
    if timeout <= 0 or max_retries < 0:
        raise ValueError("timeout must be positive and max_retries nonnegative")
    own_client = client is None
    client = client or httpx.Client(timeout=timeout, follow_redirects=True)
    posts: dict[str, Post] = {}
    errors: list[str] = []
    try:
        for feed_url in topic.rss_urls:
            url = str(feed_url)
            for attempt in range(max_retries + 1):
                try:
                    response = client.get(url, timeout=timeout)
                    response.raise_for_status()
                    for post in parse_feed(response.content, url, now=now, max_age=max_age):
                        posts.setdefault(post.id, post)
                    break
                except (httpx.TimeoutException, httpx.TransportError, httpx.HTTPStatusError) as exc:
                    if attempt < max_retries:
                        time.sleep(min(2 ** attempt, 4))
                        continue
                    LOG.warning("Feed fetch failed after %d attempts: %s", attempt + 1, type(exc).__name__)
                    errors.append(f"feed fetch failed: {type(exc).__name__}")
                except ValueError as exc:
                    LOG.warning("Feed parse failed: %s", exc)
                    errors.append(str(exc))
                    break
    finally:
        if own_client:
            client.close()
    return list(posts.values()), errors
