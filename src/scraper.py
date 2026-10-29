"""Fetch and normalize RSS/Atom without mutating state."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import logging
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import feedparser
import httpx
from src.retry import retry_delay

LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class Post:
    id: str
    url: str
    title: str
    published_at: datetime
    source_url: str
    date_inferred: bool = False


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
                          source_url=source_url, date_inferred=stamp is None))
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
                    response = exc.response if isinstance(exc, httpx.HTTPStatusError) else None
                    status = response.status_code if response is not None else None
                    retryable = status is None or status == 429 or 500 <= status <= 599
                    delay = retry_delay(attempt, response.headers if response is not None else None)
                    if retryable and delay is not None and attempt < max_retries:
                        time.sleep(delay)
                        continue
                    LOG.warning("Feed fetch failed after %d attempts: %s", attempt + 1, type(exc).__name__)
                    errors.append(f"feed fetch failed: {type(exc).__name__}")
                    break
                except ValueError as exc:
                    LOG.warning("Feed parse failed: %s", exc)
                    errors.append(str(exc))
                    break
    finally:
        if own_client:
            client.close()
    return list(posts.values()), errors
