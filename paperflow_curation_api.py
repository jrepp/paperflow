"""HTTP adapters for curator operations and bounded read projections."""
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Header, Path, Query
from fastapi.responses import PlainTextResponse

from paperflow_curation import (
    AddClaim, AddEvidence, AddMember, AddRevision, Annotate, CurationService, Review,
    AddIdentifier, AddRelationship, ChangeBucket, ChangeMember, SupersedeClaim,
    ItemDetail, ItemSummary, BucketDetail, BucketSummary, ClaimDetail, ClaimOverview,
    Page, RevisionPreview, EvidenceSummary,
)
from paperflow_domain import (
    Annotation, BucketMembership, BucketClaim, Claim, ClaimReview, ContentRevision,
    EvidenceAssessment, EvidenceReview, Item, ResearchBucket, Identifier, ItemRelationship, LifecycleEvent,
)
from paperflow_ingest_models import SourceObservation

Key = Annotated[str | None, Header(alias="Idempotency-Key", min_length=1, max_length=200)]
Limit = Annotated[int, Query(ge=1, le=200)]
Offset = Annotated[int, Query(ge=0)]


def curation_router(service: CurationService) -> APIRouter:
    router = APIRouter(prefix="/api/research", tags=["curation"])

    def write(key, operation, body, action):
        return service.command(key, operation, body.model_dump(mode="json", exclude_unset=True), action)

    @router.get("/items", response_model=list[Item])
    def items(channel: str | None = None, query: str | None = None, limit: Limit = 50, offset: Offset = 0):
        return service.items(channel=channel, query=query, limit=limit, offset=offset)

    @router.post("/items", response_model=Item, status_code=201)
    def add_item(body: Item, key: Key = None):
        return write(key, "item.create", body, lambda s: s.add(body))

    @router.get("/items/{item_id}", response_model=ItemSummary | ItemDetail)
    def item(item_id: UUID, view: Literal["full", "compact"] = "full"):
        return service.item_summary(item_id) if view == "compact" else service.item(item_id)

    @router.post("/items/{item_id}/revisions", response_model=ContentRevision, status_code=201)
    def revision(item_id: UUID, body: AddRevision, key: Key = None):
        return write(key, f"{item_id}.revision", body, lambda s: s.add_revision(item_id, body))

    @router.get("/revisions/{revision_id}", response_model=ContentRevision)
    def revision_detail(revision_id: UUID):
        return service.revision(revision_id)

    @router.post("/revisions/{revision_id}/annotations", response_model=Annotation, status_code=201)
    def annotate(revision_id: UUID, body: Annotate, key: Key = None):
        return write(key, f"{revision_id}.annotate", body, lambda s: s.annotate(revision_id, body))

    @router.get("/buckets", response_model=Page[ResearchBucket])
    def buckets(limit: Limit = 10, offset: Offset = 0):
        return service.page(ResearchBucket, limit=limit, offset=offset)

    @router.post("/buckets", response_model=ResearchBucket, status_code=201)
    def create_bucket(body: ResearchBucket, key: Key = None):
        return write(key, "bucket.create", body, lambda s: s.add(body))

    @router.get("/buckets/{bucket_id}", response_model=BucketSummary | BucketDetail)
    def bucket(bucket_id: UUID, view: Literal["full", "compact"] = "full"):
        return service.bucket_summary(bucket_id) if view == "compact" else service.bucket(bucket_id)

    @router.post("/buckets/{bucket_id}/members", response_model=BucketMembership, status_code=201)
    def add_member(bucket_id: UUID, body: AddMember, key: Key = None):
        return write(key, f"{bucket_id}.member", body, lambda s: s.add_member(bucket_id, body))

    @router.post("/buckets/{bucket_id}/claims", response_model=Claim, status_code=201)
    def add_claim(bucket_id: UUID, body: AddClaim, key: Key = None):
        return write(key, f"{bucket_id}.claim", body, lambda s: s.add_claim(bucket_id, body))

    @router.get("/claims/{claim_id}", response_model=ClaimOverview | ClaimDetail)
    def claim(claim_id: UUID, view: Literal["full", "compact"] = "full"):
        return service.claim_summary(claim_id) if view == "compact" else service.claim(claim_id)

    @router.post("/claims/{claim_id}/reviews", response_model=ClaimReview, status_code=201)
    def review_claim(claim_id: UUID, body: Review, key: Key = None):
        return write(key, f"{claim_id}.review", body, lambda s: s.review_claim(claim_id, body))

    @router.post("/claims/{claim_id}/evidence", response_model=EvidenceAssessment, status_code=201)
    def evidence(claim_id: UUID, body: AddEvidence, key: Key = None):
        return write(key, f"{claim_id}.evidence", body, lambda s: s.add_evidence(claim_id, body))

    @router.get("/claims/{claim_id}/evidence", response_model=Page[EvidenceSummary])
    def evidence_page(claim_id: UUID, limit: Limit = 10, offset: Offset = 0):
        return service.evidence_page(claim_id, limit, offset)

    @router.post("/evidence/{assessment_id}/reviews", response_model=EvidenceReview, status_code=201)
    def review_evidence(assessment_id: UUID, body: Review, key: Key = None):
        return write(key, f"{assessment_id}.review", body, lambda s: s.review_evidence(assessment_id, body))

    @router.patch("/buckets/{bucket_id}", response_model=ResearchBucket)
    def change_bucket(bucket_id: UUID, body: ChangeBucket, key: Key = None):
        return write(key, f"{bucket_id}.change", body, lambda s: s.change(ResearchBucket, bucket_id, body))

    @router.get("/memberships/{membership_id}", response_model=BucketMembership)
    def membership(membership_id: UUID):
        return service.membership(membership_id)

    @router.patch("/memberships/{membership_id}", response_model=BucketMembership)
    def change_member(membership_id: UUID, body: ChangeMember, key: Key = None):
        return write(key, f"{membership_id}.change", body, lambda s: s.change(BucketMembership, membership_id, body))

    @router.post("/claims/{claim_id}/supersede", response_model=Claim)
    def supersede(claim_id: UUID, body: SupersedeClaim, key: Key = None):
        return write(key, f"{claim_id}.supersede", body, lambda s: s.change(Claim, claim_id, body))

    @router.get("/identifiers/resolve", response_model=Item)
    def resolve(namespace: str, value: str):
        return service.resolve(namespace, value)

    @router.post("/items/{item_id}/identifiers", response_model=Identifier, status_code=201)
    def identify(item_id: UUID, body: AddIdentifier, key: Key = None):
        return write(key, f"{item_id}.identify", body, lambda s: s.add_identifier(item_id, body))

    @router.post("/items/{item_id}/relationships", response_model=ItemRelationship, status_code=201)
    def relate(item_id: UUID, body: AddRelationship, key: Key = None):
        return write(key, f"{item_id}.relate", body, lambda s: s.add_relationship(item_id, body))

    # All collection endpoints execute LIMIT/OFFSET in storage. Revisions return
    # previews; full source text is available at /revisions/{revision_id}.
    def collection(path, model, parent, field):
        parameter = {Item: "item_id", ContentRevision: "revision_id", ResearchBucket: "bucket_id",
                     Claim: "claim_id", EvidenceAssessment: "assessment_id", BucketMembership: "membership_id"}[parent]
        path = path.replace("{parent_id}", "{" + parameter + "}")
        def endpoint(parent_id: UUID = Path(alias=parameter), limit: Limit = 10, offset: Offset = 0):
            return service.page(model, parent=parent, parent_id=parent_id, field=field, limit=limit, offset=offset)
        router.add_api_route(path, endpoint, methods=["GET"], response_model=Page[RevisionPreview if model is ContentRevision else model], name=f"page_{model.__name__}_{field}")

    collection("/items/{parent_id}/revisions", ContentRevision, Item, "item_id")
    collection("/items/{parent_id}/observations", SourceObservation, Item, "item_id")
    collection("/items/{parent_id}/identifiers", Identifier, Item, "item_id")
    collection("/items/{parent_id}/relationships", ItemRelationship, Item, "source_item_id")
    collection("/items/{parent_id}/incoming-relationships", ItemRelationship, Item, "target_item_id")
    collection("/revisions/{parent_id}/annotations", Annotation, ContentRevision, "revision_id")
    collection("/buckets/{parent_id}/members", BucketMembership, ResearchBucket, "bucket_id")
    collection("/buckets/{parent_id}/claims", BucketClaim, ResearchBucket, "bucket_id")
    collection("/claims/{parent_id}/reviews", ClaimReview, Claim, "claim_id")
    collection("/evidence/{parent_id}/reviews", EvidenceReview, EvidenceAssessment, "assessment_id")
    collection("/buckets/{parent_id}/history", LifecycleEvent, ResearchBucket, "entity_id")
    collection("/memberships/{parent_id}/history", LifecycleEvent, BucketMembership, "entity_id")
    collection("/claims/{parent_id}/history", LifecycleEvent, Claim, "entity_id")

    @router.get("/buckets/{bucket_id}/export", response_class=PlainTextResponse)
    def export(bucket_id: UUID):
        return PlainTextResponse(service.export(bucket_id), media_type="text/markdown")

    return router
