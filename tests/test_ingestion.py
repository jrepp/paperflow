from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from paperflow_cli import app
from paperflow_domain import Item
from paperflow_ingest_models import ChannelConfig
from paperflow_ingestion import default_registry, persist_batch
from paperflow_settings import StorageSettings, load_research_settings
from paperflow_storage_sqlite import SQLiteResearchStorage

RSS = b'''<rss version="2.0"><channel><title>Test</title>
<item><guid isPermaLink="false">story-1</guid><title>Example</title>
<link>https://example.org/article</link><comments>https://news.ycombinator.com/item?id=123</comments>
<description><![CDATA[<p>Source excerpt</p><script>bad()</script>]]></description></item>
</channel></rss>'''
ATOM = b'''<feed xmlns="http://www.w3.org/2005/Atom"><title>Test</title>
<entry><id>https://tildes.net/~comp/test</id><title>Example</title>
<link href="https://example.org/article"/><updated>2026-01-01T12:00:00Z</updated>
<summary>Source excerpt</summary></entry></feed>'''


@pytest.mark.parametrize(("payload", "dated"), [(RSS, False), (ATOM, True)], ids=["rss", "atom"])
def test_rss_and_atom_discovery_separate_discussion_from_article(payload, dated):
    channel = ChannelConfig(url="https://example.org/feed")
    batch = default_registry().collect("test", channel, lambda url: payload)
    entry = batch.entries[0]
    assert str(entry.url) == "https://example.org/article"
    assert entry.discussion_url != entry.url
    assert entry.excerpt == "Source excerpt"
    assert entry.signals.score is None
    assert entry.comments_status == "not_requested"
    assert (entry.published_at is not None) == dated


def test_hn_enrichment_bounded_and_no_invented_comment_score():
    channel = ChannelConfig(url="https://news.ycombinator.com/rss", enrichers=("hackernews",), comment_limit=1)
    calls = []
    def get(url):
        calls.append(url)
        if url.endswith("/rss"):
            return RSS
        if url.endswith("123.json"):
            return json.dumps({"id": 123, "type": "story", "score": 12, "descendants": 30, "kids": [456, 789]}).encode()
        return json.dumps({"id": 456, "parent": 123, "type": "comment", "text": "A <b>comment</b>"}).encode()
    batch = default_registry().collect("hn", channel, get)
    assert len(calls) == 3
    entry = batch.entries[0]
    assert entry.signals.score == 12
    assert entry.signals.comment_count == 30
    assert entry.comments[0].text == "A comment"
    assert entry.comments[0].score is None


def test_optional_enrichment_failure_and_deleted_item():
    channel = ChannelConfig(url="https://news.ycombinator.com/rss", enrichers=("hackernews",))
    def get(url):
        if url.endswith("/rss"):
            return RSS
        raise OSError("offline")
    batch = default_registry().collect("hn", channel, get)
    assert len(batch.entries) == 1
    assert batch.warnings
    removed = default_registry().collect("hn", channel, lambda url: RSS if url.endswith("/rss") else b'{"deleted":true}')
    assert not removed.entries


def test_bad_registry_config_fails_before_network_and_html_is_rejected():
    def unexpected(url):
        pytest.fail("must not fetch invalid compositions")
    with pytest.raises(ValueError, match="unknown enrichment"):
        default_registry().collect("test", ChannelConfig(url="https://example.org", enrichers=("missing",)), unexpected)
    with pytest.raises(ValueError, match="not an RSS/Atom"):
        default_registry().collect("test", ChannelConfig(url="https://example.org"), lambda url: b"<html>Login</html>")


def test_custom_component_registration_needs_no_runner_change():
    class Custom:
        def enrich(self, entry, channel, get):
            return entry.model_copy(update={"excerpt": "Custom excerpt"})
    registry = default_registry()
    registry.enrichers["custom"] = Custom()
    batch = registry.collect("custom", ChannelConfig(url="https://example.org", enrichers=("custom",)), lambda url: RSS)
    assert batch.entries[0].excerpt == "Custom excerpt"


