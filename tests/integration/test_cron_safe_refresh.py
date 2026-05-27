from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from paperflow_refresh import run_refresh

container_module = pytest.importorskip("testcontainers.core.container")
DockerContainer = container_module.DockerContainer


def _write_config(tmp_path: Path) -> Path:
    output_dir = tmp_path / "radar-output"
    config_path = tmp_path / "arxiv-radar.yaml"
    config_path.write_text(
        "\n".join(
            [
                f"output_dir: {output_dir}",
                "storage_root: /storage/emulated/0/Books",
                "lookback_days: 7",
                "recent_limit: 1",
                "cited_limit: 1",
                "citation_candidate_limit: 1",
                "sources:",
                "  arxiv:",
                "    enabled: true",
                "    strict: true",
                "categories:",
                "  AI:",
                "    query: cat:cs.AI",
                "    target_path: AI/Research radar",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return config_path


def _write_cached_report(tmp_path: Path) -> Path:
    output_dir = tmp_path / "radar-output"
    output_dir.mkdir(parents=True)
    report_path = output_dir / "arxiv-radar-2026-05-26.json"
    report_path.write_text(
        json.dumps(
            {
                "generated_at": "2026-05-26T12:00:00+00:00",
                "source": "arxiv",
                "lookback_days": 7,
                "storage_root": "/storage/emulated/0/Books",
                "categories": [
                    {
                        "name": "AI",
                        "query": "cat:cs.AI",
                        "target_path": "AI/Research radar",
                        "recent": [
                            {
                                "arxiv_id": "2401.12345v2",
                                "resolved_id": "2401.12345v2",
                                "title": "Cron Safe Radar Refresh",
                                "authors": ["A. Researcher"],
                                "published": "2026-05-25T00:00:00Z",
                                "updated": "2026-05-25T00:00:00Z",
                                "primary_category": "cs.AI",
                                "abs_url": "https://arxiv.org/abs/2401.12345",
                                "pdf_url": "https://arxiv.org/pdf/2401.12345.pdf",
                                "summary": "A fixture paper for cron refresh testing.",
                                "suggested_filename": "Cron Safe Radar Refresh.pdf",
                                "target_path": "/storage/emulated/0/Books/AI/Research radar/Cron Safe Radar Refresh.pdf",
                            }
                        ],
                        "highly_cited": [],
                    }
                ],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return report_path


def _container_host_pid(container: DockerContainer) -> int:
    wrapped = container.get_wrapped_container()
    wrapped.reload()
    pid = int(wrapped.attrs["State"]["Pid"])
    if pid <= 0:
        pytest.skip("container host PID is not available")
    return pid


def test_update_only_treats_live_lock_as_clean_noop(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path)
    lock_path = tmp_path / "locks" / "radar-refresh.lock"
    status_path = tmp_path / "radar-refresh-status.json"
    log_dir = tmp_path / "logs"

    try:
        container = DockerContainer("alpine:3.20").with_command("sleep 30")
        with container as running:
            lock_path.parent.mkdir(parents=True)
            lock_path.write_text(
                json.dumps({"pid": _container_host_pid(running)}) + "\n",
                encoding="utf-8",
            )

            result = run_refresh(
                config_path=str(config_path),
                db_path=str(tmp_path / "radar.db"),
                offline=True,
                update_only=True,
                lock_path=str(lock_path),
                status_path=str(status_path),
                log_dir=str(log_dir),
            )
    except Exception as exc:
        pytest.skip(f"Docker is not available for testcontainers: {exc}")

    assert result.exit_code == 0
    assert result.locked is True
    assert lock_path.exists()

    status = json.loads(status_path.read_text(encoding="utf-8"))
    assert status["locked"] is True
    assert status["exit_code"] == 0
    assert status["report_path"] == ""
    assert (log_dir / "radar-refresh-latest.log").exists()


def test_update_only_replaces_stale_lock_and_writes_refresh_state(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path)
    _write_cached_report(tmp_path)
    lock_path = tmp_path / "locks" / "radar-refresh.lock"
    status_path = tmp_path / "radar-refresh-status.json"
    log_dir = tmp_path / "logs"
    db_path = tmp_path / "radar.db"

    lock_path.parent.mkdir(parents=True)
    lock_path.write_text(
        json.dumps({"pid": 999999, "created_at": "2026-05-26T00:00:00+00:00"})
        + "\n",
        encoding="utf-8",
    )

    result = run_refresh(
        config_path=str(config_path),
        db_path=str(db_path),
        offline=True,
        update_only=True,
        lock_path=str(lock_path),
        status_path=str(status_path),
        log_dir=str(log_dir),
    )

    assert result.exit_code == 0
    assert result.locked is False
    assert not lock_path.exists()
    assert Path(result.report_path).exists()
    assert (log_dir / "radar-refresh-latest.log").exists()

    status = json.loads(status_path.read_text(encoding="utf-8"))
    assert status["exit_code"] == 0
    assert status["sources"] == [
        {
            "source": "offline-cache",
            "status": "completed",
            "report_path": str(tmp_path / "radar-output" / "arxiv-radar-2026-05-26.json"),
        }
    ]
    assert status["updated_count"] == 1
    assert status["error_count"] == 0
    assert status["report_path"] == result.report_path

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT arxiv_id, paper_key, source_ids_json FROM papers"
        ).fetchone()
    assert row == (
        "2401.12345",
        "arxiv:2401.12345",
        '{"arxiv": "2401.12345"}',
    )
