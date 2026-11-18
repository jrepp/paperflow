"""Index lifecycle tests use known vectors so ranking failures are reproducible."""
import numpy as np

import paperflow_story_vectors as vectors


class Embedder:
    def encode(self, texts):
        return np.array([[float(term in text) for term in ('voyager', 'wind', 'market')] for text in texts])


def test_changed_and_removed_stories_leave_candidate_search(tmp_path, monkeypatch):
    monkeypatch.setattr(vectors, '_model', lambda: Embedder())
    index = tmp_path / 'stories.mv2'
    documents = [('space', 'voyager'), ('power', 'wind')]
    assert vectors.semantic_candidates('voyager', documents, index_path=index)[0] == 'space'
    documents = [('space', 'market'), ('another', 'voyager')]
    hits = vectors.semantic_candidates('voyager', documents, index_path=index)
    assert hits[0] == 'another'
    assert set(hits) == {'space', 'another'}
    assert vectors.semantic_candidates('market', documents, index_path=index)[0] == 'space'
