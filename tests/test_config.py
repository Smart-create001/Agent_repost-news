"""Phase 1 configuration acceptance checks; no network access required."""

from pathlib import Path

import pytest

from src.config import ConfigError, load_config, validate_live_config


ROOT = Path(__file__).resolve().parents[1]


def sample_yaml() -> str:
    return """schema_version: 1
topics:
  - id: one
    name: One
    telegram: {message_thread_id: 101}
    rss_urls: [https://news.example.org/feed.xml]
  - id: two
    name: Two
    telegram: {message_thread_id: 102}
    rss_urls: [https://news.example.org/other.xml]
"""


def write_yaml(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(content, encoding="utf-8")
    return path


def test_tc1_01_valid_two_topics(tmp_path):
    config = load_config(write_yaml(tmp_path, sample_yaml()))
    assert [topic.id for topic in config.topics] == ["one", "two"]
    assert config.app.default_model == "gemini-2.5-flash"


def test_tc1_02_duplicate_ids(tmp_path):
    with pytest.raises(ConfigError, match="topic IDs must be unique"):
        load_config(write_yaml(tmp_path, sample_yaml().replace("id: two", "id: one")))


def test_tc1_03_missing_rss(tmp_path):
    with pytest.raises(ConfigError, match="requires at least one RSS URL"):
        load_config(write_yaml(tmp_path, sample_yaml().replace("rss_urls: [https://news.example.org/feed.xml]", "rss_urls: []")))


def test_tc1_04_invalid_thread(tmp_path):
    with pytest.raises(ConfigError, match="message_thread_id"):
        load_config(write_yaml(tmp_path, sample_yaml().replace("message_thread_id: 101", "message_thread_id: abc")))


def test_tc1_05_missing_live_key(tmp_path, monkeypatch):
    for name in ("GEMINI_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(ConfigError, match="GEMINI_API_KEY"):
        validate_live_config(load_config(write_yaml(tmp_path, sample_yaml())), env_file=tmp_path / "absent")


def test_tc1_06_offline_no_secrets(tmp_path, monkeypatch):
    for name in ("GEMINI_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
        monkeypatch.delenv(name, raising=False)
    assert len(load_config(write_yaml(tmp_path, sample_yaml())).topics) == 2


def test_tc1_07_invalid_yaml(tmp_path):
    with pytest.raises(ConfigError, match="Invalid YAML"):
        load_config(write_yaml(tmp_path, "topics: [bad"))


def test_tc1_08_secret_hygiene():
    assert ".env" in (ROOT / ".gitignore").read_text().splitlines()
    assert (ROOT / ".env.example").read_text().splitlines() == [
        "GEMINI_API_KEY=", "TELEGRAM_BOT_TOKEN=", "TELEGRAM_CHAT_ID="
    ]


def test_invalid_url(tmp_path):
    with pytest.raises(ConfigError, match="rss_urls"):
        load_config(write_yaml(tmp_path, sample_yaml().replace("https://news.example.org/feed.xml", "not-a-url")))


def test_repository_config_is_valid_offline():
    config = load_config(ROOT / "config.yaml")
    assert len(config.topics) == 2
    assert {topic.id for topic in config.topics} == {"ai_tech", "gold_price"}
    assert config.app.default_model == "gemini-3.1-flash-lite"
    assert {topic.id: topic.telegram.message_thread_id for topic in config.topics} == {
        "ai_tech": 5, "gold_price": 6}


def test_model_configuration_is_overridable(tmp_path):
    config = load_config(write_yaml(tmp_path, sample_yaml() + "\napp:\n  default_model: custom-model\n"))
    assert config.app.default_model == "custom-model"


def test_editorial_output_token_limit_is_validated(tmp_path):
    config = load_config(write_yaml(tmp_path, sample_yaml() + "\napp:\n  max_output_tokens: 4096\n"))
    assert config.app.max_output_tokens == 4096
    with pytest.raises(ConfigError):
        load_config(write_yaml(tmp_path, sample_yaml() + "\napp:\n  max_output_tokens: 20000\n"))
