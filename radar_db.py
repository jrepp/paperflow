from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Generator

from booxdrop_cli import (
    DEFAULT_PDF_CACHE_DIR,
    load_manifest,
    load_radar_report,
    cache_file_path,
)

DEFAULT_DB_PATH = "artifacts/radar.db"

SCHEMA_VERSION = 5

SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS papers (
    arxiv_id         TEXT NOT NULL PRIMARY KEY,
    paper_key        TEXT,
    doi              TEXT,
    semantic_scholar_id TEXT,
    corpus_id        TEXT,
    openalex_id      TEXT,
    source_ids_json  TEXT NOT NULL DEFAULT '{}',
    source_metadata_json TEXT NOT NULL DEFAULT '{}',
    resolved_id      TEXT NOT NULL,
    title            TEXT NOT NULL DEFAULT '',
    authors          TEXT NOT NULL DEFAULT '[]',
    summary          TEXT NOT NULL DEFAULT '',
    pdf_url          TEXT NOT NULL DEFAULT '',
    abs_url          TEXT NOT NULL DEFAULT '',
    primary_category TEXT NOT NULL DEFAULT '',
    published        TEXT NOT NULL DEFAULT '',
    updated          TEXT NOT NULL DEFAULT '',
    suggested_filename TEXT NOT NULL DEFAULT '',
    citation_count   INTEGER,
    influential_citation_count INTEGER,
    citation_source_url TEXT,
    discovered_at    TEXT NOT NULL DEFAULT '',
    discovered_via   TEXT NOT NULL DEFAULT ''
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS radar_reports (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    generated_at TEXT NOT NULL,
    report_path TEXT NOT NULL DEFAULT '',
    lookback_days INTEGER,
    config_snapshot TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS radar_report_papers (
    report_id  INTEGER NOT NULL REFERENCES radar_reports(id),
    arxiv_id   TEXT NOT NULL REFERENCES papers(arxiv_id),
    category   TEXT NOT NULL DEFAULT '',
    section    TEXT NOT NULL DEFAULT '',
    UNIQUE(report_id, arxiv_id, section)
);

CREATE TABLE IF NOT EXISTS export_batches (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at   TEXT NOT NULL,
    section      TEXT NOT NULL DEFAULT '',
    categories   TEXT NOT NULL DEFAULT '[]',
    exclude_categories TEXT NOT NULL DEFAULT '[]',
    top_n        INTEGER,
    min_citations INTEGER,
    max_citations INTEGER,
    since_date   TEXT,
    lookback_days INTEGER,
    paper_count  INTEGER NOT NULL DEFAULT 0,
    source       TEXT NOT NULL DEFAULT 'export'
);

CREATE TABLE IF NOT EXISTS export_batch_papers (
    batch_id  INTEGER NOT NULL REFERENCES export_batches(id),
    arxiv_id  TEXT NOT NULL REFERENCES papers(arxiv_id),
    category  TEXT NOT NULL DEFAULT '',
    section   TEXT NOT NULL DEFAULT '',
    target_path TEXT NOT NULL DEFAULT '',
    UNIQUE(batch_id, arxiv_id)
);

CREATE TABLE IF NOT EXISTS curation_decisions (
    arxiv_id      TEXT NOT NULL PRIMARY KEY REFERENCES papers(arxiv_id),
    status        TEXT NOT NULL DEFAULT 'discovered'
                    CHECK(status IN ('discovered','curated','skipped','archived','exported','synced')),
    category      TEXT NOT NULL DEFAULT '',
    section       TEXT NOT NULL DEFAULT '',
    target_path   TEXT NOT NULL DEFAULT '',
    curated_at    TEXT,
    exported_at   TEXT,
    notes         TEXT
);

CREATE TABLE IF NOT EXISTS devices (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL DEFAULT '',
    host       TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT '',
    UNIQUE(host)
);

CREATE TABLE IF NOT EXISTS device_sync_state (
    device_id  INTEGER NOT NULL REFERENCES devices(id),
    arxiv_id   TEXT NOT NULL REFERENCES papers(arxiv_id),
    status     TEXT NOT NULL DEFAULT 'pending'
                 CHECK(status IN ('pending','uploading','synced','confirmed_external','failed','conflict')),
    target_path TEXT NOT NULL DEFAULT '',
    synced_at  TEXT,
    confirmed_at TEXT,
    error_msg  TEXT,
    UNIQUE(device_id, arxiv_id)
);

CREATE TABLE IF NOT EXISTS sync_sessions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id   INTEGER NOT NULL REFERENCES devices(id),
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    applied     INTEGER NOT NULL DEFAULT 0,
    papers_total INTEGER NOT NULL DEFAULT 0,
    papers_synced INTEGER NOT NULL DEFAULT 0,
    papers_failed INTEGER NOT NULL DEFAULT 0,
    papers_skipped INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS sync_session_papers (
    session_id INTEGER NOT NULL REFERENCES sync_sessions(id),
    arxiv_id   TEXT NOT NULL REFERENCES papers(arxiv_id),
    outcome    TEXT NOT NULL DEFAULT 'skipped'
                 CHECK(outcome IN ('uploaded','skipped','failed','already_present','moved')),
    target_path TEXT NOT NULL DEFAULT '',
    detail     TEXT,
    UNIQUE(session_id, arxiv_id)
);

CREATE INDEX IF NOT EXISTS idx_rrp_report ON radar_report_papers(report_id);
CREATE INDEX IF NOT EXISTS idx_rrp_paper  ON radar_report_papers(arxiv_id);
CREATE INDEX IF NOT EXISTS idx_ebp_batch  ON export_batch_papers(batch_id);
CREATE INDEX IF NOT EXISTS idx_ebp_paper  ON export_batch_papers(arxiv_id);
CREATE INDEX IF NOT EXISTS idx_dss_device ON device_sync_state(device_id);
CREATE INDEX IF NOT EXISTS idx_dss_paper  ON device_sync_state(arxiv_id);
CREATE INDEX IF NOT EXISTS idx_dss_status ON device_sync_state(status);
CREATE INDEX IF NOT EXISTS idx_ssp_session ON sync_session_papers(session_id);
CREATE INDEX IF NOT EXISTS idx_cd_status  ON curation_decisions(status);
CREATE UNIQUE INDEX IF NOT EXISTS idx_papers_paper_key ON papers(paper_key)
WHERE paper_key IS NOT NULL AND paper_key != '';

CREATE TABLE IF NOT EXISTS paper_retrievals (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    arxiv_id       TEXT NOT NULL REFERENCES papers(arxiv_id),
    retrieved_at   TEXT NOT NULL,
    source         TEXT NOT NULL DEFAULT 'arxiv_api'
                     CHECK(source IN ('arxiv_api','openalex','manual','ingest','radar')),
    resolved_id    TEXT NOT NULL DEFAULT '',
    metadata_json  TEXT,
    UNIQUE(arxiv_id, retrieved_at, source)
);

CREATE TABLE IF NOT EXISTS extractions (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    arxiv_id       TEXT NOT NULL REFERENCES papers(arxiv_id),
    resolved_id    TEXT NOT NULL DEFAULT '',
    extraction_type TEXT NOT NULL DEFAULT 'pdf_to_markdown'
                      CHECK(extraction_type IN ('pdf_to_markdown','pdf_to_text','pdf_to_images')),
    status         TEXT NOT NULL DEFAULT 'pending'
                     CHECK(status IN ('pending','running','completed','failed')),
    input_path     TEXT NOT NULL DEFAULT '',
    output_path    TEXT NOT NULL DEFAULT '',
    output_sha256  TEXT,
    page_count     INTEGER,
    char_count     INTEGER,
    extractor      TEXT NOT NULL DEFAULT '',
    started_at     TEXT,
    completed_at   TEXT,
    error_msg      TEXT,
    UNIQUE(arxiv_id, resolved_id, extraction_type)
);

CREATE INDEX IF NOT EXISTS idx_pr_paper   ON paper_retrievals(arxiv_id);
CREATE INDEX IF NOT EXISTS idx_pr_time    ON paper_retrievals(retrieved_at);
CREATE INDEX IF NOT EXISTS idx_ext_paper  ON extractions(arxiv_id);
CREATE INDEX IF NOT EXISTS idx_ext_status ON extractions(status);
CREATE INDEX IF NOT EXISTS idx_ext_type   ON extractions(extraction_type);

CREATE TABLE IF NOT EXISTS enrichments (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    arxiv_id        TEXT NOT NULL REFERENCES papers(arxiv_id),
    enrichment_type TEXT NOT NULL DEFAULT 'llm_summary'
                     CHECK(enrichment_type IN ('llm_summary','category_summary','foundational_summary')),
    model           TEXT NOT NULL DEFAULT '',
    variant         TEXT,
    prompt_version  INTEGER NOT NULL DEFAULT 1,
    status          TEXT NOT NULL DEFAULT 'pending'
                     CHECK(status IN ('pending','completed','failed')),
    summary_path    TEXT NOT NULL DEFAULT '',
    prompt_path     TEXT NOT NULL DEFAULT '',
    prompt_sha256   TEXT,
    source_basis    TEXT NOT NULL DEFAULT '',
    confidence      TEXT,
    started_at      TEXT,
    completed_at    TEXT,
    error_msg       TEXT,
    UNIQUE(arxiv_id, enrichment_type, model, prompt_version)
);

CREATE TABLE IF NOT EXISTS reference_edges (
    source_arxiv_id    TEXT NOT NULL REFERENCES papers(arxiv_id),
    target_openalex_id TEXT NOT NULL DEFAULT '',
    target_arxiv_id    TEXT,
    target_title       TEXT NOT NULL DEFAULT '',
    target_citation_count INTEGER,
    depth              INTEGER NOT NULL DEFAULT 1,
    discovered_at      TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (source_arxiv_id, target_openalex_id)
);

CREATE TABLE IF NOT EXISTS periodical_builds (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at         TEXT NOT NULL,
    finished_at        TEXT,
    paper_count        INTEGER NOT NULL DEFAULT 0,
    foundational_count INTEGER NOT NULL DEFAULT 0,
    reference_depth    INTEGER NOT NULL DEFAULT 0,
    model              TEXT NOT NULL DEFAULT '',
    output_pdf         TEXT NOT NULL DEFAULT '',
    status             TEXT NOT NULL DEFAULT 'running'
                          CHECK(status IN ('running','completed','failed')),
    error_msg          TEXT,
    manifest_sha256    TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS periodical_build_papers (
    build_id   INTEGER NOT NULL REFERENCES periodical_builds(id),
    arxiv_id   TEXT NOT NULL REFERENCES papers(arxiv_id),
    paper_role TEXT NOT NULL DEFAULT 'primary'
                 CHECK(paper_role IN ('primary','foundational')),
    UNIQUE(build_id, arxiv_id)
);

CREATE INDEX IF NOT EXISTS idx_enr_paper   ON enrichments(arxiv_id);
CREATE INDEX IF NOT EXISTS idx_enr_status  ON enrichments(status);
CREATE INDEX IF NOT EXISTS idx_enr_type    ON enrichments(enrichment_type);
CREATE INDEX IF NOT EXISTS idx_ref_source  ON reference_edges(source_arxiv_id);
CREATE INDEX IF NOT EXISTS idx_ref_target  ON reference_edges(target_arxiv_id);
CREATE INDEX IF NOT EXISTS idx_pb_status   ON periodical_builds(status);
CREATE INDEX IF NOT EXISTS idx_pbp_build   ON periodical_build_papers(build_id);

CREATE TABLE IF NOT EXISTS source_refreshes (
    source TEXT NOT NULL,
    category TEXT NOT NULL,
    query_hash TEXT NOT NULL,
    refreshed_at TEXT NOT NULL,
    since TEXT,
    cursor TEXT,
    status TEXT NOT NULL DEFAULT 'completed',
    item_count INTEGER NOT NULL DEFAULT 0,
    error_msg TEXT,
    PRIMARY KEY (source, category, query_hash)
);
"""

SCHEMA_MIGRATIONS = {
    4: [
        "ALTER TABLE periodical_builds ADD COLUMN manifest_sha256 TEXT NOT NULL DEFAULT ''",
    ],
    5: [
        "ALTER TABLE papers ADD COLUMN paper_key TEXT",
        "ALTER TABLE papers ADD COLUMN doi TEXT",
        "ALTER TABLE papers ADD COLUMN semantic_scholar_id TEXT",
        "ALTER TABLE papers ADD COLUMN corpus_id TEXT",
        "ALTER TABLE papers ADD COLUMN openalex_id TEXT",
        "ALTER TABLE papers ADD COLUMN source_ids_json TEXT NOT NULL DEFAULT '{}'",
        "ALTER TABLE papers ADD COLUMN source_metadata_json TEXT NOT NULL DEFAULT '{}'",
        "UPDATE papers SET paper_key = 'arxiv:' || arxiv_id WHERE paper_key IS NULL OR paper_key = ''",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_papers_paper_key ON papers(paper_key) WHERE paper_key IS NOT NULL AND paper_key != ''",
        """CREATE TABLE IF NOT EXISTS source_refreshes (
            source TEXT NOT NULL,
            category TEXT NOT NULL,
            query_hash TEXT NOT NULL,
            refreshed_at TEXT NOT NULL,
            since TEXT,
            cursor TEXT,
            status TEXT NOT NULL DEFAULT 'completed',
            item_count INTEGER NOT NULL DEFAULT 0,
            error_msg TEXT,
            PRIMARY KEY (source, category, query_hash)
        )""",
    ],
}


@dataclass
class PaperRecord:
    arxiv_id: str
    resolved_id: str
    paper_key: str = ""
    doi: str | None = None
    semantic_scholar_id: str | None = None
    corpus_id: str | None = None
    openalex_id: str | None = None
    source_ids: dict = field(default_factory=dict)
    source_metadata: dict = field(default_factory=dict)
    title: str = ""
    authors: list[str] = field(default_factory=list)
    summary: str = ""
    pdf_url: str = ""
    abs_url: str = ""
    primary_category: str = ""
    published: str = ""
    updated: str = ""
    suggested_filename: str = ""
    citation_count: int | None = None
    influential_citation_count: int | None = None
    citation_source_url: str | None = None
    discovered_at: str = ""
    discovered_via: str = ""


@dataclass
class DeviceRecord:
    id: int
    name: str
    host: str
    created_at: str


@dataclass
class SyncStateRecord:
    device_id: int
    arxiv_id: str
    status: str = "pending"
    target_path: str = ""
    synced_at: str | None = None
    confirmed_at: str | None = None
    error_msg: str | None = None


@dataclass
class RetrievalRecord:
    arxiv_id: str
    retrieved_at: str
    source: str = "arxiv_api"
    resolved_id: str = ""
    metadata_json: str | None = None


@dataclass
class SourceRefreshRecord:
    source: str
    category: str
    query_hash: str
    refreshed_at: str = ""
    since: str | None = None
    cursor: str | None = None
    status: str = "completed"
    item_count: int = 0
    error_msg: str | None = None


@dataclass
class ExtractionRecord:
    arxiv_id: str
    resolved_id: str
    extraction_type: str = "pdf_to_markdown"
    status: str = "pending"
    input_path: str = ""
    output_path: str = ""
    output_sha256: str | None = None
    page_count: int | None = None
    char_count: int | None = None
    extractor: str = ""
    started_at: str | None = None
    completed_at: str | None = None
    error_msg: str | None = None


@dataclass
class EnrichmentRecord:
    arxiv_id: str
    enrichment_type: str = "llm_summary"
    model: str = ""
    variant: str | None = None
    prompt_version: int = 1
    status: str = "pending"
    summary_path: str = ""
    prompt_path: str = ""
    prompt_sha256: str | None = None
    source_basis: str = ""
    confidence: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    error_msg: str | None = None


@dataclass
class ReferenceEdge:
    source_arxiv_id: str
    target_openalex_id: str = ""
    target_arxiv_id: str | None = None
    target_title: str = ""
    target_citation_count: int | None = None
    depth: int = 1
    discovered_at: str = ""


@dataclass
class PeriodicalBuild:
    id: int | None = None
    started_at: str = ""
    finished_at: str | None = None
    paper_count: int = 0
    foundational_count: int = 0
    reference_depth: int = 0
    model: str = ""
    output_pdf: str = ""
    status: str = "running"
    error_msg: str | None = None


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _connect(db_path: str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def get_db(db_path: str = DEFAULT_DB_PATH) -> Generator[sqlite3.Connection, None, None]:
    conn = _connect(db_path)
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_path: str = DEFAULT_DB_PATH) -> bool:
    with get_db(db_path) as conn:
        conn.executescript(SCHEMA_DDL)
        existing = conn.execute(
            "SELECT version FROM schema_version"
        ).fetchone()
        if existing is None:
            conn.execute(
                "INSERT INTO schema_version (version) VALUES (?)",
                (SCHEMA_VERSION,),
            )
            return True
        old_version = existing["version"]
        if old_version < SCHEMA_VERSION:
            for v in range(old_version + 1, SCHEMA_VERSION + 1):
                for stmt in SCHEMA_MIGRATIONS.get(v, []):
                    conn.execute(stmt)
            conn.execute(
                "UPDATE schema_version SET version = ?",
                (SCHEMA_VERSION,),
            )
            return True
        return False


def upsert_paper(conn: sqlite3.Connection, paper: PaperRecord) -> None:
    paper_key = paper.paper_key or (f"arxiv:{paper.arxiv_id}" if paper.arxiv_id else "")
    conn.execute(
        """INSERT INTO papers (
            arxiv_id, paper_key, doi, semantic_scholar_id, corpus_id,
            openalex_id, source_ids_json, source_metadata_json,
            resolved_id, title, authors, summary,
            pdf_url, abs_url, primary_category, published, updated,
            suggested_filename, citation_count, influential_citation_count,
            citation_source_url, discovered_at, discovered_via
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(arxiv_id) DO UPDATE SET
            paper_key = COALESCE(NULLIF(excluded.paper_key, ''), papers.paper_key),
            doi = COALESCE(NULLIF(excluded.doi, ''), papers.doi),
            semantic_scholar_id = COALESCE(NULLIF(excluded.semantic_scholar_id, ''), papers.semantic_scholar_id),
            corpus_id = COALESCE(NULLIF(excluded.corpus_id, ''), papers.corpus_id),
            openalex_id = COALESCE(NULLIF(excluded.openalex_id, ''), papers.openalex_id),
            source_ids_json = CASE
                WHEN excluded.source_ids_json = '{}' THEN papers.source_ids_json
                ELSE excluded.source_ids_json END,
            source_metadata_json = CASE
                WHEN excluded.source_metadata_json = '{}' THEN papers.source_metadata_json
                ELSE excluded.source_metadata_json END,
            resolved_id = COALESCE(NULLIF(excluded.resolved_id, ''), papers.resolved_id),
            title = COALESCE(NULLIF(excluded.title, ''), papers.title),
            authors = CASE
                WHEN excluded.authors = '[]' THEN papers.authors
                ELSE excluded.authors END,
            summary = COALESCE(NULLIF(excluded.summary, ''), papers.summary),
            pdf_url = COALESCE(NULLIF(excluded.pdf_url, ''), papers.pdf_url),
            abs_url = COALESCE(NULLIF(excluded.abs_url, ''), papers.abs_url),
            primary_category = COALESCE(NULLIF(excluded.primary_category, ''), papers.primary_category),
            published = COALESCE(NULLIF(excluded.published, ''), papers.published),
            updated = COALESCE(NULLIF(excluded.updated, ''), papers.updated),
            suggested_filename = COALESCE(NULLIF(excluded.suggested_filename, ''), papers.suggested_filename),
            citation_count = CASE
                WHEN excluded.citation_count IS NOT NULL THEN excluded.citation_count
                ELSE papers.citation_count END,
            influential_citation_count = CASE
                WHEN excluded.influential_citation_count IS NOT NULL THEN excluded.influential_citation_count
                ELSE papers.influential_citation_count END,
            citation_source_url = COALESCE(NULLIF(excluded.citation_source_url, ''), papers.citation_source_url)
        """,
        (
            paper.arxiv_id,
            paper_key,
            paper.doi,
            paper.semantic_scholar_id,
            paper.corpus_id,
            paper.openalex_id,
            json.dumps(paper.source_ids),
            json.dumps(paper.source_metadata),
            paper.resolved_id,
            paper.title,
            json.dumps(paper.authors),
            paper.summary,
            paper.pdf_url,
            paper.abs_url,
            paper.primary_category,
            paper.published,
            paper.updated,
            paper.suggested_filename,
            paper.citation_count,
            paper.influential_citation_count,
            paper.citation_source_url,
            paper.discovered_at or _now_iso(),
            paper.discovered_via,
        ),
    )


def record_retrieval(
    conn: sqlite3.Connection,
    retrieval: RetrievalRecord,
) -> None:
    conn.execute(
        """INSERT OR IGNORE INTO paper_retrievals
           (arxiv_id, retrieved_at, source, resolved_id, metadata_json)
        VALUES (?, ?, ?, ?, ?)""",
        (
            retrieval.arxiv_id,
            retrieval.retrieved_at or _now_iso(),
            retrieval.source,
            retrieval.resolved_id,
            retrieval.metadata_json,
        ),
    )


def latest_retrieval(
    conn: sqlite3.Connection, arxiv_id: str
) -> dict | None:
    row = conn.execute(
        """SELECT arxiv_id, retrieved_at, source, resolved_id, metadata_json
           FROM paper_retrievals
           WHERE arxiv_id = ?
           ORDER BY retrieved_at DESC LIMIT 1""",
        (arxiv_id,),
    ).fetchone()
    return dict(row) if row else None


def upsert_source_refresh(
    conn: sqlite3.Connection,
    refresh: SourceRefreshRecord,
) -> None:
    conn.execute(
        """INSERT INTO source_refreshes
           (source, category, query_hash, refreshed_at, since, cursor, status, item_count, error_msg)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(source, category, query_hash) DO UPDATE SET
           refreshed_at = excluded.refreshed_at,
           since = excluded.since,
           cursor = excluded.cursor,
           status = excluded.status,
           item_count = excluded.item_count,
           error_msg = excluded.error_msg""",
        (
            refresh.source,
            refresh.category,
            refresh.query_hash,
            refresh.refreshed_at or _now_iso(),
            refresh.since,
            refresh.cursor,
            refresh.status,
            refresh.item_count,
            refresh.error_msg,
        ),
    )


def get_source_refresh(
    conn: sqlite3.Connection,
    *,
    source: str,
    category: str,
    query_hash: str,
) -> dict | None:
    row = conn.execute(
        """SELECT * FROM source_refreshes
           WHERE source = ? AND category = ? AND query_hash = ?""",
        (source, category, query_hash),
    ).fetchone()
    return dict(row) if row else None


def upsert_extraction(conn: sqlite3.Connection, ext: ExtractionRecord) -> None:
    conn.execute(
        """INSERT INTO extractions (
            arxiv_id, resolved_id, extraction_type, status,
            input_path, output_path, output_sha256,
            page_count, char_count, extractor,
            started_at, completed_at, error_msg
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(arxiv_id, resolved_id, extraction_type) DO UPDATE SET
            status = excluded.status,
            input_path = COALESCE(NULLIF(excluded.input_path, ''), extractions.input_path),
            output_path = COALESCE(NULLIF(excluded.output_path, ''), extractions.output_path),
            output_sha256 = COALESCE(excluded.output_sha256, extractions.output_sha256),
            page_count = COALESCE(excluded.page_count, extractions.page_count),
            char_count = COALESCE(excluded.char_count, extractions.char_count),
            extractor = COALESCE(NULLIF(excluded.extractor, ''), extractions.extractor),
            started_at = COALESCE(excluded.started_at, extractions.started_at),
            completed_at = COALESCE(excluded.completed_at, extractions.completed_at),
            error_msg = excluded.error_msg
        """,
        (
            ext.arxiv_id,
            ext.resolved_id,
            ext.extraction_type,
            ext.status,
            ext.input_path,
            ext.output_path,
            ext.output_sha256,
            ext.page_count,
            ext.char_count,
            ext.extractor,
            ext.started_at,
            ext.completed_at,
            ext.error_msg,
        ),
    )


def get_extraction(
    conn: sqlite3.Connection,
    arxiv_id: str,
    resolved_id: str = "",
    extraction_type: str = "pdf_to_markdown",
) -> dict | None:
    if not resolved_id:
        paper = conn.execute(
            "SELECT resolved_id FROM papers WHERE arxiv_id = ?", (arxiv_id,)
        ).fetchone()
        if not paper:
            return None
        resolved_id = paper["resolved_id"]
    row = conn.execute(
        """SELECT * FROM extractions
           WHERE arxiv_id = ? AND resolved_id = ? AND extraction_type = ?""",
        (arxiv_id, resolved_id, extraction_type),
    ).fetchone()
    return dict(row) if row else None


def get_enrichment(
    conn: sqlite3.Connection,
    arxiv_id: str,
    enrichment_type: str = "llm_summary",
    model: str = "",
    prompt_version: int = 1,
) -> dict | None:
    row = conn.execute(
        """SELECT * FROM enrichments
           WHERE arxiv_id = ? AND enrichment_type = ?
             AND model = ? AND prompt_version = ?""",
        (arxiv_id, enrichment_type, model, prompt_version),
    ).fetchone()
    return dict(row) if row else None


def upsert_enrichment(conn: sqlite3.Connection, enr: EnrichmentRecord) -> None:
    conn.execute(
        """INSERT INTO enrichments (
            arxiv_id, enrichment_type, model, variant, prompt_version,
            status, summary_path, prompt_path, prompt_sha256,
            source_basis, confidence, started_at, completed_at, error_msg
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(arxiv_id, enrichment_type, model, prompt_version) DO UPDATE SET
            status = excluded.status,
            summary_path = COALESCE(NULLIF(excluded.summary_path, ''), enrichments.summary_path),
            prompt_path = COALESCE(NULLIF(excluded.prompt_path, ''), enrichments.prompt_path),
            prompt_sha256 = COALESCE(excluded.prompt_sha256, enrichments.prompt_sha256),
            source_basis = COALESCE(NULLIF(excluded.source_basis, ''), enrichments.source_basis),
            confidence = COALESCE(NULLIF(excluded.confidence, ''), enrichments.confidence),
            started_at = COALESCE(excluded.started_at, enrichments.started_at),
            completed_at = COALESCE(excluded.completed_at, enrichments.completed_at),
            error_msg = excluded.error_msg
        """,
        (
            enr.arxiv_id,
            enr.enrichment_type,
            enr.model,
            enr.variant,
            enr.prompt_version,
            enr.status,
            enr.summary_path,
            enr.prompt_path,
            enr.prompt_sha256,
            enr.source_basis,
            enr.confidence,
            enr.started_at,
            enr.completed_at,
            enr.error_msg,
        ),
    )


def record_reference_edge(conn: sqlite3.Connection, edge: ReferenceEdge) -> None:
    conn.execute(
        """INSERT OR IGNORE INTO reference_edges
           (source_arxiv_id, target_openalex_id, target_arxiv_id,
            target_title, target_citation_count, depth, discovered_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            edge.source_arxiv_id,
            edge.target_openalex_id,
            edge.target_arxiv_id,
            edge.target_title,
            edge.target_citation_count,
            edge.depth,
            edge.discovered_at or _now_iso(),
        ),
    )


def record_reference_edges(
    conn: sqlite3.Connection,
    source_arxiv_id: str,
    refs: list[dict],
) -> int:
    count = 0
    for ref in refs:
        edge = ReferenceEdge(
            source_arxiv_id=source_arxiv_id,
            target_openalex_id=ref.get("openalex_id", ""),
            target_arxiv_id=ref.get("arxiv_id"),
            target_title=ref.get("title", ""),
            target_citation_count=ref.get("citation_count"),
            depth=ref.get("depth", 1),
        )
        record_reference_edge(conn, edge)
        count += 1
    return count


def start_periodical_build(
    conn: sqlite3.Connection,
    *,
    paper_count: int = 0,
    reference_depth: int = 0,
    model: str = "",
    manifest_sha256: str = "",
) -> int:
    cursor = conn.execute(
        """INSERT INTO periodical_builds
           (started_at, paper_count, reference_depth, model, status, manifest_sha256)
        VALUES (?, ?, ?, ?, 'running', ?)""",
        (_now_iso(), paper_count, reference_depth, model, manifest_sha256),
    )
    return cursor.lastrowid


def finish_periodical_build(
    conn: sqlite3.Connection,
    build_id: int,
    *,
    paper_count: int = 0,
    foundational_count: int = 0,
    output_pdf: str = "",
    status: str = "completed",
    error_msg: str | None = None,
) -> None:
    conn.execute(
        """UPDATE periodical_builds SET
           finished_at = ?, paper_count = ?, foundational_count = ?,
           output_pdf = ?, status = ?, error_msg = ?
        WHERE id = ?""",
        (_now_iso(), paper_count, foundational_count, output_pdf, status, error_msg, build_id),
    )


def record_periodical_paper(
    conn: sqlite3.Connection,
    build_id: int,
    arxiv_id: str,
    paper_role: str = "primary",
) -> None:
    conn.execute(
        """INSERT OR IGNORE INTO periodical_build_papers
           (build_id, arxiv_id, paper_role) VALUES (?, ?, ?)""",
        (build_id, arxiv_id, paper_role),
    )


def pending_extractions(
    conn: sqlite3.Connection,
    extraction_type: str = "pdf_to_markdown",
) -> list[dict]:
    rows = conn.execute(
        """SELECT e.*, p.pdf_url, p.title
           FROM extractions e
           JOIN papers p ON p.arxiv_id = e.arxiv_id
           WHERE e.status IN ('pending', 'failed') AND e.extraction_type = ?
           ORDER BY e.started_at DESC NULLS LAST""",
        (extraction_type,),
    ).fetchall()
    return [dict(r) for r in rows]


def entry_to_paper(entry: dict, discovered_via: str = "radar") -> PaperRecord:
    return PaperRecord(
        arxiv_id=entry.get("arxiv_id", ""),
        resolved_id=entry.get("resolved_id", ""),
        paper_key=entry.get("paper_key", ""),
        doi=entry.get("doi"),
        semantic_scholar_id=entry.get("semantic_scholar_id"),
        corpus_id=str(entry.get("corpus_id")) if entry.get("corpus_id") is not None else None,
        openalex_id=entry.get("openalex_id"),
        source_ids=entry.get("source_ids", {}) if isinstance(entry.get("source_ids", {}), dict) else {},
        source_metadata=entry.get("source_metadata", {}) if isinstance(entry.get("source_metadata", {}), dict) else {},
        title=entry.get("title", ""),
        authors=entry.get("authors", []),
        summary=entry.get("summary", ""),
        pdf_url=entry.get("pdf_url", ""),
        abs_url=entry.get("abs_url", ""),
        primary_category=entry.get("primary_category", ""),
        published=entry.get("published", ""),
        updated=entry.get("updated", ""),
        suggested_filename=entry.get("suggested_filename", ""),
        citation_count=entry.get("citation_count"),
        influential_citation_count=entry.get("influential_citation_count"),
        citation_source_url=entry.get("citation_source_url"),
        discovered_via=discovered_via,
    )


def ingest_radar_report(conn: sqlite3.Connection, report_path: str) -> dict:
    report = load_radar_report(report_path)
    generated_at = report.get("generated_at", _now_iso())
    lookback_days = report.get("lookback_days")

    cursor = conn.execute(
        "INSERT INTO radar_reports (generated_at, report_path, lookback_days) VALUES (?, ?, ?)",
        (generated_at, str(report_path), lookback_days),
    )
    report_id = cursor.lastrowid

    paper_count = 0
    for category_block in report.get("categories", []):
        category_name = category_block.get("name", "")
        for section in ("recent", "highly_cited"):
            for entry in category_block.get(section, []):
                arxiv_id = entry.get("arxiv_id", "")
                if not arxiv_id:
                    continue
                paper = entry_to_paper(entry, discovered_via="radar")
                upsert_paper(conn, paper)
                record_retrieval(conn, RetrievalRecord(
                    arxiv_id=arxiv_id,
                    retrieved_at=generated_at,
                    source="radar",
                    resolved_id=entry.get("resolved_id", ""),
                    metadata_json=json.dumps({"category": category_name, "section": section}),
                ))
                conn.execute(
                    """INSERT OR IGNORE INTO radar_report_papers
                       (report_id, arxiv_id, category, section) VALUES (?, ?, ?, ?)""",
                    (report_id, arxiv_id, category_name, section),
                )
                paper_count += 1

    return {"report_id": report_id, "paper_count": paper_count}


def ingest_manifest(
    conn: sqlite3.Connection,
    manifest_path: str,
    *,
    source: str = "export",
    section: str = "",
    categories: list[str] | None = None,
    top_n: int | None = None,
    min_citations: int | None = None,
    max_citations: int | None = None,
    since_date: str | None = None,
    lookback_days: int | None = None,
) -> dict:
    manifest = load_manifest(manifest_path)
    entries = [e for e in manifest.get("entries", []) if isinstance(e, dict)]
    created_at = manifest.get("generated_at", _now_iso())

    cursor = conn.execute(
        """INSERT INTO export_batches
           (created_at, section, categories, exclude_categories,
            top_n, min_citations, max_citations, since_date,
            lookback_days, paper_count, source)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            created_at,
            section,
            json.dumps(categories or []),
            "[]",
            top_n,
            min_citations,
            max_citations,
            since_date,
            lookback_days,
            len(entries),
            source,
        ),
    )
    batch_id = cursor.lastrowid

    for entry in entries:
        arxiv_id = entry.get("arxiv_id", "")
        if not arxiv_id:
            continue
        paper = entry_to_paper(entry, discovered_via=source)
        upsert_paper(conn, paper)
        conn.execute(
            """INSERT OR IGNORE INTO export_batch_papers
               (batch_id, arxiv_id, category, section, target_path)
            VALUES (?, ?, ?, ?, ?)""",
            (
                batch_id,
                arxiv_id,
                entry.get("category", ""),
                entry.get("section", ""),
                entry.get("target_path", ""),
            ),
        )

        existing = conn.execute(
            "SELECT status FROM curation_decisions WHERE arxiv_id = ?",
            (arxiv_id,),
        ).fetchone()
        if existing is None:
            conn.execute(
                """INSERT INTO curation_decisions
                   (arxiv_id, status, category, section, target_path, curated_at, exported_at)
                VALUES (?, 'curated', ?, ?, ?, ?, ?)""",
                (
                    arxiv_id,
                    entry.get("category", ""),
                    entry.get("section", ""),
                    entry.get("target_path", ""),
                    created_at if source in ("curation", "tui") else None,
                    created_at,
                ),
            )
        elif existing["status"] in ("discovered",):
            conn.execute(
                """UPDATE curation_decisions SET
                   status = 'curated',
                   category = ?,
                   section = ?,
                   target_path = ?,
                   curated_at = COALESCE(curated_at, ?),
                   exported_at = ?
                WHERE arxiv_id = ?""",
                (
                    entry.get("category", ""),
                    entry.get("section", ""),
                    entry.get("target_path", ""),
                    created_at,
                    created_at,
                    arxiv_id,
                ),
            )
        elif existing["status"] in ("curated",):
            conn.execute(
                """UPDATE curation_decisions SET
                   exported_at = COALESCE(exported_at, ?)
                WHERE arxiv_id = ?""",
                (created_at, arxiv_id),
            )

    return {"batch_id": batch_id, "paper_count": len(entries)}


def register_device(
    conn: sqlite3.Connection, host: str, name: str = ""
) -> DeviceRecord:
    existing = conn.execute(
        "SELECT id, name, host, created_at FROM devices WHERE host = ?",
        (host,),
    ).fetchone()
    if existing:
        if name and name != existing["name"]:
            conn.execute("UPDATE devices SET name = ? WHERE id = ?", (name, existing["id"]))
        return DeviceRecord(
            id=existing["id"],
            name=name or existing["name"],
            host=existing["host"],
            created_at=existing["created_at"],
        )
    now = _now_iso()
    cursor = conn.execute(
        "INSERT INTO devices (name, host, created_at) VALUES (?, ?, ?)",
        (name or host, host, now),
    )
    return DeviceRecord(id=cursor.lastrowid, name=name or host, host=host, created_at=now)


def ensure_sync_states(
    conn: sqlite3.Connection,
    device_id: int,
    arxiv_ids: list[tuple[str, str]],
) -> int:
    created = 0
    for arxiv_id, target_path in arxiv_ids:
        existing = conn.execute(
            "SELECT status FROM device_sync_state WHERE device_id = ? AND arxiv_id = ?",
            (device_id, arxiv_id),
        ).fetchone()
        if existing is None:
            conn.execute(
                """INSERT INTO device_sync_state
                   (device_id, arxiv_id, status, target_path)
                VALUES (?, ?, 'pending', ?)""",
                (device_id, arxiv_id, target_path),
            )
            created += 1
    return created


def mark_synced(
    conn: sqlite3.Connection,
    device_id: int,
    arxiv_id: str,
    status: str = "synced",
    error_msg: str | None = None,
) -> None:
    now = _now_iso()
    conn.execute(
        """INSERT INTO device_sync_state
           (device_id, arxiv_id, status, target_path, synced_at, confirmed_at)
        SELECT ?, ?, ?, cd.target_path, ?, ?
        FROM curation_decisions cd WHERE cd.arxiv_id = ?
        ON CONFLICT(device_id, arxiv_id) DO UPDATE SET
            status = excluded.status,
            synced_at = COALESCE(excluded.synced_at, device_sync_state.synced_at),
            confirmed_at = COALESCE(excluded.confirmed_at, device_sync_state.confirmed_at),
            error_msg = ?""",
        (device_id, arxiv_id, status, now, now, arxiv_id, error_msg),
    )
    conn.execute(
        """UPDATE device_sync_state SET synced_at = ?, confirmed_at = ?, status = ?, error_msg = ?
        WHERE device_id = ? AND arxiv_id = ?""",
        (now, now, status, error_msg, device_id, arxiv_id),
    )


def reconcile_device(
    conn: sqlite3.Connection,
    device_id: int,
    device_file_basenames: dict[str, str],
) -> dict:
    curated = conn.execute(
        "SELECT arxiv_id, target_path, status FROM curation_decisions WHERE status IN ('curated', 'exported', 'synced')"
    ).fetchall()
    reconciled = 0
    newly_tracked = 0
    for row in curated:
        arxiv_id = row["arxiv_id"]
        target_path = row["target_path"]
        basename = target_path.rsplit("/", 1)[-1] if "/" in target_path else target_path
        actual_path = device_file_basenames.get(basename)

        existing_state = conn.execute(
            "SELECT status FROM device_sync_state WHERE device_id = ? AND arxiv_id = ?",
            (device_id, arxiv_id),
        ).fetchone()

        if actual_path:
            if existing_state is None:
                conn.execute(
                    """INSERT INTO device_sync_state
                       (device_id, arxiv_id, status, target_path, synced_at, confirmed_at)
                    VALUES (?, ?, 'confirmed_external', ?, ?, ?)""",
                    (device_id, arxiv_id, actual_path, _now_iso(), _now_iso()),
                )
                newly_tracked += 1
            elif existing_state["status"] in ("pending", "failed"):
                conn.execute(
                    """UPDATE device_sync_state SET status = 'confirmed_external',
                       confirmed_at = ?, target_path = ?,
                       error_msg = NULL
                    WHERE device_id = ? AND arxiv_id = ?""",
                    (_now_iso(), actual_path, device_id, arxiv_id),
                )
                reconciled += 1
            elif actual_path != target_path:
                conn.execute(
                    """UPDATE device_sync_state SET target_path = ?
                    WHERE device_id = ? AND arxiv_id = ?""",
                    (actual_path, device_id, arxiv_id),
                )
        else:
            if existing_state and existing_state["status"] == "synced":
                conn.execute(
                    """UPDATE device_sync_state SET status = 'pending',
                       synced_at = NULL, confirmed_at = NULL,
                       error_msg = 'file missing from device at last reconcile'
                    WHERE device_id = ? AND arxiv_id = ?""",
                    (device_id, arxiv_id),
                )
                reconciled += 1

    return {"reconciled": reconciled, "newly_tracked": newly_tracked}


def create_sync_session(
    conn: sqlite3.Connection,
    device_id: int,
) -> int:
    cursor = conn.execute(
        "INSERT INTO sync_sessions (device_id, started_at) VALUES (?, ?)",
        (device_id, _now_iso()),
    )
    return cursor.lastrowid


def finish_sync_session(
    conn: sqlite3.Connection,
    session_id: int,
    *,
    papers_total: int = 0,
    papers_synced: int = 0,
    papers_failed: int = 0,
    papers_skipped: int = 0,
    applied: bool = False,
) -> None:
    conn.execute(
        """UPDATE sync_sessions SET
           finished_at = ?, applied = ?,
           papers_total = ?, papers_synced = ?,
           papers_failed = ?, papers_skipped = ?
        WHERE id = ?""",
        (_now_iso(), int(applied), papers_total, papers_synced, papers_failed, papers_skipped, session_id),
    )


def record_sync_outcome(
    conn: sqlite3.Connection,
    session_id: int,
    arxiv_id: str,
    outcome: str,
    target_path: str = "",
    detail: str | None = None,
) -> None:
    conn.execute(
        """INSERT OR IGNORE INTO sync_session_papers
           (session_id, arxiv_id, outcome, target_path, detail)
        VALUES (?, ?, ?, ?, ?)""",
        (session_id, arxiv_id, outcome, target_path, detail),
    )


def get_pending_sync(
    conn: sqlite3.Connection,
    device_id: int,
) -> list[dict]:
    rows = conn.execute(
        """SELECT dss.arxiv_id, dss.status, dss.target_path,
                  p.title, p.pdf_url, p.resolved_id, p.suggested_filename
           FROM device_sync_state dss
           JOIN papers p ON p.arxiv_id = dss.arxiv_id
           WHERE dss.device_id = ? AND dss.status IN ('pending', 'failed')
           ORDER BY p.citation_count DESC NULLS LAST, p.published DESC""",
        (device_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_synced_papers(
    conn: sqlite3.Connection,
    device_id: int,
) -> list[dict]:
    rows = conn.execute(
        """SELECT dss.arxiv_id, dss.status, dss.target_path,
                  dss.synced_at, dss.confirmed_at,
                  p.title, p.resolved_id
           FROM device_sync_state dss
           JOIN papers p ON p.arxiv_id = dss.arxiv_id
           WHERE dss.device_id = ? AND dss.status IN ('synced', 'confirmed_external')
           ORDER BY p.citation_count DESC NULLS LAST""",
        (device_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def db_status(conn: sqlite3.Connection) -> dict:
    papers = conn.execute("SELECT COUNT(*) AS c FROM papers").fetchone()["c"]
    reports = conn.execute("SELECT COUNT(*) AS c FROM radar_reports").fetchone()["c"]
    batches = conn.execute("SELECT COUNT(*) AS c FROM export_batches").fetchone()["c"]
    devices = conn.execute("SELECT COUNT(*) AS c FROM devices").fetchone()["c"]
    sessions = conn.execute("SELECT COUNT(*) AS c FROM sync_sessions").fetchone()["c"]
    retrievals = conn.execute("SELECT COUNT(*) AS c FROM paper_retrievals").fetchone()["c"]
    source_refreshes = conn.execute("SELECT COUNT(*) AS c FROM source_refreshes").fetchone()["c"]
    extractions_completed = conn.execute(
        "SELECT COUNT(*) AS c FROM extractions WHERE status = 'completed'"
    ).fetchone()["c"]
    extractions_pending = conn.execute(
        "SELECT COUNT(*) AS c FROM extractions WHERE status IN ('pending', 'running', 'failed')"
    ).fetchone()["c"]

    enrichments_completed = conn.execute(
        "SELECT COUNT(*) AS c FROM enrichments WHERE status = 'completed'"
    ).fetchone()["c"]
    enrichments_pending = conn.execute(
        "SELECT COUNT(*) AS c FROM enrichments WHERE status IN ('pending', 'running', 'failed')"
    ).fetchone()["c"]

    enrichment_type_counts = {}
    for row in conn.execute("SELECT enrichment_type, status, COUNT(*) AS c FROM enrichments GROUP BY enrichment_type, status"):
        key = f"{row['enrichment_type']}:{row['status']}"
        enrichment_type_counts[key] = row["c"]

    reference_count = conn.execute("SELECT COUNT(*) AS c FROM reference_edges").fetchone()["c"]
    reference_targets = conn.execute("SELECT COUNT(DISTINCT target_arxiv_id) AS c FROM reference_edges WHERE target_arxiv_id IS NOT NULL").fetchone()["c"]

    periodical_builds = conn.execute("SELECT COUNT(*) AS c FROM periodical_builds").fetchone()["c"]
    periodical_last = conn.execute(
        "SELECT started_at, status, paper_count, foundational_count FROM periodical_builds ORDER BY started_at DESC LIMIT 1"
    ).fetchone()

    curation_counts = {}
    for row in conn.execute("SELECT status, COUNT(*) AS c FROM curation_decisions GROUP BY status"):
        curation_counts[row["status"]] = row["c"]

    extraction_type_counts = {}
    for row in conn.execute("SELECT extraction_type, status, COUNT(*) AS c FROM extractions GROUP BY extraction_type, status"):
        key = f"{row['extraction_type']}:{row['status']}"
        extraction_type_counts[key] = row["c"]

    recent_papers = conn.execute(
        """SELECT p.arxiv_id, p.title, p.discovered_at,
                  pr.retrieved_at, pr.source AS retrieval_source
           FROM papers p
           LEFT JOIN paper_retrievals pr ON pr.arxiv_id = p.arxiv_id
           GROUP BY p.arxiv_id
           ORDER BY MAX(pr.retrieved_at) DESC NULLS LAST, p.discovered_at DESC
           LIMIT 5"""
    ).fetchall()

    return {
        "papers": papers,
        "reports": reports,
        "export_batches": batches,
        "devices": devices,
        "sync_sessions": sessions,
        "retrievals": retrievals,
        "source_refreshes": source_refreshes,
        "extractions_completed": extractions_completed,
        "extractions_pending": extractions_pending,
        "extraction_type_counts": extraction_type_counts,
        "enrichments_completed": enrichments_completed,
        "enrichments_pending": enrichments_pending,
        "enrichment_type_counts": enrichment_type_counts,
        "reference_edges": reference_count,
        "reference_targets": reference_targets,
        "periodical_builds": periodical_builds,
        "periodical_last": dict(periodical_last) if periodical_last else None,
        "curation_counts": curation_counts,
        "recent_papers": [dict(r) for r in recent_papers],
    }


def seed_from_json_artifacts(
    conn: sqlite3.Connection,
    *,
    radar_report_dir: str = "arxiv-radar-output",
    curated_path: str = "artifacts/arxiv-radar-curated.json",
    manifest_path: str = "artifacts/arxiv-radar-manifest.json",
    staged_path: str = "artifacts/arxiv-radar-staged.json",
) -> dict:
    results: dict = {"reports": [], "manifests": [], "curated": None, "staged": None, "extractions": 0}

    report_dir = Path(radar_report_dir)
    if report_dir.exists():
        for report_file in sorted(report_dir.glob("arxiv-radar-*.json")):
            r = ingest_radar_report(conn, str(report_file))
            results["reports"].append({"path": str(report_file), **r})

    curated = Path(curated_path)
    if curated.exists():
        results["curated"] = ingest_manifest(
            conn, str(curated), source="curation"
        )

    manifest = Path(manifest_path)
    if manifest.exists():
        results["manifests"].append(
            ingest_manifest(conn, str(manifest), source="export")
        )

    staged = Path(staged_path)
    if staged.exists():
        results["staged"] = ingest_manifest(
            conn, str(staged), source="staged"
        )

    md_cache = Path("artifacts/markdown-cache")
    if md_cache.exists():
        for md_file in sorted(md_cache.glob("*.md")):
            stem = md_file.stem
            paper = conn.execute(
                "SELECT arxiv_id, resolved_id FROM papers WHERE resolved_id = ? OR arxiv_id = ?",
                (stem, stem),
            ).fetchone()
            if paper:
                text = md_file.read_text(encoding="utf-8")
                sha = hashlib.sha256(text.encode()).hexdigest()
                upsert_extraction(conn, ExtractionRecord(
                    arxiv_id=paper["arxiv_id"],
                    resolved_id=paper["resolved_id"],
                    extraction_type="pdf_to_markdown",
                    status="completed",
                    input_path=str(cache_file_path(DEFAULT_PDF_CACHE_DIR, {"resolved_id": paper["resolved_id"]})),
                    output_path=str(md_file),
                    output_sha256=sha,
                    char_count=len(text),
                    extractor="pymupdf4llm",
                    completed_at=_now_iso(),
                ))
                results["extractions"] += 1

    return results


def build_staged_manifest_from_db(
    conn: sqlite3.Connection,
    device_id: int,
    cache_dir: str = DEFAULT_PDF_CACHE_DIR,
) -> dict:
    pending = get_pending_sync(conn, device_id)
    if not pending:
        return {"entries": [], "selected_count": 0}

    from booxdrop_cli import DEFAULT_STORAGE_ROOT

    storage_root = DEFAULT_STORAGE_ROOT
    entries = []
    categories: dict[str, dict[str, list[str]]] = {}

    for paper in pending:
        paper_record = conn.execute(
            "SELECT * FROM papers WHERE arxiv_id = ?", (paper["arxiv_id"],)
        ).fetchone()

        entry = {
            "category": "",
            "section": "",
            "arxiv_id": paper["arxiv_id"],
            "resolved_id": paper_record["resolved_id"],
            "title": paper_record["title"],
            "authors": json.loads(paper_record["authors"]),
            "summary": paper_record["summary"],
            "pdf_url": paper_record["pdf_url"],
            "abs_url": paper_record["abs_url"],
            "published": paper_record["published"],
            "updated": paper_record["updated"],
            "primary_category": paper_record["primary_category"],
            "suggested_filename": paper_record["suggested_filename"],
            "target_path": paper["target_path"],
        }
        if paper_record["citation_count"] is not None:
            entry["citation_count"] = paper_record["citation_count"]
        if paper_record["influential_citation_count"] is not None:
            entry["influential_citation_count"] = paper_record["influential_citation_count"]
        if paper_record["citation_source_url"]:
            entry["citation_source_url"] = paper_record["citation_source_url"]

        entry["local_pdf_path"] = str(cache_file_path(cache_dir, entry))

        curation = conn.execute(
            "SELECT category, section FROM curation_decisions WHERE arxiv_id = ?",
            (paper["arxiv_id"],),
        ).fetchone()
        if curation:
            entry["category"] = curation["category"]
            entry["section"] = curation["section"]
            cat_name = curation["category"]
            cat = categories.setdefault(cat_name, {"physical_targets": [], "shelf_targets": []})
            cat["physical_targets"].append(entry["target_path"])
            cat["shelf_targets"].append(entry["target_path"])

        entries.append(entry)

    return {
        "generated_at": _now_iso(),
        "source": "arxiv-radar-db-sync",
        "storage_root": storage_root,
        "selected_count": len(entries),
        "entries": entries,
        "sync_contract": {
            "kind": "library_sync_contract",
            "version": 1,
            "storage_root": storage_root,
            "categories": categories,
        },
    }