def test_persistence_replay_and_updates_preserve_item_identity(tmp_path):
    storage = SQLiteResearchStorage(StorageSettings(path=tmp_path / "research.db"))
    storage.initialize()
    channel = ChannelConfig(url="https://example.org/feed")
    first = default_registry().collect("test", channel, lambda url: RSS)
    persist_batch(storage, first)
    persist_batch(storage, first)
    second = default_registry().collect("test", channel, lambda url: RSS.replace(b"Source excerpt", b"Updated excerpt"))
    persist_batch(storage, second)
    namespace = "feed:" + hashlib.sha256(str(channel.url).encode()).hexdigest()
    with storage.transaction() as repo:
        item = repo.resolve_identifier(namespace, "story-1")
        observations = repo.observations_for_item(item.id)
        assert len(observations) == 2
        assert observations[-1].entry.excerpt == "Updated excerpt"
        assert repo.get(Item, item.id).kind == "discussion"
    with sqlite3.connect(tmp_path / "research.db") as conn:
        assert conn.execute("SELECT COUNT(*) FROM research_items").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM research_revisions").fetchone()[0] == 2


def test_failed_batch_rolls_back_every_record(tmp_path):
    from paperflow_ingest_models import SourceObservation
    storage = SQLiteResearchStorage(StorageSettings(path=tmp_path / "research.db"))
    storage.initialize()
    batch = default_registry().collect("test", ChannelConfig(url="https://example.org/feed"), lambda url: RSS)
    # Force a failure at the observation write, after item and alias creation.
    from unittest.mock import patch
    from paperflow_storage_sqlite import SQLiteResearchRepository
    original = SQLiteResearchRepository.add
    def fail(self, record):
        if isinstance(record, SourceObservation):
            raise RuntimeError("interrupted")
        return original(self, record)
    with patch.object(SQLiteResearchRepository, "add", fail), pytest.raises(RuntimeError):
        persist_batch(storage, batch)
    with sqlite3.connect(tmp_path / "research.db") as conn:
        assert conn.execute("SELECT COUNT(*) FROM research_items").fetchone()[0] == 0


def test_example_settings_and_cli(tmp_path, monkeypatch):
    assert set(load_research_settings("ingestion.example.yaml").ingestion.channels) == {
        "hackernews", "lemmy-technology", "mbin", "tildes-computing", "huggingface-papers"}
    config = tmp_path / "config.yaml"
    config.write_text("research:\n  ingestion:\n    channels:\n      example:\n        url: https://example.org/feed\n")
    from paperflow_ingestion import IntegrationRegistry
    original = IntegrationRegistry.collect
    monkeypatch.setattr(IntegrationRegistry, "collect", lambda self, name, channel: original(self, name, channel, lambda url: RSS))
    result = CliRunner().invoke(app, ["sources", "collect", "example", "--config", str(config)])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["channel"] == "example"


def test_version_one_upgrade_and_feed_scoped_identity(tmp_path):
    path = tmp_path / "research.db"
    storage = SQLiteResearchStorage(StorageSettings(path=path))
    storage.initialize()
    with sqlite3.connect(path) as conn:
        conn.execute("DROP TABLE research_source_observations")
        conn.execute("UPDATE research_schema_version SET version=1")
    storage.initialize()
    for url in ("https://one.example/feed", "https://two.example/feed"):
        batch = default_registry().collect("test", ChannelConfig(url=url), lambda url: RSS)
        persist_batch(storage, batch)
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT version FROM research_schema_version").fetchone()[0] == 6
        assert conn.execute("SELECT COUNT(*) FROM research_items").fetchone()[0] == 2


@pytest.mark.parametrize("format", ["rss", "atom"])
def test_hundreds_of_feed_entries_are_bounded_and_persisted(tmp_path, format):
    entries = range(275)
    if format == "rss":
        payload = '<rss version="2.0"><channel><title>Large feed</title>' + ''.join(
            f'<item><guid>item-{i}</guid><title>Article {i}</title><link>https://example.org/{i}</link></item>'
            for i in entries) + '</channel></rss>'
    else:
        payload = '<feed xmlns="http://www.w3.org/2005/Atom"><title>Large feed</title>' + ''.join(
            f'<entry><id>item-{i}</id><title>Article {i}</title><link href="https://example.org/{i}"/></entry>'
            for i in entries) + '</feed>'
    channel = ChannelConfig(url="https://example.org/feed", limit=250, kind="article")
    batch = default_registry().collect("large-feed", channel, lambda url: payload.encode())
    assert len(batch.entries) == 250
    assert batch.entries[-1].source_id == "item-249"
    store = SQLiteResearchStorage(StorageSettings(path=tmp_path/'large.db'))
    store.initialize()
    persist_batch(store, batch)
    persist_batch(store, batch)
    with sqlite3.connect(tmp_path/'large.db') as db:
        assert db.execute('SELECT count(*) FROM research_items').fetchone()[0] == 250
        assert db.execute('SELECT count(*) FROM research_source_observations').fetchone()[0] == 250
    with pytest.raises(ValueError):
        ChannelConfig(url="https://example.org/feed", limit=1001)
