from __future__ import annotations

import json
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from paperflow_api import create_app
from paperflow_api_client import APIClient, APIError
from paperflow_cli import app
from paperflow_settings import ResearchSettings


@pytest.fixture
def client(tmp_path):
    settings = ResearchSettings(storage={"path": tmp_path / "test.db"}, monitor={"schedule_enabled": False})
    with TestClient(create_app(settings)) as client:
        yield client


def post(client, path, payload):
    response = client.post("/api/research" + path, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def test_full_curator_journey_and_export(client):
    source = post(client, "/items", {"kind": "article", "title": "Evaluation report", "canonical_url": "https://example.org/article"})
    paper = post(client, "/items", {"kind": "paper", "title": "Evaluation study", "canonical_url": "https://example.org/paper"})
    source_revision = post(client, f"/items/{source['id']}/revisions", {"markdown": "Models work on task A.", "evidence_basis": "excerpt"})
    revision = post(client, f"/items/{paper['id']}/revisions", {"markdown": "Task A improved. Task B did not.", "evidence_basis": "abstract"})
    bucket = post(client, "/buckets", {"title": "Evaluation", "research_question": "Where does the finding apply?"})
    other = post(client, "/buckets", {"title": "Methods", "research_question": "How was it tested?"})
    member = post(client, f"/buckets/{bucket['id']}/members", {"item_id": source["id"]})
    assert post(client, f"/buckets/{bucket['id']}/members", {"item_id": source["id"]}) == member
    post(client, f"/buckets/{other['id']}/members", {"item_id": source["id"], "note": "Other context"})
    post(client, f"/buckets/{bucket['id']}/members", {"item_id": paper["id"], "role": "candidate_citation"})
    quote = post(client, f"/revisions/{source_revision['id']}/annotations", {"quote": "Models work", "creator": "researcher"})
    claim = post(client, f"/buckets/{bucket['id']}/claims", {"statement": "Models improve every task", "creator": "researcher", "source_annotation_id": quote["id"]})
    supporting = post(client, f"/claims/{claim['id']}/evidence", {"revision_id": revision["id"], "stance": "supports", "rationale": "Task A only", "creator": "researcher"})
    post(client, f"/claims/{claim['id']}/evidence", {"revision_id": revision["id"], "stance": "challenges", "rationale": "Not Task B", "creator": "researcher"})
    post(client, f"/evidence/{supporting['id']}/reviews", {"decision": "accepted", "reviewer": "reviewer"})
    post(client, f"/evidence/{supporting['id']}/reviews", {"decision": "rejected", "reviewer": "reviewer", "note": "Too broad"})
    detail = client.get(f"/api/research/buckets/{bucket['id']}").json()
    assert len(detail["members"]) == 2
    evidence = detail["claims"][0]["evidence"]
    assert evidence[0]["review_status"] == "rejected"
    assert len(evidence[0]["reviews"]) == 2
    assert evidence[1]["review_status"] == "proposed"
    brief = client.get(f"/api/research/buckets/{bucket['id']}/export")
    assert brief.status_code == 200
    assert "supports — rejected" in brief.text
    assert "challenges — proposed" in brief.text
    assert revision["id"] in brief.text
    assert "https://example.org/paper" in brief.text


def test_invalid_annotations_and_cross_revision_evidence(client):
    item = post(client, "/items", {"kind": "paper", "title": "Repeated words"})
    revision = post(client, f"/items/{item['id']}/revisions", {"markdown": "repeat repeat"})
    assert post(client, f"/items/{item['id']}/revisions", {"markdown": "repeat repeat"}) == revision
    ambiguous = client.post(f"/api/research/revisions/{revision['id']}/annotations", json={"quote": "repeat", "creator": "test"})
    assert ambiguous.status_code == 422
    quote = post(client, f"/revisions/{revision['id']}/annotations", {"quote": "repeat", "start": 0, "creator": "test"})
    new = post(client, f"/items/{item['id']}/revisions", {"markdown": "changed"})
    bucket = post(client, "/buckets", {"title": "Test", "research_question": "Question"})
    claim = post(client, f"/buckets/{bucket['id']}/claims", {"statement": "Statement", "creator": "test"})
    bad = client.post(f"/api/research/claims/{claim['id']}/evidence", json={"revision_id": new["id"], "annotation_id": quote["id"], "stance": "supports", "rationale": "wrong revision", "creator": "test"})
    assert bad.status_code == 422
    assert client.get(f"/api/research/claims/{claim['id']}").json()["evidence"] == []


def test_not_found_conflicts_search_and_unknown_fields(client):
    assert client.get(f"/api/research/items/{uuid4()}").status_code == 404
    item = post(client, "/items", {"kind": "article", "title": "Find this"})
    assert len(client.get("/api/research/items?query=Find").json()) == 1
    assert client.get("/api/research/items?channel=missing").json() == []
    assert client.post("/api/research/items", json={"kind": "article", "title": "Typo", "typo": 1}).status_code == 422
    bucket = post(client, "/buckets", {"title": "Test", "research_question": "Q"})
    post(client, f"/buckets/{bucket['id']}/members", {"item_id": item["id"]})
    assert client.post(f"/api/research/buckets/{bucket['id']}/members", json={"item_id": item["id"], "note": "changed"}).status_code == 409


def test_curator_cli_calls_api_and_exports(tmp_path, monkeypatch):
    calls = []
    bucket_id = str(uuid4())
    def request(self, method, path, body=None):
        calls.append((method, path, body))
        return "# Export\n" if path.endswith("/export") else {"id": bucket_id}
    monkeypatch.setattr(APIClient, "request", request)
    runner = CliRunner()
    result = runner.invoke(app, ["curate", "--api-url", "http://localhost:8000", "bucket-create", "A question", "--question", "Why?"])
    assert result.exit_code == 0, result.output
    assert calls[-1][0:2] == ("POST", "/api/research/buckets")
    assert calls[-1][2]["research_question"] == "Why?"
    output = tmp_path / "brief.md"
    result = runner.invoke(app, ["curate", "--api-url", "http://localhost:8000", "export", bucket_id, "--output", str(output)])
    assert result.exit_code == 0, result.output
    assert output.read_text() == "# Export\n"


def test_api_cli_json_input_and_failure(monkeypatch):
    calls = []
    def request(self, method, path, body=None):
        calls.append(body)
        return body
    monkeypatch.setattr(APIClient, "request", request)
    result = CliRunner().invoke(app, ["api", "--api-url", "http://localhost:8000", "request", "POST", "/api/research/items", "--body", "-"], input='{"title":"Test"}')
    assert result.exit_code == 0
    assert calls == [{"title": "Test"}]
    def fail(*args):
        raise APIError("HTTP 409: conflict")
    monkeypatch.setattr(APIClient, "request", fail)
    result = CliRunner().invoke(app, ["curate", "--api-url", "http://localhost:8000", "buckets"])
    assert result.exit_code == 1
    assert "HTTP 409" in result.output


def test_progressive_disclosure_and_context_measurement(monkeypatch):
    item_id = str(uuid4())
    payload = {"item": {"id": item_id, "title": "A source", "kind": "article", "canonical_url": None},
               "observations": [], "revisions": [{"id": str(uuid4()), "evidence_basis": "excerpt", "markdown": "source " * 2000}]}
    monkeypatch.setattr(APIClient, "request", lambda *args: payload)
    runner = CliRunner()
    compact = runner.invoke(app, ["curate", "--measure", "item", item_id])
    assert compact.exit_code == 0, compact.output
    assert "excerpt" in compact.stdout
    assert len(compact.stdout) < 1000
    metrics = json.loads(compact.stderr)
    assert metrics['output_chars'] == len(compact.stdout.rstrip('\n'))
    assert metrics['reduction_percent'] > 90
    raw = runner.invoke(app, ["curate", "--json", "--measure", "item", item_id])
    assert json.loads(raw.stdout) == payload
    assert json.loads(raw.stderr)['reduction_percent'] == 0
    full = runner.invoke(app, ["curate", "--full", "item", item_id])
    assert json.loads(full.stdout) == payload


def test_guide_offline_and_discovery_pagination(monkeypatch):
    def unexpected(*args):
        raise AssertionError('Guide should not call the service')
    monkeypatch.setattr(APIClient, "request", unexpected)
    runner = CliRunner()
    guide = runner.invoke(app, ['curate'])
    assert guide.exit_code == 0
    assert 'four steps' in guide.stdout
    calls = []
    def request(self, method, path, body=None):
        calls.append(path)
        return []
    monkeypatch.setattr(APIClient, 'request', request)
    result = runner.invoke(app, ['curate', '--measure', 'items', '--channel', 'hackernews'])
    assert result.exit_code == 0
    assert 'limit=10' in calls[0]
    assert 'offset=0' in calls[0]
    assert '--offset 10' in result.stdout
    assert json.loads(result.stderr)['output_chars'] == len(result.stdout.rstrip('\n'))
    raw = runner.invoke(app, ['curate', '--json', 'items'])
    assert json.loads(raw.stdout) == []
