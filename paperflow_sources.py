from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime

from paperflow_radar import normalize_arxiv_id


@dataclass(frozen=True)
class SourceFetchSpec:
    source: str
    category_name: str
    query: str
    target_path: str
    since: str | None = None
    limit: int | None = None
    cursor: str | None = None


@dataclass(frozen=True)
class SourceFetchResult:
    source: str
    source_url: str
    source_date: str | None
    records: list[dict]
    next_cursor: str | None
    refreshed_at: str


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def query_hash(payload: dict) -> str:
    normalized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def normalize_doi(value: str | None) -> str:
    value = str(value or "").strip().lower()
    value = re.sub(r"^https?://(dx\.)?doi\.org/", "", value)
    return value


def paper_key_for_record(record: dict) -> str:
    existing = str(record.get("paper_key") or "").strip()
    if existing:
        return existing
    arxiv_id = normalize_arxiv_id(str(record.get("arxiv_id") or "").strip())
    if arxiv_id:
        return f"arxiv:{arxiv_id}"
    doi = normalize_doi(record.get("doi"))
    if doi:
        return f"doi:{doi}"
    corpus_id = str(record.get("corpus_id") or "").strip()
    if corpus_id:
        return f"s2:{corpus_id}"
    abs_url = str(record.get("abs_url") or record.get("pdf_url") or "").strip()
    if abs_url:
        digest = hashlib.sha256(abs_url.encode("utf-8")).hexdigest()[:24]
        return f"url:{digest}"
    title = " ".join(str(record.get("title") or "").lower().split())
    digest = hashlib.sha256(title.encode("utf-8")).hexdigest()[:24]
    return f"title:{digest}"


def normalize_record_identity(record: dict, *, source: str) -> dict:
    normalized = dict(record)
    arxiv_id = normalize_arxiv_id(str(normalized.get("arxiv_id") or "").strip())
    if arxiv_id:
        normalized["arxiv_id"] = arxiv_id
    normalized["paper_key"] = paper_key_for_record(normalized)

    source_ids = normalized.get("source_ids")
    if not isinstance(source_ids, dict):
        source_ids = {}
    if arxiv_id:
        source_ids.setdefault("arxiv", arxiv_id)
    doi = normalize_doi(normalized.get("doi"))
    if doi:
        normalized["doi"] = doi
        source_ids.setdefault("doi", doi)
    if normalized.get("semantic_scholar_id"):
        source_ids.setdefault("semantic_scholar", normalized["semantic_scholar_id"])
    if normalized.get("corpus_id"):
        source_ids.setdefault("semantic_scholar_corpus", str(normalized["corpus_id"]))
    source_ids.setdefault(source, normalized["paper_key"])
    normalized["source_ids"] = source_ids

    metadata = normalized.get("source_metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    metadata.setdefault(source, {})
    normalized["source_metadata"] = metadata
    return normalized
