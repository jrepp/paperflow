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
    worker = AssistantWorker(settings, runner=runner(output), assessment_runner=assess,
        repeat_review_runner=lambda *args: {'verdict': 'all_claims_covered'})
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


def test_independent_repeat_review_routes_disagreement_to_unresolved(tmp_path, monkeypatch):
    monkeypatch.setattr('paperflow_story_vectors.semantic_candidates', lambda *args, **kwargs: [])
    settings, storage, item, rev, output = fixture(tmp_path)
    observe(storage, item)
    seen = []
    def assess(ctx, item, candidates):
        if not candidates:
            return decision('new_story', text=ctx.revision.markdown)
        return decision('repeated_coverage', candidates[0]['story']['id'], ctx.revision.markdown)
    def review(ctx, item, candidate):
        seen.append((ctx.revision.markdown, candidate))
        return {'verdict': 'possible_material_change'}
    worker = AssistantWorker(settings, runner=runner(output), assessment_runner=assess, repeat_review_runner=review)
    worker.initialize()
    assert worker.tick()['outcome'] == 'new_story'
    assert worker.tick()['status'] == 'succeeded'
    assert not seen
    service = CurationService(storage)
    other = service.add(Item(kind='article', title=item.title))
    other_rev = service.add(ContentRevision.from_markdown(item_id=other.id,
        markdown=rev.markdown + ' An independent study confirmed the measurement.', evidence_basis='abstract'))
    observe(storage, other)
    assert worker.tick()['outcome'] == 'unresolved'
    assert worker.tick() is None
    assert seen[0][0] == other_rev.markdown
    assert seen[0][1]['account']
    assert seen[0][1]['evidence'][0]['text'] == rev.markdown
    with storage.transaction() as repo:
        assessed = repo.story_assessments(revision_id=other_rev.id)[0]
        assert assessed.outcome == 'unresolved'
        finding = next(f for f in assessed.findings if f.rule_id == 'independent-repeat-review')
        assert finding.result == 'unresolved' and 'verdict=possible_material_change' in finding.rationale
        assert len(repo.story_sources(assessed.story_id)) == 1


def test_repeat_review_is_metered_with_separate_budget_and_complete_input(tmp_path):
    import asyncio
    import json
    from pydantic_ai.models.function import FunctionModel
    from pydantic_ai.messages import ModelResponse, ToolCallPart, UserPromptPart
    from pydantic_ai.usage import RunUsage
    from paperflow_assistant.runtime import Context, fingerprint
    from paperflow_assistant.stories import review_repeat

    settings, storage, item, rev, output = fixture(tmp_path)
    worker = AssistantWorker(settings)
    worker.initialize()
    worker.store.reconcile(storage, fingerprint(settings.assistant))
    job = worker.store.claim(3)
    context = Context(job, rev, storage, worker.store, settings.assistant, None, RunUsage())
    candidate = {'account': 'Original story', 'evidence': [{'text': rev.markdown, 'truncated': False}]}
    def respond(messages, info):
        prompt = next(json.loads(part.content) for message in messages for part in message.parts
                      if isinstance(part, UserPromptPart))
        assert prompt['incoming_title'] == item.title
        assert prompt['incoming_text'] == rev.markdown
        assert prompt['incoming_truncated'] is False
        assert prompt['earlier_coverage'] == candidate
        assert 'proposed_findings' not in prompt and 'proposed_decision' not in prompt
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name,
            {'verdict': 'all_claims_covered'})])
    result = asyncio.run(review_repeat(context, item.model_dump(mode='json'), candidate, FunctionModel(respond)))
    assert result.verdict == 'all_claims_covered'
    records = worker.store.request_metrics(job['id'], job['attempts'])
    assert len(records) == 1
    assert records[0]['role'] == 'story_repeat_review'
    assert records[0]['model'] == settings.assistant.decision_model
    assert records[0]['prices']['model'] == settings.assistant.decision_model
    assert context.usage.requests == 1


def test_independent_review_failure_retries_without_suppression(tmp_path, monkeypatch):
    monkeypatch.setattr('paperflow_story_vectors.semantic_candidates', lambda *args, **kwargs: [])
    settings, storage, item, rev, output = fixture(tmp_path)
    observe(storage, item)
    calls = []
    def assess(ctx, item, candidates):
        return (decision('repeated_coverage', candidates[0]['story']['id'], ctx.revision.markdown)
                if candidates else decision('new_story', text=ctx.revision.markdown))
    def failing_review(*args):
        calls.append(True)
        raise ConnectionError('unavailable')
    worker = AssistantWorker(settings, runner=runner(output), assessment_runner=assess, repeat_review_runner=failing_review)
    worker.initialize()
    assert worker.tick()['outcome'] == 'new_story'
    assert worker.tick()['status'] == 'succeeded'
    other = CurationService(storage).add(Item(kind='article', title=item.title))
    other_rev = CurationService(storage).add(ContentRevision.from_markdown(item_id=other.id,
        markdown=rev.markdown, evidence_basis='abstract'))
    observe(storage, other)
    assert worker.tick()['status'] == 'failed'
    with storage.transaction() as repo:
        assert not repo.story_assessments(revision_id=other_rev.id)
    with worker.store.connection(True) as conn:
        conn.execute("UPDATE assistant_jobs SET available_at=0 WHERE status='queued'")
    worker.repeat_review_runner = lambda *args: {'verdict': 'insufficient_evidence'}
    assert worker.tick()['outcome'] == 'unresolved'
    assert len(calls) == 1


def test_repeat_review_cannot_suppress_when_incoming_text_is_truncated(tmp_path, monkeypatch):
    monkeypatch.setattr('paperflow_story_vectors.semantic_candidates', lambda *args, **kwargs: [])
    settings, storage, item, rev, output = fixture(tmp_path)
    observe(storage, item)
    def assess(ctx, item, candidates):
        return (decision('repeated_coverage', candidates[0]['story']['id'], ctx.revision.markdown[:60])
                if candidates else decision('new_story', text=ctx.revision.markdown))
    worker = AssistantWorker(settings, runner=runner(output), assessment_runner=assess,
        repeat_review_runner=lambda *args: {'verdict': 'all_claims_covered'})
    worker.initialize()
    assert worker.tick()['outcome'] == 'new_story'
    assert worker.tick()['status'] == 'succeeded'
    other = CurationService(storage).add(Item(kind='article', title=item.title))
    other_rev = CurationService(storage).add(ContentRevision.from_markdown(item_id=other.id,
        markdown=rev.markdown + ' More detail.' * 4000, evidence_basis='abstract'))
    observe(storage, other)
    assert worker.tick()['outcome'] == 'unresolved'
    with storage.transaction() as repo:
        assessment = repo.story_assessments(revision_id=other_rev.id)[0]
        assert 'complete_input=false' in assessment.findings[-1].rationale
