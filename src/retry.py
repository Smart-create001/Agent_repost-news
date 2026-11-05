"""Bounded retry delays for provider Retry-After headers."""

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import math


def retry_delay(headers, attempt: int, *, now: datetime | None = None) -> float | None:
    """Return seconds to wait, or None when the header cannot be safely honored."""
    delay = min(2 ** attempt, 4)
    value = headers.get("Retry-After") if headers is not None else None
    if value is None:
        return delay
    value = str(value).strip()
    if value.isascii() and value.isdigit():
        if len(value) > 10:
            return None
        required = int(value)
    else:
        try:
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                return None
            required = max(0, math.ceil((date - (now or datetime.now(timezone.utc))).total_seconds()))
        except (TypeError, ValueError, OverflowError):
            return None
    return max(delay, required) if required <= 30 else None
