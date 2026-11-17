from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from urllib.error import HTTPError

import pytest

from paperflow_ingest_models import ChannelConfig
from paperflow_ingestion import default_registry, persist_batch
from paperflow_settings import StorageSettings
from paperflow_storage_sqlite import SQLiteResearchStorage

PAYLOAD = (Path(__file__).parent / "fixtures/huggingface-daily-papers.json").read_bytes()


def config(**overrides):
    return ChannelConfig(**dict(url="https://huggingface.co/api/daily_papers", discovery="huggingface-papers",
                                kind="paper", limit=3, **overrides))


def test_registry_maps_source_evidence_and_preserves_unknown_signals():
    calls = []
    def get(url):
        calls.append(url)
        return PAYLOAD
    batch = default_registry().collect("hf", config(), get)
    assert calls == ["https://huggingface.co/api/daily_papers?limit=3"]
    first, zero, unknown = batch.entries
    assert first.source_id == "2611.10439"
    assert str(first.url) == "https://arxiv.org/abs/2611.10439"
    assert str(first.discussion_url) == "https://huggingface.co/papers/2611.10439"
    assert first.published_at.isoformat() == "2026-11-07T00:00:00+00:00"
    assert first.excerpt == "Source abstract with evidence."
    assert first.signals.score == 8 and first.signals.comment_count == 4
    assert first.comments_status == "not_requested" and not first.comments
    assert zero.signals.score == 0 and zero.signals.comment_count == 0
    assert unknown.signals.score is None and unknown.signals.comment_count is None
    assert not batch.warnings


def test_partial_invalid_metadata_and_rows_are_reported():
    row = json.loads(PAYLOAD)[0]
    row["paper"].update(publishedAt="2026-11-07", upvotes=True)
    row["numComments"] = -1
    channel = config().model_copy(update={"limit": 10})
    batch = default_registry().collect("hf", channel, lambda _: json.dumps([
        None, {"paper": {"id": "https://evil.example", "title": "Bad"}}, row, row,
    ]).encode())
    assert len(batch.entries) == 1
    assert len(batch.warnings) == 5
    assert batch.entries[0].published_at is None
    assert batch.entries[0].signals.score is None
    assert batch.entries[0].signals.comment_count is None


@pytest.mark.parametrize("payload", [b'{}', b'null', b'<html>unavailable</html>'])
def test_invalid_responses_fail_the_run(payload):
    with pytest.raises(ValueError):
        default_registry().collect("hf", config(), lambda _: payload)


@pytest.mark.parametrize("changes", [{"kind": "discussion"}, {"url": "https://example.org/api/daily_papers"}])
def test_invalid_configuration_fails_before_network(changes):
    channel = ChannelConfig.model_validate(config().model_dump() | changes)
    with pytest.raises(ValueError):
        default_registry().collect("hf", channel, lambda _: pytest.fail("unexpected request"))


def test_transport_failure_preserves_retry_information():
    error = HTTPError(str(config().url), 429, "Too many requests", {"Retry-After": "60"}, None)
    def get(_):
        raise error
    with pytest.raises(HTTPError) as raised:
        default_registry().collect("hf", config(), get)
    assert raised.value is error


def test_bounded_request_preserves_date_and_replaces_limit():
    channel = ChannelConfig.model_validate(config().model_dump() | {
        "url": "https://huggingface.co/api/daily_papers?date=2026-11-14&limit=100", "limit": 1})
    calls = []
    def get(url):
        calls.append(url)
        return PAYLOAD
    batch = default_registry().collect("hf", channel, get)
    assert calls == ["https://huggingface.co/api/daily_papers?date=2026-11-14&limit=1"]
    assert len(batch.entries) == 1


def test_persistence_replay_and_refreshed_signal_keep_paper_identity(tmp_path):
    storage = SQLiteResearchStorage(StorageSettings(path=tmp_path / "research.db"))
    storage.initialize()
    channel = config().model_copy(update={"limit": 1})
    batch = default_registry().collect("hf", channel, lambda _: PAYLOAD)
    persist_batch(storage, batch)
    persist_batch(storage, batch)
    payload = json.loads(PAYLOAD)
    payload[0]["paper"]["upvotes"] = 10
    refreshed = default_registry().collect("hf", channel, lambda _: json.dumps(payload).encode())
    persist_batch(storage, refreshed)
    namespace = "feed:" + hashlib.sha256(str(channel.url).encode()).hexdigest()
    with storage.transaction() as repo:
        item = repo.resolve_identifier(namespace, "2611.10439")
        assert item.kind == "paper"
        assert str(item.canonical_url) == "https://arxiv.org/abs/2611.10439"
        observations = repo.observations_for_item(item.id)
        assert [ob.entry.signals.score for ob in observations] == [8, 10]
        assert all(ob.channel == "hf" and ob.feed_url == channel.url for ob in observations)
    with sqlite3.connect(tmp_path / "research.db") as conn:
        assert conn.execute("SELECT COUNT(*) FROM research_items").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM research_revisions").fetchone()[0] == 1
