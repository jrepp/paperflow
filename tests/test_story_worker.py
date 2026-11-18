from test_assistant import fixture, runner
from datetime import datetime, UTC
from uuid import uuid4
from paperflow_ingest_models import SourceObservation, SourceEntry


def observe(storage, item):
    with storage.transaction(write=True) as repo:
        repo.add(SourceObservation(item_id=item.id, batch_id=uuid4(), channel='test',
            feed_url='https://example.com/feed', entry=SourceEntry(source_id=str(item.id), title=item.title,
            url='https://example.com/' + str(item.id)), observed_at=datetime.now(UTC)))
        for revision in repo.revisions_for_item(item.id):
            repo.mark_public_revision(revision.id)
from paperflow_assistant.runtime import AssistantWorker
from paperflow_domain import Item, ContentRevision
from paperflow_curation import CurationService


def decision(outcome, story_id=None, text=None):
    return {'outcome': outcome, 'story_id': story_id, 'findings': [
        {'rule_id': 'identity', 'result': outcome, 'rationale': 'Source comparison',
         'passages': [text] if text else [], 'missing_evidence': '' if text else 'The full source was unavailable.'}]}


def test_worker_routes_repeat_without_curation(tmp_path, monkeypatch):
    monkeypatch.setattr("paperflow_story_vectors.semantic_candidates", lambda *args, **kwargs: [])
    settings, storage, item, rev, output = fixture(tmp_path)
    observe(storage, item)
    calls = []
    def assess(ctx, item, candidates):
        calls.append(candidates)
        if candidates:
            return decision('repeated_coverage', candidates[0]['story']['id'], ctx.revision.markdown)
        return decision('new_story', text=ctx.revision.markdown)
    worker = AssistantWorker(settings, runner=runner(output), assessment_runner=assess)
    worker.initialize()
    assert worker.tick()['outcome'] == 'new_story'
    assert worker.tick()['status'] == 'succeeded'
    with storage.transaction() as repo:
        assert len(repo.published_stories()) == 1
    service = CurationService(storage)
    other = service.add(Item(kind='article', title=item.title))
    other_rev = service.add(ContentRevision.from_markdown(item_id=other.id, markdown=rev.markdown, evidence_basis='abstract'))
    observe(storage, other)
    assert worker.tick()['outcome'] == 'repeated_coverage'
    assert worker.tick() is None
    with storage.transaction() as repo:
        stories = repo.published_stories()
        assert len(stories) == 1
        assert len(repo.story_sources(stories[0].id)) == 2
        assert repo.story_assessments(revision_id=other_rev.id)[0].outcome == 'repeated_coverage'


def test_material_updates_coalesce_and_include_prior_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr('paperflow_story_vectors.semantic_candidates', lambda *args, **kwargs: [])
    settings, storage, item, rev, output = fixture(tmp_path)
    service = CurationService(storage)
    other = service.add(Item(kind='article', title=item.title + ' corrected'))
    other_rev = service.add(ContentRevision.from_markdown(item_id=other.id,
        markdown=rev.markdown + ' The reported result was corrected.', evidence_basis='abstract'))
    observe(storage, item)
    observe(storage, other)
    contexts = []
    def curate(ctx, item):
        contexts.append(ctx.story_context)
        result = dict(output)
        result['findings'] = [{**output['findings'][0], 'citations': [
            {'revision_id': str(rev.id), 'quote': rev.markdown, 'start': 0},
            {'revision_id': str(other_rev.id), 'quote': other_rev.markdown, 'start': 0}]}]
        return result, {}, {}, {}
    def assess(ctx, item, candidates):
        return decision('material_contribution', candidates[0]['story']['id'], ctx.revision.markdown) if candidates else decision('new_story', text=ctx.revision.markdown)
    worker = AssistantWorker(settings, runner=curate, assessment_runner=assess)
    worker.initialize()
    assert worker.tick()['outcome'] == 'new_story'
    with worker.store.connection(True) as conn:
        conn.execute("UPDATE assistant_meta SET value='curation' WHERE key='last_story_stage'")
    assert worker.tick()['outcome'] == 'material_contribution'
    assert worker.tick()['status'] == 'succeeded'
    assert worker.tick() is None
    assert len(contexts) == 1
    assert len(contexts[0]['pending']) == 2
    assert {s['revision_id'] for s in contexts[0]['sources']} == {str(rev.id), str(other_rev.id)}
    with storage.transaction() as repo:
        story = repo.published_stories()[0]
        assert not repo.story_pending(story.id)
        assert len(repo.story_sources(story.id)) == 2


