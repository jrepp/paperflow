"""Source-neutral ingestion contracts; no network or storage dependencies."""
from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import AnyHttpUrl, AwareDatetime, BaseModel, ConfigDict, Field

from paperflow_domain import Record, Text, now


class SourceRemovedError(ValueError):
    """An enrichment source reports that a discovered item is unavailable."""


class ChannelConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    url: AnyHttpUrl
    discovery: str = "feed"
    enrichers: tuple[str, ...] = ()
    kind: Literal["article", "discussion", "paper"] = "discussion"
    limit: int = Field(default=10, ge=1, le=1000)
    comment_limit: int = Field(default=3, ge=0, le=10)
    interval_seconds: int = Field(default=900, ge=10)


class IngestionSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    channels: dict[str, ChannelConfig] = Field(default_factory=dict)


class Signals(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    score: int | None = None
    comment_count: int | None = Field(default=None, ge=0)


class CommentSample(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    source_id: Text
    url: AnyHttpUrl
    text: str
    score: int | None = None


class SourceEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    source_id: Text
    title: Text
    url: AnyHttpUrl
    discussion_url: AnyHttpUrl | None = None
    published_at: AwareDatetime | None = None
    excerpt: str = ""
    signals: Signals = Field(default_factory=Signals)
    comments: tuple[CommentSample, ...] = ()
    comments_status: Literal["not_requested", "sampled", "unavailable"] = "not_requested"


class IngestBatch(Record):
    channel: Text
    config: ChannelConfig
    entries: tuple[SourceEntry, ...]
    warnings: tuple[str, ...] = ()
    fetched_at: AwareDatetime = Field(default_factory=now)


class SourceObservation(Record):
    item_id: UUID
    batch_id: UUID
    channel: Text
    feed_url: AnyHttpUrl
    entry: SourceEntry
    observed_at: AwareDatetime
