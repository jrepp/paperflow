from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from paperflow_domain import (
    Annotation, BucketClaim, BucketMembership, Claim, ClaimReview, ContentRevision,
    EvidenceAssessment, EvidenceReview, Identifier, Item, ResearchBucket,
)
from paperflow_settings import ResearchSettings, StorageSettings, load_research_settings
from paperflow_storage import ConflictError, ReferenceError, ResearchStorage, StorageError
from paperflow_storage_sqlite import SQLiteResearchStorage


@pytest.fixture
def store(tmp_path) -> ResearchStorage:
    storage = SQLiteResearchStorage(StorageSettings(path=tmp_path / "research.db"))
    storage.initialize()
    return storage


def test_curator_journey_preserves_sources_and_opposing_evidence(store):
    article = Item(kind="article", title="Small models win")
    paper = Item(kind="paper", title="A bounded evaluation")
    manuscript = Item(kind="manuscript", title="My review")
    bucket = ResearchBucket(title="Model size", research_question="Under which conditions?",
                            target_manuscript_id=manuscript.id)
    other = ResearchBucket(title="Evaluation", research_question="What was measured?")
    revision = ContentRevision.from_markdown(item_id=paper.id, markdown="Small models win on task A.")
    annotation = Annotation(revision_id=revision.id, quote="task A", start=20, end=26, creator="reader")
    claim = Claim(statement="Small models always win", creator="reader")
    support = EvidenceAssessment(claim_id=claim.id, revision_id=revision.id, annotation_id=annotation.id,
                                 stance="supports", rationale="On the tested task only", creator="reader")
    challenge = EvidenceAssessment(claim_id=claim.id, revision_id=revision.id, stance="challenges",
                                   rationale="The generalization is not established", creator="reader")
    first_review = EvidenceReview(assessment_id=support.id, decision="accepted", reviewer="reader")
    later_review = EvidenceReview(assessment_id=support.id, decision="needs_review", reviewer="reader",
                                 note="Reconsider scope")
    claim_review = ClaimReview(claim_id=claim.id, decision="accepted", reviewer="reader")
    with store.transaction() as repo:
        for record in (article, paper, manuscript, bucket, other, revision, annotation, claim, support, challenge,
                       first_review, later_review, claim_review):
            repo.add(record)
        repo.add(Identifier(item_id=paper.id, namespace="doi", value="10.1234/example"))
        repo.add(BucketMembership(bucket_id=bucket.id, item_id=article.id, note="Starting point"))
        repo.add(BucketMembership(bucket_id=other.id, item_id=article.id, note="Methods context"))
        repo.add(BucketClaim(bucket_id=bucket.id, claim_id=claim.id))
    with store.transaction() as repo:
        assert repo.get(Item, paper.id) == paper
        assert repo.resolve_identifier("doi", "10.1234/example") == paper
        assert repo.bucket_members(bucket.id)[0].item_id == repo.bucket_members(other.id)[0].item_id
        assert repo.bucket_members(bucket.id)[0].note != repo.bucket_members(other.id)[0].note
        assert repo.evidence_for_claim(claim.id) == [support, challenge]
        assert repo.reviews_for_assessment(support.id) == [first_review, later_review]
        assert repo.reviews_for_assessment(challenge.id) == []
        assert repo.reviews_for_claim(claim.id) == [claim_review]
        assert repo.bucket_claims(bucket.id)[0].claim_id == claim.id
        assert repo.buckets() == [bucket, other]


def test_source_revision_does_not_retarget_annotations(store):
    item = Item(kind="article", title="Changing article")
    old = ContentRevision.from_markdown(item_id=item.id, markdown="Original statement")
    new = ContentRevision.from_markdown(item_id=item.id, markdown="Corrected statement")
    annotation = Annotation(revision_id=old.id, quote="Original", start=0, end=8, creator="reader")
    with store.transaction() as repo:
        for record in (item, old, annotation, new):
            repo.add(record)
    with store.transaction() as repo:
        assert repo.get(Annotation, annotation.id).revision_id == old.id
        assert repo.get(ContentRevision, old.id).markdown == "Original statement"
        with pytest.raises(ReferenceError):
            repo.add(Annotation(revision_id=new.id, quote="Original", start=0, end=8, creator="reader"))


def test_replay_conflicts_and_transaction_rollback(store):
    item = Item(kind="paper", title="Paper")
    with store.transaction() as repo:
        assert repo.add(item) == repo.add(item)
    with pytest.raises(ConflictError):
        with store.transaction() as repo:
            repo.add(item.model_copy(update={"title": "Different"}))
    rolled_back = Item(kind="article", title="Must roll back")
    with pytest.raises(ReferenceError):
        with store.transaction() as repo:
            repo.add(rolled_back)
            repo.add(BucketMembership(bucket_id=uuid4(), item_id=rolled_back.id))
    with store.transaction() as repo:
        assert repo.get(Item, rolled_back.id) is None
        assert repo.get(Item, item.id) == item


