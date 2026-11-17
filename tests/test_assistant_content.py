from datetime import UTC, datetime, timedelta

import pytest

from paperflow_assistant.content import source_revision, public_url
from paperflow_assistant.runtime import AssistantWorker, fingerprint
from paperflow_domain import ContentRevision, Item
from paperflow_ingestion import persist_batch
from paperflow_ingest_models import ChannelConfig, IngestBatch, SourceEntry
from paperflow_settings import ResearchSettings
from paperflow_storage_sqlite import SQLiteResearchStorage


def test_content_excludes_navigation_and_does_not_claim_full_article():
    item = Item(kind='article', title='Source')
    text = 'Measured results and limitations. ' * 30
    rev = source_revision(item, 'https://example.com/story', get=lambda _: (
        '<nav>Menu</nav><article><h1>Results</h1><p>' + text + '</p>'
        '<script>secret()</script><aside>Ad</aside></article><footer>Footer</footer>').encode())
    assert text.strip() in rev.markdown
    assert not any(value in rev.markdown for value in ('Menu', 'secret', 'Footer', 'Ad'))
    assert rev.evidence_basis == 'excerpt'


def test_arxiv_html_and_unavailable_content():
    item = Item(kind='paper', title='Source')
    urls = []
    def get(url):
        urls.append(url)
        return ('<article class="ltx_document">' + 'Paper findings. ' * 50 + '</article>').encode()
    rev = source_revision(item, 'https://arxiv.org/abs/2611.12345', get=get)
    assert urls == ['https://arxiv.org/html/2611.12345']
    assert rev.evidence_basis == 'full_text'
    with pytest.raises(ValueError):
        source_revision(item, 'https://example.com', get=lambda _: b'<main>Access denied</main>')


@pytest.mark.parametrize('url', ['file:///etc/passwd', 'http://127.0.0.1/', 'http://[::1]/', 'http://user:password@example.com/'])
def test_nonpublic_content_is_rejected(url):
    with pytest.raises(ValueError):
        public_url(url)


def test_visible_items_without_excerpts_get_content_and_queue_priority(tmp_path, monkeypatch):
    settings = ResearchSettings(storage={'path': tmp_path/'research.db'},
                                assistant={'path': tmp_path/'assistant.db'})
    storage = SQLiteResearchStorage(settings.storage)
    storage.initialize()
    now = datetime.now(UTC)
    persist_batch(storage, IngestBatch(channel='news', config=ChannelConfig(url='https://example.com/feed'),
        entries=(SourceEntry(source_id='top', title='Top article', url='https://example.com/top', published_at=now),)))
    with storage.transaction(write=True) as repo:
        top = repo.items()[0]
        back = repo.add(Item(kind='article', title='Historical backlog', published_at=now-timedelta(days=90)))
        repo.add(ContentRevision.from_markdown(item_id=back.id, markdown='Backlog imported later.'))
    fetched = []
    def extract(item, url):
        fetched.append(url)
        return ContentRevision.from_markdown(item_id=item.id, markdown='Measured content. '*100,
                                              processor_version='public-content-v1')
    monkeypatch.setattr('paperflow_assistant.content.source_revision', extract)
    worker = AssistantWorker(settings)
    worker.initialize()
    priority = worker.publication_priority()
    worker.store.reconcile(storage, fingerprint(settings.assistant))
    job = worker.store.claim(3, priority_revisions=priority)
    rev, item = worker.store.document(job['revision_id'])
    assert item['id'] == str(top.id)
    assert rev['evidence_basis'] == 'full_text'
    worker.publication_priority()
    assert fetched == ['https://example.com/top']


def test_fetch_failure_cooldown_and_daily_budget(tmp_path):
    from paperflow_assistant.store import AssistantStore
    store = AssistantStore(tmp_path/'assistant.db')
    store.initialize()
    assert store.reserve_content_fetch('first', 100)
    assert not store.reserve_content_fetch('first', 100)
    with store.connection(True) as conn:
        conn.execute('INSERT INTO assistant_meta VALUES (?, ?)', ('attempts:'+datetime.now(UTC).date().isoformat(), '100'))
    assert not store.reserve_content_fetch('second', 100)


def test_superseded_excerpt_is_not_claimed_after_content_arrives(tmp_path):
    settings = ResearchSettings(storage={'path': tmp_path/'research.db'}, assistant={'path': tmp_path/'assistant.db'})
    storage = SQLiteResearchStorage(settings.storage)
    storage.initialize()
    with storage.transaction(write=True) as repo:
        item = repo.add(Item(kind='paper', title='Paper'))
        repo.add(ContentRevision.from_markdown(item_id=item.id, markdown='Excerpt', evidence_basis='excerpt'))
        full = repo.add(ContentRevision.from_markdown(item_id=item.id, markdown='Full paper'))
    worker = AssistantWorker(settings)
    worker.initialize()
    worker.store.reconcile(storage, fingerprint(settings.assistant))
    assert worker.store.claim(3)['revision_id'] == str(full.id)
    assert worker.store.claim(3) is None
