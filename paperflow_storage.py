"""Storage ports consumed by application services; no SQLite types escape here."""
from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Protocol, TypeVar, TYPE_CHECKING
from uuid import UUID

from paperflow_domain import (
    BucketClaim, BucketMembership, ClaimReview, EvidenceAssessment, EvidenceReview,
    Item, Record, ResearchBucket, ContentRevision, Annotation,
)
from paperflow_ingest_models import SourceObservation

if TYPE_CHECKING:
    from paperflow_reader.models import ReaderState, ReadingPreferences

T = TypeVar("T", bound=Record)


class StorageError(Exception):
    """Base error adapters expose to application callers."""


class ConflictError(StorageError):
    """An identity or uniqueness constraint would be violated."""


class ReferenceError(StorageError):
    """A referenced record is absent or inconsistent."""


class ResearchRepository(Protocol):
    def topic_candidates(self, start, end, limit: int): ...

    def topics(self): ...

    def get_topic(self, topic_id: UUID): ...

    def put_topic(self, topic, expected_version: int | None = None): ...

    def topic_run(self, run_id: UUID): ...

    def topic_runs(self, limit: int = 20): ...

    def save_topic_run(self, run): ...

    def topic_members(self, topic_id: UUID, run_id: UUID): ...

    def save_topic_edit(self, edit): ...

    def topic_edits(self, topic_id: UUID): ...

    def reader_pdf(self, item_id: UUID, *, include_data: bool = False): ...

    def add_reader_pdf(self, asset, data: bytes): ...

    def reader_state(self, item_id: UUID) -> ReaderState: ...

    def put_reader_state(self, state: ReaderState) -> None: ...

    def reading_preferences(self) -> ReadingPreferences: ...

    def put_reading_preferences(self, preferences: ReadingPreferences) -> None: ...

    def reader_feed(self, *, channel=None, query='', kind=None, saved=False, limit=20, before=None, topic_id=None, topic_start=None, topic_end=None): ...

    def reader_channels(self, *, saved: bool = False, query: str = '', kind: str | None = None): ...

    def latest_for_item(self, model, item_id: UUID): ...

    def add(self, record: T) -> T:
        """Append a record; exact replay by ID is a no-op, changes conflict."""
        ...

    def replace(self, record: T, expected_version: int) -> T: ...

    def receipt(self, key: str) -> tuple[str, dict] | None: ...

    def save_receipt(self, key: str, fingerprint: str, result: dict) -> None: ...

    def count(self, model, field: str, value) -> int: ...

    def page(self, model: type[T], *, field: str | None = None, value=None,
             limit: int = 10, offset: int = 0, preview: bool = False) -> dict: ...

    def latest_review(self, model, field: str, value) -> dict | None: ...

    def revision_preview(self, revision_id: UUID) -> dict: ...

    def get(self, model: type[T], record_id: UUID) -> T | None: ...

    def resolve_identifier(self, namespace: str, value: str) -> Item | None: ...

    def bucket_members(self, bucket_id: UUID) -> list[BucketMembership]: ...

    def buckets(self) -> list[ResearchBucket]: ...

    def bucket_claims(self, bucket_id: UUID) -> list[BucketClaim]: ...

    def reviews_for_claim(self, claim_id: UUID) -> list[ClaimReview]: ...

    def observations_for_item(self, item_id: UUID) -> list[SourceObservation]: ...

    def items(self, *, channel: str | None = None, query: str | None = None,
              limit: int = 50, offset: int = 0) -> list[Item]: ...

    def revisions_for_item(self, item_id: UUID) -> list[ContentRevision]: ...

    def annotations_for_revision(self, revision_id: UUID) -> list[Annotation]: ...

    def evidence_for_claim(self, claim_id: UUID) -> list[EvidenceAssessment]: ...

    def reviews_for_assessment(self, assessment_id: UUID) -> list[EvidenceReview]:
        """Insertion order; the last review is current, no reviews means proposed."""
        ...


class ResearchStorage(Protocol):
    def initialize(self) -> None: ...

    def transaction(self, *, write: bool = False) -> AbstractContextManager[ResearchRepository]:
        """Commit on success, roll back all writes on failure."""
        ...


def validate_record(repo: ResearchRepository, record: T) -> T:
    """Adapter-independent domain validation; called by services and adapters."""
    from paperflow_domain import (Claim, Identifier, ItemRelationship, ResearchBucket,
                                 BucketMembership, BucketClaim, Annotation, LifecycleEvent)
    record = type(record).model_validate(record.model_dump(mode="json"))
    def linked(model, record_id):
        value = repo.get(model, record_id)
        if value is None:
            raise ReferenceError(f"missing {model.__name__}")
        return value
    if isinstance(record, (Identifier, ContentRevision, SourceObservation)):
        linked(Item, record.item_id)
    if isinstance(record, ResearchBucket) and record.target_manuscript_id:
        if linked(Item, record.target_manuscript_id).kind != "manuscript":
            raise ReferenceError("bucket target must be a manuscript item")
    if isinstance(record, BucketMembership):
        linked(ResearchBucket, record.bucket_id)
        linked(Item, record.item_id)
    if isinstance(record, BucketClaim):
        linked(ResearchBucket, record.bucket_id)
        linked(Claim, record.claim_id)
    if isinstance(record, Annotation):
        if not record.matches(linked(ContentRevision, record.revision_id)):
            raise ReferenceError("annotation must match the exact passage in its revision")
    if isinstance(record, Claim):
        if record.source_annotation_id:
            linked(Annotation, record.source_annotation_id)
        if record.superseded_by:
            target = linked(Claim, record.superseded_by)
            seen = {record.id}
            while True:
                if target.id in seen:
                    raise ReferenceError("claim supersession cannot form a cycle")
                seen.add(target.id)
                if not target.superseded_by:
                    break
                target = linked(Claim, target.superseded_by)
    if isinstance(record, EvidenceAssessment):
        linked(Claim, record.claim_id)
        linked(ContentRevision, record.revision_id)
        if record.annotation_id:
            if linked(Annotation, record.annotation_id).revision_id != record.revision_id:
                raise ReferenceError("evidence annotation belongs to another revision")
    if isinstance(record, EvidenceReview):
        linked(EvidenceAssessment, record.assessment_id)
    if isinstance(record, ClaimReview):
        linked(Claim, record.claim_id)
    if isinstance(record, ItemRelationship):
        linked(Item, record.source_item_id)
        linked(Item, record.target_item_id)
        if record.source_revision_id:
            if linked(ContentRevision, record.source_revision_id).item_id != record.source_item_id:
                raise ReferenceError("relationship provenance belongs to another item")
    if isinstance(record, LifecycleEvent):
        model = {"bucket": ResearchBucket, "membership": BucketMembership, "claim": Claim}[record.entity_kind]
        linked(model, record.entity_id)
    return record


class ValidatingRepository:
    """Services retain invariants even when an adapter only implements persistence."""
    def __init__(self, repository):
        self.repository = repository

    def __getattr__(self, name):
        return getattr(self.repository, name)

    def add(self, record):
        return self.repository.add(validate_record(self, record))

    def replace(self, record, expected_version):
        return self.repository.replace(validate_record(self, record), expected_version)
