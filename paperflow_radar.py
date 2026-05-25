from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath


def sanitize_filename(name: str) -> str:
    name = re.sub(r'[\\/:*?"<>|]+', " ", name)
    name = re.sub(r"\s+", " ", name).strip().rstrip(".")
    return name or "untitled"


def normalize_arxiv_id(arxiv_id: str) -> str:
    return re.sub(r"v\d+$", "", arxiv_id, flags=re.IGNORECASE)


def storage_target_path(
    storage_root: str,
    relative_target_path: str,
    filename: str,
) -> str:
    return str(PurePosixPath(storage_root) / relative_target_path / filename)


def single_category_report(
    *,
    source: str,
    source_url: str,
    source_date: str | None,
    storage_root: str,
    category_name: str,
    query: str,
    target_path: str,
    recent: list[dict],
    highly_cited: list[dict] | None = None,
    lookback_days: int = 1,
) -> dict:
    generated_at = datetime.now(UTC).isoformat()
    return {
        "generated_at": generated_at,
        "source": source,
        "source_url": source_url,
        "source_date": source_date,
        "storage_root": storage_root,
        "lookback_days": lookback_days,
        "categories": [
            {
                "name": category_name,
                "query": query,
                "target_path": target_path,
                "recent": recent,
                "highly_cited": highly_cited or [],
            }
        ],
    }


def radar_markdown(report: dict) -> str:
    lines = [
        f"# Research Radar ({report['generated_at'][:10]})",
        "",
        f"Source: {report.get('source', 'arxiv')}",
        "",
        f"Lookback window: {report['lookback_days']} days",
        "",
    ]
    for category in report["categories"]:
        lines.append(f"## {category['name']}")
        lines.append("")
        lines.append(f"Target path: `{category['target_path']}`")
        lines.append("")
        lines.append("### New This Week")
        lines.append("")
        if category["recent"]:
            for item in category["recent"]:
                score = ""
                if "huggingface_upvotes" in item:
                    score = f", HF upvotes: {item.get('huggingface_upvotes', 0)}"
                lines.append(
                    f"- `{item['arxiv_id']}` {item['title']} ({item['published'][:10]}{score})"
                )
        else:
            lines.append("- None")
        lines.append("")
        lines.append("### Highly Cited")
        lines.append("")
        if category["highly_cited"]:
            for item in category["highly_cited"]:
                lines.append(
                    f"- `{item['arxiv_id']}` {item['title']} (citations: {item.get('citation_count', 0)})"
                )
        else:
            lines.append("- None")
        lines.append("")
    return "\n".join(lines)


def write_radar_outputs(
    report: dict,
    output_dir: str,
    *,
    prefix: str,
) -> tuple[Path, Path]:
    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = str(report.get("source_date") or report["generated_at"][:10])
    json_path = target_dir / f"{prefix}-{stamp}.json"
    md_path = target_dir / f"{prefix}-{stamp}.md"
    json_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    md_path.write_text(radar_markdown(report) + "\n", encoding="utf-8")
    return json_path, md_path


def latest_report_path(output_dir: str, *, prefix: str) -> Path:
    base = Path(output_dir)
    matches = sorted(
        base.glob(f"{prefix}-*.json"), key=lambda path: path.stat().st_mtime
    )
    if not matches:
        raise ValueError(f"no {prefix} report found in {output_dir}")
    return matches[-1]
