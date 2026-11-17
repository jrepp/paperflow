"""Durability, evidence fidelity, internal memory boundaries, and quality denominators."""
import asyncio
import json
import time
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RunUsage

from paperflow_api import create_app
from paperflow_assistant.models import Analysis, MemoryInput
from paperflow_assistant.quality import validate_analysis, wilson
from paperflow_assistant.runtime import AssistantWorker, Context, fingerprint, run_agent
from paperflow_curation import CurationService
from paperflow_domain import Item, ContentRevision
from paperflow_monitor import IntegrationMonitor
from paperflow_settings import ResearchSettings
from paperflow_storage import ConflictError
from paperflow_storage_sqlite import SQLiteResearchStorage


def fixture(tmp_path):
    settings = ResearchSettings(storage={'path': tmp_path/'research.db'},
        assistant={'enabled': True, 'path': tmp_path/'assistant.db', 'evaluation_sample_rate': 1},
        monitor={'schedule_enabled': False, 'poll_seconds': 0.05}, topics={'enabled': False})
    storage = SQLiteResearchStorage(settings.storage)
    storage.initialize()
    service = CurationService(storage)
    item = service.add(Item(kind='paper', title='Measured retrieval latency'))
    rev = service.add(ContentRevision.from_markdown(item_id=item.id,
        markdown='The study measured lower retrieval latency in a controlled benchmark.', evidence_basis='abstract'))
    output = {'summary': 'The study reports lower retrieval latency in a controlled benchmark.',
              'category': 'computing', 'evidence_basis': 'abstract',
              'findings': [{'statement': 'The benchmark measured lower latency.', 'kind': 'reported_result',
                            'citations': [{'revision_id': str(rev.id), 'start': 0, 'quote': rev.markdown}],
                            'uncertainty': 'The abstract gives no effect size.'}],
              'limitations': ['Only the abstract is available.'], 'biases': [], 'effects': [],
              'research_gaps': ['Full text is needed to assess downstream effects.']}
    return settings, storage, item, rev, output


def runner(output, quality=True):
    def run(ctx, item):
        return output, {}, {'selected': True, 'evaluator_version': 'fixture-v1', 'model': 'test-judge',
                           'judgment': {'factual_support': quality, 'neutrality': True}}, {'input_tokens': 12, 'output_tokens': 5}
    return run


def test_durable_jobs_restart_and_no_duplicate_work(tmp_path):
    settings, storage, item, rev, output = fixture(tmp_path)
    worker = AssistantWorker(settings, runner=runner(output))
    worker.initialize()
    worker.store.reconcile(storage, fingerprint(settings.assistant))
    claimed = worker.store.claim(3)
    assert claimed and claimed['attempts'] == 1
    restarted = AssistantWorker(settings, runner=runner(output))
    restarted.initialize()
    assert restarted.tick()['status'] == 'succeeded'
    assert restarted.tick() is None
    metrics = restarted.store.metrics()
    assert metrics['eligible_jobs'] == metrics['counts']['succeeded'] == 1
    assert metrics['retries'] == 1
    assert metrics['quality']['sample_evaluated'] == 1
    assert metrics['quality']['human_accuracy'] is None
    assert metrics['evidence_basis'] == {'abstract': 1}
    assert restarted.store.search_history('retrieval')[0]['revision_id'] == str(rev.id)
    assert settings.assistant.path.stat().st_mode & 0o777 == 0o600


def test_bad_citations_and_evidence_basis_do_not_complete_jobs(tmp_path):
    settings, storage, item, rev, output = fixture(tmp_path)
    output['findings'][0]['citations'][0]['quote'] = 'Invented evidence'
    worker = AssistantWorker(settings, runner=runner(output))
    worker.initialize()
    assert worker.tick()['status'] == 'failed'
    assert worker.store.metrics()['counts'] == {'queued': 1}
    output['findings'][0]['citations'][0]['quote'] = rev.markdown
    output['evidence_basis'] = 'full_text'
    with pytest.raises(ValueError, match='basis'):
        validate_analysis(Analysis.model_validate(output), rev, settings.assistant, storage)


def test_memory_replay_versions_search_and_exclusion(tmp_path):
    settings, storage, item, rev, output = fixture(tmp_path)
    worker = AssistantWorker(settings, runner=runner(output))
    worker.initialize()
    memory = MemoryInput(kind='insight', text='INTERNAL-CANARY: retrieval needs replication.', scope='retrieval',
        basis='hypothesis', citations=output['findings'][0]['citations'], uncertainty='Unverified',
        counterevidence='', revisit_when='A replication arrives').model_dump(mode='json')
    first = worker.store.memory_write('run', 'key', memory, 'model')
    assert worker.store.memory_write('run', 'key', memory, 'model') == first
    with pytest.raises(ConflictError):
        worker.store.memory_write('run', 'key', {**memory, 'text': 'different'}, 'model')
    revised = worker.store.memory_write('run2', 'key', memory, 'model', record_id=first['id'], expected_version=1, status='disputed')
    assert revised['version'] == 2
    with pytest.raises(ConflictError):
        worker.store.memory_write('run2', 'stale', memory, 'model', record_id=first['id'], expected_version=1)
    assert worker.store.search_memory('replication')[0]['status'] == 'disputed'
    worker.tick()
    monitor = IntegrationMonitor(settings)
    with TestClient(create_app(settings, monitor=monitor)) as client:
        for path in ('/api/reader/feed', '/api/reader/items/'+str(item.id), '/api/monitor/curation', '/api/monitor/curation/runs'):
            response = client.get(path)
            assert response.status_code == 200
            assert 'INTERNAL-CANARY' not in response.text
        assert 'output' not in client.get('/api/monitor/curation/runs').json()[0]
        assert client.get('/api/assistant/memory').status_code == 404


