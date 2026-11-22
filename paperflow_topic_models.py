"""Editorial topics sit above stable ingestion sources and immutable items."""
from datetime import datetime, timedelta
from typing import Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from paperflow_domain import Record, Text

Sensitivity = Literal['urgent', 'timely', 'lasting']
HALF_LIFE_HOURS = {'urgent': 3, 'timely': 12, 'lasting': 72}


class TopicSettings(BaseModel):
    model_config = ConfigDict(extra='forbid')
    enabled: bool = True
    auto_publish: bool = True
    interval_seconds: int = Field(default=900, ge=60, le=86400)
    window_hours: int = Field(default=24, ge=1, le=168)
    min_items: int = Field(default=2, ge=2, le=100)
    max_topics: int = Field(default=12, ge=1, le=50)
    max_candidates: int = Field(default=2000, ge=10, le=10000)


class Topic(Record):
    key: Text
    title: Text
    description: str = ''
    persistence: Literal['dynamic', 'persistent'] = 'dynamic'
    sensitivity: Sensitivity = 'timely'
    editorial_status: Literal['published', 'draft', 'suppressed'] = 'published'
    terms: tuple[Text, ...] = ()
    included_item_ids: tuple[UUID, ...] = Field(default=(), max_length=1000)
    excluded_item_ids: tuple[UUID, ...] = Field(default=(), max_length=1000)
    editorial_title: bool = False
    version: int = Field(default=1, ge=1)
    current_run_id: UUID | None = None
    last_seen_at: AwareDatetime | None = None
    expires_at: AwareDatetime | None = None


class TopicCandidate(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    item_id: UUID
    observation_id: UUID
    first_seen_at: AwareDatetime
    title: str
    excerpt: str
    channel: str


class TopicAssignment(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    item_id: UUID
    observation_id: UUID
    first_seen_at: AwareDatetime
    reason: Text


class TopicProposal(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    key: Text
    title: Text
    terms: tuple[Text, ...]
    assignments: tuple[TopicAssignment, ...]


class TopicEdition(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    topic_id: UUID
    title: Text
    assignments: tuple[TopicAssignment, ...]


class TopicRun(Record):
    window_start: AwareDatetime
    window_end: AwareDatetime
    processor_version: Text
    input_count: int = Field(ge=0)
    unassigned_count: int = Field(ge=0)
    editions: tuple[TopicEdition, ...]


class EditTopic(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_version: int = Field(ge=1)
    editor: Text
    note: str = ''
    title: Text | None = None
    description: str | None = None
    editorial_status: Literal['published', 'draft', 'suppressed'] | None = None
    persistence: Literal['dynamic', 'persistent'] | None = None
    sensitivity: Sensitivity | None = None
    terms: tuple[Text, ...] | None = None

    included_item_ids: tuple[UUID, ...] | None = Field(default=None, max_length=1000)
    excluded_item_ids: tuple[UUID, ...] | None = Field(default=None, max_length=1000)

    @model_validator(mode='after')
    def no_null_changes(self) -> Self:
        for key in self.model_fields_set - {'expected_version','editor','note'}:
            if getattr(self,key) is None:
                raise ValueError(f'{key} cannot be null')
        return self


class CreateTopic(BaseModel):
    model_config = ConfigDict(extra='forbid')
    title: Text
    description: str = ''
    sensitivity: Sensitivity = 'lasting'
    terms: tuple[Text, ...] = Field(min_length=1, max_length=30)
    editor: Text


class TopicEdit(Record):
    topic_id: UUID
    editor: Text
    note: str = ''
    before: Topic | None
    after: Topic


class TopicSummary(BaseModel):
    topic: Topic
    item_count: int
    unread_count: int
    saved_count: int
    relevance: float
    lifecycle: Literal['active', 'quiet', 'expired']


def relevance(sensitivity: Sensitivity, newest: datetime | None, at: datetime) -> float:
    if newest is None:
        return 0.0
    hours = max(0, (at-newest)/timedelta(hours=1))
    return round(2 ** (-hours/HALF_LIFE_HOURS[sensitivity]), 4)