def test_unresolved_and_assessment_failure_do_not_curate(tmp_path, monkeypatch):
    monkeypatch.setattr('paperflow_story_vectors.semantic_candidates', lambda *args, **kwargs: [])
    settings, storage, item, rev, output = fixture(tmp_path)
    observe(storage, item)
    def fail(*args):
        raise ValueError('temporary failure')
    worker = AssistantWorker(settings, runner=runner(output), assessment_runner=fail)
    worker.initialize()
    assert worker.tick()['status'] == 'failed'
    assert worker.tick() is None
    with worker.store.connection(True) as conn:
        conn.execute("UPDATE assistant_jobs SET available_at=0 WHERE status='queued'")
    worker.assessment_runner = lambda *args: decision('unresolved')
    assert worker.tick()['outcome'] == 'unresolved'
    assert worker.tick() is None
    with storage.transaction() as repo:
        assert repo.story_assessments(revision_id=rev.id)[0].outcome == 'unresolved'
        assert not repo.published_stories()


def test_existing_success_backfills_without_recurating(tmp_path, monkeypatch):
    monkeypatch.setattr('paperflow_story_vectors.semantic_candidates', lambda *args, **kwargs: [])
    settings, storage, item, rev, output = fixture(tmp_path)
    calls = []
    def curate(ctx, item):
        calls.append(item)
        return runner(output)(ctx, item)
    worker = AssistantWorker(settings, runner=curate,
        assessment_runner=lambda ctx, item, candidates: decision('new_story', text=ctx.revision.markdown))
    worker.initialize()
    assert worker.tick()['status'] == 'succeeded'
    observe(storage, item)
    assert worker.tick()['outcome'] == 'new_story'
    assert worker.tick() is None
    assert len(calls) == 1
    with storage.transaction() as repo:
        assert len(repo.published_stories()) == 1
        assert not repo.story_pending(repo.published_stories()[0].id)


def test_published_story_recovers_after_private_store_failure(tmp_path, monkeypatch):
    monkeypatch.setattr('paperflow_story_vectors.semantic_candidates', lambda *args, **kwargs: [])
    settings, storage, item, rev, output = fixture(tmp_path)
    observe(storage, item)
    calls = []
    def curate(ctx, item):
        calls.append(item)
        return runner(output)(ctx, item)
    worker = AssistantWorker(settings, runner=curate,
        assessment_runner=lambda ctx, item, candidates: decision('new_story', text=ctx.revision.markdown))
    worker.initialize()
    assert worker.tick()['outcome'] == 'new_story'
    finish = worker.store.finish
    def interrupted(*args):
        raise OSError('private store interrupted')
    monkeypatch.setattr(worker.store, 'finish', interrupted)
    assert worker.tick()['status'] == 'failed'
    monkeypatch.setattr(worker.store, 'finish', finish)
    with worker.store.connection(True) as conn:
        conn.execute("UPDATE assistant_jobs SET available_at=0 WHERE status='queued'")
    assert worker.tick()['status'] == 'succeeded'
    assert len(calls) == 1
    with storage.transaction() as repo:
        assert len(repo.published_stories()) == 1


def test_numbered_assessment_passages_preserve_extracted_evidence():
    from paperflow_assistant.stories import evidence_passages, ReferencedDecision, resolve_selection
    import pytest
    markdown = "A corrected result measured 93.2%.\nThe original estimate was 95%."
    passages = evidence_passages(markdown)
    selection = ReferencedDecision(outcome="new_story", findings=[
        {"rule_id": "identity", "result": "distinct", "rationale": "The measurement identifies the result.", "passage_ids": [1]}])
    assert resolve_selection(selection, passages).findings[0].passages == ["A corrected result measured 93.2%."]
    selection.findings[0].passage_ids = [len(passages) + 1]
    with pytest.raises(ValueError, match='passage numbers'):
        resolve_selection(selection, passages)


def test_public_analysis_rejects_private_revision_of_observed_item(tmp_path):
    import pytest
    from paperflow_assistant.models import Analysis
    from paperflow_assistant.quality import validate_analysis

    settings, storage, item, revision, output = fixture(tmp_path)
    observe(storage, item)
    with storage.transaction(write=True) as repo:
        private = repo.add(ContentRevision.from_markdown(item_id=item.id, markdown='Private note text'))
    output['findings'][0]['citations'].append({'revision_id': str(private.id), 'quote': private.markdown, 'start': 0})
    with pytest.raises(ValueError, match='private revisions'):
        validate_analysis(Analysis.model_validate(output), revision, settings.assistant, storage, public_only=True)
