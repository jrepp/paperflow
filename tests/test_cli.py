from __future__ import annotations

import sys
from pathlib import Path
import json

from typer.testing import CliRunner

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from paperflow_cli import app
from booxdrop_cli import merge_manual_library_into_manifest


runner = CliRunner()


def test_paperflow_root_is_project_centric():
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "Project-centric research paper discovery" in result.output
    assert "publish" in result.output
    assert "radar" in result.output
    assert "device" in result.output


def test_publish_group_exposes_planning_pipeline():
    result = runner.invoke(app, ["publish", "--help"])

    assert result.exit_code == 0
    assert "corpus" in result.output
    assert "threads" in result.output
    assert "queue" in result.output
    assert "issue" in result.output


def test_publish_corpus_command_renders_pipeline(tmp_path):
    manifest = tmp_path / "manifest.json"
    corpus = tmp_path / "corpus.json"
    manifest.write_text(
        json.dumps(
            {
                "source": "arxiv",
                "entries": [
                    {
                        "arxiv_id": "2401.00001",
                        "title": "Pretty Pipeline Output",
                        "summary": "A test record.",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "publish",
            "corpus",
            str(manifest),
            "--output",
            str(corpus),
        ],
    )

    assert result.exit_code == 0
    assert "Publishing Corpus" in result.output
    assert "deduplicate papers" in result.output
    assert "paper_count" in result.output
    assert corpus.exists()


def test_publish_queue_list_empty_is_pretty(tmp_path):
    queue = tmp_path / "queue.json"

    result = runner.invoke(
        app,
        [
            "publish",
            "queue",
            "list",
            "--queue-path",
            str(queue),
        ],
    )

    assert result.exit_code == 0
    assert "Periodical Queue" in result.output
    assert "queue is empty" in result.output


def test_publish_queue_add_editorial(tmp_path):
    queue = tmp_path / "queue.json"

    result = runner.invoke(
        app,
        [
            "publish",
            "queue",
            "add-editorial",
            "--queue-path",
            str(queue),
            "--title",
            "The Research Radar House Style",
            "--issue",
            "1",
            "--thesis",
            "Establish the voice and visual grammar.",
            "--section",
            "Typography",
            "--section",
            "Voice",
        ],
    )

    assert result.exit_code == 0
    assert "Queued Editorial Issue" in result.output
    assert "The Research Radar House Style" in result.output
    assert queue.exists()


def test_manual_library_merges_into_manifest(tmp_path):
    library = tmp_path / "research-radar-library.json"
    library.write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "category": "AI",
                        "section": "manual",
                        "arxiv_id": "2605.15184",
                        "title": "Is Grep All You Need?",
                        "target_path": "/storage/emulated/0/Books/AI/Research radar/grep.pdf",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    manifest = {
        "storage_root": "/storage/emulated/0/Books",
        "selected_count": 0,
        "entries": [],
        "sync_contract": {
            "kind": "library_sync_contract",
            "version": 1,
            "storage_root": "/storage/emulated/0/Books",
            "categories": {},
        },
    }

    merged = merge_manual_library_into_manifest(manifest, library_path=str(library))

    assert merged["selected_count"] == 1
    assert merged["entries"][0]["arxiv_id"] == "2605.15184"
    assert merged["sync_contract"]["categories"]["AI"]["physical_targets"] == [
        "/storage/emulated/0/Books/AI/Research radar/grep.pdf"
    ]
