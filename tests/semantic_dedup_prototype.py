"""Offline-only candidate classifier; deliberately not imported by production code."""

from dataclasses import dataclass
from datetime import datetime, timedelta
import re

from src.scraper import canonical_url


@dataclass(frozen=True)
class Story:
    url: str
    title: str
    published_at: datetime


_COMMON = {"ai", "model", "models", "video", "from", "with", "for", "the", "and",
           "google", "launches", "launched", "unveils", "debuts", "releases",
           "announces", "adds", "expands", "updates", "patches", "new"}
_UPDATE = {"adds", "expands", "updates", "improves", "enables", "extends"}
_ISSUE = {"bug", "flaw", "breach", "vulnerability", "security"}


def _tokens(title: str) -> set[str]:
    return set(re.findall(r"[a-z]+|\d+(?:\.\d+)*", title.casefold()))


def classify_pair(older: Story, newer: Story) -> str:
    """Return DUPLICATE, UPDATE, or DISTINCT for controlled candidate fixtures."""
    old_tokens = _tokens(older.title)
    new_tokens = _tokens(newer.title)
    old_numbers = {token for token in old_tokens if token[0].isdigit()}
    new_numbers = {token for token in new_tokens if token[0].isdigit()}
    new_facts = new_numbers - old_numbers
    same_url = canonical_url(older.url) == canonical_url(newer.url)
    if same_url:
        return "UPDATE" if new_facts or new_tokens & _UPDATE else "DUPLICATE"
    if abs(newer.published_at - older.published_at) > timedelta(days=7):
        return "DISTINCT"
    if new_tokens & _ISSUE:
        return "DISTINCT"
    product = (old_tokens & new_tokens) - _COMMON
    if not old_numbers & new_numbers or len(product) < 3:
        return "DISTINCT"
    if new_facts or new_tokens & _UPDATE:
        return "UPDATE"
    return "DUPLICATE" if len(product) >= 4 else "DISTINCT"
