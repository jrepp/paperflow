"""Editorial window, lifecycle, provenance, retry and reader contracts."""
from datetime import timedelta
from pathlib import Path
import sys
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from paperflow_domain import now
from paperflow_settings import ResearchSettings
from paperflow_storage_sqlite import SQLiteResearchStorage
from paperflow_ingest_models import IngestBatch, ChannelConfig, SourceEntry
from paperflow_ingestion import persist_batch
from paperflow_topic_models import CreateTopic, EditTopic, TopicSettings, TopicProposal, TopicAssignment
from paperflow_topics import TopicService
from paperflow_storage import ConflictError
from paperflow_api import create_app


@pytest.fixture
def workspace(tmp_path):
    settings=ResearchSettings(storage={'path':tmp_path/'topics.db'},monitor={'enabled':False})
    store=SQLiteResearchStorage(settings.storage);store.initialize()
    return settings,store,now()


def ingest(store,at,entries,channel='science'):
    batch=IngestBatch(channel=channel,config=ChannelConfig(url=f'https://{channel}.example/feed'),fetched_at=at,
        entries=tuple(SourceEntry(source_id=key,title=title,url=f'https://example.org/{key}') for key,title in entries))
    return persist_batch(store,batch)


def seeded(workspace):
    settings,store,at=workspace
    ingest(store,at-timedelta(hours=2),[('1','Rust safety memory'),('2','Rust safety tooling'),('3','Unrelated astronomy discovery')])
    return TopicService(store),at


def test_window_does_not_renew_old_items_on_refresh_and_history_survives(workspace):
    service,at=seeded(workspace)
    run=service.curate(at=at)
    assert run.input_count == 3 and run.unassigned_count == 1
    assert len(run.editions) == 1
    summary=service.summaries(at=at)[0]
    assert summary.item_count == 2 and summary.topic.title == 'Rust Safety'
    topic_id=summary.topic.id
    assert service.summaries(at=at+timedelta(hours=25)) == []
    ingest(workspace[1],at+timedelta(hours=24),[('1','Rust safety memory'),('2','Rust safety tooling')])
    later=service.curate(at=at+timedelta(hours=25))
    assert later.input_count == 0
    assert service.summaries(at=at+timedelta(hours=25)) == []
    with workspace[1].transaction() as repo:
        assert len(repo.topic_members(topic_id,run.id)) == 2
        assert repo.topic_runs()[0].id == later.id


def test_editorial_overrides_survive_runs_and_persistent_topics_can_be_quiet(workspace):
    service,at=seeded(workspace);service.curate(at=at)
    original=service.summaries(at=at)[0].topic
    renamed=service.edit(original.id,EditTopic(expected_version=original.version,editor='editor',title='Safe Rust',sensitivity='urgent',editorial_status='suppressed'))
    assert renamed.title == 'Safe Rust'
    with pytest.raises(ConflictError):
        service.edit(original.id,EditTopic(expected_version=original.version,editor='editor',title='Stale'))
    service.curate(at=at+timedelta(seconds=1))
    assert not service.summaries(at=at+timedelta(seconds=1))
    editorial=service.summaries(at=at+timedelta(seconds=1),editorial=True)[0]
    assert editorial.topic.title == 'Safe Rust' and editorial.topic.editorial_status == 'suppressed'
    persistent=service.create(CreateTopic(title='Systems',terms=('rust safety',),editor='editor'))
    service.curate(at=at+timedelta(seconds=2))
    assert next(r for r in service.summaries(at=at+timedelta(seconds=2)) if r.topic.id==persistent.id).item_count == 2
    quiet=service.summaries(at=at+timedelta(days=2))
    assert len(quiet)==1 and quiet[0].lifecycle=='quiet' and quiet[0].item_count==0
    assert service.detail(original.id)['edits'][0]['editor']=='editor'
    with pytest.raises(ValueError,match='Latest'):
        service.create(CreateTopic(title='Latest',terms=('news',),editor='editor'))


def test_boundary_window_and_time_sensitivity_are_independent(workspace):
    _,store,at=workspace
    ingest(store,at-timedelta(hours=24),[('1','Rust safety boundary')])
    ingest(store,at-timedelta(hours=1),[('2','Rust safety recent')])
    ingest(store,at,[('3','Rust safety excluded at end')])
    service=TopicService(store);run=service.curate(at=at)
    assert run.input_count==2
    topic=service.summaries(at=at)[0].topic
    urgent=service.edit(topic.id,EditTopic(expected_version=topic.version,editor='e',sensitivity='urgent'))
    urgent_score=service.summaries(at=at)[0].relevance
    service.edit(topic.id,EditTopic(expected_version=urgent.version,editor='e',sensitivity='lasting'))
    assert service.summaries(at=at)[0].relevance>urgent_score
    assert len(service.summaries(at=at))==1


def test_concurrent_exact_run_retry_and_restart(workspace):
    service,at=seeded(workspace);run_id=uuid4()
    with ThreadPoolExecutor(max_workers=4) as pool:
        runs=list(pool.map(lambda _:service.curate(at=at,run_id=run_id),range(4)))
    assert all(run==runs[0] for run in runs)
    restarted=TopicService(SQLiteResearchStorage(workspace[0].storage))
    assert restarted.curate(at=at,run_id=run_id)==runs[0]
    assert len(restarted.runs())==1


