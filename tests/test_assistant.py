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
    assert set(worker.store.spend()['by_role_and_model']) == {'reasoning:test', 'evaluation:test'}


def test_failed_model_request_is_metered_as_unknown_not_free(tmp_path):
    from pydantic_ai.models.function import FunctionModel
    settings, storage, item, rev, output = fixture(tmp_path)
    async def unavailable(messages, info):
        raise RuntimeError('synthetic provider failure')
    def failing_runner(context, item):
        return asyncio.run(run_agent(context, item, FunctionModel(unavailable), TestModel()))
    worker = AssistantWorker(settings, runner=failing_runner)
    worker.initialize()
    assert worker.tick()['status'] == 'failed'
    report = worker.store.spend()
    assert report['unpriced_requests'] == 1
    assert report['estimate_incomplete'] and report['billed_usd'] is None
    assert report['estimated_usd_per_completed_job'] is None


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


def test_rolling_token_budget_survives_restart_and_expires(tmp_path, monkeypatch):
    settings, storage, item, rev, output = fixture(tmp_path)
    worker = AssistantWorker(settings)
    worker.initialize()
    worker.store.reconcile(storage, 'one')
    now = time.time()
    worker.store.start_request('budget', 'job', 1, 'reasoning', 'test', now)
    worker.store.finish_request('budget', {'usage': {'input_tokens': 80, 'output_tokens': 20}})
    assert worker.store.claim(3, 100) is None
    worker.store.recover()
    assert worker.store.claim(3, 100) is None
    monkeypatch.setattr('paperflow_assistant.store.time.time', lambda: now + 86401)
    assert worker.store.claim(3, 100)


def test_fresh_revisions_take_priority_over_historical_backlog(tmp_path):
    settings, storage, item, rev, output = fixture(tmp_path)
    worker = AssistantWorker(settings)
    worker.initialize()
    worker.store.reconcile(storage, 'one')
    newer = CurationService(storage).add(ContentRevision.from_markdown(item_id=item.id,
        markdown='A new version reports a different result.', evidence_basis='abstract'))
    worker.store.reconcile(storage, 'one')
    assert worker.store.claim(3)['revision_id'] == str(newer.id)
    assert worker.store.metrics()['counts'] == {'queued': 1, 'running': 1}


def test_cadence_intake_and_prices_do_not_requeue_completed_content(tmp_path):
    settings, *_ = fixture(tmp_path)
    before = fingerprint(settings.assistant)
    settings.assistant.interval_seconds = 900
    settings.assistant.items_per_cycle = 10
    settings.assistant.reasoning_prices.input_per_million = 2
    assert fingerprint(settings.assistant) == before
    settings.assistant.reasoning_model = 'different-model'
    assert fingerprint(settings.assistant) != before


def test_cost_rates_cache_peak_off_peak_and_unknown_models(tmp_path):
    from datetime import UTC, datetime
    from decimal import Decimal
    from paperflow_assistant.usage import estimate_cost
    settings, *_ = fixture(tmp_path)
    usage = {'input_tokens': 1000000, 'cache_read_tokens': 500000, 'output_tokens': 100000}
    prices = settings.assistant.reasoning_prices
    peak = datetime(2026, 11, 17, 8, tzinfo=UTC)
    off_peak = datetime(2026, 11, 17, 12, tzinfo=UTC)
    assert Decimal(estimate_cost(usage, prices, 'deepseek-flash', peak)[0]) == Decimal('0.273')
    assert Decimal(estimate_cost(usage, prices, 'deepseek-flash', off_peak)[0]) == Decimal('0.1365')
    prices.holiday_dates_utc = ['2026-11-17']
    assert estimate_cost(usage, prices, 'deepseek-flash', peak)[1] == 'off_peak'
    assert estimate_cost(usage, prices, 'unknown-model', peak)[0] is None
    assert estimate_cost({**usage, 'cache_read_tokens': 2000000}, prices, 'deepseek-flash', peak)[0] is None


