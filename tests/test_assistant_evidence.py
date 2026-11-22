import asyncio
import json

from pydantic_ai.messages import ModelResponse, ToolCallPart, UserPromptPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.usage import RunUsage

from paperflow_assistant.evidence import EVIDENCE_BLOCK_SIZE, select_evidence
from paperflow_assistant.runtime import AssistantWorker, Context
from test_assistant import fixture


def test_selection_is_metered_exact_and_rejects_invalid_blocks(tmp_path):
    settings, storage, item, rev, _ = fixture(tmp_path)
    worker = AssistantWorker(settings)
    worker.initialize()
    context = Context({'id': 'selection', 'attempts': 1}, rev, storage, worker.store,
                      settings.assistant, None, RunUsage())
    text = ''.join(str(i) * EVIDENCE_BLOCK_SIZE for i in range(6))
    def choose(messages, info):
        prompt = next(json.loads(part.content) for message in messages for part in message.parts
                      if isinstance(part, UserPromptPart))
        assert len(prompt['blocks']) == 6
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {'block_ids': ['2', '4']})])
    ranges = asyncio.run(select_evidence(context, FunctionModel(choose), text))
    assert ranges == [(0, EVIDENCE_BLOCK_SIZE), (2*EVIDENCE_BLOCK_SIZE, 3*EVIDENCE_BLOCK_SIZE), (4*EVIDENCE_BLOCK_SIZE, 5*EVIDENCE_BLOCK_SIZE)]
    assert [text[a:b][0] for a, b in ranges] == ['0', '2', '4']
    assert worker.store.request_metrics('selection', 1)[0]['role'] == 'evidence_selection'
    def invalid(messages, info):
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {'block_ids': ['19']})])
    context.usage = RunUsage()
    assert asyncio.run(select_evidence(context, FunctionModel(invalid), text)) is None
    assert asyncio.run(select_evidence(context, FunctionModel(choose), 'short')) is None


def test_selected_passages_reach_reasoner_with_offsets_and_coverage_limits(tmp_path, monkeypatch):
    from pydantic_ai.models.test import TestModel
    from paperflow_assistant.runtime import run_agent, revision_passage
    from paperflow_curation import CurationService
    from paperflow_domain import ContentRevision
    settings, storage, item, rev, output = fixture(tmp_path)
    settings.assistant.evaluation_sample_rate = 0
    text = rev.markdown + '\n' + 'Results and caveats. ' * 4000
    rev = CurationService(storage).add(ContentRevision.from_markdown(item_id=item.id, markdown=text))
    output['evidence_basis'] = 'full_text'
    output['findings'][0]['citations'][0]['revision_id'] = str(rev.id)
    worker = AssistantWorker(settings)
    worker.initialize()
    context = Context({'id': 'abc12345', 'attempts': 1}, rev, storage, worker.store,
                      settings.assistant, None, RunUsage())
    async def choose(*args):
        return [(0, EVIDENCE_BLOCK_SIZE), (2*EVIDENCE_BLOCK_SIZE, 3*EVIDENCE_BLOCK_SIZE), (4*EVIDENCE_BLOCK_SIZE, 5*EVIDENCE_BLOCK_SIZE)]
    monkeypatch.setattr('paperflow_assistant.evidence.select_evidence', choose)
    def reason(messages, info):
        prompt = next(json.loads(part.content) for message in messages for part in message.parts
                      if isinstance(part, UserPromptPart))
        assert prompt['source_truncated'] is True
        assert 'incomplete coverage' in prompt['task']
        for passage in prompt['selected_source_passages']:
            assert passage['text'] == text[passage['start']:passage['end']]
        assert revision_passage(context, rev, 2*EVIDENCE_BLOCK_SIZE, 20)['already_supplied']
        assert revision_passage(context, rev, EVIDENCE_BLOCK_SIZE, 20)['text'] == text[EVIDENCE_BLOCK_SIZE:EVIDENCE_BLOCK_SIZE+20]
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, output)])
    result = asyncio.run(run_agent(context, item.model_dump(mode='json'), FunctionModel(reason), TestModel()))
    assert result[0]['summary'] == output['summary']


def test_evidence_selection_schema_reaches_typesafe_adapter(tmp_path, monkeypatch):
    from pydantic_ai.models.typesafe import TypeSafeModel
    from pydantic_ai.providers.typesafe import TypeSafeProvider
    settings, storage, item, rev, _ = fixture(tmp_path)
    worker = AssistantWorker(settings)
    worker.initialize()
    context = Context({'id': 'adapter', 'attempts': 1}, rev, storage, worker.store,
                      settings.assistant, None, RunUsage())
    model = TypeSafeModel('jev-1.14.0', provider=TypeSafeProvider(api_key='test-key'))
    calls = []
    async def stop_before_network(*args, **kwargs):
        calls.append((args, kwargs))
        raise RuntimeError('stop before network')
    monkeypatch.setattr(model.client, 'system_one', stop_before_network)
    assert asyncio.run(select_evidence(context, model, 'source ' * 5000)) is None
    assert calls, 'TypeSafe must accept the selector schema before reaching the transport'
