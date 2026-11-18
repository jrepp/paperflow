"""Static published-site emitter: decay policy, channels, and build manifest."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from importlib.resources import files

import pytest

from paperflow_ingest_models import ChannelConfig, IngestBatch, SourceEntry
from paperflow_ingestion import persist_batch_in_repository
from paperflow_publish_site import build_site_from_storage
from paperflow_settings import StorageSettings
from paperflow_storage_sqlite import SQLiteResearchStorage


def _batch(channel: str, url: str, titles: list[tuple[str, datetime]]) -> IngestBatch:
    return IngestBatch(
        channel=channel,
        config=ChannelConfig(url=url),
        entries=tuple(
            SourceEntry(source_id=title, title=title, url=f"{url}/{index}", published_at=when)
            for index, (title, when) in enumerate(titles)
        ),
    )


def _seed(storage: SQLiteResearchStorage, batches: list[IngestBatch]) -> None:
    storage.initialize()
    with storage.transaction(write=True) as repo:
        for batch in batches:
            persist_batch_in_repository(repo, batch)


def _storage(tmp_path: Path) -> SQLiteResearchStorage:
    return SQLiteResearchStorage(StorageSettings(path=tmp_path / "artifacts" / "radar.db"))


def test_site_renders_channels_with_decay_and_manifest(tmp_path: Path) -> None:
    now = datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc)
    fresh = now - timedelta(hours=2)
    older = now - timedelta(hours=30)
    stale = now - timedelta(hours=200)
    storage = _storage(tmp_path)
    _seed(storage, [
        _batch("hackernews", "https://news.ycombinator.com/rss", [
            ("Fresh news item", fresh), ("Older news item", older), ("Stale news item", stale),
        ]),
        _batch("huggingface-papers", "https://huggingface.co/papers", [("Fresh paper", older)]),
    ])
    out = tmp_path / "site"

    manifest = build_site_from_storage(storage, out, window_hours=168, per_channel_limit=2, now=now)

    assert manifest["build_id"] == "20261115T120000Z"
    assert manifest["epoch"] == int(now.timestamp())
    assert manifest["policy"] == {"window_hours": 168, "per_channel_limit": 2}
    counts = {channel["name"]: channel["count"] for channel in manifest["channels"]}
    assert counts == {"hackernews": 2, "huggingface-papers": 1}
    assert manifest["total_items"] == 3
    page = (out / "index.html").read_text(encoding="utf-8")
    assert "Fresh news item" in page and "Older news item" in page and "Fresh paper" in page
    assert "Stale news item" not in page
    stylesheet = files("paperflow_reader").joinpath("static/reader.css").read_text()
    assert "<style>" + stylesheet + "</style>" in page
    assert '<link rel="stylesheet"' not in page
    assert '<main id="main" class="shell py-8" tabindex="-1">' in page
    assert page.count('<h1>Latest Stories</h1>') == 1
    assert '<summary>How stories are selected</summary>' in page
    assert 'up to 2 recent items per source from the past 7 days' in page
    assert '<a class="skip" href="#main">' in page
    assert "<a href=\"https://news.ycombinator.com/rss/0\">Fresh news item</a>" in page
    stored = json.loads((out / "build.json").read_text(encoding="utf-8"))
    assert stored["build_id"] == manifest["build_id"]
    balanced = page.split('<ol class="feed-list" role="list">')[1].split("</ol>")[0]
    assert balanced.index("Fresh news item") < balanced.index("Fresh paper") or balanced.index(
        "Fresh paper") < balanced.index("Older news item")


def test_site_balances_channels_and_survives_empty(tmp_path: Path) -> None:
    now = datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc)
    storage = _storage(tmp_path / "empty")
    storage.initialize()
    out = tmp_path / "empty-site"
    manifest = build_site_from_storage(storage, out, now=now)
    assert manifest["channels"] == [] and manifest["total_items"] == 0
    assert "No recent items yet." in (out / "index.html").read_text(encoding="utf-8")

    seeded = _storage(tmp_path)
    _seed(seeded, [
        _batch("channel-a", "https://a.example/feed", [(f"a{i}", now - timedelta(hours=1, minutes=i)) for i in range(20)]),
        _batch("channel-b", "https://b.example/feed", [(f"b{i}", now - timedelta(hours=1, minutes=i)) for i in range(20)]),
    ])
    manifest = build_site_from_storage(seeded, tmp_path / "site", per_channel_limit=20, now=now)
    assert manifest["total_items"] == 40
    page = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    latest = page.split('<ol class="feed-list" role="list">')[1].split("</ol>")[0]
    order = [line for line in latest.split('<li class="feed-row">')[1:] if line.strip()]
    assert page.count('<ol class="feed-list"') == 1
    assert page.count("<h2>") == manifest["total_items"]
    assert len(order) == manifest["total_items"]
    assert page.count('href="https://a.example/feed/0"') == 1
    assert page.count('href="https://b.example/feed/19"') == 1
    first_five = [entry.split("</a>")[0] for entry in order[:5]]
    assert sum('href="https://a.example' in name for name in first_five) >= 2
    assert sum('href="https://b.example' in name for name in first_five) >= 2


def test_workspace_link_is_opt_in_and_escaped(tmp_path: Path) -> None:
    storage = _storage(tmp_path)
    storage.initialize()
    out = tmp_path / "site"
    build_site_from_storage(storage, out)
    page = (out / "index.html").read_text()
    assert "Open workspace" not in page
    assert 'href="/live"' not in page

    build_site_from_storage(storage, out, workspace_url='/workspace?label="reader"&mode=read')
    page = (out / "index.html").read_text()
    assert 'href="/workspace?label=&quot;reader&quot;&amp;mode=read">Open workspace</a>' in page


@pytest.mark.parametrize("url", [
    "javascript:alert(1)", "data:text/html,hello", "//reader.example.com",
    "https://user:password@reader.example.com", "https:///missing-host",
    "/\\reader.example.com", "https://reader.example.com/\npath",
])
def test_workspace_link_rejects_unsafe_urls(tmp_path: Path, url: str) -> None:
    storage = _storage(tmp_path)
    storage.initialize()
    out = tmp_path / "site"
    with pytest.raises(ValueError):
        build_site_from_storage(storage, out, workspace_url=url)
    assert not out.exists()


def test_publish_cli_uses_external_settings_and_overrides(tmp_path: Path, monkeypatch) -> None:
    from typer.testing import CliRunner
    from paperflow_cli import app

    storage = _storage(tmp_path)
    storage.initialize()
    config = tmp_path / "settings.yaml"
    config.write_text(
        f"research:\n  storage:\n    path: {storage.settings.path}\n"
        "  publishing:\n    workspace_url: https://reader.example.com/yaml\n"
    )
    out = tmp_path / "site"
    runner = CliRunner()
    args = ["publish", "site", "--config", str(config), "--out", str(out)]

    def publish(extra: list[str] | None = None) -> str:
        result = runner.invoke(app, args + (extra or []))
        assert result.exit_code == 0, result.output
        return (out / "index.html").read_text()

    assert 'href="https://reader.example.com/yaml"' in publish()
    monkeypatch.setenv("PAPERFLOW_PUBLISHING__WORKSPACE_URL", "https://reader.example.com/env")
    assert 'href="https://reader.example.com/env"' in publish()
    assert 'href="https://reader.example.com/cli"' in publish([
        "--workspace-url", "https://reader.example.com/cli",
    ])
    assert "Open workspace" not in publish(["--workspace-url", ""])
    monkeypatch.setenv("PAPERFLOW_PUBLISHING__WORKSPACE_URL", "")
    assert "Open workspace" not in publish()


def _curated_site(tmp_path):
    from paperflow_assistant.store import AssistantStore
    from paperflow_domain import ContentRevision

    now = datetime.now(timezone.utc)
    storage = _storage(tmp_path)
    entry = SourceEntry(source_id='paper', title='A paper <script>bad()</script>',
                        url='https://example.com/paper', discussion_url='https://example.com/discussion',
                        signals={'score': 0, 'comment_count': 12}, published_at=now)
    _seed(storage, [IngestBatch(channel='papers', config=ChannelConfig(url='https://example.com', kind='paper'), entries=(entry,))])
    with storage.transaction(write=True) as repo:
        item = repo.items(channel='papers', limit=1)[0]
        revision = repo.add(ContentRevision.from_markdown(item_id=item.id, markdown='An observed result.'))
        repo.mark_public_revision(revision.id)
    assistant = AssistantStore(tmp_path / 'curation.db')
    assistant.initialize()
    assistant.reconcile(storage, 'test')
    with assistant.connection(True) as conn:
        job = conn.execute("SELECT * FROM assistant_jobs WHERE id NOT IN (SELECT job_id FROM assistant_story_jobs)").fetchone()
        conn.execute("UPDATE assistant_jobs SET status='running' WHERE id=?", (job['id'],))
    analysis = dict(summary='A model summary <img src=x onerror=bad()>', category='science', evidence_basis='abstract',
                    findings=[dict(statement='An observed result.', kind='reported_result', uncertainty='Limited evidence.',
                                   citations=[dict(revision_id=str(revision.id), quote='An observed result.', start=0)])],
                    limitations=['Only the abstract was available.'], biases=[], effects=[], research_gaps=['Replication needed.'])
    assistant.finish(job['id'], analysis, {}, {}, {}, 1)
    # Private records must never leak through the public projection.
    with assistant.connection(True) as conn:
        conn.execute("UPDATE assistant_jobs SET error='PRIVATE_RUN_DIAGNOSTIC'")
    out = tmp_path / 'site'
    build_site_from_storage(storage, out, now=now, assistant_store=assistant)
    return out, storage, assistant, item


def test_published_curation_includes_stats_and_complete_markdown(tmp_path):
    from html import unescape
    import re
    from urllib.parse import unquote

    out, storage, assistant, item = _curated_site(tmp_path)
    page = (out / 'index.html').read_text()
    assert page.index('A model summary &lt;img') < page.index('<details class="curation-card">')
    assert '<details class="curation-card">' in page
    assert '<details class="curation-card" open' not in page
    assert '<dt>Score</dt><dd>0</dd>' in page
    assert '<dt>Comments</dt><dd>12</dd>' in page
    assert 'href="https://example.com/discussion"' in page
    assert f'href="#curation-{item.id}"' in page
    assert 'A model summary &lt;img' in page
    assert '<script>bad()' not in page
    assert '<img src=x' not in page
    assert 'PRIVATE_RUN_DIAGNOSTIC' not in page
    markdown = unquote(unescape(re.search('href="data:text/markdown;charset=utf-8,([^"]+)"', page)[1]))
    assert '## Findings' in markdown
    assert 'Only the abstract was available.' in markdown
    assert 'Replication needed.' in markdown
    assert 'Evidence (' in markdown
    assert {p.name for p in out.iterdir()} == {'index.html', 'build.json'}

    from paperflow_domain import ContentRevision
    with storage.transaction(write=True) as repo:
        repo.add(ContentRevision.from_markdown(item_id=item.id, markdown='Updated source.'))
    build_site_from_storage(storage, out, assistant_store=assistant)
    assert 'earlier source revision' in (out / 'index.html').read_text()


def test_missing_curation_does_not_mislabel_source_excerpt(tmp_path):
    out, storage, _, _ = _curated_site(tmp_path)
    build_site_from_storage(storage, out)
    page = (out / 'index.html').read_text()
    assert 'No model curation available yet.' in page
    assert 'Model curation unavailable.' not in page
    assert page.index('<details class="curation-card">') < page.index('No model curation available yet.')
    assert 'Model analysis' not in page
    assert 'Read curation Markdown' not in page


def test_cli_and_api_publish_the_configured_public_analysis(tmp_path):
    from fastapi.testclient import TestClient
    from paperflow_api import create_app
    from paperflow_publish_site import build_site
    from paperflow_settings import ResearchSettings
    import time

    out, storage, assistant, _ = _curated_site(tmp_path)
    config = tmp_path / 'config.yaml'
    config.write_text(f'research:\n  storage:\n    path: {storage.settings.path}\n'
                      f'  assistant:\n    enabled: true\n    path: {assistant.path}\n')
    build_site(str(config), out)
    assert 'Model analysis' in (out / 'index.html').read_text()

    token = tmp_path / 'token'
    token.write_text('test-build-token-' * 3)
    settings = ResearchSettings(storage=storage.settings, monitor={'enabled': False},
        assistant={'enabled': True, 'path': assistant.path},
        publishing={'build_api_enabled': True, 'build_token_file': token, 'releases_dir': tmp_path/'releases'})
    headers = {'Authorization': 'Bearer ' + token.read_text()}
    with TestClient(create_app(settings)) as client:
        job = client.post('/api/publish/builds', headers=headers).json()
        for _ in range(200):
            job = client.get('/api/publish/builds/' + job['id'], headers=headers).json()
            if job['status'] != 'running':
                break
            time.sleep(.01)
        assert job['status'] == 'succeeded'
        page = (settings.publishing.releases_dir/job['release_id']/'index.html').read_text()
        assert 'Model analysis' in page
        assert 'Read curation Markdown' in page


def test_shared_items_have_unique_row_anchors_and_one_document(tmp_path):
    import re
    out, storage, assistant, item = _curated_site(tmp_path)
    with storage.transaction(write=True) as repo:
        persist_batch_in_repository(repo, IngestBatch(channel='another-channel',
            config=ChannelConfig(url='https://example.com/another-feed', kind='paper'),
            entries=(SourceEntry(source_id='same-paper', title=item.title, url=item.canonical_url),)))
    build_site_from_storage(storage, out, assistant_store=assistant)
    page = (out / 'index.html').read_text()
    ids = re.findall(r'(?<![\w-])id="([^"]+)"', page)
    assert len(ids) == len(set(ids))
    assert page.count(f'id="curation-{item.id}"') == 1



def test_real_story_publication_discloses_pending_contribution(tmp_path):
    from paperflow_domain import ContentRevision, Item
    from paperflow_story_models import Story, StoryAssessment, StoryFinding

    storage = _storage(tmp_path)
    now = datetime.now(timezone.utc)
    _seed(storage, [_batch('public', 'https://example.org/feed', [('Primary source', now), ('Correction', now)])])
    story = Story(title='Finding')
    with storage.transaction(write=True) as repo:
        items = {item.title: item for item in repo.items(channel='public')}
        item, second = items['Primary source'], items['Correction']
        revision = repo.add(ContentRevision.from_markdown(item_id=item.id, markdown='Original finding'))
        repo.mark_public_revision(revision.id)
        initial = repo.apply_story_assessment(StoryAssessment(revision_id=revision.id, outcome='new_story',
            evidence_basis='full_text', creator='editor', rule_set_version='v1', findings=(
                StoryFinding(rule_id='initial', rule_version='v1', result='matched', rationale='Original evidence', passages=('Original finding',)),)), story)
        published = repo.publish_story(story.id, 1, 'Finding', '# Finding\n\nOriginal account', [initial.id])
        revised = repo.add(ContentRevision.from_markdown(item_id=second.id, markdown='Corrected measurement'))
        repo.mark_public_revision(revised.id)
        repo.apply_story_assessment(StoryAssessment(revision_id=revised.id, outcome='material_contribution',
            story_id=story.id, story_version=published.version, evidence_basis='full_text', creator='editor',
            rule_set_version='v1', findings=(StoryFinding(rule_id='correction', rule_version='v1', result='matched',
                rationale='Measurement corrected', passages=('Corrected measurement',)),)))
    out = tmp_path / 'site'
    build_site_from_storage(storage, out)
    page = (out / 'index.html').read_text()
    assert page.count(f'id="story-{story.id}"') == 1
    assert '<div class="curation-body"><div class="card-actions"><div class="card-rating"' in page
    assert 'awaiting an update for new material' in page
    assert 'Original account' in page and 'Measurement corrected' in page
    assert 'https://example.org/feed/0' in page and 'https://example.org/feed/1' in page


def test_story_eligibility_window_privacy_counts_and_links(tmp_path):
    from paperflow_domain import ContentRevision, Item
    from paperflow_story_models import Story, StoryAssessment, StoryFinding

    now = datetime.now(timezone.utc)
    storage = _storage(tmp_path)
    _seed(storage, [_batch('public', 'https://example.org/feed', [
        ('Old public source', now - timedelta(days=20)),
        ('Fresh repeat', now - timedelta(hours=1)),
        ('Fresh public only', now - timedelta(hours=2)),
        ('Outside cap', now - timedelta(hours=3)),
    ])])
    with storage.transaction(write=True) as repo:
        by_name = {item.title: item for item in repo.items(channel='public')}
        revisions = {name: repo.add(ContentRevision.from_markdown(item_id=item.id, markdown=name))
                     for name, item in by_name.items()}
        for revision in revisions.values():
            repo.mark_public_revision(revision.id)
        private = repo.add(Item(kind='note', title='SECRET MANUAL SOURCE'))
        private_revision = repo.add(ContentRevision.from_markdown(item_id=private.id, markdown='SECRET EVIDENCE'))
        story = Story(title='Public story')
        def assess(revision, outcome, **kwargs):
            return StoryAssessment(revision_id=revision.id, outcome=outcome, evidence_basis='full_text',
                creator='editor', rule_set_version='1', findings=(StoryFinding(rule_id='coverage',
                rule_version='1', result='yes', rationale='Corrected the measurement', passages=(revision.markdown,)),), **kwargs)
        first = repo.apply_story_assessment(assess(revisions['Old public source'], 'new_story'), story)
        published = repo.publish_story(story.id, 1, story.title, 'Public summary', [first.id])
        repo.apply_story_assessment(assess(revisions['Fresh repeat'], 'repeated_coverage',
            story_id=story.id, story_version=published.version))
        secret_story = Story(title='SECRET STORY')
        secret_first = repo.apply_story_assessment(assess(private_revision, 'new_story'), secret_story)
        secret_published = repo.publish_story(secret_story.id, 1, secret_story.title, 'SECRET ACCOUNT', [secret_first.id])
        repo.apply_story_assessment(assess(revisions['Fresh public only'], 'repeated_coverage',
            story_id=secret_story.id, story_version=secret_published.version))
    out = tmp_path / 'site'
    class PrivateAnalysis:
        def analysis(self, item_id):
            return {'revision_id': 'unused', 'analysis': {'summary': 'SECRET ANALYSIS'}}

    manifest = build_site_from_storage(storage, out, per_channel_limit=2, now=now, assistant_store=PrivateAnalysis())
    page = (out / 'index.html').read_text()
    assert manifest['total_items'] == 2 and manifest['channels'] == [{'name': 'public', 'count': 2}]
    assert manifest['total_stories'] == 1
    assert 'Public summary' in page and 'Old public source' in page and 'Fresh repeat' in page
    assert 'SECRET' not in page
    assert 'Fresh public only' in page and 'Outside cap' not in page
    assert f'id="item-{by_name["Fresh repeat"].id}"' in page
    assert f'id="curation-{by_name["Fresh repeat"].id}"' in page
    # Repeats do not move the material timestamp or place an old story ahead of newer material.
    with storage.transaction() as repo:
        assert repo.get_story(story.id).material_at == published.material_at
    # Even an unpublished private contribution may influence later rationales.
    with storage.transaction(write=True) as repo:
        repo.apply_story_assessment(assess(private_revision, 'material_contribution',
            story_id=story.id, story_version=published.version))
    manifest = build_site_from_storage(storage, out, per_channel_limit=2, now=now, assistant_store=PrivateAnalysis())
    page = (out / 'index.html').read_text()
    assert manifest['total_stories'] == 0
    assert 'SECRET' not in page and 'Public summary' not in page


def test_old_database_cli_site_build_without_story_tables(tmp_path):
    import sqlite3
    from paperflow_publish_site import build_site

    now = datetime.now(timezone.utc)
    storage = _storage(tmp_path)
    _seed(storage, [_batch('public', 'https://example.org/feed', [('Legacy source', now)])])
    with sqlite3.connect(storage.settings.path) as conn:
        conn.execute('PRAGMA foreign_keys=OFF')
        for table in ('research_story_pending', 'research_story_sources', 'research_story_assessments',
                      'research_story_versions', 'research_stories'):
            conn.execute('DROP TABLE ' + table)
    config = tmp_path / 'config.yaml'
    config.write_text(f'research:\n  storage:\n    path: {storage.settings.path}\n')
    manifest = build_site(str(config), tmp_path / 'site')
    assert manifest['total_items'] == 1 and manifest['total_stories'] == 0
    assert 'Legacy source' in (tmp_path / 'site' / 'index.html').read_text()


def test_story_order_uses_material_change_not_repeat_arrival(tmp_path, monkeypatch):
    import paperflow_story_storage
    from paperflow_domain import ContentRevision
    from paperflow_story_models import Story, StoryAssessment

    now = datetime.now(timezone.utc)
    class FrozenDatetime(datetime):
        clock = None
        @classmethod
        def now(cls, tz=None):
            return cls.clock
    FrozenDatetime.clock = now - timedelta(hours=3)
    monkeypatch.setattr(paperflow_story_storage, 'datetime', FrozenDatetime)
    storage = _storage(tmp_path)
    _seed(storage, [_batch('public', 'https://example.org/feed', [
        ('First source', now - timedelta(hours=5)), ('Newer source', now - timedelta(hours=4)),
        ('Late repeat', now - timedelta(minutes=5))])])
    with storage.transaction(write=True) as repo:
        items = {item.title: item for item in repo.items(channel='public')}
        revisions = {name: repo.add(ContentRevision.from_markdown(item_id=item.id, markdown=name))
                     for name, item in items.items()}
        for revision in revisions.values():
            repo.mark_public_revision(revision.id)
        def first(name, title):
            story = Story(title=title)
            a = repo.apply_story_assessment(StoryAssessment(revision_id=revisions[name].id,
                outcome='new_story', evidence_basis='full_text', creator='editor', rule_set_version='1'), story)
            return repo.publish_story(story.id, 1, title, title + ' summary', [a.id])
        older = first('First source', 'Older material story')
        FrozenDatetime.clock = now - timedelta(hours=1)
        first('Newer source', 'Newer material story')
        repo.apply_story_assessment(StoryAssessment(revision_id=revisions['Late repeat'].id,
            outcome='repeated_coverage', story_id=older.id, story_version=older.version,
            evidence_basis='full_text', creator='editor', rule_set_version='1'))
    build_site_from_storage(storage, tmp_path / 'site', now=now)
    page = (tmp_path / 'site' / 'index.html').read_text()
    assert page.index('Newer material story') < page.index('Older material story')


def test_published_markdown_renders_prose_without_executable_html():
    from paperflow_publish_site import _render_markdown

    result = _render_markdown('# Finding\n\nSummary with **evidence**.\n\n'
        '## Details\n\n- One result\n\n<script>alert(1)</script>\n\n[bad](javascript:alert(1))\n\n'
        '[unverified](https://example.org) ![remote image](https://example.org/pixel)', 'Finding')
    assert '<h1>' not in result
    assert '<strong>evidence</strong>' in result
    assert '<h2>Details</h2>' in result
    assert '<li>One result</li>' in result
    assert '<script>' not in result
    assert 'href="javascript:' not in result
    assert 'href="https://example.org' not in result
    assert '<img' not in result
    assert 'unverified' in result and 'remote image' in result


def test_private_revision_of_observed_item_is_not_published(tmp_path):
    from paperflow_domain import ContentRevision
    from paperflow_story_models import Story, StoryAssessment

    now = datetime.now(timezone.utc)
    storage = _storage(tmp_path)
    _seed(storage, [_batch('public', 'https://example.org/feed', [('Observed item', now)])])
    with storage.transaction(write=True) as repo:
        item = repo.items()[0]
        private = repo.add(ContentRevision.from_markdown(item_id=item.id, markdown='PRIVATE REVISION MATERIAL',
                                                        processor_version='public-content-v1'))
        story = Story(title='PRIVATE TITLE')
        assessment = repo.apply_story_assessment(StoryAssessment(revision_id=private.id, outcome='new_story',
            evidence_basis='full_text', creator='editor', rule_set_version='v1'), story)
        repo.publish_story(story.id, 1, story.title, 'PRIVATE ACCOUNT', [assessment.id])
    class PrivateAnalysis:
        def analysis(self, item_id):
            return {'revision_id': str(private.id), 'analysis': {'summary': 'PRIVATE ANALYSIS'}}
    out = tmp_path / 'site'
    build_site_from_storage(storage, out, now=now, assistant_store=PrivateAnalysis())
    assert 'PRIVATE' not in (out / 'index.html').read_text()


def test_plain_public_item_hides_analysis_citing_private_revision(tmp_path):
    from paperflow_domain import ContentRevision

    out, storage, assistant, item = _curated_site(tmp_path)
    with storage.transaction(write=True) as repo:
        private = repo.add(ContentRevision.from_markdown(item_id=item.id, markdown='Private cited material'))
    with assistant.connection(True) as conn:
        row = conn.execute("SELECT id, output FROM assistant_jobs WHERE output IS NOT NULL").fetchone()
        analysis = json.loads(row['output'])
        analysis['findings'][0]['citations'].append({'revision_id': str(private.id), 'quote': private.markdown, 'start': 0})
        conn.execute('UPDATE assistant_jobs SET output=? WHERE id=?', (json.dumps(analysis), row['id']))
    build_site_from_storage(storage, out, assistant_store=assistant)
    page = (out / 'index.html').read_text()
    assert 'Private cited material' not in page and 'A model summary' not in page


def test_verified_previous_release_preserves_only_its_exact_public_markdown(tmp_path):
    import hashlib
    from uuid import uuid4
    from paperflow_domain import ContentRevision

    out, storage, assistant, item = _curated_site(tmp_path)
    old_html = (out / 'index.html').read_bytes()
    original_manifest = json.loads((out / 'build.json').read_text())
    root = tmp_path / 'releases'
    release_id = uuid4().hex
    release = root / release_id
    release.mkdir(parents=True)
    (release / 'index.html').write_bytes(old_html)
    manifest = dict(original_manifest, release_id=release_id, index_sha256=hashlib.sha256(old_html).hexdigest())
    (release / 'build.json').write_text(json.dumps(manifest))
    receipts = root.parent / '.build-jobs'
    receipts.mkdir()
    receipt = {'id': release_id, 'status': 'succeeded', 'release_id': release_id, 'build_id': manifest['build_id']}
    (receipts / (release_id + '.json')).write_text(json.dumps(receipt))
    with storage.transaction(write=True) as repo:
        public = repo.revisions_for_item(item.id)[0]
        repo._conn.execute('DELETE FROM research_public_revisions WHERE revision_id=?', (str(public.id),))
        repo.add(ContentRevision.from_markdown(item_id=item.id, markdown='PRIVATE CHANGED REVISION'))
        assert not repo.is_public_revision(public.id)
    with assistant.connection(True) as conn:
        row = conn.execute('SELECT id,output FROM assistant_jobs WHERE output IS NOT NULL').fetchone()
        analysis = json.loads(row['output'])
        analysis['summary'] = 'PRIVATE CHANGED SUMMARY'
        conn.execute("INSERT INTO assistant_jobs (id,revision_id,fingerprint,status,queued_at,finished_at,output,validation,evaluation) SELECT ?,revision_id,fingerprint,'succeeded',queued_at,finished_at+1,?,?,? FROM assistant_jobs WHERE id=?", ('new-private-run', json.dumps(analysis), '{}', '{}', row['id']))
    def render():
        build_site_from_storage(storage, out, assistant_store=assistant, releases_dir=root)
        return (out / 'index.html').read_text()
    page = render()
    assert 'A model summary &lt;img' in page
    published_run = row['id']
    assert page.count(f'data-version="{published_run}"') == 2
    assert 'new-private-run' not in page
    assert 'PRIVATE CHANGED' not in page
    assert f'id="curation-{item.id}"' in page
    with storage.transaction() as repo:
        assert not repo.is_public_revision(public.id)
    # A copied release with no matching receipt, or a modified index, is never proof.
    (receipts / (release_id + '.json')).unlink()
    assert 'A model summary &lt;img' not in render()
    (receipts / (release_id + '.json')).write_text(json.dumps(receipt))
    (release / 'index.html').write_bytes(old_html + b'altered')
    assert 'A model summary &lt;img' not in render()
    (release / 'index.html').write_bytes(old_html)
    manifest['release_id'] = uuid4().hex
    (release / 'build.json').write_text(json.dumps(manifest))
    assert 'A model summary &lt;img' not in render()
    # Even a forged index and recomputed digest cannot introduce altered Markdown.
    forged = old_html.replace(b'A%20model%20summary', b'SECRET%20FORGED%20SUMMARY')
    assert forged != old_html
    (release / 'index.html').write_bytes(forged)
    manifest['release_id'] = release_id
    manifest['index_sha256'] = hashlib.sha256(forged).hexdigest()
    (release / 'build.json').write_text(json.dumps(manifest))
    assert 'SECRET FORGED' not in render()
    assert 'A model summary &lt;img' not in render()
    # An unverifiable newer release must not revive content from an older one.
    import os
    (release / 'index.html').write_bytes(old_html)
    manifest['index_sha256'] = hashlib.sha256(old_html).hexdigest()
    (release / 'build.json').write_text(json.dumps(manifest))
    newer = root / uuid4().hex
    newer.mkdir()
    os.utime(newer, (release.stat().st_mtime + 10, release.stat().st_mtime + 10))
    assert 'A model summary &lt;img' not in render()


def test_unanalysed_item_has_source_disclosure_but_no_rating():
    from paperflow_domain import Item
    from paperflow_publish_site import _card

    result = _card(Item(kind='article', title='A fresh source'), {})
    assert 'for A fresh source"' in result
    assert 'Source details' in result
    assert 'data-rating=' not in result
