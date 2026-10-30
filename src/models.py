"""Validated configuration models."""

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, StrictInt, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class AppSettings(StrictModel):
    timezone: str = "Asia/Bangkok"
    default_model: str = "gemini-2.5-flash"
    max_posts_per_topic: int = Field(default=20, ge=1)
    request_timeout_seconds: int = Field(default=20, ge=1)
    max_retries: int = Field(default=3, ge=0)

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("timezone must be a valid IANA timezone") from exc
        return value


class AISettings(StrictModel):
    max_output_tokens: int = Field(default=8192, ge=256, le=16384, strict=True)
    batch_size: int = Field(default=4, ge=1, le=20, strict=True)
    max_output_retries: int = Field(default=1, ge=0, le=3, strict=True)


class AlertSettings(StrictModel):
    enabled: bool = True
    relevance_threshold: float = Field(default=0.75, ge=0, le=1)
    generate_audio: bool = False


class BriefingSettings(StrictModel):
    enabled: bool = True
    reporting_hours: int = Field(default=24, ge=1)
    generate_audio: bool = True
    max_summary_words: int = Field(default=500, ge=1)


class TTSSettings(StrictModel):
    voice: str = "th-TH-PremwadeeNeural"
    rate: str = "+0%"


class FeatureSettings(StrictModel):
    audio_enabled: bool = False

    @field_validator("audio_enabled")
    @classmethod
    def audio_not_supported(cls, value: bool) -> bool:
        if value:
            raise ValueError("audio_enabled=true is not supported in this text-only release")
        return value


class TelegramTopic(StrictModel):
    message_thread_id: StrictInt = Field(gt=0)


class Topic(StrictModel):
    id: str = Field(min_length=1, pattern=r"^[A-Za-z0-9_-]+$")
    name: str = Field(min_length=1)
    enabled: bool = True
    keywords: list[str] = Field(default_factory=list)
    context: str = ""
    telegram: TelegramTopic
    rss_urls: list[HttpUrl] = Field(default_factory=list)

    @model_validator(mode="after")
    def enabled_has_feed(self) -> "Topic":
        if self.enabled and not self.rss_urls:
            raise ValueError(f"enabled topic '{self.id}' requires at least one RSS URL")
        return self


class AgentConfig(StrictModel):
    schema_version: int = Field(ge=1, le=1)
    app: AppSettings = Field(default_factory=AppSettings)
    ai: AISettings = Field(default_factory=AISettings)
    alert: AlertSettings = Field(default_factory=AlertSettings)
    briefing: BriefingSettings = Field(default_factory=BriefingSettings)
    tts: TTSSettings = Field(default_factory=TTSSettings)
    features: FeatureSettings = Field(default_factory=FeatureSettings)
    topics: list[Topic] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_topic_ids(self) -> "AgentConfig":
        if self.ai.batch_size > self.app.max_posts_per_topic:
            raise ValueError("ai.batch_size must not exceed app.max_posts_per_topic")
        ids = [topic.id for topic in self.topics]
        if len(ids) != len(set(ids)):
            raise ValueError("topic IDs must be unique")
        return self
