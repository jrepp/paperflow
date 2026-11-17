"""Static published-site emitter: channels with a balanced decay policy.

Renders a standalone static site. Each ingestion channel contributes its freshest items up to
a balanced per-channel cap; items older than the decay window leave the
channels; the front page interleaves channels so no single source dominates.
Output is one self-contained index.html plus build.json carrying the build
identity, the applied policy, and per-channel counts.
"""
from __future__ import annotations

import html
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from paperflow_settings import PublishingSettings, load_research_settings
from paperflow_storage_sqlite import SQLiteResearchStorage

DEFAULT_WINDOW_HOURS = 168
DEFAULT_PER_CHANNEL_LIMIT = 20
_SCAN_LIMIT = 400


def build_site(config_path: str, out_dir: str | Path, *, window_hours: int = DEFAULT_WINDOW_HOURS,
               per_channel_limit: int = DEFAULT_PER_CHANNEL_LIMIT, now: datetime | None = None,
               workspace_url: str | None = None) -> dict:
    """Load the configured research storage and render the static bundle."""
    overrides = {"publishing": {"workspace_url": workspace_url}} if workspace_url is not None else {}
    settings = load_research_settings(config_path, **overrides)
    return build_site_from_storage(SQLiteResearchStorage(settings.storage), out_dir,
                                   window_hours=window_hours, per_channel_limit=per_channel_limit, now=now,
                                   workspace_url=settings.publishing.workspace_url)


def build_site_from_storage(storage: SQLiteResearchStorage, out_dir: str | Path, *,
                            window_hours: int = DEFAULT_WINDOW_HOURS,
                            per_channel_limit: int = DEFAULT_PER_CHANNEL_LIMIT,
                            now: datetime | None = None, workspace_url: str | None = None) -> dict:
    """Render the published bundle from an existing research storage."""
    workspace_url = PublishingSettings(workspace_url=workspace_url).workspace_url
    if not 1 <= window_hours <= 24 * 30:
        raise ValueError("window_hours outside 1..720")
    if not 1 <= per_channel_limit <= 200:
        raise ValueError("per_channel_limit outside 1..200")
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=window_hours)
    sections: list[dict] = []
    with storage.transaction() as repo:
        for row in repo.reader_channels():
            name = row["channel"]
            picked: list[tuple[datetime, object]] = []
            stale_streak = 0
            for item in repo.items(channel=name, limit=200):
                when = item.published_at or item.created_at
                if when.tzinfo is None:
                    when = when.replace(tzinfo=timezone.utc)
                if when < cutoff:
                    stale_streak += 1
                    if stale_streak >= 10:
                        break
                    continue
                stale_streak = 0
                picked.append((when, item))
                if len(picked) >= per_channel_limit:
                    break
            if picked:
                picked.sort(key=lambda pair: pair[0], reverse=True)
                sections.append({"name": name, "items": picked})
    sections.sort(key=lambda section: section["name"])
    build_id = now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    manifest = {
        "build_id": build_id,
        "created_utc": now.astimezone(timezone.utc).isoformat(),
        "epoch": int(now.timestamp()),
        "policy": {"window_hours": window_hours, "per_channel_limit": per_channel_limit},
        "channels": [{"name": section["name"], "count": len(section["items"])} for section in sections],
        "total_items": sum(len(section["items"]) for section in sections),
    }
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "index.html").write_text(_render(sections, manifest, workspace_url=workspace_url), encoding="utf-8")
    (out / "build.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def _balanced(sections: list[dict], limit: int = 30) -> list[tuple]:
    """Interleave channels round-robin so the top of the page shares exposure."""
    queues = [list(section["items"]) for section in sections]
    merged: list[tuple] = []
    while queues and len(merged) < limit:
        for queue in list(queues):
            if not queue:
                queues.remove(queue)
                continue
            merged.append(queue.pop(0))
            if len(merged) >= limit:
                break
    return merged


def _age_text(when: datetime, now: datetime) -> str:
    seconds = max(0, int((now - when).total_seconds()))
    if seconds < 3600:
        return f"{max(1, seconds // 60)}m ago"
    if seconds < 86400:
        return f"{seconds // 3600}h ago"
    return f"{seconds // 86400}d ago"


def _host(url: str | None) -> str:
    if not url:
        return ""
    rest = str(url).split("://", 1)[-1]
    return rest.split("/", 1)[0]


def _item_li(pair: tuple[datetime, object], now: datetime) -> str:
    when, item = pair
    url = html.escape(str(item.canonical_url or ""))
    host = html.escape(_host(str(item.canonical_url or "")))
    title = html.escape(item.title)
    age = html.escape(_age_text(when, now))
    ref = f'<a href="{url}">{title}</a>' if url else title
    meta = f" <span class=\"meta\">{host} · {age}</span>" if host else f" <span class=\"meta\">{age}</span>"
    return f"<li>{ref}{meta}</li>"


def _render(sections: list[dict], manifest: dict, *, workspace_url: str | None = None) -> str:
    now = datetime.fromtimestamp(manifest["epoch"], tz=timezone.utc)
    esc = html.escape
    latest = _balanced(sections)
    parts = [
        "<!doctype html><meta charset=\"utf-8\">",
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">",
        "<title>Paperflow — published channels</title>",
        "<style>",
        "body{font:16px/1.6 system-ui,sans-serif;margin:0 auto;max-width:46rem;padding:1rem 1.25rem;color:#1a1a1a}",
        "a{color:#0a5ad6;text-decoration:none}a:hover{text-decoration:underline}",
        "h1{font-size:1.4rem;margin:.2rem 0}h2{font-size:1.05rem;margin:1.6rem 0 .4rem;color:#444}",
        "ol,ul{padding-left:1.2rem}.meta{color:#777;font-size:.85rem}",
        ".sub{color:#666;margin:.1rem 0 0}.foot{margin-top:2rem;color:#888;font-size:.85rem;border-top:1px solid #ddd;padding-top:.6rem}",
        "</style>",
        "<h1>Paperflow</h1><p class=\"sub\">Published paperflow channels — balanced decay, refreshed every publish.</p>",
    ]
    if latest:
        parts.append("<h2>Latest</h2><ol>" + "".join(_item_li(pair, now) for pair in latest) + "</ol>")
    for section in sections:
        name = esc(section["name"])
        parts.append(f"<h2>{name} <span class=\"meta\">({len(section['items'])})</span></h2><ol>"
                     + "".join(_item_li(pair, now) for pair in section["items"]) + "</ol>")
    if not latest:
        parts.append("<p>No published items inside the decay window yet.</p>")
    workspace_link = f' · <a href="{esc(workspace_url, quote=True)}">Open workspace</a>' if workspace_url else ""
    policy = manifest["policy"]
    parts.append("<p class=\"foot\">Build " + esc(manifest["build_id"])
                 + " · window " + str(policy["window_hours"]) + "h, cap " + str(policy["per_channel_limit"])
                 + " per channel" + workspace_link + "</p>")
    return "\n".join(parts)
