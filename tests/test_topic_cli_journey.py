"""Real HTTP journeys for progressive topic curation against disposable storage."""
import json
import socket
import threading
import time
from datetime import timedelta
from uuid import uuid4

import pytest
import uvicorn
from typer.testing import CliRunner

from paperflow_api import create_app
from paperflow_cli import app
from paperflow_domain import now
from paperflow_ingest_models import ChannelConfig, IngestBatch, SourceEntry
from paperflow_ingestion import persist_batch
from paperflow_settings import ResearchSettings
from paperflow_storage_sqlite import SQLiteResearchStorage


@pytest.fixture
def topic_server(tmp_path):
    settings = ResearchSettings(storage={'path': tmp_path / 'topics.db'}, monitor={'enabled': False})
    store = SQLiteResearchStorage(settings.storage)
    store.initialize()
    batch = IngestBatch(channel='science', config=ChannelConfig(url='https://example.org/feed'),
        fetched_at=now()-timedelta(minutes=1), entries=tuple(
            SourceEntry(source_id=str(i), title=title, url=f'https://example.org/{i}', excerpt='An extended source excerpt for examination.')
            for i, title in enumerate(['Rust safety memory', 'Rust safety tooling', 'Unrelated astronomy discovery'])))
    persist_batch(store, batch)
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    sock.listen(128)
    server = uvicorn.Server(uvicorn.Config(create_app(settings), log_level='error', lifespan='on'))
    thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(.01)
    assert server.started
    yield f'http://127.0.0.1:{sock.getsockname()[1]}'
    server.should_exit = True
    thread.join(timeout=10)
    sock.close()
    assert not thread.is_alive()


def invoke(url, *args):
    result = CliRunner().invoke(app, ['topics', '--api-url', url, *args])
    assert result.exit_code == 0, result.output
    return result


def data(url, *args):
    return json.loads(invoke(url, '--json', *args).stdout)


def test_real_cli_brief_and_overrides_survive_recuration(topic_server):
    url = topic_server
    data(url, 'run')
    topics = data(url, 'list')['records']
    topic = next(row['topic'] for row in topics if row['topic']['title'] == 'Rust Safety')
    brief = data(url, 'brief', 'rust safety', '--limit', '1', '--excerpt-chars', '10')
    assert brief['total'] == 2 and brief['next_offset'] == 1
    assert len(brief['records'][0]['excerpt']) <= 10 and brief['records'][0]['excerpt_truncated']
    assert data(url, 'brief', topic['id'][:8], '--offset', '1')['total'] == 2
    automatic = data(url, 'brief', topic['id'])['records']
    from paperflow_api_client import APIClient
    all_items = APIClient(url).request('GET', '/api/research/items')
    unrelated = next(item for item in all_items if item['title'] == 'Unrelated astronomy discovery')
    removed = automatic[0]['item_id']
    result = data(url, 'curate', topic['id'], '--include', unrelated['id'], '--exclude', removed)
    ids = {row['item_id'] for row in result['records']}
    assert unrelated['id'] in ids and removed not in ids
    data(url, 'run')
    ids = {row['item_id'] for row in data(url, 'brief', topic['id'])['records']}
    assert unrelated['id'] in ids and removed not in ids
    feed = APIClient(url).request('GET', f"/api/reader/feed?topic={topic['id']}")
    assert {row['item']['id'] for row in feed['records']} == ids
    reset = data(url, 'curate', topic['id'], '--reset', unrelated['id'], '--reset', removed)
    ids = {row['item_id'] for row in reset['records']}
    assert removed in ids and unrelated['id'] not in ids
    invalid = CliRunner().invoke(app, ['topics', '--api-url', url, 'curate', topic['id'], '--include', str(uuid4())])
    assert invalid.exit_code != 0
    assert {row['item_id'] for row in data(url, 'brief', topic['id'])['records']} == ids


def test_real_cli_measure_and_unknown_selector(topic_server):
    url = topic_server
    data(url, 'run')
    result = invoke(url, '--measure', 'brief', 'rust safety', '--limit', '1')
    measurement = json.loads(result.stderr.strip())
    assert measurement['requests'] == 2 and measurement['response_bytes'] > 0
    assert measurement['output_characters'] > 0 and measurement['estimated_tokens'] > 0
    failed = CliRunner().invoke(app, ['topics', '--api-url', url, 'edit', 'does not exist', '--title', 'Oops'])
    assert failed.exit_code != 0 and 'not found' in failed.output.lower()


def test_ambiguous_title_and_stale_version_do_not_write(topic_server):
    url = topic_server
    first = data(url, 'create', 'First', '--term', 'rust')
    second = data(url, 'create', 'Second', '--term', 'astronomy')
    data(url, 'edit', second['id'], '--title', 'First')
    result = CliRunner().invoke(app, ['topics', '--api-url', url, 'edit', 'first', '--title', 'Wrong'])
    assert result.exit_code != 0 and 'ambiguous' in result.output.lower()
    assert data(url, 'brief', first['id'])['topic']['title'] == 'First'
    assert data(url, 'brief', second['id'])['topic']['title'] == 'First'
    result = CliRunner().invoke(app, ['topics', '--api-url', url, 'edit', second['id'], '--version', '1', '--title', 'Wrong'])
    assert result.exit_code != 0 and '409' in result.output
    assert data(url, 'brief', second['id'])['topic']['title'] == 'First'


def test_inclusion_expires_with_rolling_window(topic_server, monkeypatch):
    import paperflow_topics
    from paperflow_api_client import APIClient

    url = topic_server
    data(url, 'run')
    topic = next(row['topic'] for row in data(url, 'list')['records'] if row['topic']['title'] == 'Rust Safety')
    items = APIClient(url).request('GET', '/api/research/items')
    unrelated = next(item for item in items if item['title'] == 'Unrelated astronomy discovery')
    data(url, 'curate', topic['id'], '--include', unrelated['id'])
    future = now() + timedelta(hours=26)
    monkeypatch.setattr(paperflow_topics, 'now', lambda: future)
    data(url, 'run')
    assert data(url, 'brief', topic['id'])['total'] == 0
    audit = data(url, '--full', 'show', topic['id'])
    assert unrelated['id'] in audit['topic']['included_item_ids']


def test_editorial_summary_is_saved_and_brief_is_bounded(topic_server):
    url = topic_server
    topic = data(url, 'create', 'Review', '--term', 'rust')
    summary = 'x' * 600
    data(url, 'edit', topic['id'], '--summary', summary)
    brief = data(url, 'brief', topic['id'])
    assert brief['topic']['editorial_summary'] == summary[:500]
    assert brief['topic']['summary_truncated'] is True
    assert 'not a generated synthesis' in brief['basis']
    audit = data(url, '--full', 'show', topic['id'])
    assert audit['topic']['description'] == summary
