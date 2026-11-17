"""Research settings: explicit overrides > environment > YAML > defaults."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from paperflow_ingest_models import IngestionSettings
from paperflow_topic_models import TopicSettings


class StorageSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    adapter: Literal["sqlite"] = "sqlite"
    path: Path = Path("artifacts/radar.db")
    busy_timeout_seconds: float = Field(default=5.0, gt=0, le=120)


class RedditSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    access_token: SecretStr | None = Field(default=None, exclude=True)
    user_agent: str = ""
    timeout_seconds: float = Field(default=20, gt=0, le=60)


class MonitorSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    schedule_enabled: bool = True
    workers: int = Field(default=2, ge=1, le=16)
    poll_seconds: float = Field(default=1, ge=0.05, le=30)
    run_timeout_seconds: float = Field(default=180, ge=1, le=3600)
    retry_seconds: int = Field(default=60, ge=1)
    max_retry_seconds: int = Field(default=3600, ge=1)
    jitter_fraction: float = Field(default=0.1, ge=0, le=1)
    jitter_max_seconds: float = Field(default=60, ge=0, le=3600)


class ServiceSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)


class TelemetrySettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    service_name: str = "paperflow"
    otlp_endpoint: str | None = None


class ResearchSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PAPERFLOW_", env_nested_delimiter="__", extra="forbid",
    )

    storage: StorageSettings = Field(default_factory=StorageSettings)
    reddit: RedditSettings = Field(default_factory=RedditSettings)
    ingestion: IngestionSettings = Field(default_factory=IngestionSettings)
    monitor: MonitorSettings = Field(default_factory=MonitorSettings)
    service: ServiceSettings = Field(default_factory=ServiceSettings)
    telemetry: TelemetrySettings = Field(default_factory=TelemetrySettings)
    topics: TopicSettings = Field(default_factory=TopicSettings)


def load_research_settings(
    config_path: str | Path = "arxiv-radar.yaml", **overrides: Any,
) -> ResearchSettings:
    """Read only the research section; leave legacy configuration untouched.

    The selected config file must exist. Relative storage paths are resolved by
    the caller's working directory, matching existing Paperflow path behavior.
    No implicit .env loading or filesystem creation occurs.
    """
    raw = yaml.safe_load(Path(config_path).read_text(encoding="utf-8"))
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError("radar config must be a YAML object")
    section = raw.get("research", {})
    if not isinstance(section, dict):
        raise ValueError("research config must be a YAML object")

    class ConfiguredResearchSettings(ResearchSettings):
        @classmethod
        def settings_customise_sources(cls, settings_cls, init_settings,
                                       env_settings, dotenv_settings, file_secret_settings):
            return init_settings, env_settings, lambda: section

    return ConfiguredResearchSettings(**overrides)
