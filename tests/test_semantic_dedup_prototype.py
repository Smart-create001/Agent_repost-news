"""Offline evaluation of a candidate design; no production routing changes."""

from datetime import datetime, timedelta, timezone

from tests.semantic_dedup_prototype import Story, classify_pair


NOW = datetime(2026, 11, 6, tzinfo=timezone.utc)


def story(url, title, *, hours=0):
    return Story(url=url, title=title, published_at=NOW + timedelta(hours=hours))


def test_same_url_without_new_facts_is_duplicate():
    a = story("https://example.org/item?utm_source=rss", "Google launches Gemini Omni 1.2 Flash video model")
    b = story("https://example.org/item", "Google launches Gemini Omni 1.2 Flash video model", hours=1)
    assert classify_pair(a, b) == "DUPLICATE"


def test_paraphrased_same_release_is_duplicate_candidate():
    a = story("https://one.example/a", "Google launches Gemini Omni 1.2 Flash video model")
    b = story("https://two.example/b", "Gemini Omni 1.2 Flash video model debuts at Google", hours=1)
    assert classify_pair(a, b) == "DUPLICATE"


def test_material_update_is_preserved_even_at_same_url():
    a = story("https://one.example/a", "Google launches Gemini Omni 1.2 Flash video model")
    b = story("https://one.example/a", "Gemini Omni 1.2 Flash adds 15 second clips", hours=5)
    assert classify_pair(a, b) == "UPDATE"


def test_material_update_at_different_url_is_preserved():
    a = story("https://one.example/a", "Google launches Gemini Omni 1.2 Flash video model")
    b = story("https://two.example/b", "Gemini Omni 1.2 Flash adds 15 second clips", hours=5)
    assert classify_pair(a, b) == "UPDATE"


def test_security_story_about_same_product_is_distinct():
    a = story("https://one.example/a", "Google launches Gemini Omni 1.2 Flash video model")
    b = story("https://two.example/b", "Gemini Omni 1.2 Flash security flaw disclosed", hours=5)
    assert classify_pair(a, b) == "DISTINCT"


def test_old_or_different_product_is_distinct():
    a = story("https://one.example/a", "Google launches Gemini Omni 1.2 Flash video model")
    b = story("https://two.example/b", "Google launches Genie 4 world model", hours=1)
    c = story("https://two.example/c", "Gemini Omni 1.2 Flash video model debuts at Google", hours=192)
    assert classify_pair(a, b) == "DISTINCT"
    assert classify_pair(a, c) == "DISTINCT"
