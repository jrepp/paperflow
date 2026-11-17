"""Curator application operations. No FastAPI, CLI, or SQLite dependencies."""
from __future__ import annotations

from typing import Generic, Literal, TypeVar
from contextlib import contextmanager
import hashlib
import json
from uuid import UUID, uuid5

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from paperflow_domain import (
    Annotation, BucketClaim, BucketMembership, Claim, ClaimReview, ContentRevision,
    EvidenceAssessment, EvidenceReview, Item, ResearchBucket, SourceText, Text, Stance,
    Identifier, ItemRelationship, LifecycleEvent,
)
from paperflow_ingest_models import SourceObservation
from paperflow_storage import ResearchStorage, ValidatingRepository, ConflictError


class NotFoundError(Exception):
    pass


def require(repo, model, record_id):
    record = repo.get(model, record_id)
    if record is None:
        raise NotFoundError(f"{model.__name__} not found")
    return record


class RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AddMember(RequestModel):
    item_id: UUID
    role: Literal["collected", "background", "method", "candidate_citation"] = "collected"
    note: str = ""
    position: int = Field(default=0, ge=0)


class AddRevision(RequestModel):
    markdown: SourceText
    evidence_basis: Literal["full_text", "abstract", "excerpt"] = "excerpt"
    processor_version: Text = "manual-v1"


class Authorship(RequestModel):
    creator: Text
    creator_kind: Literal["human", "processor"] = "human"
    processor_version: Text | None = None
    processing_run_id: UUID | None = None


class Annotate(Authorship):
    quote: SourceText
    start: int | None = Field(default=None, ge=0)
    note: str = ""
    creator: Text


class AddClaim(Authorship):
    statement: Text
    scope: str = ""
    source_annotation_id: UUID | None = None
    creator: Text
    creator_kind: Literal["human", "processor"] = "human"


class Review(RequestModel):
    reviewer_kind: Literal["human", "processor"] = "human"
    processor_version: Text | None = None
    processing_run_id: UUID | None = None
    decision: Literal["accepted", "rejected", "needs_review"]
    reviewer: Text
    note: str = ""


class AddEvidence(Authorship):
    revision_id: UUID
    annotation_id: UUID | None = None
    stance: Stance
    rationale: Text
    creator: Text
    creator_kind: Literal["human", "processor"] = "human"
    processor_version: Text | None = None


class LifecycleChange(Authorship):
    expected_version: int = Field(ge=1)


class ChangeBucket(LifecycleChange):
    status: Literal["active", "archived"]


class ChangeMember(LifecycleChange):
    role: Literal["collected", "background", "method", "candidate_citation"] | None = None
    note: str | None = None
    position: int | None = Field(default=None, ge=0)
    active: bool | None = None


class SupersedeClaim(LifecycleChange):
    replacement_claim_id: UUID


class AddIdentifier(RequestModel):
    namespace: Text
    value: Text


class AddRelationship(Authorship):
    target_item_id: UUID
    kind: Literal["links_to", "discusses", "cites", "version_of"]
    note: str = ""
    source_revision_id: UUID | None = None


class ItemDetail(BaseModel):
    item: Item
    observations: list[SourceObservation]
    revisions: list[ContentRevision]


class EvidenceDetail(BaseModel):
    assessment: EvidenceAssessment
    revision: ContentRevision
    annotation: Annotation | None
    reviews: list[EvidenceReview]
    review_status: str


class ClaimDetail(BaseModel):
    claim: Claim
    reviews: list[ClaimReview]
    review_status: str
    evidence: list[EvidenceDetail]


class MemberDetail(BaseModel):
    membership: BucketMembership
    item: Item


class BucketDetail(BaseModel):
    bucket: ResearchBucket
    members: list[MemberDetail]
    claims: list[ClaimDetail]


P = TypeVar("P")


class PageInfo(BaseModel):
    total: int
    limit: int
    offset: int
    next_offset: int | None


class Page(PageInfo, Generic[P]):
    records: list[P]


class RevisionPreview(BaseModel):
    id: UUID
    item_id: UUID
    created_at: AwareDatetime
    processor_version: str
    evidence_basis: Literal["full_text", "abstract", "excerpt"]
    content_hash: str
    preview_text: str
    character_count: int


class ItemSummary(BaseModel):
    item: Item
    revisions: list[RevisionPreview]
    observations: list[SourceObservation]
    observation_count: int
    revision_page: PageInfo
    projection: Literal["compact"]


class ClaimSummary(BaseModel):
    claim: Claim
    review_status: str
    latest_review: ClaimReview | None
    evidence_count: int


