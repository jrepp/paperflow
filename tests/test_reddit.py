from __future__ import annotations

import json
import sys
import urllib.error
from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError
from typer.testing import CliRunner

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from paperflow_cli import app
from paperflow_settings import RedditSettings, load_research_settings
from paperflow_sources_reddit import (
    RedditAccessError, RedditProbeRequest, RedditTransport, fixture_transport, probe_reddit,
)

FIXTURE = Path(__file__).parent / "fixtures" / "reddit-synthetic.json"


def bundle():
    return json.loads(FIXTURE.read_text())


def test_normalization_signals_and_discovery_links():
    result = probe_reddit(RedditProbeRequest(subreddit="LocalLLaMA"), fixture_transport(bundle()), mode="fixture")
    assert result.mode == "fixture"
    post = result.posts[0]
    assert post.channel == "reddit:localllama"
    assert post.score == 120
    assert post.comments[0].score == 42
    assert post.comments[1].score is None
    assert post.comments_status == "sampled"
    assert post.paper_links == ("https://arxiv.org/abs/2401.00001", "https://doi.org/10.1234/example")
    assert "author" not in post.model_dump()


def test_request_bounds_and_comment_failure_preserve_posts():
    calls = []
    def fetch(path, params):
        calls.append((path, params))
        if path.startswith("comments/"):
            raise RedditAccessError("Denied")
        return bundle()["listing"]
    result = probe_reddit(RedditProbeRequest(subreddit="LocalLLaMA", limit=1), fetch)
    assert len(calls) == 2
    assert calls[1][1]["sort"] == "top"
    assert result.posts[0].comments_status == "unavailable"
    assert result.warnings
    calls.clear()
    probe_reddit(RedditProbeRequest(subreddit="LocalLLaMA", comments_per_post=0), fetch)
    assert len(calls) == 1
    for kwargs in ({"subreddit": "../escape"}, {"subreddit": "test", "limit": 11}):
        with pytest.raises(ValidationError):
            RedditProbeRequest(**kwargs)


def test_deleted_content_and_duplicates_are_skipped():
    data = bundle()
    listing = data["listing"]["data"]["children"]
    listing.append(listing[0])
    comments = data["comments"]["example"][1]["data"]["children"]
    comments[0]["data"]["body"] = "[deleted]"
    result = probe_reddit(RedditProbeRequest(subreddit="LocalLLaMA"), fixture_transport(data))
    assert len(result.posts) == 1
    assert len(result.posts[0].comments) == 1
    listing[0]["data"]["removed_by_category"] = "deleted"
    assert not probe_reddit(RedditProbeRequest(subreddit="LocalLLaMA"), fixture_transport(data)).posts


def test_cli_offline_never_constructs_live_transport(monkeypatch):
    def forbidden(*args):
        raise AssertionError("network transport must not be initialized")
    monkeypatch.setattr("paperflow_sources_reddit.RedditTransport", forbidden)
    result = CliRunner().invoke(app, ["sources", "reddit-probe", "--fixture", str(FIXTURE)])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["mode"] == "fixture"


def test_live_requires_credentials_and_redacts_secrets(tmp_path, monkeypatch):
    with pytest.raises(RedditAccessError, match="ACCESS_TOKEN"):
        RedditTransport(RedditSettings())
    config = tmp_path / "config.yaml"
    config.write_text("research: {}")
    monkeypatch.setenv("PAPERFLOW_REDDIT__ACCESS_TOKEN", "secret-token")
    settings = load_research_settings(config)
    assert "secret-token" not in repr(settings)
    assert "secret-token" not in settings.model_dump_json()
    with pytest.raises(RedditAccessError, match="USER_AGENT"):
        RedditTransport(settings.reddit)


@pytest.mark.parametrize("status", [401, 403, 429, 503])
def test_http_failures_are_actionable_and_do_not_echo_response(status):
    transport = RedditTransport(RedditSettings(access_token=SecretStr("secret-token"), user_agent="test-agent"))
    class FailingOpener:
        def open(self, request, timeout):
            raise urllib.error.HTTPError(request.full_url, status, "secret-token", {}, None)
    transport._opener = FailingOpener()
    with pytest.raises(RedditAccessError) as raised:
        transport("r/test/top", {"limit": 1})
    assert "secret-token" not in str(raised.value)


def test_different_subreddit_is_not_silently_mislabeled():
    with pytest.raises(ValueError, match="different subreddit"):
        probe_reddit(RedditProbeRequest(subreddit="MachineLearning"), fixture_transport(bundle()))
