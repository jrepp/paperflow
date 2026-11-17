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
from urllib.parse import quote
from datetime import datetime, timedelta, timezone
from pathlib import Path
from importlib.resources import files

from paperflow_domain import ContentRevision
from paperflow_ingest_models import SourceObservation
from paperflow_settings import PublishingSettings, load_research_settings
from paperflow_storage_sqlite import SQLiteResearchStorage

DEFAULT_WINDOW_HOURS = 168
DEFAULT_PER_CHANNEL_LIMIT = 20
_SCAN_LIMIT = 400


def publication_sections(repo, *, now=None, window_hours=DEFAULT_WINDOW_HOURS,
                         per_channel_limit=DEFAULT_PER_CHANNEL_LIMIT):
    """Shared publication selection for rendering and model-work priority."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=window_hours)
    sections = []
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
    return sections


def build_site(config_path: str, out_dir: str | Path, *, window_hours: int = DEFAULT_WINDOW_HOURS,
               per_channel_limit: int = DEFAULT_PER_CHANNEL_LIMIT, now: datetime | None = None,
               workspace_url: str | None = None) -> dict:
    """Load the configured research storage and render the static bundle."""
    overrides = {"publishing": {"workspace_url": workspace_url}} if workspace_url is not None else {}
    settings = load_research_settings(config_path, **overrides)
    return build_site_from_storage(SQLiteResearchStorage(settings.storage), out_dir,
                                   window_hours=window_hours, per_channel_limit=per_channel_limit, now=now,
                                   workspace_url=settings.publishing.workspace_url,
                                   assistant_store=_assistant_store(settings.assistant))


def build_site_from_storage(storage: SQLiteResearchStorage, out_dir: str | Path, *,
                            window_hours: int = DEFAULT_WINDOW_HOURS,
                            per_channel_limit: int = DEFAULT_PER_CHANNEL_LIMIT,
                            now: datetime | None = None, workspace_url: str | None = None,
                            assistant_store=None) -> dict:
    """Render the published bundle from an existing research storage."""
    workspace_url = PublishingSettings(workspace_url=workspace_url).workspace_url
    if not 1 <= window_hours <= 24 * 30:
        raise ValueError("window_hours outside 1..720")
    if not 1 <= per_channel_limit <= 200:
        raise ValueError("per_channel_limit outside 1..200")
    now = now or datetime.now(timezone.utc)
    cards = {}
    with storage.transaction() as repo:
        sections = publication_sections(repo, now=now, window_hours=window_hours,
                                        per_channel_limit=per_channel_limit)
        for section in sections:
            for _, item in section["items"]:
                revision = repo.latest_for_item(ContentRevision, item.id)
                result = assistant_store.analysis(item.id) if assistant_store else None
                cards[item.id] = {
                    "observation": repo.latest_for_item(SourceObservation, item.id),
                    "result": result,
                    "stale": bool(result and revision and str(revision.id) != result["revision_id"]),
                }
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
    (out / "index.html").write_text(_render(sections, manifest, workspace_url=workspace_url, cards=cards), encoding="utf-8")
    (out / "build.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def _assistant_store(settings):
    # Do not create or initialize a private database as a side effect of publishing.
    if settings.enabled and settings.path.is_file():
        from paperflow_assistant.store import AssistantStore
        return AssistantStore(settings.path)
    return None


def _curation_markdown(item, result):
    analysis = result["analysis"]
    lines = [f"# {item.title}", "", "Model-generated curation", "",
             f"Source: {item.canonical_url or 'Unavailable'}",
             f"Category: {analysis['category']}", f"Evidence basis: {analysis['evidence_basis']}",
             f"Source revision: {result['revision_id']}", f"Curation run: {result['run_id']}",
             "", "## Summary", "", analysis["summary"]]

    def citations(values):
        for citation in values:
            lines.extend(["", f"Evidence ({citation['revision_id']}, offset {citation['start']}):",
                          "", "> " + citation["quote"].replace("\n", "\n> ")])

    for key, heading in (("findings", "Findings"), ("biases", "Bias assessments")):
        lines.extend(["", f"## {heading}"])
        for finding in analysis[key]:
            lines.extend(["", f"### {finding['kind'].replace('_', ' ').title()}", "",
                          finding["statement"], "", "Uncertainty: " + finding["uncertainty"]])
            citations(finding["citations"])
    for key, heading in (("limitations", "Limitations"), ("research_gaps", "Research gaps")):
        lines.extend(["", f"## {heading}", "", *["- " + value for value in analysis[key]]])
    lines.extend(["", "## Possible downstream effects"])
    for effect in analysis["effects"]:
        lines.extend(["", f"### Order {effect['order']}: {effect['hypothesis']}", "",
                      "Mechanism: " + " → ".join(effect["mechanism"]),
                      "Assumptions: " + "; ".join(effect["assumptions"]),
                      "Time horizon: " + effect["time_horizon"], "Falsifier: " + effect["falsifier"],
                      "Uncertainty: " + effect["uncertainty"]])
        citations(effect["citations"])
    return "\n".join(lines) + "\n"


def _card(item, card):
    esc = html.escape
    observation = card.get("observation")
    result = card.get("result")
    metadata = [("Type", item.kind), ("Published", (item.published_at or item.created_at).astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))]
    if item.authors:
        metadata.append(("Authors", ", ".join(item.authors)))
    links = []
    if observation:
        entry = observation.entry
        metadata.extend([("Source", observation.channel), ("Stats observed", observation.observed_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))])
        for label, value in (("Score", entry.signals.score), ("Comments", entry.signals.comment_count)):
            if value is not None:
                metadata.append((label, str(value)))
        if entry.discussion_url:
            links.append(f'<a href="{esc(str(entry.discussion_url))}">Discussion</a>')
        if entry.url != item.canonical_url:
            links.append(f'<a href="{esc(str(entry.url))}">Source entry</a>')
    parts = []
    if result:
        if card.get('stale'):
            parts.append('<p class="muted">This curation describes an earlier source revision.</p>')
        parts.append('<div class="model-summary"><h3>Model analysis</h3><p>'
                     + esc(result['analysis']['summary']) + '</p><p class="muted">Evidence: '
                     + esc(result['analysis']['evidence_basis'].replace('_', ' ')) + '</p></div>')
    parts += ['<details class="curation-card"><summary aria-label="Curation &amp; details for '
             + esc(item.title) + '">Curation &amp; details</summary><div class="curation-body">']
    if result:
        analysis = result["analysis"]
        metadata.extend([("Category", analysis["category"]), ("Evidence", analysis["evidence_basis"].replace('_', ' '))])
        parts.append('<h3>Findings</h3><ul>')
        for finding in analysis["findings"]:
            parts.append('<li>' + esc(finding["statement"]) + '<p class="muted">'
                         + esc(finding["kind"].replace('_', ' ') + ' · ' + finding["uncertainty"]) + '</p></li>')
        parts.append('</ul><h3>Limitations</h3><ul>' + ''.join('<li>' + esc(v) + '</li>' for v in analysis['limitations']) + '</ul>')
        links.append(f'<a href="#curation-{item.id}">Read curation Markdown</a>')
    else:
        parts.append('<p class="muted">No model curation available yet.</p>')
        if observation and observation.entry.excerpt:
            parts.append('<h3>Source excerpt</h3><p>' + esc(observation.entry.excerpt[:1200]) + '</p>')
    parts.append('<dl>' + ''.join('<div><dt>' + esc(k) + '</dt><dd>' + esc(v) + '</dd></div>' for k, v in metadata) + '</dl>')
    parts.append('<div class="curation-links">' + ' · '.join(links) + '</div></div></details>')
    return ''.join(parts)


def _document(item, card):
    result = card.get("result")
    if not result:
        return ''
    markdown = _curation_markdown(item, result)
    return (f'<section class="curation-document" id="curation-{item.id}" tabindex="-1" aria-label="Curation Markdown">'
            f'<a href="#item-{item.id}">Back to link</a><h2>{html.escape(item.title)}</h2>'
            f'<a download="curation-{item.id}.md" href="data:text/markdown;charset=utf-8,{quote(markdown, safe="")}">Download Markdown</a>'
            '<pre>' + html.escape(markdown) + '</pre></section>')


def _balanced(sections: list[dict]) -> list[tuple]:
    """Interleave all selected items so each channel shares exposure."""
    queues = [list(section["items"]) for section in sections]
    merged: list[tuple] = []
    while queues:
        for queue in list(queues):
            if not queue:
                queues.remove(queue)
                continue
            merged.append(queue.pop(0))
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


def _item_li(pair: tuple[datetime, object], now: datetime, cards: dict, suffix: str = "") -> str:
    when, item = pair
    url = html.escape(str(item.canonical_url or ""))
    host = html.escape(_host(str(item.canonical_url or "")))
    title = html.escape(item.title)
    age = html.escape(_age_text(when, now))
    ref = f'<a href="{url}">{title}</a>' if url else title
    meta = f"{host} · {age}" if host else age
    return f'<li class="feed-row"><article id="item-{item.id}{suffix}"><div class="meta item-meta">{meta}</div><h2>{ref}</h2>{_card(item, cards.get(item.id, {}))}</article></li>'


def _render(sections: list[dict], manifest: dict, *, workspace_url: str | None = None, cards: dict | None = None) -> str:
    now = datetime.fromtimestamp(manifest["epoch"], tz=timezone.utc)
    cards = cards or {}
    esc = html.escape
    latest = _balanced(sections)
    # Embed the same packaged stylesheet as the interactive reader so published
    # bundles work offline and never depend on a live application's asset route.
    stylesheet = files("paperflow_reader").joinpath("static/reader.css").read_text(encoding="utf-8")
    parts = [
        '<!doctype html><html lang="en"><head>',
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        "<title>Paperflow</title>",
        "<style>" + stylesheet + "</style></head><body>",
        '<a class="skip" href="#main">Skip to content</a>',
        '<header class="masthead"><div class="shell topbar">'
        '<a href="./" class="brand" aria-label="Paperflow home">Paperflow '
        '<span aria-hidden="true">/</span></a></div></header>',
        '<main id="main" class="shell py-8" tabindex="-1">',
        '<div class="feed-toolbar"><h1>Latest</h1></div>',
        '<p class="muted">Recent reading from all sources.</p>',
    ]
    if latest:
        rows, seen = [], set()
        for pair in latest:
            item_id = pair[1].id
            suffix = f"-{len(rows)}" if item_id in seen else ""
            rows.append(_item_li(pair, now, cards, suffix))
            seen.add(item_id)
        parts.append('<ol class="feed-list" role="list">' + "".join(rows) + "</ol>")
    else:
        parts.append("<p>No recent items yet.</p>")
    documents = {item.id: item for _, item in latest}
    parts.extend(_document(item, cards.get(item.id, {})) for item in documents.values())
    parts.append("</main>")
    workspace_link = f' · <a href="{esc(workspace_url, quote=True)}">Open workspace</a>' if workspace_url else ""
    parts.append('<footer class="shell py-8 muted">Updated ' + now.strftime("%Y-%m-%d %H:%M UTC")
                 + workspace_link + "</footer></body></html>")
    return "\n".join(parts)
