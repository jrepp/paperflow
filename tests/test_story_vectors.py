"""Index lifecycle tests use known vectors so ranking failures are reproducible."""
from datetime import UTC, datetime, timedelta
import json

import numpy as np

import paperflow_story_vectors as vectors


class Embedder:
    def encode(self, texts):
        return np.array([[float(term in text) for term in ('voyager', 'wind', 'market')] for text in texts])


def test_changed_and_removed_stories_leave_candidate_search(tmp_path, monkeypatch):
    monkeypatch.setattr(vectors, '_model', lambda: Embedder())
    index = tmp_path / 'stories.mv2'
    today = datetime.now(UTC)
    documents = [('space', 'voyager', today), ('power', 'wind', today)]
    assert vectors.semantic_candidates('voyager', documents, index_path=index)[0] == 'space'
    documents = [('space', 'market', today), ('another', 'voyager', today - timedelta(weeks=1)),
                 ('old', 'voyager', today - timedelta(weeks=4))]
    hits = vectors.semantic_candidates('voyager', documents, index_path=index)
    assert len(json.loads((tmp_path / 'stories.weeks/manifest.json').read_text())) == 2
    assert hits[0] == 'another'
    assert set(hits) == {'space', 'another'}
    assert vectors.semantic_candidates('market', documents, index_path=index)[0] == 'space'


def test_capacity_rollover_and_failed_replacement(tmp_path, monkeypatch):
    import memvid_sdk
    import pytest

    monkeypatch.setattr(vectors, '_model', lambda: Embedder())
    append = vectors._append

    def small_index(index, documents):
        if len(documents) > 1:
            raise memvid_sdk.CapacityExceededError('test limit')
        append(index, documents)

    monkeypatch.setattr(vectors, '_append', small_index)
    index = tmp_path / 'stories.mv2'
    today = datetime.now(UTC)
    documents = [('space', 'voyager', today), ('power', 'wind', today)]
    assert vectors.semantic_candidates('wind', documents, index_path=index)[0] == 'power'
    manifest = tmp_path / 'stories.weeks/manifest.json'
    original = manifest.read_bytes()
    assert len(next(iter(json.loads(original).values()))['parts']) == 2

    def disk_full(*args):
        raise OSError('disk full')

    monkeypatch.setattr(vectors, '_append', disk_full)
    with pytest.raises(OSError):
        vectors.semantic_candidates('wind', documents + [('extra', 'market', today)], index_path=index)
    assert manifest.read_bytes() == original
    assert vectors.semantic_candidates('wind', documents, index_path=index)[0] == 'power'


def test_legacy_capacity_failure_gets_one_fresh_retry_budget(tmp_path):
    from paperflow_assistant.store import AssistantStore

    store = AssistantStore(tmp_path / 'assistant.db')
    store.initialize()
    with store.connection(True) as conn:
        conn.execute('ALTER TABLE assistant_jobs DROP COLUMN retry_offset')
        conn.execute("""INSERT INTO assistant_jobs (id,revision_id,status,error,attempts)
                     VALUES ('story','revision','failed','CapacityExceededError',3)""")
        conn.execute("INSERT INTO assistant_story_jobs (job_id,revision_id) VALUES ('story','revision')")
    store.initialize()
    with store.connection(True) as conn:
        row = conn.execute("SELECT status,attempts,retry_offset FROM assistant_jobs").fetchone()
        assert tuple(row) == ('queued', 3, 3)
        conn.execute("UPDATE assistant_jobs SET status='failed',attempts=6")
    store.initialize()
    with store.connection() as conn:
        assert tuple(conn.execute("SELECT status,attempts,retry_offset FROM assistant_jobs").fetchone()) == ('failed', 6, 3)
