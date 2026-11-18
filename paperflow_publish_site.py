"""Static published-site emitter: channels with a balanced decay policy.

Renders a standalone static site. Each ingestion channel contributes its freshest items up to
a balanced per-channel cap; items older than the decay window leave the
channels; the front page interleaves channels so no single source dominates.
Output is one self-contained index.html plus build.json carrying the build
identity, the applied policy, and per-channel counts.
"""
from __future__ import annotations

import html

from markdown_it import MarkdownIt
import json
from urllib.parse import quote
from datetime import datetime, timedelta, timezone
from pathlib import Path
from importlib.resources import files

from paperflow_domain import ContentRevision, Item
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
        for item in repo.publication_items(name, cutoff, per_channel_limit):
            when = item.published_at or item.created_at
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            if when < cutoff:
                continue
            picked.append((when, item))
        if picked:
            picked.sort(key=lambda pair: pair[0], reverse=True)
            sections.append({"name": name, "items": picked[:per_channel_limit]})
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
    stories = []
    with storage.transaction() as repo:
        sections = publication_sections(repo, now=now, window_hours=window_hours,
                                        per_channel_limit=per_channel_limit)
        selected_ids = {item.id for section in sections for _, item in section["items"]}
        story_items = set()
        if hasattr(repo, "story_schema_available") and repo.story_schema_available():
            stories = _story_projection(repo, selected_ids)
            story_items = repo.story_associated_items()
        manifest_sections = [{"name": section["name"], "count": len(section["items"])} for section in sections]
        attached = {source["item"].id for story in stories for source in story["sources"]}
        sections = [{"name": section["name"], "items": [pair for pair in section["items"] if pair[1].id not in attached]}
                    for section in sections]
        sections = [section for section in sections if section["items"]]
        for section in sections:
            for _, item in section["items"]:
                revision = repo.latest_for_item(ContentRevision, item.id)
                result = assistant_store.analysis(item.id) if assistant_store and item.id not in story_items else None
                if result and not _public_analysis(repo, result):
                    result = None
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
        "channels": manifest_sections,
        "total_items": sum(section["count"] for section in manifest_sections),
        "total_stories": len(stories),
    }
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "index.html").write_text(_render(sections, manifest, workspace_url=workspace_url, cards=cards, stories=stories), encoding="utf-8")
    (out / "build.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def _public_analysis(repo, result):
    """Do not expose an analysis of, or citing, a private source revision."""
    def references(value):
        if isinstance(value, dict):
            for key, entry in value.items():
                if key == 'revision_id':
                    yield entry
                else:
                    yield from references(entry)
        elif isinstance(value, list):
            for entry in value:
                yield from references(entry)
    return all(repo.is_public_revision(value) for value in references(result))


def _story_projection(repo, selected_ids):
    """Project stories grounded entirely in observed sources and selected for this feed."""
    projected = []
    for story in repo.published_stories():
        assessments = {a.id: a for a in repo.story_assessments(story_id=story.id)}
        cited = repo.story_version_assessment_ids(story.id, story.version)
        if cited is None:
            continue
        observed = {}
        assessment_items = {}
        for assessment in assessments.values():
            revision = repo.get(ContentRevision, assessment.revision_id)
            if revision:
                assessment_items[assessment.id] = revision.item_id
            if revision and revision.item_id not in observed:
                observed[revision.item_id] = bool(repo.latest_for_item(SourceObservation, revision.item_id))
        # The account itself can contain evidence from every cited assessment.
        # It cannot safely be displayed when any of those inputs is private.
        if any(not repo.is_public_revision(a.revision_id) for a in assessments.values()):
            continue
        if any(aid not in assessments or not repo.is_public_revision(assessments[aid].revision_id)
               or not observed.get(assessment_items.get(aid), False) for aid in cited):
            continue
        story_sources = repo.story_sources(story.id)
        # An assessment may quote or summarize another candidate source. Without
        # per-assessment input provenance, private membership taints the story.
        if any(not observed.get(source.item_id, False) or not repo.is_public_revision(source.revision_id)
               for source in story_sources):
            continue
        sources = []
        for source in story_sources:
            item = repo.get(Item, source.item_id)
            if item is None or not observed.get(item.id, False):
                continue
            latest = repo.latest_for_item(ContentRevision, item.id)
            sources.append({"item": item, "source": source,
                            "assessment": assessments.get(source.assessment_id),
                            "stale": bool(latest and latest.id != source.revision_id)})
        if not any(source["item"].id in selected_ids for source in sources):
            continue
        pending = any(observed.get(assessment_items.get(a.id), False)
                      for a in repo.story_pending(story.id))
        projected.append({"story": story, "sources": sources, "pending": pending})
    return sorted(projected, key=lambda row: (row["story"].material_at, str(row["story"].id)), reverse=True)


def _story_row(row, now, anchors):
    story = row["story"]
    esc = html.escape
    summary = next((line.strip() for line in story.account.splitlines() if line.strip() and not line.lstrip().startswith('#')), '')
    parts = [f'<li class="feed-row"><article id="story-{story.id}" tabindex="-1">',
             '<div class="meta item-meta">Updated ' + esc(_age_text(story.material_at, now)) + '</div>',
             '<h2><a href="#story-account-' + str(story.id) + '">' + esc(story.title) + '</a></h2>',
             '<p>' + esc(summary) + '</p>',
             '<p><a class="read-story" href="#story-account-' + str(story.id) + '">Read story</a></p>',
             '<details class="curation-card"><summary>Sources</summary><div class="curation-body"><ul>']
    if row.get('pending'):
        parts.insert(4, '<p class="muted">This account is awaiting an update for new material.</p>')
    changes = [finding.rationale for entry in row["sources"] if entry["assessment"]
               and entry["assessment"].outcome == 'material_contribution'
               for finding in entry["assessment"].findings]
    if changes:
        parts.insert(4, '<p><strong>What changed:</strong> ' + esc(changes[-1]) + '</p>')
    for entry in row["sources"]:
        item, assessment = entry["item"], entry["assessment"]
        title = esc(item.title)
        if item.canonical_url:
            title = '<a href="' + esc(str(item.canonical_url), quote=True) + '">' + title + '</a>'
        aliases = ''
        if item.id not in anchors:
            anchors.add(item.id)
            aliases = f'<span id="item-{item.id}"></span><span id="curation-{item.id}"></span>'
        parts.append('<li>' + aliases + title)
        if entry["stale"]:
            parts.append('<p class="muted">Assessment describes an earlier source revision.</p>')
        if assessment:
            parts.append('<p>Contribution: ' + esc(assessment.outcome.replace('_', ' '))
                         + ' · Evidence: ' + esc(assessment.evidence_basis.replace('_', ' ')) + '</p>')
            for finding in assessment.findings:
                parts.append('<p>' + esc(finding.rationale) + '</p>')
                for passage in finding.passages:
                    parts.append('<blockquote>' + esc(passage) + '</blockquote>')
                if finding.missing_evidence:
                    parts.append('<p>Missing evidence: ' + esc(finding.missing_evidence) + '</p>')
        else:
            parts.append('<p class="muted">Contribution assessment unavailable.</p>')
        parts.append('</li>')
    parts.append('</ul></div></details></article></li>')
    return ''.join(parts)


def _render_markdown(markdown, title=None):
    """Render untrusted stored text without permitting embedded HTML or unsafe links."""
    lines = markdown.splitlines()
    if title and lines and lines[0].strip().startswith('# '):
        if lines[0].strip()[2:].strip() == title:
            lines = lines[1:]
    renderer = MarkdownIt('commonmark', {'html': False})
    # Generated prose cannot introduce unverified destinations or remote images.
    renderer.renderer.rules['link_open'] = lambda tokens, idx, options, env: '<span>'
    renderer.renderer.rules['link_close'] = lambda tokens, idx, options, env: '</span>'
    renderer.renderer.rules['image'] = lambda tokens, idx, options, env: html.escape(tokens[idx].content)
    return renderer.render('\n'.join(lines))


def _document_shell(anchor, back_anchor, back_label, title, markdown, body, filename, label):
    return (f'<section class="curation-document" id="{anchor}" tabindex="-1" aria-label="{label}">'
            f'<a class="document-back" href="#{back_anchor}">{back_label}</a>'
            f'<h2>{html.escape(title)}</h2><div class="document-prose">{body}</div>'
            f'<div class="document-actions"><a download="{filename}" '
            f'href="data:text/markdown;charset=utf-8,{quote(markdown, safe="")}">Download Markdown</a></div></section>')


def _story_document(row):
    story = row["story"]
    return _document_shell(f'story-account-{story.id}', f'story-{story.id}', 'Back to story',
        story.title, story.account, _render_markdown(story.account, story.title),
        f'story-{story.id}.md', 'Story and curation')


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
    if links:
        parts.append('<div class="curation-links">' + ' · '.join(links) + '</div>')
        links = []
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
    if links:
        parts.append('<div class="curation-links">' + ' · '.join(links) + '</div>')
    parts.append('<details class="source-metadata"><summary>Source details</summary><dl>' + ''.join('<div><dt>' + esc(k) + '</dt><dd>' + esc(v) + '</dd></div>' for k, v in metadata) + '</dl></details>')
    parts.append('</div></details>')
    return ''.join(parts)


def _document(item, card):
    result = card.get("result")
    if not result:
        return ''
    markdown = _curation_markdown(item, result)
    # Keep technical provenance in the downloadable original and after the article.
    content = markdown.split('## Summary', 1)[-1]
    body = '<h3>Summary</h3>' + _render_markdown(content)
    source_link = (f'<a href="{html.escape(str(item.canonical_url), quote=True)}">Open source</a><br>'
                   if item.canonical_url else '')
    body += ('<details class="document-provenance"><summary>Provenance</summary><p>' + source_link
             + 'Source revision: ' + html.escape(result['revision_id']) + '<br>Curation run: '
             + html.escape(result['run_id']) + '</p></details>')
    return _document_shell(f'curation-{item.id}', f'item-{item.id}', 'Back to link', item.title,
                           markdown, body, f'curation-{item.id}.md', 'Curation Markdown')


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
    return f'<li class="feed-row"><article id="item-{item.id}{suffix}" tabindex="-1"><div class="meta item-meta">{meta}</div><h2>{ref}</h2>{_card(item, cards.get(item.id, {}))}</article></li>'


def _render(sections: list[dict], manifest: dict, *, workspace_url: str | None = None, cards: dict | None = None, stories: list[dict] | None = None) -> str:
    now = datetime.fromtimestamp(manifest["epoch"], tz=timezone.utc)
    cards = cards or {}
    esc = html.escape
    latest = _balanced(sections)
    stories = stories or []
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
        '</a></div></header>',
        '<main id="main" class="shell py-8" tabindex="-1">',
        '<div class="feed-toolbar"><h1>Latest</h1></div>',
        '<p class="muted published-intro">Recent reading from all sources.</p>',
    ]
    if latest or stories:
        anchors = {pair[1].id for pair in latest}
        rows = [(row["story"].material_at, _story_row(row, now, anchors)) for row in stories]
        seen = set()
        for pair in latest:
            item_id = pair[1].id
            suffix = f"-{len(rows)}" if item_id in seen else ""
            rows.append((pair[0], _item_li(pair, now, cards, suffix)))
            seen.add(item_id)
        if stories:
            rows.sort(key=lambda row: row[0], reverse=True)
        parts.append('<ol class="feed-list" role="list">' + ''.join(row[1] for row in rows) + '</ol>')
    else:
        parts.append("<p>No recent items yet.</p>")
    parts.extend(_story_document(row) for row in stories)
    documents = {item.id: item for _, item in latest}
    parts.extend(_document(item, cards.get(item.id, {})) for item in documents.values())
    parts.append("</main>")
    workspace_link = f' · <a href="{esc(workspace_url, quote=True)}">Open workspace</a>' if workspace_url else ""
    parts.append('<footer class="shell py-8 muted">Updated ' + now.strftime("%Y-%m-%d %H:%M UTC")
                 + workspace_link + "</footer></body></html>")
    return "\n".join(parts)
