"""Prepare spoken Thai separately from Telegram display text."""

import re
import unicodedata


URL = re.compile(r"https?://[^\s<>()]+", re.IGNORECASE)
MARKDOWN = re.compile(r"[`*_~#>\[\]{}]")


def spoken_thai(text: str) -> str:
    if not isinstance(text, str):
        raise ValueError("TTS text must be a string")
    text = URL.sub(" ", text)
    text = MARKDOWN.sub(" ", text)
    text = "".join(char if (unicodedata.category(char)[0] not in {"C", "S"}) else " " for char in text)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        raise ValueError("TTS text is empty after sanitization")
    return text


def split_telegram(text: str, *, limit: int = 4096) -> list[str]:
    if not text or not text.strip() or limit < 1:
        raise ValueError("Telegram message must be nonempty and limit positive")
    chunks = []
    remaining = text
    while len(remaining) > limit:
        cut = remaining.rfind("\n", 0, limit + 1)
        if cut < 1:
            cut = remaining.rfind(" ", 0, limit + 1)
        if cut < 1:
            cut = limit
        chunks.append(remaining[:cut])
        remaining = remaining[cut:]
        if remaining.startswith(("\n", " ")):
            chunks[-1] += remaining[0]
            remaining = remaining[1:]
    if remaining:
        chunks.append(remaining)
    return chunks