def test_style_checks_include_effects_and_uncertainty_but_not_source_quotes(tmp_path):
    settings, storage, item, rev, output = fixture(tmp_path)
    output['effects'] = [{'order': 2, 'hypothesis': 'Latency changes adoption.',
        'mechanism': ['A faster response changes use.', 'Repeated use changes demand.'],
        'assumptions': ['Other conditions stay constant.'], 'time_horizon': 'One year.',
        'falsifier': 'Demand remains constant.', 'uncertainty': ' '.join(['uncertain']*26)+'.',
        'citations': output['findings'][0]['citations']}]
    result = validate_analysis(Analysis.model_validate(output), rev, settings.assistant, storage)
    assert result['long_sentences'] == 1


def test_request_ledger_preserves_failures_retries_and_spend(tmp_path):
    settings, storage, item, rev, output = fixture(tmp_path)
    worker = AssistantWorker(settings, runner=runner(output))
    worker.initialize()
    worker.tick()
    for request_id, role, attempt, cost in [('a','reasoning',1,'0.2'),('b','decision',2,'0.01'),('c','evaluation',1,None)]:
        worker.store.start_request(request_id, 'job', attempt, role, 'test-model', time.time())
        worker.store.finish_request(request_id, {'status': 'succeeded' if cost else 'failed',
            'estimated_usd': cost, 'usage': {'input_tokens': 100, 'output_tokens': 10, 'cache_read_tokens': 50} if cost else None})
    report = worker.store.spend(7, 50)
    assert report['estimated_usd'] == '0.21'
    assert report['retry_estimated_usd'] == '0.01'
    assert report['projected_daily_usd'] == '10.50'
    assert report['input_tokens'] == 200 and report['cache_read_tokens'] == 100
    assert report['unpriced_requests'] == 1 and report['estimate_incomplete']
    assert report['billed_usd'] is None
    worker.store.start_request('interrupted', 'job', 3, 'reasoning', 'test', time.time())
    worker.store.recover()
    assert worker.store.spend()['unpriced_requests'] == 2


def test_prompt_contains_embedded_voice_and_memory_contract():
    from paperflow_assistant.runtime import PROMPT
    assert 'Paperflow research assistant running inside the paper curation harness' in PROMPT
    assert 'rhetorical questions' in PROMPT
    assert 'record_research_insight' in PROMPT
    assert 'Never change a possible outcome into a certain outcome' in PROMPT
    assert 'bucket status' in PROMPT


def test_reference_checks_reject_an_evaluator_that_passes_everything():
    from paperflow_assistant.evaluation import evaluate
    from paperflow_assistant.models import AssistantSettings, QualityJudgment
    model = TestModel(custom_output_args={key: True for key in QualityJudgment.model_fields})
    report = asyncio.run(evaluate(AssistantSettings(), model=model))
    assert report['passed'] == 1 and report['total'] == 3


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


def test_agent_trigger_wakes_manual_monitor_and_coalesces(tmp_path, monkeypatch):
    import threading
    import paperflow_assistant.runtime as runtime
    settings, storage, item, rev, output = fixture(tmp_path)
    settings.monitor.poll_seconds = 30
    entered, release = threading.Event(), threading.Event()
    calls = []
    def blocked(ctx, item):
        calls.append(ctx.job['id'])
        entered.set()
        assert release.wait(5)
        return runner(output)(ctx, item)
    original = runtime.AssistantWorker
    monkeypatch.setattr(runtime, 'AssistantWorker', lambda s: original(s, runner=blocked))
    monitor = IntegrationMonitor(settings)
    with TestClient(create_app(settings, monitor=monitor)) as client:
        try:
            response = client.post('/api/monitor/curation/runs')
            assert response.status_code == 202
            assert response.json()['status'] == 'requested'
            assert not response.json()['coalesced']
            assert entered.wait(2), 'Manual trigger must wake the long poll immediately'
            for _ in range(3):
                duplicate = client.post('/api/monitor/curation/runs').json()
                assert duplicate['coalesced'] and duplicate['status'] == 'running'
                assert client.get(duplicate['metrics_url']).status_code == 200
                assert client.get(duplicate['runs_url']).status_code == 200
            assert len(calls) == 1
            assert not monitor._curation_requested
            schema = client.get('/openapi.json').json()
            assert schema['paths']['/api/monitor/curation/runs']['post']['operationId'] == 'trigger_curation'
        finally:
            release.set()