class EvidenceSummary(BaseModel):
    assessment: EvidenceAssessment
    revision: RevisionPreview
    latest_review: EvidenceReview | None
    review_status: str


class ClaimOverview(ClaimSummary):
    evidence: list[EvidenceSummary]
    evidence_page: PageInfo
    projection: Literal["compact"]


class BucketSummary(BaseModel):
    bucket: ResearchBucket
    members: list[MemberDetail]
    claims: list[ClaimSummary]
    member_page: PageInfo
    claim_page: PageInfo
    projection: Literal["compact"]


class CurationService:
    def __init__(self, storage: ResearchStorage):
        self.storage = storage

    @contextmanager
    def _transaction(self):
        with self.storage.transaction() as repo:
            yield ValidatingRepository(repo)

    def command(self, key, operation, payload, execute):
        """Persist a response and its writes in one serialized transaction."""
        fingerprint = hashlib.sha256(json.dumps({"operation": operation, "payload": payload}, sort_keys=True).encode()).hexdigest()
        with self.storage.transaction(write=True) as repo:
            previous = repo.receipt(key) if key else None
            if previous:
                if previous[0] != fingerprint:
                    raise ConflictError("idempotency key was used for another operation or payload")
                return previous[1]
            class UnitStorage:
                @contextmanager
                def transaction(self, **kwargs):
                    yield repo
            result = execute(CurationService(UnitStorage()))
            result = result.model_dump(mode="json") if isinstance(result, BaseModel) else result
            if key:
                repo.save_receipt(key, fingerprint, result)
            return result

    def add(self, record):
        with self._transaction() as repo:
            return repo.add(record)

    def items(self, **filters):
        with self._transaction() as repo:
            return repo.items(**filters)

    def item(self, item_id: UUID) -> ItemDetail:
        with self._transaction() as repo:
            return ItemDetail(item=require(repo, Item, item_id),
                observations=repo.observations_for_item(item_id), revisions=repo.revisions_for_item(item_id))

    def buckets(self):
        with self._transaction() as repo:
            return repo.buckets()

    def add_member(self, bucket_id: UUID, request: AddMember):
        with self._transaction() as repo:
            require(repo, ResearchBucket, bucket_id)
            require(repo, Item, request.item_id)
            existing = next((m for m in repo.bucket_members(bucket_id) if m.item_id == request.item_id), None)
            if existing:
                if any(getattr(existing, key) != value for key, value in request.model_dump().items()):
                    from paperflow_storage import ConflictError
                    raise ConflictError("membership exists with different notes or role")
                return existing
            return repo.add(BucketMembership(bucket_id=bucket_id, **request.model_dump()))

    def add_revision(self, item_id: UUID, request: AddRevision):
        revision = ContentRevision.from_markdown(item_id=item_id, **request.model_dump())
        record_id = uuid5(item_id, f"{revision.processor_version}:{revision.evidence_basis}:{revision.content_hash}")
        with self._transaction() as repo:
            require(repo, Item, item_id)
            # Reuse existing feed/manual revisions regardless of their ID scheme.
            existing = next((r for r in repo.revisions_for_item(item_id)
                if (r.content_hash, r.processor_version, r.evidence_basis) ==
                   (revision.content_hash, revision.processor_version, revision.evidence_basis)), None)
            return existing or repo.add(revision.model_copy(update={"id": record_id}))

    def annotate(self, revision_id: UUID, request: Annotate):
        with self._transaction() as repo:
            revision = require(repo, ContentRevision, revision_id)
            start = request.start
            if start is None:
                start = revision.markdown.find(request.quote)
                if start < 0:
                    raise ValueError("quote not found in the source revision")
                if revision.markdown.find(request.quote, start + 1) >= 0:
                    raise ValueError("quote occurs more than once; provide a start offset")
            return repo.add(Annotation(revision_id=revision_id, start=start,
                end=start + len(request.quote), **request.model_dump(exclude={"start"})))

    def annotations(self, revision_id):
        with self._transaction() as repo:
            require(repo, ContentRevision, revision_id)
            return repo.annotations_for_revision(revision_id)

    def add_claim(self, bucket_id: UUID, request: AddClaim):
        with self._transaction() as repo:
            require(repo, ResearchBucket, bucket_id)
            claim = repo.add(Claim(**request.model_dump()))
            repo.add(BucketClaim(bucket_id=bucket_id, claim_id=claim.id))
            return claim

    def review_claim(self, claim_id: UUID, request: Review):
        with self._transaction() as repo:
            require(repo, Claim, claim_id)
            return repo.add(ClaimReview(claim_id=claim_id, **request.model_dump()))

    def add_evidence(self, claim_id: UUID, request: AddEvidence):
        assessment = EvidenceAssessment(claim_id=claim_id, **request.model_dump())
        with self._transaction() as repo:
            require(repo, Claim, claim_id)
            require(repo, ContentRevision, request.revision_id)
            return repo.add(assessment)

    def review_evidence(self, assessment_id: UUID, request: Review):
        with self._transaction() as repo:
            require(repo, EvidenceAssessment, assessment_id)
            return repo.add(EvidenceReview(assessment_id=assessment_id, **request.model_dump()))

    def _claim(self, repo, claim_id) -> ClaimDetail:
        claim = require(repo, Claim, claim_id)
        reviews = repo.reviews_for_claim(claim_id)
        evidence = []
        for assessment in repo.evidence_for_claim(claim_id):
            decisions = repo.reviews_for_assessment(assessment.id)
            evidence.append(EvidenceDetail(assessment=assessment,
                revision=require(repo, ContentRevision, assessment.revision_id),
                annotation=require(repo, Annotation, assessment.annotation_id) if assessment.annotation_id else None,
                reviews=decisions, review_status=decisions[-1].decision if decisions else "proposed"))
        return ClaimDetail(claim=claim, reviews=reviews, review_status=reviews[-1].decision if reviews else "proposed", evidence=evidence)

    def claim(self, claim_id: UUID) -> ClaimDetail:
        with self._transaction() as repo:
            return self._claim(repo, claim_id)

    def bucket(self, bucket_id: UUID) -> BucketDetail:
        with self._transaction() as repo:
            bucket = require(repo, ResearchBucket, bucket_id)
            return BucketDetail(bucket=bucket,
                members=[MemberDetail(membership=m, item=require(repo, Item, m.item_id)) for m in repo.bucket_members(bucket_id)],
                claims=[self._claim(repo, c.claim_id) for c in repo.bucket_claims(bucket_id)])

    def export(self, bucket_id: UUID) -> str:
        detail = self.bucket(bucket_id)
        lines = [f"# {detail.bucket.title}", "", detail.bucket.research_question, "", detail.bucket.description, "", "## Collected sources", ""]
        for member in detail.members:
            item = member.item
            lines.extend([f"- {item.title} ({member.membership.role}; item {item.id})",
                          f"  Source: {item.canonical_url or 'local note'}", f"  Note: {member.membership.note}"])
        lines.extend(["", "## Claims and evidence", ""])
        for view in detail.claims:
            lines.extend([f"### {view.claim.statement}", "", f"Review: {view.review_status}; claim {view.claim.id}",
                          f"Scope: {view.claim.scope}", f"Creator: {view.claim.creator} ({view.claim.creator_kind})",
                          f"Version: {view.claim.version}; superseded by: {view.claim.superseded_by or 'none'}", ""])
            if not view.evidence:
                lines.append("No evidence recorded; unresolved.")
            for evidence in view.evidence:
                assessment = evidence.assessment
                source = self.item(evidence.revision.item_id).item
                lines.extend([f"- {assessment.stance} — {evidence.review_status}: {assessment.rationale}",
                    f"  Evidence basis: {evidence.revision.evidence_basis}; revision {evidence.revision.id}",
                    f"  Source: {source.canonical_url or source.id}",
                    f"  Assessor: {assessment.creator} ({assessment.creator_kind})"])
                if evidence.annotation:
                    lines.append(f"  Passage: {evidence.annotation.quote}")
                if evidence.reviews:
                    lines.append(f"  Latest reviewer: {evidence.reviews[-1].reviewer} ({evidence.reviews[-1].reviewer_kind}); {evidence.reviews[-1].note}")
            lines.append("")
        return "\n".join(lines) + "\n"


    def resolve(self, namespace, value):
        namespace, value = self.normalize_identifier(namespace, value)
        with self._transaction() as repo:
            result = repo.resolve_identifier(namespace, value)
            if result is None:
                raise NotFoundError("identifier not found")
            return result

    @staticmethod
    def normalize_identifier(namespace, value):
        namespace, value = namespace.strip().lower(), value.strip()
        if namespace == "doi":
            for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
                if value.lower().startswith(prefix):
                    value = value[len(prefix):]
                    break
            value = value.lower()
        if not namespace or not value:
            raise ValueError("identifier namespace and value are required")
        return namespace, value

    def add_identifier(self, item_id, request):
        namespace, value = self.normalize_identifier(request.namespace, request.value)
        identifier = Identifier(id=uuid5(item_id, f"identifier:{namespace}:{value}"),
                                item_id=item_id, namespace=namespace, value=value)
        with self._transaction() as repo:
            existing = repo.get(Identifier, identifier.id)
            return existing or repo.add(identifier)


    def add_relationship(self, item_id, request):
        return self.add(ItemRelationship(source_item_id=item_id, **request.model_dump()))

    def change(self, model, record_id, request):
        with self._transaction() as repo:
            old = require(repo, model, record_id)
            if old.version != request.expected_version:
                raise ConflictError("stale version; reload the record")
            data = request.model_dump(exclude_none=True)
            actor = {key: data.pop(key) for key in tuple(data) if key in Authorship.model_fields}
            data.pop("expected_version")
            if model is Claim:
                if old.superseded_by:
                    raise ConflictError("claim is already superseded")
                data = {"superseded_by": data["replacement_claim_id"]}
            changed = model.model_validate({**old.model_dump(), **data, "version": old.version + 1})
            repo.replace(changed, old.version)
            repo.add(LifecycleEvent(entity_id=record_id,
                entity_kind={ResearchBucket: "bucket", BucketMembership: "membership", Claim: "claim"}[model],
                version=changed.version, action="supersede" if model is Claim else "update",
                before=old.model_dump(mode="json"), after=changed.model_dump(mode="json"), **actor))
            return changed

    def page(self, model, *, parent=None, parent_id=None, field=None, limit=10, offset=0):
        with self._transaction() as repo:
            if parent and not repo.count(parent, "id", parent_id):
                raise NotFoundError(f"{parent.__name__} not found")
            return repo.page(model, field=field, value=parent_id, limit=limit, offset=offset,
                             preview=model is ContentRevision)

    def membership(self, membership_id):
        with self._transaction() as repo:
            return require(repo, BucketMembership, membership_id)

    def revision(self, revision_id):
        with self._transaction() as repo:
            return require(repo, ContentRevision, revision_id)

    def item_summary(self, item_id):
        with self._transaction() as repo:
            item = require(repo, Item, item_id)
            revisions = repo.page(ContentRevision, field="item_id", value=item_id, limit=3, preview=True)
            return {"item": item.model_dump(mode="json"), "revisions": revisions["records"],
                    "observations": [], "observation_count": repo.count(SourceObservation, "item_id", item_id),
                    "revision_page": {k:v for k,v in revisions.items() if k != "records"}, "projection": "compact"}

    def _claim_summary(self, repo, claim_id):
        claim = require(repo, Claim, claim_id)
        review = repo.latest_review(ClaimReview, "claim_id", claim_id)
        return {"claim": claim.model_dump(mode="json"), "review_status": review["decision"] if review else "proposed",
                "latest_review": review, "evidence_count": repo.count(EvidenceAssessment, "claim_id", claim_id)}

    def bucket_summary(self, bucket_id):
        with self._transaction() as repo:
            bucket = require(repo, ResearchBucket, bucket_id)
            members = repo.page(BucketMembership, field="bucket_id", value=bucket_id)
            claims = repo.page(BucketClaim, field="bucket_id", value=bucket_id)
            return {"bucket": bucket.model_dump(mode="json"),
                    "members": [{"membership": m, "item": require(repo, Item, UUID(m["item_id"])).model_dump(mode="json")} for m in members["records"]],
                    "claims": [self._claim_summary(repo, UUID(c["claim_id"])) for c in claims["records"]],
                    "member_page": {k:v for k,v in members.items() if k != "records"},
                    "claim_page": {k:v for k,v in claims.items() if k != "records"}, "projection": "compact"}

    def evidence_page(self, claim_id, limit=10, offset=0):
        with self._transaction() as repo:
            return self._evidence_page(repo, claim_id, limit, offset)

    def _evidence_page(self, repo, claim_id, limit=10, offset=0):
        require(repo, Claim, claim_id)
        page = repo.page(EvidenceAssessment, field="claim_id", value=claim_id, limit=limit, offset=offset)
        records = []
        for assessment in page["records"]:
            review = repo.latest_review(EvidenceReview, "assessment_id", assessment["id"])
            records.append({"assessment": assessment, "revision": repo.revision_preview(UUID(assessment["revision_id"])),
                            "latest_review": review, "review_status": review["decision"] if review else "proposed"})
        return {**page, "records": records}


    def claim_summary(self, claim_id):
        with self._transaction() as repo:
            summary = self._claim_summary(repo, claim_id)
            page = self._evidence_page(repo, claim_id)
        return {**summary, "evidence": page["records"], "evidence_page": {k:v for k,v in page.items() if k != "records"}, "projection": "compact"}
