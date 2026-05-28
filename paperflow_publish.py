from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from paperflow_sources import normalize_record_identity


DEFAULT_PUBLISHING_CORPUS_PATH = "artifacts/publishing-corpus.json"


def _read_json(path: str) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _record_source(payload: dict[str, Any], path: str) -> str:
    source = str(payload.get("source") or "").strip()
    if source:
        return source
    if "entries" in payload:
        return "manifest"
    return Path(path).stem


def _records_from_radar_report(payload: dict[str, Any]) -> list[dict[str, Any]]:
    records = []
    for category in payload.get("categories") or []:
        if not isinstance(category, dict):
            continue
        category_name = str(category.get("name") or "Uncategorized")
        for section in ["recent", "highly_cited"]:
            for record in category.get(section) or []:
                if isinstance(record, dict):
                    enriched = dict(record)
                    enriched.setdefault("category", category_name)
                    enriched.setdefault("section", section)
                    records.append(enriched)
    return records


def records_from_artifact(path: str) -> list[dict[str, Any]]:
    payload = _read_json(path)
    source = _record_source(payload, path)
    if isinstance(payload.get("entries"), list):
        records = [entry for entry in payload["entries"] if isinstance(entry, dict)]
    else:
        records = _records_from_radar_report(payload)
    normalized = []
    for record in records:
        item = normalize_record_identity(record, source=source)
        sources = item.get("sources")
        if not isinstance(sources, list):
            sources = []
        if source not in sources:
            sources.append(source)
        item["sources"] = sources
        item.setdefault("artifact_paths", [])
        item["artifact_paths"].append(path)
        normalized.append(item)
    return normalized


def _merge_record(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    merged = dict(existing)
    for key, value in incoming.items():
        if key in {"sources", "artifact_paths"}:
            continue
        if value not in (None, "", [], {}):
            if merged.get(key) in (None, "", [], {}):
                merged[key] = value
    merged["sources"] = sorted(set(existing.get("sources", [])) | set(incoming.get("sources", [])))
    merged["artifact_paths"] = sorted(
        set(existing.get("artifact_paths", [])) | set(incoming.get("artifact_paths", []))
    )
    merged["citation_count"] = max(
        int(existing.get("citation_count") or 0),
        int(incoming.get("citation_count") or 0),
    )
    return merged


def build_publishing_corpus(
    artifact_paths: list[str],
    *,
    output_path: str = DEFAULT_PUBLISHING_CORPUS_PATH,
) -> dict[str, Any]:
    papers: dict[str, dict[str, Any]] = {}
    for artifact_path in artifact_paths:
        for record in records_from_artifact(artifact_path):
            key = str(record.get("paper_key") or "")
            if not key:
                continue
            papers[key] = _merge_record(papers[key], record) if key in papers else record
    corpus = {
        "version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "artifact_paths": artifact_paths,
        "paper_count": len(papers),
        "papers": sorted(
            papers.values(),
            key=lambda item: (
                int(item.get("citation_count") or 0),
                str(item.get("published") or ""),
                str(item.get("title") or ""),
            ),
            reverse=True,
        ),
    }
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(corpus, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return corpus


def load_publishing_corpus(corpus_path: str = DEFAULT_PUBLISHING_CORPUS_PATH) -> dict[str, Any]:
    return _read_json(corpus_path)


def _tokenize_topic_text(record: dict[str, Any]) -> list[str]:
    text = " ".join(
        [
            str(record.get("title") or ""),
            str(record.get("summary") or ""),
            " ".join(str(item) for item in (record.get("ai_keywords") or [])),
            str(record.get("primary_category") or ""),
            str(record.get("category") or ""),
        ]
    ).lower()
    raw_tokens = [
        token.strip(".,:;()[]{}!?\"'")
        for token in text.replace("-", " ").replace("/", " ").split()
    ]
    stop = {
        "a",
        "about",
        "across",
        "an",
        "and",
        "are",
        "as",
        "into",
        "by",
        "for",
        "from",
        "in",
        "is",
        "of",
        "on",
        "or",
        "paper",
        "that",
        "this",
        "through",
        "the",
        "their",
        "these",
        "to",
        "using",
        "which",
        "with",
    }
    return [token for token in raw_tokens if len(token) > 3 and token not in stop]


def propose_publishing_threads(
    corpus: dict[str, Any],
    *,
    limit: int = 10,
    papers_per_thread: int = 8,
) -> list[dict[str, Any]]:
    papers = [paper for paper in corpus.get("papers", []) if isinstance(paper, dict)]
    token_to_papers: dict[str, list[dict[str, Any]]] = defaultdict(list)
    token_counts: Counter[str] = Counter()
    for paper in papers:
        tokens = set(_tokenize_topic_text(paper))
        token_counts.update(tokens)
        for token in tokens:
            token_to_papers[token].append(paper)

    topics = []
    for token, count in token_counts.most_common(limit * 4):
        candidates = sorted(
            token_to_papers[token],
            key=lambda item: (
                len(item.get("sources", [])),
                int(item.get("citation_count") or 0),
                str(item.get("published") or ""),
            ),
            reverse=True,
        )[:papers_per_thread]
        if len(candidates) < 2:
            continue
        focus = candidates[0]
        topics.append(
            {
                "id": f"thread-{token}",
                "title": f"{token.title()} as an evergreen research thread",
                "thread": token,
                "paper_count": len(candidates),
                "source_count": len({source for paper in candidates for source in paper.get("sources", [])}),
                "focus": str(focus.get("paper_key") or focus.get("arxiv_id") or ""),
                "focus_title": str(focus.get("title") or "Untitled"),
                "supporting_papers": [
                    {
                        "paper_key": str(paper.get("paper_key") or ""),
                        "arxiv_id": str(paper.get("arxiv_id") or ""),
                        "title": str(paper.get("title") or "Untitled"),
                        "sources": paper.get("sources", []),
                        "citation_count": int(paper.get("citation_count") or 0),
                    }
                    for paper in candidates[1:]
                ],
            }
        )
        if len(topics) >= limit:
            break
    return topics