def test_trigger_coalesces_pending_requests_and_rejects_unavailable(tmp_path, monkeypatch):
    settings, *_ = fixture(tmp_path)
    monitor = IntegrationMonitor(settings)
    with pytest.raises(RuntimeError):
        monitor.trigger_curation()
    monkeypatch.setattr(IntegrationMonitor, 'accepting', property(lambda self: True))
    monitor.assistant = object()
    assert not monitor.trigger_curation()['coalesced']
    assert monitor.trigger_curation()['coalesced']
    assert monitor._wake.is_set()
    settings.assistant.enabled = False
    with TestClient(create_app(settings)) as client:
        assert client.post('/api/monitor/curation/runs').status_code == 503


def test_budget_failure_has_exact_limit_and_persisted_diagnostics(tmp_path, caplog):
    from pydantic_ai.usage import UsageLimits
    settings, storage, item, rev, output = fixture(tmp_path)
    settings.assistant.token_limit = 120000
    def exhaust(ctx, item):
        # A recovered decision-tool error must not replace the terminal failure.
        ctx.failure = {'category': 'execution', 'limit': 'stale'}
        ctx.failure_exception = ValueError('previous tool error')
        ctx.usage.input_tokens = 127416
        ctx.usage.output_tokens = 3109
        ctx.usage.requests = 7
        UsageLimits(total_tokens_limit=120000).check_tokens(ctx.usage)
    worker = AssistantWorker(settings, runner=exhaust)
    worker.initialize()
    result = worker.tick()
    detail = result['failure']
    assert detail['limit'] == 'total_tokens'
    assert detail['configured_limit'] == 120000 and detail['observed'] == 130525
    assert detail['usage']['requests'] == 7
    assert worker.store.runs()[0]['usage']['failure'] == detail
    diagnostics = worker.store.diagnostics(settings.assistant)
    assert diagnostics['failure_groups'] == {'UsageLimitExceeded:total_tokens': 1}
    assert diagnostics['rolling_tokens_used'] == 0  # Injected runner does not make metered requests.
    assert diagnostics['queue_blocked_reason'] == 'retry_backoff'
    assert 'curation.attempt.failed' in caplog.text
    assert '130525' in caplog.text
    monitor = IntegrationMonitor(settings)
    with TestClient(create_app(settings, monitor=monitor)) as client:
        assert client.get('/health/curation').json()['failure']['observed'] == 130525
        assert client.get('/health/ready').status_code == 200


def test_request_budget_is_projected_and_unknown_error_text_is_never_logged(tmp_path, caplog):
    from paperflow_assistant.diagnostics import failure_details
    from pydantic_ai.exceptions import UsageLimitExceeded
    settings, *_ = fixture(tmp_path)
    usage = RunUsage(requests=12)
    exc = UsageLimitExceeded('The next request would exceed the request_limit of 12')
    detail = failure_details(exc, settings.assistant, usage)
    assert detail['observed'] == 13 and detail['usage']['requests'] == 12
    assert detail['measurement'] == 'projected_next_request'
    secret = 'private-token-and-internal-host'
    assert secret not in json.dumps(failure_details(UsageLimitExceeded(secret), settings.assistant, usage))
    def fail(ctx, item):
        raise ValueError(secret)
    worker = AssistantWorker(settings, runner=fail)
    worker.initialize()
    result = worker.tick()
    assert secret not in caplog.text
    assert secret not in json.dumps(result)
    assert secret not in json.dumps(worker.store.runs())


def test_budget_degrades_curation_but_not_readiness_or_process_heartbeat(tmp_path, monkeypatch):
    settings, *_ = fixture(tmp_path)
    monitor = IntegrationMonitor(settings)
    with TestClient(create_app(settings, monitor=monitor)) as client:
        monitor.assistant_error = 'UsageLimitExceeded'
        monitor.assistant_failure = {'category': 'budget', 'limit': 'requests', 'configured_limit': 12, 'observed': 13}
        assert client.get('/health/ready').status_code == 200
        curation = client.get('/health/curation')
        assert curation.status_code == 503
        assert curation.json()['degraded']
        from pydantic import SecretStr
        settings.monitor.heartbeat_url = SecretStr('https://example.org/check')
        calls = []
        monkeypatch.setattr('paperflow_heartbeat.ping', lambda url, healthy: calls.append(healthy))
        assert monitor._send_heartbeat() == 'success'
        assert calls == [True]
        monitor.assistant_failure = {'category': 'execution'}
        assert client.get('/health/ready').status_code == 503
        monitor.assistant_error = None
        monitor.assistant_failure = None
        from concurrent.futures import Future
        monitor._assistant_future = Future()
        monitor._assistant_started_at = time.time()-10000
        assert client.get('/health/ready').status_code == 503
        assert client.get('/health/curation').json()['overdue']


