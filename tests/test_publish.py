from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from paperflow_publish import build_publishing_corpus, propose_publishing_threads


def test_build_publishing_corpus_dedupes_manifest_and_report(tmp_path):
    manifest = tmp_path / "manifest.json"
    report = tmp_path / "report.json"
    manifest.write_text(
        json.dumps(
            {
                "source": "arxiv",
                "entries": [
                    {
                        "arxiv_id": "2401.00001v1",
                        "title": "Retrieval Evaluation for Agents",
                        "summary": "Retrieval evaluation for agent systems.",
                        "category": "AI",
                        "citation_count": 4,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    report.write_text(
        json.dumps(
            {
                "source": "huggingface-papers",
                "categories": [
                    {
                        "name": "AI",
                        "recent": [
                            {
                                "arxiv_id": "2401.00001",
                                "title": "Retrieval Evaluation for Agents",
                                "summary": "Agent retrieval eval from another source.",
                                "huggingface_upvotes": 20,
                                "citation_count": 20,
                            },
                            {
                                "arxiv_id": "2401.00002",
                                "title": "Retrieval Grounding Benchmarks",
                                "summary": "Benchmarks for retrieval and grounding.",
                                "citation_count": 7,
                            },
                        ],
                        "highly_cited": [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    corpus = build_publishing_corpus(
        [str(manifest), str(report)],
        output_path=str(tmp_path / "corpus.json"),
    )

    assert corpus["paper_count"] == 2
    first = corpus["papers"][0]
    assert first["paper_key"] == "arxiv:2401.00001"
    assert first["citation_count"] == 20
    assert set(first["sources"]) == {"arxiv", "huggingface-papers"}


def test_propose_publishing_threads_groups_cross_corpus_terms(tmp_path):
    corpus = {
        "papers": [
            {
                "paper_key": "a",
                "title": "Retrieval Evaluation for Agents",
                "summary": "retrieval evaluation agents",
                "sources": ["arxiv", "huggingface-papers"],
                "citation_count": 20,
            },
            {
                "paper_key": "b",
                "title": "Retrieval Grounding Benchmarks",
                "summary": "retrieval grounding benchmark",
                "sources": ["semantic_scholar"],
                "citation_count": 10,
            },
            {
                "paper_key": "c",
                "title": "Graph Optimization",
                "summary": "graph optimization",
                "sources": ["arxiv"],
                "citation_count": 1,
            },
        ]
    }

    threads = propose_publishing_threads(corpus, limit=3, papers_per_thread=3)

    retrieval = next(topic for topic in threads if topic["thread"] == "retrieval")
    assert retrieval["focus"] == "a"
    assert retrieval["source_count"] == 3
    assert retrieval["supporting_papers"][0]["paper_key"] == "b"