def test_real_agent_wiring_without_network_and_quality(tmp_path):
    settings, storage, item, rev, output = fixture(tmp_path)
    worker = AssistantWorker(settings)
    worker.initialize()
    worker.store.reconcile(storage, fingerprint(settings.assistant))
    job = worker.store.claim(3)
    context = Context(job, rev, storage, worker.store, settings.assistant, None, RunUsage())
    judge = {'factual_support': True, 'evidence_limits': True, 'uncertainty': True,
             'causal_reasoning': True, 'neutrality': True, 'coverage': True, 'category_fit': True}
    result = asyncio.run(run_agent(context, item.model_dump(mode='json'),
        TestModel(call_tools=[], custom_output_args=output), TestModel(call_tools=[], custom_output_args=judge)))
    assert result[0]['summary'] == output['summary']
    assert result[2]['judgment'] == judge
    assert result[3]['requests'] >= 2


def test_monitor_runs_curator_and_keeps_ingestion_pool_separate(tmp_path, monkeypatch):
    settings, storage, item, rev, output = fixture(tmp_path)
    settings.monitor.schedule_enabled = True
    import paperflow_assistant.runtime as runtime
    original = runtime.AssistantWorker
    monkeypatch.setattr(runtime, 'AssistantWorker', lambda s: original(s, runner=runner(output)))
    monitor = IntegrationMonitor(settings)
    monitor.start()
    try:
        deadline = time.monotonic()+5
        while not monitor.last_assistant_run and time.monotonic() < deadline:
            time.sleep(0.05)
        assert monitor.last_assistant_run
        assert monitor._assistant_pool is not monitor._pool
        assert monitor.health()['curation']['enabled']
        assert monitor.assistant.store.metrics()['counts']['succeeded'] == 1
    finally:
        monitor.close()


def test_quality_intervals_and_empty_samples(tmp_path):
    settings, storage, item, rev, output = fixture(tmp_path)
    worker = AssistantWorker(settings)
    worker.initialize()
    assert worker.store.metrics()['completion_rate'] is None
    assert worker.store.metrics()['quality']['groups'] == {}
    assert wilson(0, 0) is None
    lo, hi = wilson(1, 1)
    assert 0 < lo < 0.21 and hi == pytest.approx(1)


def test_daily_budget_survives_restart(tmp_path):
    settings, storage, item, rev, output = fixture(tmp_path)
    worker = AssistantWorker(settings)
    worker.initialize()
    worker.store.reconcile(storage, 'one')
    worker.store.reconcile(storage, 'two')
    assert worker.store.claim(3, 1)
    worker.store.recover()
    assert worker.store.claim(3, 1) is None


def test_prompt_contains_embedded_voice_and_memory_contract():
    from paperflow_assistant.runtime import PROMPT
    assert 'Paperflow research assistant running inside the paper curation harness' in PROMPT
    assert 'rhetorical questions' in PROMPT
    assert 'record_research_insight' in PROMPT
    assert 'Never change a possible outcome into a certain outcome' in PROMPT


def test_decision_tool_enforces_parent_ids_depth_and_audits(tmp_path):
    from types import SimpleNamespace
    from pydantic_ai import Agent, ModelRetry
    from paperflow_assistant.models import DecisionRequest, Judgment
    from paperflow_assistant.runtime import attach_tools
    settings, storage, item, rev, output = fixture(tmp_path)
    settings.assistant.decision_depth = 2
    worker = AssistantWorker(settings)
    worker.initialize()
    worker.store.reconcile(storage, fingerprint(settings.assistant))
    job = worker.store.claim(3)
    judge = Agent(TestModel(custom_output_args={'supported': True, 'evidence_sufficient': True}), output_type=Judgment)
    deps = Context(job, rev, storage, worker.store, settings.assistant, judge, RunUsage())
    agent = Agent(TestModel(), deps_type=Context)
    attach_tools(agent)
    tool = agent._function_toolset.tools['evaluate_decisions'].function
    request = DecisionRequest(question='Does the source report lower latency?', criteria='An explicit statement of lower latency.',
                              citations=output['findings'][0]['citations'])
    async def exercise():
        async with judge:
            first = await tool(SimpleNamespace(deps=deps), request)
            second = await tool(SimpleNamespace(deps=deps), request.model_copy(update={'parent_ids': [first['id']]}))
            assert second['depth'] == 2
            with pytest.raises(ModelRetry, match='depth'):
                await tool(SimpleNamespace(deps=deps), request.model_copy(update={'parent_ids': [second['id']]}))
            with pytest.raises(ModelRetry, match='parents'):
                await tool(SimpleNamespace(deps=deps), request.model_copy(update={'parent_ids': ['missing']}))
    asyncio.run(exercise())
    with worker.store.connection() as conn:
        assert conn.execute('SELECT COUNT(*) FROM assistant_decisions').fetchone()[0] == 2


def test_static_publication_does_not_include_internal_memory(tmp_path):
    from paperflow_publish_site import build_site_from_storage
    settings, storage, item, rev, output = fixture(tmp_path)
    worker = AssistantWorker(settings)
    worker.initialize()
    worker.store.memory_write('run', 'internal', {'kind': 'follow_up', 'text': 'PRIVATE-MEMORY-CANARY'}, 'test')
    destination = tmp_path/'published'
    build_site_from_storage(storage, destination)
    assert all('PRIVATE-MEMORY-CANARY' not in p.read_text() for p in destination.iterdir())