@pytest.mark.parametrize('message,key,cap,observed', [
    ('Exceeded the input_tokens_limit of 100 (input_tokens=101)', 'input_tokens', 100, 101),
    ('Exceeded the output_tokens_limit of 100 (output_tokens=101)', 'output_tokens', 100, 101),
    ('The next tool call(s) would exceed the tool_calls_limit of 40 (tool_calls=41).', 'tool_calls', 40, 41),
])
def test_sdk_limit_parser_allowlists_numeric_fields(tmp_path, message, key, cap, observed):
    from paperflow_assistant.diagnostics import failure_details
    from pydantic_ai.exceptions import UsageLimitExceeded
    settings, *_ = fixture(tmp_path)
    detail = failure_details(UsageLimitExceeded(message), settings.assistant, RunUsage())
    assert (detail['limit'], detail['configured_limit'], detail['observed']) == (key, cap, observed)


def test_source_reads_reuse_supplied_context_and_preserve_new_offsets(tmp_path):
    from paperflow_assistant.runtime import revision_passage
    settings, storage, item, rev, output = fixture(tmp_path)
    worker = AssistantWorker(settings)
    worker.initialize()
    context = Context({}, rev, storage, worker.store, settings.assistant, None, RunUsage())
    context.supplied_passages[str(rev.id)] = [(0, 12)]
    repeated = revision_passage(context, rev, 0, 12)
    assert repeated['already_supplied'] and 'text' not in repeated
    new = revision_passage(context, rev, 12, 20)
    assert new['start'] == 12 and new['text'] == rev.markdown[12:32]
    assert revision_passage(context, rev, 15, 10)['already_supplied']
    other = ContentRevision.from_markdown(item_id=item.id, markdown='Different evidence.')
    assert revision_passage(context, other, 0, 9)['text'] == 'Different'


def test_large_source_window_and_budget_are_supplied_to_model(tmp_path):
    from pydantic_ai.models.function import FunctionModel
    from pydantic_ai.messages import ModelResponse, ToolCallPart, UserPromptPart
    settings, storage, item, rev, output = fixture(tmp_path)
    settings.assistant.evaluation_sample_rate = 0
    rev = CurationService(storage).add(ContentRevision.from_markdown(
        item_id=item.id, processor_version='public-content-v1',
        markdown=rev.markdown + '\n' + 'Measured results. ' * 6000))
    output['evidence_basis'] = 'full_text'
    output['findings'][0]['citations'][0]['revision_id'] = str(rev.id)
    worker = AssistantWorker(settings)
    worker.initialize()
    worker.store.reconcile(storage, fingerprint(settings.assistant))
    job = worker.store.claim(3)
    context = Context(job, rev, storage, worker.store, settings.assistant, None, RunUsage())
    def respond(messages, info):
        prompt = next(json.loads(part.content) for message in messages for part in message.parts
                      if isinstance(part, UserPromptPart))
        assert prompt['source_text'] == rev.markdown
        assert prompt['source_included_range'] == [0, len(rev.markdown)]
        assert prompt['source_truncated'] is False
        assert prompt['budget']['total_tokens'] == 200000
        assert info.model_settings['max_tokens'] == 32000
        assert {tool.name for tool in info.function_tools} == {'search_history', 'read_revision', 'evaluate_decisions'}
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, output)])
    result = asyncio.run(run_agent(context, item.model_dump(mode='json'), FunctionModel(respond), TestModel()))
    assert result[0]['summary'] == output['summary']


