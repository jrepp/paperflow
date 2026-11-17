"""User journeys through the real service and SQLite adapter, without network or IdP."""
import hashlib
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from paperflow_api import create_app
from paperflow_ingest_models import ChannelConfig
from paperflow_ingestion import default_registry, persist_batch
from paperflow_settings import ResearchSettings
from paperflow_storage_sqlite import SQLiteResearchStorage


@pytest.fixture
def settings(tmp_path):
    return ResearchSettings(storage={'path': tmp_path/'journey.db'}, monitor={'enabled': False})


def ingest(settings, channel, count=1):
    storage = SQLiteResearchStorage(settings.storage)
    storage.initialize()
    entries = ''.join(f'<item><guid>{i}</guid><title>Story {i}</title><link>https://example.org/{i}</link><comments>https://news.ycombinator.com/item?id={i+1}</comments><description>Source excerpt</description></item>' for i in range(count))
    rss = f'<rss version="2.0"><channel><title>Fixture</title>{entries}</channel></rss>'.encode()
    batch = default_registry().collect(channel, ChannelConfig(url=f'https://{channel}.example/feed'), lambda _: rss)
    persist_batch(storage, batch)
    persist_batch(storage, batch)


def post(client, path, body, key=None):
    result = client.post('/api/research'+path, json=body, headers={'Idempotency-Key': key} if key else {})
    assert result.status_code == 201, result.text
    return result.json()


def test_browse_channels(settings):
    ingest(settings, 'technology', 3)
    ingest(settings, 'science', 2)
    with TestClient(create_app(settings)) as client:
        first = client.get('/api/research/items?channel=technology&limit=2').json()
        second = client.get('/api/research/items?channel=technology&limit=2&offset=2').json()
        science = client.get('/api/research/items?channel=science').json()
        assert len(first) == 2 and len(second) == 1 and len(science) == 2
        assert len({v['id'] for v in first+second+science}) == 5
        assert client.get('/api/research/items?channel=missing').json() == []
        assert client.get('/api/research/items?limit=0').status_code == 422


def test_read_source_context(settings):
    ingest(settings, 'technology')
    with TestClient(create_app(settings)) as client:
        item = client.get('/api/research/items').json()[0]
        markdown = 'Complete source. '*10000 + 'TAIL'
        revision = post(client, f"/items/{item['id']}/revisions", {'markdown': markdown, 'evidence_basis': 'full_text'})
        preview = client.get(f"/api/research/items/{item['id']}?view=compact")
        assert preview.status_code == 200 and len(preview.content) < 6000
        assert 'TAIL' not in preview.text
        full = client.get(f"/api/research/revisions/{revision['id']}").json()
        assert full['markdown'] == markdown
        assert full['content_hash'] == hashlib.sha256(markdown.encode()).hexdigest()
        observations = client.get(f"/api/research/items/{item['id']}/observations").json()
        assert observations['total'] == 1
        entry = observations['records'][0]['entry']
        assert entry['url'] == 'https://example.org/0'
        assert entry['discussion_url'] == 'https://news.ycombinator.com/item?id=1'
        assert entry['comments_status'] == 'not_requested'
        assert entry['signals']['score'] is None


def test_collect_and_restart(settings):
    ingest(settings, 'technology')
    with TestClient(create_app(settings)) as client:
        assert client.get('/health/ready').status_code == 200
        item = client.get('/api/research/items').json()[0]
        bucket = post(client, '/buckets', {'title': 'Methods', 'research_question': 'What works?'})
        path = f"/buckets/{bucket['id']}/members"
        body = {'item_id': item['id'], 'note': 'Investigate'}
        member = post(client, path, body, 'collect-story')
        assert post(client, path, body, 'collect-story') == member
    with TestClient(create_app(settings)) as client:
        assert post(client, path, body, 'collect-story') == member
        assert client.get('/api/research'+path).json()['total'] == 1
        response = client.post('/api/research'+path, json={**body, 'note': 'Changed'}, headers={'Idempotency-Key': 'collect-story'})
        assert response.status_code == 409


def test_review_evidence(settings):
    with TestClient(create_app(settings)) as client:
        item = post(client, '/items', {'kind': 'paper', 'title': 'Study', 'canonical_url': 'https://example.org/study'})
        revision = post(client, f"/items/{item['id']}/revisions", {'markdown': 'Task A improved. Task B did not.', 'evidence_basis': 'abstract'})
        quote = post(client, f"/revisions/{revision['id']}/annotations", {'quote': 'Task B did not.', 'creator': 'curator'})
        bucket = post(client, '/buckets', {'title': 'Scope', 'research_question': 'Does every task improve?'})
        claim = post(client, f"/buckets/{bucket['id']}/claims", {'statement': 'All tasks improve', 'creator': 'curator'})
        body = {'revision_id': revision['id'], 'annotation_id': quote['id'], 'stance': 'challenges', 'rationale': 'Task B is a counterexample', 'creator': 'curator'}
        evidence = post(client, f"/claims/{claim['id']}/evidence", body)
        post(client, f"/evidence/{evidence['id']}/reviews", {'decision': 'accepted', 'reviewer': 'curator'})
        newer = post(client, f"/items/{item['id']}/revisions", {'markdown': 'Updated results'})
        bad = client.post(f"/api/research/claims/{claim['id']}/evidence", json={**body, 'revision_id': newer['id']})
        assert bad.status_code == 422
        assert client.get(f"/api/research/revisions/{revision['id']}").json()['markdown'][quote['start']:quote['end']] == quote['quote']
        export = client.get(f"/api/research/buckets/{bucket['id']}/export")
        assert export.status_code == 200
        for expected in ['challenges — accepted', revision['id'], 'https://example.org/study']:
            assert expected in export.text
