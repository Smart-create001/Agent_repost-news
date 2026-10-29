"""Bounded retry delays honoring provider Retry-After values."""
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import math


def retry_delay(attempt, headers=None, retry_after=None, *, max_wait=30):
    value = retry_after if retry_after is not None else (headers or {}).get("Retry-After")
    if value is None:
        return min(2 ** attempt, 4)
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        try:
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                return None
            seconds = (date - datetime.now(timezone.utc)).total_seconds()
        except (TypeError, ValueError, OverflowError):
            return None
    if not math.isfinite(seconds) or seconds > max_wait:
        return None
    return max(0, seconds)