def test_worker_claims_only_current_harness_configuration(tmp_path):
    settings, storage, item, rev, output = fixture(tmp_path)
    worker = AssistantWorker(settings)
    worker.initialize()
    worker.store.reconcile(storage, 'old-harness')
    current = fingerprint(settings.assistant)
    worker.store.reconcile(storage, current)
    job = worker.store.claim(3, fingerprint=current)
    assert job['fingerprint'] == current
    assert worker.store.claim(3, fingerprint=current) is None


def test_citation_offsets_are_resolved_only_for_unique_exact_quotes(tmp_path):
    from paperflow_assistant.models import Citation
    from paperflow_assistant.quality import validate_citations
    settings, storage, item, rev, output = fixture(tmp_path)
    rev = CurationService(storage).add(ContentRevision.from_markdown(
        item_id=item.id, markdown='αβ introduction. Unique measured result. Repeated. Repeated.'))
    quote = Citation(revision_id=rev.id, quote='Unique measured result.')
    validate_citations([quote], storage)
    assert quote.start == rev.markdown.index(quote.quote)
    assert rev.markdown[quote.start:quote.start+len(quote.quote)] == quote.quote
    for text in ('Invented measurement.', 'Repeated.'):
        with pytest.raises(ValueError, match='Citation does not match'):
            validate_citations([Citation(revision_id=rev.id, quote=text)], storage)
    positioned = Citation(revision_id=rev.id, quote='Repeated.', start=rev.markdown.rindex('Repeated.'))
    validate_citations([positioned], storage)
    assert positioned.start == rev.markdown.rindex('Repeated.')
    with pytest.raises(ValueError):
        validate_citations([Citation(revision_id=uuid4(), quote='Unique measured result.')], storage)


def test_wrapped_quotes_preserve_exact_source_and_reject_changed_words(tmp_path):
    from paperflow_assistant.models import Citation
    from paperflow_assistant.quality import validate_citations
    settings, storage, item, rev, output = fixture(tmp_path)
    rev = CurationService(storage).add(ContentRevision.from_markdown(item_id=item.id,
        markdown='αβ. The study\n  did not improve\tlatency. A\nresult. A result.'))
    citation = Citation(revision_id=rev.id, quote='The study did not improve latency.')
    validate_citations([citation], storage)
    assert citation.quote == 'The study\n  did not improve\tlatency.'
    assert citation.start == 4
    assert rev.markdown[citation.start:citation.start+len(citation.quote)] == citation.quote
    for quote in ('The study did improve latency.', 'A  result.'):
        with pytest.raises(ValueError):
            validate_citations([Citation(revision_id=rev.id, quote=quote)], storage)


def test_stage_health_prioritizes_overdue_and_keeps_success_evidence(tmp_path):
    from paperflow_assistant.store import AssistantStore, assessment_key

    store = AssistantStore(tmp_path/'stages.db')
    store.initialize()
    now = time.time()
    story = assessment_key('active', 'r')
    with store.connection(True) as conn:
        conn.executemany("INSERT INTO assistant_jobs(id,revision_id,fingerprint,status,started_at,queued_at) VALUES (?,?,?,?,?,?)",
            [(story,'r','active','running',now-900,now-1000),('analysis','r','active','succeeded',now-3600,now-4000),
             ('backlog','z','active','queued',None,now-1800),('obsolete','r','old','queued',None,now-999999)])
        conn.execute("INSERT INTO assistant_story_jobs(job_id,revision_id) VALUES (?,?)",(story,'r'))
        conn.executemany("INSERT INTO assistant_attempts(job_id,attempt,status,finished_at,error) VALUES (?,?,?,?,?)",
            [(story,1,'failed',now-1800,'CapacityExceededError'),('analysis',1,'succeeded',now-3000,None)])
    stages = store.pipeline_stages('active', overdue=True, budget_blocked=False, now=now)
    assert [(s['stage'],s['condition']) for s in stages] == [('assessment','overdue'),('analysis','completing')]
    assert stages[0]['running_since'] == now-900
    assert stages[0]['last_failure']['error'] == 'CapacityExceededError'
    assert stages[1]['attempts_24h']['succeeded'] == stages[1]['queued'] == 1
    assert stages[1]['oldest_queued_at'] == now-1800
    assert store.pipeline_stages('active',overdue=False,budget_blocked=True,now=now)[1]['condition'] == 'budget_blocked'