def test_identity_and_membership_uniqueness(store):
    a, b = Item(kind="paper", title="A"), Item(kind="article", title="B")
    bucket = ResearchBucket(title="B", research_question="Q")
    with store.transaction() as repo:
        for record in (a, b, bucket):
            repo.add(record)
        repo.add(Identifier(item_id=a.id, namespace="doi", value="10.1234/x"))
        repo.add(BucketMembership(bucket_id=bucket.id, item_id=a.id))
        with pytest.raises(ConflictError):
            repo.add(Identifier(item_id=b.id, namespace="doi", value="10.1234/x"))
        with pytest.raises(ConflictError):
            repo.add(BucketMembership(bucket_id=bucket.id, item_id=a.id))


def test_cross_revision_evidence_is_rejected(store):
    item = Item(kind="paper", title="P")
    a = ContentRevision.from_markdown(item_id=item.id, markdown="A")
    b = ContentRevision.from_markdown(item_id=item.id, markdown="B")
    quote = Annotation(revision_id=a.id, quote="A", start=0, end=1, creator="reader")
    claim = Claim(statement="C", creator="reader")
    with store.transaction() as repo:
        for record in (item, a, b, quote, claim):
            repo.add(record)
        with pytest.raises(ReferenceError):
            repo.add(EvidenceAssessment(claim_id=claim.id, revision_id=b.id, annotation_id=quote.id,
                                        stance="supports", rationale="Wrong revision", creator="reader"))


def test_domain_validation_and_json_roundtrip():
    item = Item(kind="article", title="Title")
    assert Item.model_validate_json(item.model_dump_json()) == item
    for kwargs in ({"kind": "unknown", "title": "Title"}, {"kind": "article", "title": " "},
                   {"kind": "article", "title": "Title", "typo": True}):
        with pytest.raises(ValidationError):
            Item(**kwargs)
    with pytest.raises(ValidationError):
        ContentRevision(item_id=item.id, markdown="x", content_hash="0" * 64,
                        processor_version="v1", evidence_basis="full_text")
    with pytest.raises(ValidationError):
        EvidenceAssessment(claim_id=uuid4(), revision_id=uuid4(), stance="supports", rationale="r",
                           creator="model", creator_kind="processor")


def test_exact_unicode_and_whitespace_anchors(store):
    item = Item(kind="article", title="Unicode source")
    revision = ContentRevision.from_markdown(item_id=item.id, markdown="\n café 🙂 \n")
    quote = Annotation(revision_id=revision.id, quote=" café 🙂 ", start=1, end=9, creator="reader")
    with store.transaction() as repo:
        for record in (item, revision, quote):
            repo.add(record)
    with store.transaction() as repo:
        assert repo.get(ContentRevision, revision.id).markdown == "\n café 🙂 \n"
        assert repo.get(Annotation, quote.id).matches(revision)


def test_bypassed_validation_and_invalid_manuscript_target(store):
    item = Item(kind="paper", title="Paper")
    with store.transaction() as repo:
        with pytest.raises(ValidationError):
            repo.add(item.model_copy(update={"title": ""}))
        repo.add(item)
        with pytest.raises(ReferenceError):
            repo.add(ResearchBucket(title="B", research_question="Q", target_manuscript_id=item.id))


def test_settings_precedence_and_partial_nested_overrides(tmp_path, monkeypatch):
    config = tmp_path / "radar.yaml"
    config.write_text("lookback_days: 7\nresearch:\n  storage:\n    path: yaml.db\n    busy_timeout_seconds: 9\n")
    assert load_research_settings(config).storage.path == Path("yaml.db")
    monkeypatch.setenv("PAPERFLOW_STORAGE__PATH", "env.db")
    assert load_research_settings(config).storage.path == Path("env.db")
    assert load_research_settings(config).storage.busy_timeout_seconds == 9
    settings = load_research_settings(config, storage={"path": "explicit.db"})
    assert settings.storage.path == Path("explicit.db")
    assert settings.storage.busy_timeout_seconds == 9
    monkeypatch.setenv("PAPERFLOW_STORAGE__BUSY_TIMEOUT_SECONDS", "-1")
    with pytest.raises(ValidationError):
        load_research_settings(config)


def test_settings_legacy_defaults_and_invalid_config(tmp_path):
    config = tmp_path / "radar.yaml"
    config.write_text("lookback_days: 7\nsources: {}\n")
    assert load_research_settings(config).storage == StorageSettings()
    for contents in ("[]", "research: []", "research:\n  typo: true"):
        config.write_text(contents)
        with pytest.raises((ValueError, ValidationError)):
            load_research_settings(config)
    with pytest.raises(FileNotFoundError):
        load_research_settings(tmp_path / "missing.yaml")
    assert ResearchSettings(storage={"path": "test.db"}).storage.path == Path("test.db")


def test_additive_initialization_and_future_version_rejection(tmp_path):
    import radar_db

    path = tmp_path / "radar.db"
    radar_db.init_db(str(path))
    with radar_db.get_db(str(path)) as conn:
        radar_db.upsert_paper(conn, radar_db.PaperRecord(arxiv_id="2401.00001", resolved_id="2401.00001"))
    storage = SQLiteResearchStorage(StorageSettings(path=path))
    storage.initialize()
    storage.initialize()
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0] == 1
        assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == radar_db.SCHEMA_VERSION
        conn.execute("UPDATE research_schema_version SET version=999")
    with pytest.raises(StorageError, match="unsupported"):
        storage.initialize()
