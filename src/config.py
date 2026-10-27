"""Load YAML configuration and validate credentials only for live runs."""

import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import ValidationError
import yaml

from src.models import AgentConfig


class ConfigError(ValueError):
    """A configuration file or live credential requirement is invalid."""


def load_config(path: str | Path = "config.yaml") -> AgentConfig:
    config_path = Path(path)
    try:
        with config_path.open(encoding="utf-8") as stream:
            data = yaml.safe_load(stream)
    except OSError as exc:
        raise ConfigError(f"Cannot read config file: {config_path}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid YAML in config file: {config_path}") from exc
    if not isinstance(data, dict):
        raise ConfigError("Config YAML must contain a mapping")
    try:
        return AgentConfig.model_validate(data)
    except ValidationError as exc:
        # Configuration may contain sensitive values; do not copy raw input into errors.
        details = "; ".join(
            f"{'.'.join(map(str, error['loc'])) or 'config'}: "
            + (str(error['ctx']['error']) if error['type'] == 'value_error' else error['type'])
            for error in exc.errors()
        )
        raise ConfigError(f"Invalid configuration: {details}") from exc


def validate_live_config(config: AgentConfig, *, env_file: str | Path | None = None) -> None:
    if env_file is not None:
        load_dotenv(env_file, override=False)
    else:
        load_dotenv(override=False)
    required = ("GEMINI_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")
    missing = [name for name in required if not os.environ.get(name, "").strip()]
    if missing:
        raise ConfigError("Missing live environment variables: " + ", ".join(missing))
    if not os.environ["TELEGRAM_CHAT_ID"].lstrip("-").isdigit():
        raise ConfigError("TELEGRAM_CHAT_ID must be an integer")
    if not any(topic.enabled for topic in config.topics):
        raise ConfigError("Live configuration requires at least one enabled topic")
    if any("example.com" in str(url).split("/")[2] for topic in config.topics if topic.enabled for url in topic.rss_urls):
        raise ConfigError("Enabled topics cannot use example.com RSS URLs in live mode")
