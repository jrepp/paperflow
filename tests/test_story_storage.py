import pytest

from paperflow_domain import ContentRevision, Item
from paperflow_settings import StorageSettings
from paperflow_storage import ConflictError
from paperflow_storage_sqlite import SQLiteResearchStorage
from paperflow_story_models import Story, StoryAssessment


def test_story_decisions_and_publication_are_durable(tmp_path):
    storage = SQLiteResearchStorage(StorageSettings(path=tmp_path / 'research.db'))
    storage.initialize()
    item = Item(kind='article', title='A new scientific result', canonical_url='https://example.org/one')
    revision = ContentRevision.from_markdown(item_id=item.id, markdown='A new scientific result has been measured.')
    def assessment(**kwargs):
        return StoryAssessment(revision_id=revision.id, evidence_basis='full_text', creator='worker',
                               creator_kind='processor', processor_version='v1', rule_set_version='r1', **kwargs)
    initial = assessment(outcome='new_story')
    story = Story(title=item.title)
    with storage.transaction(write=True) as repo:
        repo.add(item)
        repo.add(revision)
        saved = repo.apply_story_assessment(initial, story)
        assert repo.apply_story_assessment(initial, Story(title=item.title)).id == saved.id
        assert repo.story_candidates(revision.id)[0].story.id == story.id
        assert [x.id for x in repo.story_pending(story.id)] == [saved.id]
        with pytest.raises(ConflictError):
            repo.publish_story(story.id, 1, item.title, 'First account', [])
        published = repo.publish_story(story.id, 1, item.title, 'First account', [saved.id])
        assert published.version == 2
        assert not repo.story_pending(story.id)
    with storage.transaction(write=True) as repo:
        assert repo.published_stories() == [published]
        stale = assessment(outcome='material_contribution', story_id=story.id, story_version=1, prior_assessment_id=saved.id)
        with pytest.raises(ConflictError):
            repo.apply_story_assessment(stale)
        later = assessment(outcome='repeated_coverage', story_id=story.id, story_version=2, prior_assessment_id=saved.id)
        repo.apply_story_assessment(later)
        assert repo.get_story(story.id).material_at == published.material_at
        assert not repo.story_pending(story.id)


def test_atomic_pending_updates_and_rollback_compatible_schema(tmp_path):
    import sqlite3

    path = tmp_path / 'research.db'
    storage = SQLiteResearchStorage(StorageSettings(path=path))
    storage.initialize()
    first = Item(kind='article', title='An early measurement')
    second = Item(kind='article', title='Measurement correction')
    revisions = [ContentRevision.from_markdown(item_id=x.id, markdown=x.title) for x in (first, second)]
    story = Story(title=first.title)
    with storage.transaction(write=True) as repo:
        for item, revision in zip((first, second), revisions):
            repo.add(item)
            repo.add(revision)
        initial = repo.apply_story_assessment(StoryAssessment(revision_id=revisions[0].id,
            outcome='new_story', evidence_basis='full_text', creator='test', rule_set_version='1'), story)
        additional = repo.apply_story_assessment(StoryAssessment(revision_id=revisions[1].id,
            story_id=story.id, story_version=1, outcome='material_contribution',
            evidence_basis='full_text', creator='test', rule_set_version='1'))
        with pytest.raises(ConflictError):
            repo.publish_story(story.id, 1, story.title, 'Stale draft', [initial.id])
        assert {a.id for a in repo.story_pending(story.id)} == {initial.id, additional.id}
    with pytest.raises(RuntimeError), storage.transaction(write=True) as repo:
        repo.publish_story(story.id, 1, story.title, 'Rolled back', [initial.id, additional.id])
        raise RuntimeError('rollback')
    with storage.transaction() as repo:
        assert repo.get_story(story.id).version == 1
        assert len(repo.story_pending(story.id)) == 2
    with sqlite3.connect(path) as conn:
        assert conn.execute('SELECT version FROM research_schema_version').fetchone() == (6,)


def test_identical_title_survives_dissimilar_long_excerpts(tmp_path):
    storage = SQLiteResearchStorage(StorageSettings(path=tmp_path / 'research.db'))
    storage.initialize()
    first = Item(kind='article', title='Distinctive quantum measurement announced')
    repeat = Item(kind='article', title='Distinctive quantum measurement announced')
    one = ContentRevision.from_markdown(item_id=first.id, markdown=' '.join(f'apple{i}' for i in range(300)))
    two = ContentRevision.from_markdown(item_id=repeat.id, markdown=' '.join(f'orange{i}' for i in range(300)))
    with storage.transaction(write=True) as repo:
        for item, revision in ((first, one), (repeat, two)):
            repo.add(item)
            repo.add(revision)
        story = Story(title=first.title)
        repo.apply_story_assessment(StoryAssessment(revision_id=one.id, outcome='new_story',
            evidence_basis='full_text', creator='test', rule_set_version='1'), story)
        candidates = repo.story_candidates(two.id, limit=1)
        assert candidates[0].story.id == story.id
        assert 'title' in candidates[0].matched
        assert candidates[0].score > 1
