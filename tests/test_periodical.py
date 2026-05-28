from __future__ import annotations

import json
import asyncio
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import paperflow_periodical as periodical


def _manifest(path: Path) -> Path:
    payload = {
        "generated_at": "2026-05-27T12:00:00Z",
        "entries": [
            {
                "arxiv_id": "2604.04916v1",
                "resolved_id": "2604.04916v1",
                "title": "First Paper",
                "category": "AI",
                "published": "2026-04-01T00:00:00Z",
                "citation_count": 12,
                "authors": ["A. Author", "B. Author"],
                "abs_url": "https://arxiv.org/abs/2604.04916",
                "pdf_url": "https://arxiv.org/pdf/2604.04916",
            },
            {
                "arxiv_id": "2604.04921v1",
                "resolved_id": "2604.04921v1",
                "title": "Second Paper",
                "category": "Data",
                "published": "2026-04-02T00:00:00Z",
                "citation_count": 5,
                "authors": ["C. Author"],
                "abs_url": "https://arxiv.org/abs/2604.04921",
                "pdf_url": "https://arxiv.org/pdf/2604.04921",
            },
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _summary(entry: dict) -> dict:
    return {
        "results_summary": f"Summary for {entry['title']}.",
        "intake_framing": "Read this for the central result.",
        "area_of_progress": "Evaluation",
        "citation_signal": "Early citation signal.",
        "source_basis": "metadata_only",
        "confidence": "medium",
        "confidence_rationale": "Test summary.",
        "primary_claims": ["Claim"],
        "required_background": ["Background"],
        "background_research": [],
        "open_questions": ["Question"],
    }


def test_build_periodical_writes_project_metadata_and_chapters(tmp_path, monkeypatch):
    manifest_path = _manifest(tmp_path / "manifest.json")
    periodical_dir = tmp_path / "tex" / "research-radar"
    stale = periodical_dir / "chapters" / "stale.tex"
    stale.parent.mkdir(parents=True)
    stale.write_text(f"{periodical.GENERATED_HEADER}\nold", encoding="utf-8")
    previous_unmarked = periodical_dir / "chapters" / "old-unmarked.tex"
    previous_unmarked.write_text("old generator output", encoding="utf-8")

    async def fake_prime_cache(*args, **kwargs):
        return None

    async def fake_summarize_paper_entry(entry, **kwargs):
        return _summary(entry)

    async def fake_summarize_category_section(category, items, **kwargs):
        return {
            "executive_summary": f"{category} overview.",
            "shared_themes": ["Theme"],
            "intake_priorities": ["Priority"],
        }

    def fake_run(*args, **kwargs):
        dist = periodical_dir.parent / "dist"
        dist.mkdir(parents=True, exist_ok=True)
        (dist / "research-radar.pdf").write_bytes(b"%PDF-1.4\n")
        return subprocess.CompletedProcess(args=args[0], returncode=0, stdout="", stderr="")

    monkeypatch.setattr(periodical, "prime_cache", fake_prime_cache)
    monkeypatch.setattr(periodical, "summarize_paper_entry", fake_summarize_paper_entry)
    monkeypatch.setattr(periodical, "summarize_category_section", fake_summarize_category_section)
    monkeypatch.setattr(periodical.subprocess, "run", fake_run)

    result = asyncio.run(
        periodical.build_periodical(
            str(manifest_path),
            cache_dir=str(tmp_path / "pdf-cache"),
            model="test/model",
            variant=None,
            summary_cache_dir=str(tmp_path / "summary-cache"),
            markdown_cache_dir=str(tmp_path / "markdown-cache"),
            prompt_version=3,
            title="Test Radar",
            max_papers=None,
            periodical_dir=str(periodical_dir),
            reference_depth=0,
            db_path=None,
        )
    )

    metadata = json.loads((periodical_dir / "build-metadata.json").read_text(encoding="utf-8"))
    assert metadata["paper_count"] == 2
    assert metadata["chapter_count"] == 3
    assert metadata["tex_class"] == "research-radar"
    assert metadata["series"] == "Research Radar"
    assert metadata["focus"] == "2604.04916v1"
    assert metadata["supporting_papers"] == ["2604.04921v1"]
    assert result["build_metadata"] == str(periodical_dir / "build-metadata.json")
    assert not stale.exists()
    assert not previous_unmarked.exists()
    assert (periodical_dir / "research-radar.tex").exists()
    assert (periodical_dir / "chapters" / "focal-paper.tex").exists()
    assert (periodical_dir / "chapters" / "supporting-context.tex").exists()


def test_render_main_tex_uses_research_radar_class():
    tex = periodical.render_periodical_main_tex(
        title="Test Radar",
        series="Research Radar",
        issue=7,
        focus_title="A Durable Paper",
        chapter_includes=["executive-summary", "AI"],
    )

    assert r"\documentclass{research-radar}" in tex
    assert r"\reporttitle{Research Radar Issue 7}" in tex
    assert "A Durable Paper" in tex
    assert r"\include{chapters/executive-summary}" in tex
    assert r"\include{chapters/AI}" in tex


def test_topic_proposals_and_queue_items(tmp_path):
    manifest_path = _manifest(tmp_path / "manifest.json")
    topics = periodical.propose_periodical_topics(
        str(manifest_path),
        limit=2,
        supporting_papers=1,
    )

    assert topics[0]["focus"] == "2604.04916v1"
    assert topics[0]["supporting_papers"][0]["id"] == "2604.04921v1"

    queue_path = tmp_path / "periodical-queue.json"
    item = periodical.add_periodical_queue_item(
        manifest_path=str(manifest_path),
        queue_path=str(queue_path),
        focus="2604.04916v1",
        title="Evergreen Test Issue",
        series="Research Radar",
        issue=3,
        supporting_papers=1,
    )

    assert item["id"] == "3-2604.04916v1"
    assert item["title"] == "Evergreen Test Issue"
    assert periodical.get_periodical_queue_item(item["id"], str(queue_path))["issue"] == 3


def test_select_entries_by_ids_preserves_queue_order(tmp_path):
    manifest_path = _manifest(tmp_path / "manifest.json")
    manifest = periodical.load_manifest(str(manifest_path))
    entries = manifest["entries"]

    selected = periodical.select_entries_by_ids(
        entries,
        ["2604.04921v1", "2604.04916v1"],
    )

    assert [entry["arxiv_id"] for entry in selected] == [
        "2604.04921v1",
        "2604.04916v1",
    ]