def test_invalid_plugin_provenance_and_candidate_overflow_leave_no_partial_changes(workspace):
    _,store,at=workspace;seeded(workspace)
    class BadCurator:
        version='bad-v1'
        def propose(self,candidates,settings):
            return [TopicProposal(key='bad',title='Invalid',terms=('bad',),assignments=(TopicAssignment(item_id=uuid4(),observation_id=uuid4(),first_seen_at=at,reason='invented'),))]
    with pytest.raises(ValueError,match='provenance'):
        TopicService(store,curator=BadCurator()).curate(at=at)
    assert TopicService(store).runs()==[]
    ingest(store,at-timedelta(minutes=1),[(str(i+10),f'Rust safety {i}') for i in range(11)])
    with pytest.raises(ValueError,match='limit exceeded'):
        TopicService(store,TopicSettings(max_candidates=10)).curate(at=at)
    assert TopicService(store).runs()==[]


def test_latest_index_topic_filter_and_api_review(workspace):
    service,at=seeded(workspace)
    with TestClient(create_app(workspace[0])) as client:
        response=client.post('/api/topics/runs',headers={'Idempotency-Key':'topic-pass'})
        assert response.status_code==201,response.text
        assert client.post('/api/topics/runs',headers={'Idempotency-Key':'topic-pass'}).json()==response.json()
        latest=client.get('/api/topics/latest').json()
        assert latest['key']=='latest' and latest['persistence']=='persistent'
        assert latest['topics'][0]['item_count']==2
        topic=latest['topics'][0]['topic']
        feed=client.get('/api/reader/feed',params={'topic':topic['id']}).json()
        assert len(feed['records'])==2
        assert client.get('/api/reader/feed',params={'topic':str(uuid4())}).status_code==404
        changed=client.patch(f"/api/topics/{topic['id']}",json={'expected_version':topic['version'],'editor':'human','editorial_status':'suppressed'})
        assert changed.status_code==200
        assert client.get('/api/topics/latest').json()['topics']==[]
        assert client.get('/api/reader/feed',params={'topic':topic['id']}).json()['records']==[]
        assert client.get('/topics').status_code==200


def test_monitor_curator_records_otel_and_does_not_overwrite_newer_manual_run(workspace):
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader
    from paperflow_telemetry import Telemetry
    from paperflow_monitor import IntegrationMonitor
    service,at=seeded(workspace)
    reader=InMemoryMetricReader();exporter=InMemorySpanExporter()
    telemetry=Telemetry(workspace[0].telemetry,metric_readers=[reader],span_processors=[SimpleSpanProcessor(exporter)])
    monitor=IntegrationMonitor(workspace[0],telemetry=telemetry)
    try:
        scheduled=monitor._run_topics()
        assert scheduled.input_count==3
        assert any(s.name=='topics.curate' for s in exporter.get_finished_spans())
        data=reader.get_metrics_data()
        names={metric.name for resource in data.resource_metrics for scope in resource.scope_metrics for metric in scope.metrics}
        assert {'paperflow.topics.runs','paperflow.topics.duration','paperflow.topics.candidates'}<=names
        newer=service.curate(at=now())
        assert monitor._run_topics().id==newer.id
    finally:
        telemetry.shutdown()


def test_topics_cli_calls_service_api(workspace,monkeypatch):
    from typer.testing import CliRunner
    from paperflow_cli import app
    from paperflow_api_client import APIClient
    calls=[]
    def fake(self,method,path,body=None):
        calls.append((method,path,body))
        return []
    monkeypatch.setattr(APIClient,'request',fake)
    result=CliRunner().invoke(app,['topics','--api-url','http://127.0.0.1:8766','list','--editorial'])
    assert result.exit_code==0,result.output
    assert calls==[('GET','/api/topics?editorial=true',None)]
    assert 'Latest' in result.output


def test_generic_headline_words_do_not_group_unrelated_articles(workspace):
    _, store, at = workspace
    ingest(store, at-timedelta(hours=1), [
        ('a', 'Markets down sharply'), ('b', 'Aircraft down safely'),
        ('c', 'Story about astronomy'), ('d', 'Story about gardening'),
        ('e', 'Ground stop delays flights'), ('f', 'Ground stop affects departures'),
    ])
    service = TopicService(store)
    service.curate(at=at)
    titles = {row.topic.title for row in service.summaries(at=at)}
    assert 'Down' not in titles and 'Story' not in titles
    assert 'Ground Stop' in titles


def test_static_edition_offers_only_current_published_topics(workspace,tmp_path):
    from paperflow_publish_site import build_site_from_storage
    service,at = seeded(workspace)
    service.curate(at=at)
    topic = service.summaries(at=at)[0].topic
    out = tmp_path / 'edition'
    def render(at):
        build_site_from_storage(workspace[1],out,now=at)
        return (out / 'index.html').read_text()
    option = f'<option value="topic:{topic.id}">{topic.title}</option>'
    assert option in render(at)
    assert option not in render(at+timedelta(hours=25))
    service.edit(topic.id,EditTopic(expected_version=topic.version,editor='editor',editorial_status='suppressed'))
    assert option not in render(at)
