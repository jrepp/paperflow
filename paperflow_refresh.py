from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from booxdrop_cli import (
    build_radar_category_report,
    latest_radar_report_path,
    load_radar_config,
    load_radar_report,
)
from paperflow_radar import write_radar_outputs
from paperflow_sources import normalize_record_identity, query_hash
from paperflow_sources_huggingface import build_report as build_huggingface_report
from radar_db import (
    DEFAULT_DB_PATH,
    SourceRefreshRecord,
    get_db,
    ingest_radar_report,
    init_db,
    upsert_source_refresh,
)

DEFAULT_REFRESH_LOCK_PATH = "artifacts/locks/radar-refresh.lock"
DEFAULT_REFRESH_STATUS_PATH = "artifacts/radar-refresh-status.json"
DEFAULT_REFRESH_LOG_DIR = "artifacts/logs"


@dataclass
class RefreshSourceConfig:
    name: str
    enabled: bool = True
    strict: bool = False
    options: dict[str, Any] = field(default_factory=dict)


@dataclass
class RefreshRunResult:
    exit_code: int
    status_path: str
    report_path: str = ""
    locked: bool = False
    sources: list[dict] = field(default_factory=list)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class RefreshLogger:
    def __init__(self, log_dir: str) -> None:
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y-%m-%d")
        self.path = self.log_dir / f"radar-refresh-{stamp}.log"
        self.latest_path = self.log_dir / "radar-refresh-latest.log"

    def write(self, event: str, **fields: object) -> None:
        payload = {"ts": _now_iso(), "event": event, **fields}
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True) + "\n")
        shutil.copyfile(self.path, self.latest_path)


class RefreshLock:
    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self.acquired = False

    def __enter__(self) -> "RefreshLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            if self._is_live_lock():
                return self
            self.path.unlink(missing_ok=True)
            fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps({"pid": os.getpid(), "created_at": _now_iso()}) + "\n")
        self.acquired = True
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.acquired:
            self.path.unlink(missing_ok=True)

    def _is_live_lock(self) -> bool:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            pid = int(payload.get("pid") or 0)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return False
        if pid <= 0:
            return False
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True


