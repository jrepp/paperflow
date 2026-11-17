"""Reader state is mutable and independent of immutable research evidence."""
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ReaderModel(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)


class ReadingAnchor(ReaderModel):
    revision_id: UUID
    character_offset: int = Field(ge=0)


class ReaderState(ReaderModel):
    item_id: UUID
    saved: bool = False
    status: Literal['unread', 'in_progress', 'finished', 'dismissed'] = 'unread'
    anchor: ReadingAnchor | None = None
    version: int = Field(default=0, ge=0)


class ChangeReaderState(ReaderModel):
    expected_version: int = Field(ge=0)
    saved: bool | None = None
    status: Literal['unread', 'in_progress', 'finished', 'dismissed'] | None = None
    anchor: ReadingAnchor | None = None

    @model_validator(mode='after')
    def reject_null_flags(self):
        for field in ('saved', 'status'):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f'{field} cannot be null')
        return self


class ReadingPreferences(ReaderModel):
    version: int = Field(default=0, ge=0)
    font_size: Literal[18, 20, 22, 24, 28] = 20
    line_height: Literal[1.5, 1.75, 2.0] = 1.75
    measure: Literal[55, 65, 75] = 65
    font: Literal['serif', 'sans'] = 'serif'
    theme: Literal['paper', 'night', 'contrast'] = 'paper'


class ChangePreferences(ReaderModel):
    expected_version: int = Field(ge=0)
    preferences: ReadingPreferences


from paperflow_domain import Item, ContentRevision
from paperflow_ingest_models import SourceObservation


class PDFAsset(ReaderModel):
    item_id: UUID
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    page_count: int = Field(ge=1)
    byte_count: int = Field(gt=0)


class ReaderDocument(ReaderModel):
    item: Item
    state: ReaderState
    revision: ContentRevision | None
    latest_revision_id: UUID | None
    observation: SourceObservation | None
    pdf: PDFAsset | None = None
