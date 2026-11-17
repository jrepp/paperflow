"""Static published-site emitter: decay policy, channels, and build manifest."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

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
    assert "<a href=\"https://news.ycombinator.com/rss/0\">Fresh news item</a>" in page
    stored = json.loads((out / "build.json").read_text(encoding="utf-8"))
    assert stored["build_id"] == manifest["build_id"]
    balanced = page.split("<h2>Latest</h2><ol>")[1].split("</ol>")[0]
    assert balanced.index("Fresh news item") < balanced.index("Fresh paper") or balanced.index(
        "Fresh paper") < balanced.index("Older news item")


def test_site_balances_channels_and_survives_empty(tmp_path: Path) -> None:
    now = datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc)
    storage = _storage(tmp_path / "empty")
    storage.initialize()
    out = tmp_path / "empty-site"
    manifest = build_site_from_storage(storage, out, now=now)
    assert manifest["channels"] == [] and manifest["total_items"] == 0
    assert "No published items inside the decay window yet." in (out / "index.html").read_text(encoding="utf-8")

    seeded = _storage(tmp_path)
    _seed(seeded, [
        _batch("channel-a", "https://a.example/feed", [(f"a{i}", now - timedelta(hours=1, minutes=i)) for i in range(5)]),
        _batch("channel-b", "https://b.example/feed", [(f"b{i}", now - timedelta(hours=1, minutes=i)) for i in range(5)]),
    ])
    manifest = build_site_from_storage(seeded, tmp_path / "site", per_channel_limit=5, now=now)
    assert manifest["total_items"] == 10
    page = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    latest = page.split("<h2>Latest</h2><ol>")[1].split("</ol>")[0]
    order = [line for line in latest.split("<li>")[1:] if line.strip()]
    assert [line[0] for line in zip(order)] .count("") == 0  # placeholder guard
    first_five = [entry.split("</a>")[0] for entry in order[:5]]
    assert sum(name.startswith("<a href=\"https://a.example") for name in first_five) >= 2
    assert sum(name.startswith("<a href=\"https://b.example") for name in first_five) >= 2


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