def _load_raw_config(config_path: str) -> dict:
    raw = yaml.safe_load(Path(config_path).read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError("radar config must be a YAML object")
    return raw


def _configured_sources(raw_config: dict) -> list[RefreshSourceConfig]:
    sources_raw = raw_config.get("sources")
    if not isinstance(sources_raw, dict):
        return [RefreshSourceConfig("arxiv", enabled=True)]
    configs = []
    for name, value in sources_raw.items():
        if value is None:
            value = {}
        if not isinstance(value, dict):
            raise ValueError(f"sources.{name} must be an object")
        configs.append(
            RefreshSourceConfig(
                name=str(name),
                enabled=bool(value.get("enabled", True)),
                strict=bool(value.get("strict", False)),
                options=value,
            )
        )
    return configs


def _filter_sources(
    configs: list[RefreshSourceConfig],
    *,
    requested: list[str],
    skipped: list[str],
) -> list[RefreshSourceConfig]:
    if requested:
        wanted = set(requested)
        selected = [config for config in configs if config.name in wanted]
    else:
        selected = [config for config in configs if config.enabled]
    if skipped:
        skip = set(skipped)
        selected = [config for config in selected if config.name not in skip]
    return selected


def _normalize_report(report: dict, *, source: str) -> dict:
    for category in report.get("categories", []):
        for section in ("recent", "highly_cited"):
            category[section] = [
                normalize_record_identity(item, source=source)
                for item in category.get(section, [])
                if isinstance(item, dict)
            ]
    return report


def _merge_reports(reports: list[dict], source_status: list[dict]) -> dict:
    generated_at = _now_iso()
    merged = {
        "generated_at": generated_at,
        "source": "multi-source",
        "source_status": source_status,
        "storage_root": reports[0].get("storage_root", "") if reports else "",
        "lookback_days": reports[0].get("lookback_days", 1) if reports else 1,
        "categories": [],
    }
    categories: dict[str, dict] = {}
    for report in reports:
        for category in report.get("categories", []):
            name = category.get("name", "")
            block = categories.setdefault(
                name,
                {
                    "name": name,
                    "query": category.get("query", ""),
                    "target_path": category.get("target_path", ""),
                    "recent": [],
                    "highly_cited": [],
                },
            )
            for section in ("recent", "highly_cited"):
                seen = {item.get("paper_key") or item.get("arxiv_id") for item in block[section]}
                for item in category.get(section, []):
                    key = item.get("paper_key") or item.get("arxiv_id")
                    if key not in seen:
                        block[section].append(item)
                        seen.add(key)
    merged["categories"] = list(categories.values())
    return merged


def _record_refresh_state(
    *,
    db_path: str,
    source: str,
    report: dict | None,
    status: str,
    error_msg: str | None = None,
) -> None:
    init_db(db_path)
    with get_db(db_path) as conn:
        if report is not None and status == "completed":
            for category in report.get("categories", []):
                count = len(category.get("recent", [])) + len(category.get("highly_cited", []))
                upsert_source_refresh(
                    conn,
                    SourceRefreshRecord(
                        source=source,
                        category=category.get("name", ""),
                        query_hash=query_hash(
                            {
                                "source": source,
                                "category": category.get("name", ""),
                                "query": category.get("query", ""),
                                "target_path": category.get("target_path", ""),
                            }
                        ),
                        refreshed_at=_now_iso(),
                        status=status,
                        item_count=count,
                        error_msg=error_msg,
                    ),
                )


def _ingest_report(db_path: str, report_path: Path) -> int:
    init_db(db_path)
    with get_db(db_path) as conn:
        result = ingest_radar_report(conn, str(report_path))
    return int(result.get("paper_count") or 0)


def _build_arxiv_refresh(config_path: str) -> dict:
    spec = load_radar_config(config_path)
    report = {
        "generated_at": _now_iso(),
        "source": "arxiv",
        "lookback_days": spec.lookback_days,
        "storage_root": spec.storage_root,
        "categories": [],
    }
    for category in spec.categories:
        report["categories"].append(build_radar_category_report(spec, category))
    return _normalize_report(report, source="arxiv")


def _build_huggingface_refresh(config: RefreshSourceConfig, raw_config: dict) -> dict:
    spec = load_radar_config(raw_config.get("_config_path", "arxiv-radar.yaml"))
    options = config.options
    report = build_huggingface_report(
        date=options.get("date"),
        storage_root=str(raw_config.get("storage_root") or spec.storage_root),
        category_name=str(options.get("category_name") or "AI"),
        target_path=str(options.get("target_path") or "AI/Hugging Face Papers"),
        limit=options.get("limit"),
        min_upvotes=options.get("min_upvotes"),
    )
    return _normalize_report(report, source="huggingface_papers")


def run_refresh(
    *,
    config_path: str,
    output_dir: str | None = None,
    db_path: str = DEFAULT_DB_PATH,
    requested_sources: list[str] | None = None,
    skipped_sources: list[str] | None = None,
    offline: bool = False,
    update_only: bool = False,
    fail_on_lock: bool = False,
    lock_path: str = DEFAULT_REFRESH_LOCK_PATH,
    status_path: str = DEFAULT_REFRESH_STATUS_PATH,
    log_dir: str = DEFAULT_REFRESH_LOG_DIR,
) -> RefreshRunResult:
    del update_only
    started_at = _now_iso()
    logger = RefreshLogger(log_dir)
    logger.write("refresh_started", config=config_path, offline=offline)
    status_target = Path(status_path)
    status_target.parent.mkdir(parents=True, exist_ok=True)

    with RefreshLock(lock_path) as lock:
        if not lock.acquired:
            exit_code = 75 if fail_on_lock else 0
            status = {
                "started_at": started_at,
                "finished_at": _now_iso(),
                "exit_code": exit_code,
                "locked": True,
                "sources": [],
                "new_count": 0,
                "updated_count": 0,
                "error_count": 0,
                "report_path": "",
            }
            status_target.write_text(json.dumps(status, indent=2) + "\n", encoding="utf-8")
            logger.write("refresh_locked", exit_code=exit_code)
            return RefreshRunResult(exit_code=exit_code, status_path=str(status_target), locked=True)

        raw_config = _load_raw_config(config_path)
        raw_config["_config_path"] = config_path
        spec = load_radar_config(config_path)
        resolved_output_dir = output_dir or spec.output_dir
        source_configs = _filter_sources(
            _configured_sources(raw_config),
            requested=requested_sources or [],
            skipped=skipped_sources or [],
        )
        if not source_configs:
            raise ValueError("no enabled refresh sources selected")

        reports: list[dict] = []
        source_status: list[dict] = []
        error_count = 0
        success_count = 0

        if offline:
            report_path = latest_radar_report_path(resolved_output_dir)
            report = load_radar_report(str(report_path))
            reports.append(_normalize_report(report, source=str(report.get("source") or "arxiv")))
            source_status.append(
                {"source": "offline-cache", "status": "completed", "report_path": str(report_path)}
            )
            success_count = 1
            logger.write("source_offline_cache", report_path=str(report_path))
        else:
            for source_config in source_configs:
                source = source_config.name
                try:
                    if source == "arxiv":
                        report = _build_arxiv_refresh(config_path)
                    elif source == "huggingface_papers":
                        report = _build_huggingface_refresh(source_config, raw_config)
                    else:
                        raise ValueError(f"source '{source}' is configured but no adapter is available")
                    reports.append(report)
                    count = sum(
                        len(category.get("recent", [])) + len(category.get("highly_cited", []))
                        for category in report.get("categories", [])
                    )
                    _record_refresh_state(
                        db_path=db_path,
                        source=source,
                        report=report,
                        status="completed",
                    )
                    source_status.append(
                        {"source": source, "status": "completed", "item_count": count}
                    )
                    success_count += 1
                    logger.write("source_completed", source=source, item_count=count)
                except Exception as exc:
                    error_count += 1
                    status = {
                        "source": source,
                        "status": "failed",
                        "strict": source_config.strict,
                        "error_msg": str(exc),
                    }
                    source_status.append(status)
                    logger.write("source_failed", **status)

        strict_failed = any(item.get("strict") and item.get("status") == "failed" for item in source_status)
        exit_code = 1 if strict_failed or success_count == 0 else 0
        merged_report = _merge_reports(reports, source_status)
        json_path, md_path = write_radar_outputs(merged_report, resolved_output_dir, prefix="research-radar")
        ingested_count = _ingest_report(db_path, json_path) if exit_code == 0 else 0
        logger.write("refresh_outputs_written", json_path=str(json_path), markdown_path=str(md_path))

        status = {
            "started_at": started_at,
            "finished_at": _now_iso(),
            "exit_code": exit_code,
            "sources": source_status,
            "new_count": 0,
            "updated_count": ingested_count,
            "error_count": error_count,
            "report_path": str(json_path),
        }
        status_target.write_text(json.dumps(status, indent=2) + "\n", encoding="utf-8")
        logger.write("refresh_finished", exit_code=exit_code, status_path=str(status_target))
        return RefreshRunResult(
            exit_code=exit_code,
            status_path=str(status_target),
            report_path=str(json_path),
            sources=source_status,
        )
