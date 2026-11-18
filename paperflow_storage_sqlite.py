"""SQLite adapter for the research domain, additive to the legacy radar schema."""
from __future__ import annotations

import sqlite3
import json
from contextvars import ContextVar
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from uuid import UUID

from paperflow_domain import (
    Annotation, BucketClaim, BucketMembership, Claim, ClaimReview, ContentRevision,
    EvidenceAssessment, EvidenceReview, Identifier, Item, Record, ResearchBucket, ItemRelationship, LifecycleEvent,
)
from paperflow_story_storage import StoryRepositoryMixin
from paperflow_settings import StorageSettings
from paperflow_ingest_models import SourceObservation
from paperflow_storage import (
    ConflictError, ReferenceError, ResearchRepository, StorageError, T, validate_record,
)

# Only these fixed names may be interpolated in SQL. JSON preserves the complete
# validated record; relational columns enforce identity, links, and query indexes.
query_measurement: ContextVar[dict | None] = ContextVar("research_query_measurement", default=None)

TABLES: dict[type[Record], tuple[str, tuple[str, ...]]] = {
    ItemRelationship: ("research_relationships", ("source_item_id", "target_item_id", "source_revision_id")),
    LifecycleEvent: ("research_lifecycle_events", ("entity_id", "version")),
    SourceObservation: ("research_source_observations", ("item_id", "batch_id", "channel")),
    Item: ("research_items", ("kind",)),
    Identifier: ("research_identifiers", ("item_id", "namespace", "value")),
    ContentRevision: ("research_revisions", ("item_id", "content_hash", "processor_version", "evidence_basis")),
    ResearchBucket: ("research_buckets", ("target_manuscript_id",)),
    BucketMembership: ("research_memberships", ("bucket_id", "item_id", "position")),
    Annotation: ("research_annotations", ("revision_id",)),
    Claim: ("research_claims", ("source_annotation_id",)),
    BucketClaim: ("research_bucket_claims", ("bucket_id", "claim_id")),
    EvidenceAssessment: ("research_assessments", ("claim_id", "revision_id", "annotation_id")),
    EvidenceReview: ("research_evidence_reviews", ("assessment_id",)),
    ClaimReview: ("research_claim_reviews", ("claim_id",)),
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS research_reader_ratings (
    reader_hash TEXT NOT NULL, kind TEXT NOT NULL, target_id TEXT NOT NULL,
    version TEXT NOT NULL, rating TEXT NOT NULL CHECK(rating IN ('helpful','needs_work')),
    PRIMARY KEY(reader_hash,kind,target_id,version)
);
CREATE TABLE IF NOT EXISTS research_public_revisions (revision_id TEXT PRIMARY KEY REFERENCES research_revisions(id));
CREATE TABLE IF NOT EXISTS research_stories (id TEXT PRIMARY KEY, version INTEGER NOT NULL, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS research_story_versions (story_id TEXT NOT NULL REFERENCES research_stories(id), version INTEGER NOT NULL, payload TEXT NOT NULL, assessment_ids TEXT NOT NULL, PRIMARY KEY(story_id, version));
CREATE TABLE IF NOT EXISTS research_story_assessments (id TEXT PRIMARY KEY, revision_id TEXT NOT NULL REFERENCES research_revisions(id), story_id TEXT REFERENCES research_stories(id), payload TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS story_assessment_revision ON research_story_assessments(revision_id);
CREATE INDEX IF NOT EXISTS story_assessment_story ON research_story_assessments(story_id);
CREATE TABLE IF NOT EXISTS research_story_sources (story_id TEXT NOT NULL REFERENCES research_stories(id), item_id TEXT NOT NULL REFERENCES research_items(id), payload TEXT NOT NULL, PRIMARY KEY(story_id,item_id));
CREATE TABLE IF NOT EXISTS research_story_pending (assessment_id TEXT PRIMARY KEY REFERENCES research_story_assessments(id), story_id TEXT NOT NULL REFERENCES research_stories(id));

CREATE TABLE IF NOT EXISTS editorial_topics (
 id TEXT PRIMARY KEY, topic_key TEXT NOT NULL UNIQUE, payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS editorial_topic_runs (
 id TEXT PRIMARY KEY, window_end TEXT NOT NULL, payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS editorial_topic_members (
 run_id TEXT NOT NULL REFERENCES editorial_topic_runs(id),
 topic_id TEXT NOT NULL REFERENCES editorial_topics(id),
 item_id TEXT NOT NULL REFERENCES research_items(id),
 observation_id TEXT NOT NULL REFERENCES research_source_observations(id),
 first_seen_at TEXT NOT NULL, payload TEXT NOT NULL,
 PRIMARY KEY(run_id,topic_id,item_id)
);
CREATE INDEX IF NOT EXISTS editorial_topic_members_item ON editorial_topic_members(topic_id,item_id);
CREATE TABLE IF NOT EXISTS editorial_topic_edits (
 id TEXT PRIMARY KEY, topic_id TEXT NOT NULL REFERENCES editorial_topics(id), payload TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reader_pdfs (
    item_id TEXT PRIMARY KEY REFERENCES research_items(id),
    metadata TEXT NOT NULL, data BLOB NOT NULL
);

CREATE TABLE IF NOT EXISTS reader_states (
    item_id TEXT PRIMARY KEY REFERENCES research_items(id), payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reader_preferences (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1), payload TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS research_relationships (
 id TEXT PRIMARY KEY, source_item_id TEXT NOT NULL REFERENCES research_items(id),
 target_item_id TEXT NOT NULL REFERENCES research_items(id),
 source_revision_id TEXT REFERENCES research_revisions(id), payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS research_relationship_source ON research_relationships(source_item_id);
CREATE INDEX IF NOT EXISTS research_relationship_target ON research_relationships(target_item_id);
CREATE TABLE IF NOT EXISTS research_lifecycle_events (
 id TEXT PRIMARY KEY, entity_id TEXT NOT NULL, version INTEGER NOT NULL, payload TEXT NOT NULL,
 UNIQUE(entity_id, version)
);
CREATE TABLE IF NOT EXISTS research_command_receipts (
 key TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, result TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS research_source_observations (
    id TEXT PRIMARY KEY, item_id TEXT NOT NULL REFERENCES research_items(id),
    batch_id TEXT NOT NULL, channel TEXT NOT NULL, payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS research_observation_item ON research_source_observations(item_id);
CREATE TABLE IF NOT EXISTS research_schema_version (
    singleton INTEGER PRIMARY KEY CHECK(singleton = 1), version INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS research_items (
    id TEXT PRIMARY KEY, kind TEXT NOT NULL, payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS research_identifiers (
    id TEXT PRIMARY KEY, item_id TEXT NOT NULL REFERENCES research_items(id),
    namespace TEXT NOT NULL, value TEXT NOT NULL, payload TEXT NOT NULL,
    UNIQUE(namespace, value)
);
CREATE TABLE IF NOT EXISTS research_revisions (
    id TEXT PRIMARY KEY, item_id TEXT NOT NULL REFERENCES research_items(id),
    content_hash TEXT NOT NULL, processor_version TEXT NOT NULL,
    evidence_basis TEXT NOT NULL, payload TEXT NOT NULL,
    UNIQUE(item_id, content_hash, processor_version, evidence_basis)
);
CREATE TABLE IF NOT EXISTS research_buckets (
    id TEXT PRIMARY KEY, target_manuscript_id TEXT REFERENCES research_items(id),
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS research_memberships (
    id TEXT PRIMARY KEY, bucket_id TEXT NOT NULL REFERENCES research_buckets(id),
    item_id TEXT NOT NULL REFERENCES research_items(id), position INTEGER NOT NULL,
    payload TEXT NOT NULL, UNIQUE(bucket_id, item_id)
);
CREATE TABLE IF NOT EXISTS research_annotations (
    id TEXT PRIMARY KEY, revision_id TEXT NOT NULL REFERENCES research_revisions(id),
    payload TEXT NOT NULL, UNIQUE(id, revision_id)
);
CREATE TABLE IF NOT EXISTS research_claims (
    id TEXT PRIMARY KEY, source_annotation_id TEXT REFERENCES research_annotations(id),
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS research_bucket_claims (
    id TEXT PRIMARY KEY, bucket_id TEXT NOT NULL REFERENCES research_buckets(id),
    claim_id TEXT NOT NULL REFERENCES research_claims(id), payload TEXT NOT NULL,
    UNIQUE(bucket_id, claim_id)
);
CREATE TABLE IF NOT EXISTS research_assessments (
    id TEXT PRIMARY KEY, claim_id TEXT NOT NULL REFERENCES research_claims(id),
    revision_id TEXT NOT NULL REFERENCES research_revisions(id),
    annotation_id TEXT, payload TEXT NOT NULL,
    FOREIGN KEY(annotation_id, revision_id) REFERENCES research_annotations(id, revision_id)
);
CREATE TABLE IF NOT EXISTS research_evidence_reviews (
    id TEXT PRIMARY KEY, assessment_id TEXT NOT NULL REFERENCES research_assessments(id),
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS research_claim_reviews (
    id TEXT PRIMARY KEY, claim_id TEXT NOT NULL REFERENCES research_claims(id),
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS research_revision_item ON research_revisions(item_id);
CREATE INDEX IF NOT EXISTS research_claim_bucket ON research_bucket_claims(bucket_id);
CREATE INDEX IF NOT EXISTS research_membership_order ON research_memberships(bucket_id, position);
CREATE INDEX IF NOT EXISTS research_assessment_claim ON research_assessments(claim_id);
CREATE INDEX IF NOT EXISTS research_review_assessment ON research_evidence_reviews(assessment_id);
CREATE INDEX IF NOT EXISTS research_claim_review_claim ON research_claim_reviews(claim_id);
"""


class SQLiteResearchRepository(StoryRepositoryMixin):
    def __init__(self, connection: sqlite3.Connection):
        self._conn = connection

    def topic_candidates(self, start, end, limit):
        from paperflow_topic_models import TopicCandidate
        # The first observation, not the latest refresh or upstream publication
        # time, determines when an item entered this local workspace.
        rows = self._conn.execute("""WITH arrivals AS (
          SELECT item_id,MIN(julianday(json_extract(payload,'$.observed_at'))) first_seen
          FROM research_source_observations GROUP BY item_id
        ) SELECT o.payload,(SELECT json_extract(f.payload,'$.observed_at') FROM research_source_observations f WHERE f.item_id=a.item_id ORDER BY julianday(json_extract(f.payload,'$.observed_at')),f.rowid LIMIT 1) FROM arrivals a
          JOIN research_source_observations o ON o.id=(
            SELECT p.id FROM research_source_observations p WHERE p.item_id=a.item_id
            AND julianday(json_extract(p.payload,'$.observed_at')) < julianday(?)
            ORDER BY julianday(json_extract(p.payload,'$.observed_at')) DESC,p.rowid DESC LIMIT 1)
          WHERE a.first_seen >= julianday(?) AND a.first_seen < julianday(?)
          ORDER BY a.first_seen,a.item_id LIMIT ?""", (end.isoformat(),start.isoformat(),end.isoformat(),limit+1)).fetchall()
        if len(rows)>limit:
            raise ValueError('Topic candidate limit exceeded; increase max_candidates before curating. No topics changed.')
        from datetime import datetime
        results=[]
        for row in rows:
            observation=SourceObservation.model_validate_json(row[0])
            first=datetime.fromisoformat(row[1].replace('Z','+00:00'))
            results.append(TopicCandidate(item_id=observation.item_id,observation_id=observation.id,
                first_seen_at=first,title=observation.entry.title,excerpt=observation.entry.excerpt[:2000],channel=observation.channel))
        return results

    def topics(self):
        from paperflow_topic_models import Topic
        return [Topic.model_validate_json(r[0]) for r in self._conn.execute('SELECT payload FROM editorial_topics ORDER BY topic_key')]

    def get_topic(self, topic_id):
        from paperflow_topic_models import Topic
        row=self._conn.execute('SELECT payload FROM editorial_topics WHERE id=?',(str(topic_id),)).fetchone()
        return Topic.model_validate_json(row[0]) if row else None

    def put_topic(self, topic, expected_version=None):
        from paperflow_topic_models import Topic
        topic=Topic.model_validate(topic.model_dump())
        if expected_version is None:
            self._conn.execute('INSERT INTO editorial_topics VALUES (?,?,?)',(str(topic.id),topic.key,topic.model_dump_json()))
        else:
            if topic.version!=expected_version+1:
                raise ConflictError('Topic version must advance by one')
            changed=self._conn.execute("UPDATE editorial_topics SET payload=? WHERE id=? AND json_extract(payload,'$.version')=?",(topic.model_dump_json(),str(topic.id),expected_version))
            if changed.rowcount!=1:
                raise ConflictError('Topic changed; reload before editing')
        return topic

    def topic_run(self, run_id):
        from paperflow_topic_models import TopicRun
        row=self._conn.execute('SELECT payload FROM editorial_topic_runs WHERE id=?',(str(run_id),)).fetchone()
        return TopicRun.model_validate_json(row[0]) if row else None

    def topic_runs(self, limit=20):
        from paperflow_topic_models import TopicRun
        return [TopicRun.model_validate_json(r[0]) for r in self._conn.execute('SELECT payload FROM editorial_topic_runs ORDER BY julianday(window_end) DESC LIMIT ?',(limit,))]

    def save_topic_run(self, run):
        self._conn.execute('INSERT INTO editorial_topic_runs VALUES (?,?,?)',(str(run.id),run.window_end.isoformat(),run.model_dump_json()))
        for edition in run.editions:
            for member in edition.assignments:
                self._conn.execute('INSERT INTO editorial_topic_members VALUES (?,?,?,?,?,?)',(str(run.id),str(edition.topic_id),str(member.item_id),str(member.observation_id),member.first_seen_at.isoformat(),member.model_dump_json()))

    def topic_members(self, topic_id, run_id):
        from paperflow_topic_models import TopicAssignment
        return [TopicAssignment.model_validate_json(r[0]) for r in self._conn.execute('SELECT payload FROM editorial_topic_members WHERE topic_id=? AND run_id=? ORDER BY first_seen_at DESC,item_id',(str(topic_id),str(run_id)))]

    def save_topic_edit(self, edit):
        self._conn.execute('INSERT INTO editorial_topic_edits VALUES (?,?,?)',(str(edit.id),str(edit.topic_id),edit.model_dump_json()))

    def topic_edits(self, topic_id):
        from paperflow_topic_models import TopicEdit
        return [TopicEdit.model_validate_json(r[0]) for r in self._conn.execute('SELECT payload FROM editorial_topic_edits WHERE topic_id=? ORDER BY rowid',(str(topic_id),))]

    def reader_pdf(self, item_id, *, include_data=False):
        from paperflow_reader.models import PDFAsset
        columns = 'metadata, data' if include_data else 'metadata'
        row = self._conn.execute(f"SELECT {columns} FROM reader_pdfs WHERE item_id=?", (str(item_id),)).fetchone()
        if not row:
            return None
        asset = PDFAsset.model_validate_json(row[0])
        return (asset, row[1]) if include_data else asset

    def add_reader_pdf(self, asset, data):
        existing = self.reader_pdf(asset.item_id)
        if existing:
            if existing != asset:
                raise ConflictError('A different PDF is already attached to this item')
            return existing
        self._conn.execute('INSERT INTO reader_pdfs VALUES (?,?,?)', (str(asset.item_id), asset.model_dump_json(), data))
        return asset

    def reader_state(self, item_id):
        from paperflow_reader.models import ReaderState
        row = self._conn.execute("SELECT payload FROM reader_states WHERE item_id=?", (str(item_id),)).fetchone()
        return ReaderState.model_validate_json(row[0]) if row else ReaderState(item_id=item_id)

    def put_reader_state(self, state):
        self._conn.execute("INSERT INTO reader_states VALUES (?,?) ON CONFLICT(item_id) DO UPDATE SET payload=excluded.payload", (str(state.item_id), state.model_dump_json()))

    def reading_preferences(self):
        from paperflow_reader.models import ReadingPreferences
        row = self._conn.execute("SELECT payload FROM reader_preferences WHERE singleton=1").fetchone()
        return ReadingPreferences.model_validate_json(row[0]) if row else ReadingPreferences()

    def put_reading_preferences(self, preferences):
        self._conn.execute("INSERT INTO reader_preferences VALUES (1,?) ON CONFLICT(singleton) DO UPDATE SET payload=excluded.payload", (preferences.model_dump_json(),))

    def latest_for_item(self, model, item_id):
        if model not in (ContentRevision, SourceObservation):
            raise ValueError('Unsupported latest item record')
        table, _ = TABLES[model]
        row = self._conn.execute(f"SELECT payload FROM {table} WHERE item_id=? ORDER BY rowid DESC LIMIT 1", (str(item_id),)).fetchone()
        return model.model_validate_json(row[0]) if row else None

    def reader_channels(self, *, saved=False, query='', kind=None):
        # One membership per item/channel; repeated observations never inflate counts.
        condition = "COALESCE(json_extract(s.payload, '$.saved'),0)=1" if saved else "COALESCE(json_extract(s.payload, '$.status'),'unread')!='dismissed'"
        condition += " AND instr(lower(json_extract(i.payload, '$.title')),lower(?))>0"
        condition += " AND (? IS NULL OR json_extract(i.payload, '$.kind')=?)"
        rows = self._conn.execute(f"""WITH membership AS (
          SELECT channel,item_id,MAX(json_extract(payload,'$.observed_at')) observed_at
          FROM research_source_observations GROUP BY channel,item_id
        ) SELECT o.channel,MAX(o.observed_at),COUNT(*),
          SUM(CASE WHEN {condition} THEN 1 ELSE 0 END),
          SUM(CASE WHEN {condition} AND COALESCE(json_extract(s.payload,'$.status'),'unread')='unread' THEN 1 ELSE 0 END),
          SUM(CASE WHEN {condition} AND COALESCE(json_extract(s.payload,'$.saved'),0)=1 THEN 1 ELSE 0 END)
          FROM membership o JOIN research_items i ON i.id=o.item_id
          LEFT JOIN reader_states s ON s.item_id=i.id GROUP BY o.channel ORDER BY o.channel""", (query,kind,kind)*3)
        return [dict(channel=r[0],observed_at=r[1],item_count=r[2],visible_count=r[3],unread_count=r[4],saved_count=r[5]) for r in rows]

    def reader_feed(self, *, channel=None, query='', kind=None, saved=False, limit=20, before=None, topic_id=None, topic_start=None, topic_end=None):
        if not 1 <= limit <= 50 or (before is not None and before < 1):
            raise ValueError('Invalid reader page')
        where, values = [], []
        if kind:
            where.append("json_extract(i.payload, '$.kind')=?")
            values.append(kind)
        if topic_id:
            where.append("""EXISTS (SELECT 1 FROM editorial_topic_members m JOIN editorial_topics t ON t.id=m.topic_id
                WHERE m.item_id=i.id AND t.id=? AND m.run_id=json_extract(t.payload,'$.current_run_id')
                AND json_extract(t.payload,'$.editorial_status')='published'
                AND julianday(m.first_seen_at)>=julianday(?) AND julianday(m.first_seen_at)<julianday(?))""")
            values.extend([str(topic_id),topic_start.isoformat(),topic_end.isoformat()])
        if channel:
            where.append('EXISTS (SELECT 1 FROM research_source_observations o WHERE o.item_id=i.id AND o.channel=?)')
            values.append(channel)
        if query:
            where.append("instr(lower(json_extract(i.payload, '$.title')), lower(?)) > 0")
            values.append(query)
        if saved:
            where.append("json_extract(s.payload, '$.saved')=1")
        else:
            where.append("COALESCE(json_extract(s.payload, '$.status'),'unread') != 'dismissed'")
        if before is not None:
            where.append('i.rowid < ?')
            values.append(before)
        sql = """SELECT i.rowid, i.payload, s.payload,
            (SELECT json_set(json_remove(r.payload, '$.markdown'), '$.preview_text', substr(json_extract(r.payload, '$.markdown'),1,240)) FROM research_revisions r WHERE r.item_id=i.id ORDER BY r.rowid DESC LIMIT 1),
            (SELECT json_remove(o.payload, '$.entry.comments', '$.entry.excerpt') FROM research_source_observations o WHERE o.item_id=i.id ORDER BY o.rowid DESC LIMIT 1)
            FROM research_items i LEFT JOIN reader_states s ON s.item_id=i.id"""
        sql += ' WHERE ' + ' AND '.join(where) + ' ORDER BY i.rowid DESC LIMIT ?'
        rows = self._conn.execute(sql, (*values, limit+1)).fetchall()
        from paperflow_reader.models import ReaderState
        records = []
        for row in rows[:limit]:
            item = json.loads(row[1])
            records.append({'item': item, 'state': json.loads(row[2]) if row[2] else ReaderState(item_id=item['id']).model_dump(mode='json'),
                            'revision': json.loads(row[3]) if row[3] else None, 'observation': json.loads(row[4]) if row[4] else None})
        return {'records': records, 'next_before': rows[limit-1][0] if len(rows) > limit else None}

    def get(self, model: type[T], record_id: UUID) -> T | None:
        table, _ = TABLES[model]
        row = self._conn.execute(f"SELECT payload FROM {table} WHERE id = ?", (str(record_id),)).fetchone()
        return model.model_validate_json(row[0]) if row else None

    def add(self, record: T) -> T:
        # Revalidate even if a caller bypassed Pydantic via model_construct/copy.
        record = validate_record(self, record)
        existing = self.get(type(record), record.id)
        if existing is not None:
            if existing != record:
                raise ConflictError("records are immutable; this ID already has different content")
            return existing
        table, indexed = TABLES[type(record)]
        columns = ("id", *indexed, "payload")
        values = [self._sql_value(getattr(record, field)) for field in ("id", *indexed)]
        values.append(record.model_dump_json())
        try:
            self._conn.execute(
                f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
                values,
            )
        except sqlite3.IntegrityError as exc:
            if exc.sqlite_errorcode == sqlite3.SQLITE_CONSTRAINT_FOREIGNKEY:
                raise ReferenceError("missing or inconsistent referenced research record") from exc
            raise ConflictError("research identity or membership already exists") from exc
        return record

    @staticmethod
    def _sql_value(value):
        return str(value) if isinstance(value, UUID) else value

    def replace(self, record: T, expected_version: int) -> T:
        if type(record) not in (ResearchBucket, BucketMembership, Claim):
            raise ValueError("record has no mutable lifecycle")
        record = validate_record(self, record)
        if record.version != expected_version + 1:
            raise ConflictError("version must advance by one")
        table, indexed = TABLES[type(record)]
        assignments = ", ".join(f"{field}=?" for field in indexed)
        cursor = self._conn.execute(
            f"UPDATE {table} SET {assignments}, payload=? WHERE id=? AND COALESCE(json_extract(payload, '$.version'), 1)=?",
            (*[self._sql_value(getattr(record, f)) for f in indexed], record.model_dump_json(), str(record.id), expected_version))
        if cursor.rowcount != 1:
            raise ConflictError("stale version; reload the record")
        return record

    def receipt(self, key):
        row = self._conn.execute("SELECT fingerprint,result FROM research_command_receipts WHERE key=?", (key,)).fetchone()
        return (row[0], json.loads(row[1])) if row else None

    def save_receipt(self, key, fingerprint, result):
        self._conn.execute("INSERT INTO research_command_receipts VALUES (?,?,?)", (key, fingerprint, json.dumps(result)))

    def count(self, model, field, value):
        table, columns = TABLES[model]
        if field not in (*columns, "id"):
            raise ValueError("unsupported query field")
        return self._conn.execute(f"SELECT count(*) FROM {table} WHERE {field}=?", (str(value),)).fetchone()[0]

    def page(self, model, *, field=None, value=None, limit=10, offset=0, preview=False):
        if not 1 <= limit <= 200 or offset < 0:
            raise ValueError("invalid pagination")
        table, columns = TABLES[model]
        if field is not None and field not in (*columns, "id"):
            raise ValueError("unsupported query field")
        where = f" WHERE {field}=?" if field else ""
        params = (self._sql_value(value),) if field else ()
        if model is BucketMembership:
            where += (" AND " if where else " WHERE ") + "COALESCE(json_extract(payload, '$.active'), 1)=1"
        expression = "payload"
        if preview and model is ContentRevision:
            expression = "json_set(json_remove(payload, '$.markdown'), '$.preview_text', substr(json_extract(payload, '$.markdown'), 1, 240), '$.character_count', length(json_extract(payload, '$.markdown')))"
        total = self._conn.execute(f"SELECT count(*) FROM {table}" + where, params).fetchone()[0]
        order = "position, rowid" if model is BucketMembership else "rowid"
        rows = self._conn.execute(f"SELECT {expression} FROM {table}" + where + f" ORDER BY {order} LIMIT ? OFFSET ?", (*params,limit,offset))
        records = [json.loads(row[0]) for row in rows]
        return {"records": records, "total": total, "limit": limit, "offset": offset,
                "next_offset": offset + limit if offset + limit < total else None}

    def latest_review(self, model, field, value):
        table, columns = TABLES[model]
        if model not in (ClaimReview, EvidenceReview) or field not in columns:
            raise ValueError("invalid review query")
        row = self._conn.execute(f"SELECT payload FROM {table} WHERE {field}=? ORDER BY rowid DESC LIMIT 1", (str(value),)).fetchone()
        return json.loads(row[0]) if row else None

    def revision_preview(self, revision_id):
        page = self.page(ContentRevision, field="id", value=revision_id, limit=1, preview=True)
        if not page["records"]:
            raise ReferenceError("missing ContentRevision")
        return page["records"][0]

    def resolve_identifier(self, namespace: str, value: str) -> Item | None:
        row = self._conn.execute(
            "SELECT item_id FROM research_identifiers WHERE namespace = ? AND value = ?",
            (namespace, value),
        ).fetchone()
        return self.get(Item, UUID(row[0])) if row else None

    def bucket_members(self, bucket_id: UUID) -> list[BucketMembership]:
        return [m for m in self._query(BucketMembership, "bucket_id", bucket_id, "position, rowid") if m.active]

    def buckets(self) -> list[ResearchBucket]:
        return [ResearchBucket.model_validate_json(row[0]) for row in
                self._conn.execute("SELECT payload FROM research_buckets ORDER BY rowid")]

    def bucket_claims(self, bucket_id: UUID) -> list[BucketClaim]:
        return self._query(BucketClaim, "bucket_id", bucket_id)

    def reviews_for_claim(self, claim_id: UUID) -> list[ClaimReview]:
        return self._query(ClaimReview, "claim_id", claim_id)

    def observations_for_item(self, item_id: UUID) -> list[SourceObservation]:
        return self._query(SourceObservation, "item_id", item_id)

    def items(self, *, channel: str | None = None, query: str | None = None,
              limit: int = 50, offset: int = 0) -> list[Item]:
        if not 1 <= limit <= 200 or offset < 0:
            raise ValueError("invalid item pagination")
        where, params = [], []
        if channel is not None:
            where.append("EXISTS (SELECT 1 FROM research_source_observations o WHERE o.item_id=i.id AND o.channel=?)")
            params.append(channel)
        if query:
            where.append("instr(lower(json_extract(i.payload, '$.title')), lower(?)) > 0")
            params.append(query)
        clause = " WHERE " + " AND ".join(where) if where else ""
        rows = self._conn.execute("SELECT i.payload FROM research_items i" + clause + " ORDER BY i.rowid DESC LIMIT ? OFFSET ?",
                                  (*params, limit, offset))
        return [Item.model_validate_json(row[0]) for row in rows]

    def save_reader_rating(self, reader, kind, target_id, version, rating):
        self._conn.execute("""INSERT INTO research_reader_ratings VALUES (?,?,?,?,?)
            ON CONFLICT(reader_hash,kind,target_id,version) DO UPDATE SET rating=excluded.rating""",
            (reader, kind, str(target_id), version, rating))

    def mark_public_revision(self, revision_id):
        self._conn.execute('INSERT OR IGNORE INTO research_public_revisions VALUES (?)', (str(revision_id),))

    def is_public_revision(self, revision_id):
        # Old ingests have no marker. A byte-identical stored feed excerpt is
        # independently provable from its immutable observation.
        if self._conn.execute("SELECT 1 FROM sqlite_master WHERE name='research_public_revisions'").fetchone():
            if self._conn.execute("SELECT 1 FROM research_public_revisions WHERE revision_id=?",
                                  (str(revision_id),)).fetchone():
                return True
        row = self._conn.execute("""SELECT 1 FROM research_revisions r
            JOIN research_source_observations o ON o.item_id=r.item_id
            WHERE r.id=? AND json_extract(o.payload,'$.entry.excerpt')=json_extract(r.payload,'$.markdown')
              AND length(json_extract(o.payload,'$.entry.excerpt'))>0 LIMIT 1""", (str(revision_id),)).fetchone()
        return bool(row)

    def publication_items(self, channel, cutoff, limit):
        rows = self._conn.execute("""SELECT i.payload FROM research_items i
            WHERE EXISTS (SELECT 1 FROM research_source_observations o WHERE o.item_id=i.id AND o.channel=?)
              AND julianday(COALESCE(json_extract(i.payload,'$.published_at'), json_extract(i.payload,'$.created_at'))) >= julianday(?)
            ORDER BY julianday(COALESCE(json_extract(i.payload,'$.published_at'), json_extract(i.payload,'$.created_at'))) DESC, i.id
            LIMIT ?""", (channel, cutoff.isoformat(), limit))
        return [Item.model_validate_json(row[0]) for row in rows]

    def revisions_for_item(self, item_id: UUID) -> list[ContentRevision]:
        return self._query(ContentRevision, "item_id", item_id)

    def annotations_for_revision(self, revision_id: UUID) -> list[Annotation]:
        return self._query(Annotation, "revision_id", revision_id)

    def evidence_for_claim(self, claim_id: UUID) -> list[EvidenceAssessment]:
        return self._query(EvidenceAssessment, "claim_id", claim_id)

    def reviews_for_assessment(self, assessment_id: UUID) -> list[EvidenceReview]:
        return self._query(EvidenceReview, "assessment_id", assessment_id)

    def _query(self, model: type[T], field: str, value: UUID, order: str = "rowid") -> list[T]:
        table, _ = TABLES[model]
        rows = self._conn.execute(
            f"SELECT payload FROM {table} WHERE {field} = ? ORDER BY {order}", (str(value),),
        )
        return [model.model_validate_json(row[0]) for row in rows]


class SQLiteResearchStorage:
    """One connection per unit of work; caller owns transaction boundaries.

    Use a disk database, optionally the existing radar.db. Schema initialization
    is explicit and never touches the legacy schema version or paper tables.
    """

    def __init__(self, settings: StorageSettings | None = None):
        self.settings = settings or StorageSettings()
        if str(self.settings.path) == ":memory:":
            raise ValueError("use a temporary file; this adapter opens a connection per transaction")

    def _connect(self) -> sqlite3.Connection:
        path = Path(self.settings.path)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            conn = sqlite3.connect(path, timeout=self.settings.busy_timeout_seconds)
        except sqlite3.Error as exc:
            raise StorageError(str(exc)) from exc
        try:
            stats = query_measurement.get()
            if stats is not None:
                def count_query(statement):
                    if statement.lstrip().upper().startswith(("SELECT", "INSERT", "UPDATE", "DELETE")):
                        stats["queries"] = stats.get("queries", 0) + 1
                conn.set_trace_callback(count_query)
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.Error as exc:
            conn.close()
            raise StorageError(str(exc)) from exc
        return conn

    def initialize(self) -> None:
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("CREATE TABLE IF NOT EXISTS research_schema_version (singleton INTEGER PRIMARY KEY CHECK(singleton = 1), version INTEGER NOT NULL)")
            row = conn.execute("SELECT version FROM research_schema_version WHERE singleton=1").fetchone()
            if row and row[0] not in (1, 2, 3, 4, 5, 6):
                raise StorageError(f"unsupported research schema version: {row[0]}")
            for statement in SCHEMA.split(";"):
                if statement.strip():
                    conn.execute(statement)
            if row and row[0] < 3:
                # Older records never captured reviewer kind or processor claim version.
                # Preserve uncertainty explicitly rather than infer human approval.
                for table in ("research_annotations", "research_claims", "research_assessments"):
                    conn.execute(f"UPDATE {table} SET payload=json_set(payload, '$.processor_version', 'legacy-unrecorded') WHERE json_extract(payload, '$.creator_kind')='processor' AND json_extract(payload, '$.processor_version') IS NULL")
                conn.execute("UPDATE research_annotations SET payload=json_set(payload, '$.creator_kind', 'unknown') WHERE json_extract(payload, '$.creator_kind') IS NULL")
                for table in ("research_evidence_reviews", "research_claim_reviews"):
                    conn.execute(f"UPDATE {table} SET payload=json_set(payload, '$.reviewer_kind', 'unknown') WHERE json_extract(payload, '$.reviewer_kind') IS NULL")
            conn.execute("INSERT INTO research_schema_version VALUES (1, 6) ON CONFLICT(singleton) DO UPDATE SET version=6")
            conn.commit()
        except sqlite3.Error as exc:
            conn.rollback()
            raise StorageError(str(exc)) from exc
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    @contextmanager
    def transaction(self, *, write=False) -> Iterator[ResearchRepository]:
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield SQLiteResearchRepository(conn)
            conn.commit()
        except sqlite3.Error as exc:
            conn.rollback()
            raise StorageError(str(exc)) from exc
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()
