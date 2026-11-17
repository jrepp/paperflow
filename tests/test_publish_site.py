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
    assistant = AssistantStore(tmp_path / 'curation.db')
    assistant.initialize()
    assistant.reconcile(storage, 'test')
    job = assistant.claim(3)
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
    ids = re.findall(r'\bid="([^"]+)"', page)
    assert len(ids) == len(set(ids))
    assert page.count(f'id="curation-{item.id}"') == 1
