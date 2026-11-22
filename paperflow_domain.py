"""Validated research records, independent of persistence and transport."""
from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Annotated, Literal, Self
from uuid import UUID, uuid4

from pydantic import (
    AnyHttpUrl, AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, model_validator,
)

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
SourceText = Annotated[str, StringConstraints(min_length=1)]
ItemKind = Literal["paper", "article", "discussion", "note", "manuscript"]
Stance = Literal["supports", "challenges", "qualifies", "provides_context", "inconclusive"]
ReviewDecision = Literal["accepted", "rejected", "needs_review"]


def now() -> datetime:
    return datetime.now(UTC)


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True)

    id: UUID = Field(default_factory=uuid4)
    created_at: AwareDatetime = Field(default_factory=now)


class AuthoredRecord(Record):
    creator: Text
    creator_kind: Literal["human", "processor", "unknown"] = "human"
    processor_version: Text | None = None
    processing_run_id: UUID | None = None

    @model_validator(mode="after")
    def validate_actor(self) -> Self:
        if self.creator_kind == "processor" and self.processor_version is None:
            raise ValueError("processor attribution requires processor_version")
        return self


class ReviewedRecord(Record):
    reviewer: Text
    reviewer_kind: Literal["human", "processor", "unknown"] = "human"
    processor_version: Text | None = None
    processing_run_id: UUID | None = None

    @model_validator(mode="after")
    def validate_actor(self) -> Self:
        if self.reviewer_kind == "processor" and self.processor_version is None:
            raise ValueError("processor attribution requires processor_version")
        return self


class Item(Record):
    kind: ItemKind
    title: Text
    canonical_url: AnyHttpUrl | None = None
    authors: tuple[Text, ...] = ()
    published_at: AwareDatetime | None = None


class Identifier(Record):
    """An authoritative alias, not an identifier mentioned in an item's text."""

    item_id: UUID
    namespace: Text
    value: Text


class ContentRevision(Record):
    item_id: UUID
    markdown: SourceText
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    processor_version: Text
    evidence_basis: Literal["full_text", "abstract", "excerpt"]

    @model_validator(mode="after")
    def verify_hash(self) -> Self:
        if hashlib.sha256(self.markdown.encode("utf-8")).hexdigest() != self.content_hash:
            raise ValueError("content_hash must match the stored Markdown")
        return self

    @classmethod
    def from_markdown(cls, *, item_id: UUID, markdown: str,
                      processor_version: str = "manual-v1",
                      evidence_basis: Literal["full_text", "abstract", "excerpt"] = "full_text") -> Self:
        return cls(item_id=item_id, markdown=markdown,
                   content_hash=hashlib.sha256(markdown.encode("utf-8")).hexdigest(),
                   processor_version=processor_version, evidence_basis=evidence_basis)


class ResearchBucket(Record):
    version: int = Field(default=1, ge=1)
    title: Text
    research_question: Text
    description: str = ""
    status: Literal["active", "archived"] = "active"
    target_manuscript_id: UUID | None = None


class BucketMembership(Record):
    version: int = Field(default=1, ge=1)
    active: bool = True
    bucket_id: UUID
    item_id: UUID
    role: Literal["collected", "background", "method", "candidate_citation"] = "collected"
    note: str = ""
    position: int = Field(default=0, ge=0)


class Annotation(AuthoredRecord):
    revision_id: UUID
    quote: SourceText
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    note: str = ""

    @model_validator(mode="after")
    def valid_range(self) -> Self:
        if self.end <= self.start or self.end - self.start != len(self.quote):
            raise ValueError("annotation offsets must span the exact quote in Unicode characters")
        return self

    def matches(self, revision: ContentRevision) -> bool:
        """Offsets refer to the stored Markdown, measured in Unicode characters."""
        return (self.revision_id == revision.id and
                revision.markdown[self.start:self.end] == self.quote)


class Claim(AuthoredRecord):
    version: int = Field(default=1, ge=1)
    superseded_by: UUID | None = None
    statement: Text
    scope: str = ""
    source_annotation_id: UUID | None = None


class BucketClaim(Record):
    bucket_id: UUID
    claim_id: UUID
    framing: str = ""


class EvidenceAssessment(AuthoredRecord):
    """Immutable interpretation; review decisions are separate audit records."""

    claim_id: UUID
    revision_id: UUID
    annotation_id: UUID | None = None
    stance: Stance
    rationale: Text


class EvidenceReview(ReviewedRecord):
    assessment_id: UUID
    decision: ReviewDecision
    note: str = ""


class ClaimReview(ReviewedRecord):
    claim_id: UUID
    decision: ReviewDecision
    note: str = ""


class ItemRelationship(AuthoredRecord):
    source_item_id: UUID
    target_item_id: UUID
    kind: Literal["links_to", "discusses", "cites", "version_of"]
    note: str = ""
    source_revision_id: UUID | None = None

    @model_validator(mode="after")
    def different_items(self) -> Self:
        if self.source_item_id == self.target_item_id:
            raise ValueError("an item cannot relate to itself")
        return self


class LifecycleEvent(AuthoredRecord):
    entity_id: UUID
    entity_kind: Literal["bucket", "membership", "claim"]
    version: int = Field(ge=2)
    action: Text
    before: dict[object, object]
    after: dict[object, object]
