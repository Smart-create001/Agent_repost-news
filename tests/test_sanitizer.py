import pytest

from src.sanitizer import split_telegram, spoken_thai


def test_tc4_01_thai_sanitization():
    spoken = spoken_thai("**ราคา 25 บาท** 😊 https://news.example.org/story")
    assert spoken == "ราคา 25 บาท"


def test_tc4_02_empty_spoken_text():
    with pytest.raises(ValueError, match="empty"):
        spoken_thai("https://news.example.org 😊")


def test_tc4_10_long_message_preserves_content_and_url():
    original = "ข่าว" * 2200 + "\nhttps://news.example.org/story"
    chunks = split_telegram(original)
    assert len(chunks) > 1
    assert all(len(chunk) <= 4096 for chunk in chunks)
    assert "".join(chunks) == original
