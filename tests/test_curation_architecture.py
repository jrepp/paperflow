"""Contracts for bounded reads, durable commands, domain rules and lifecycle."""
from __future__ import annotations

import copy
import json
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from paperflow_api import create_app
from paperflow_curation import CurationService
from paperflow_domain import Annotation, Claim, ContentRevision, EvidenceAssessment, Item, ResearchBucket, ItemRelationship
from paperflow_settings import ResearchSettings, StorageSettings
from paperflow_storage import ReferenceError
from paperflow_storage_sqlite import SQLiteResearchStorage


@pytest.fixture
def client(tmp_path):
    settings = ResearchSettings(storage={'path': tmp_path/'research.db'}, monitor={'enabled': False})
    with TestClient(create_app(settings)) as client:
        yield client


def post(client, path, body, key=None):
    response = client.post('/api/research'+path, json=body, headers={'Idempotency-Key': key} if key else {})
    assert response.status_code == 201, response.text
    return response.json()


def seed(client):
    item = post(client, '/items', {'kind':'article','title':'Source'})
    revision = post(client, f"/items/{item['id']}/revisions", {'markdown':'Evidence passage'})
    bucket = post(client, '/buckets', {'title':'Question','research_question':'Under what conditions?'})
    claim = post(client, f"/buckets/{bucket['id']}/claims", {'statement':'Scoped finding','creator':'reader'})
    return item, revision, bucket, claim


def test_idempotency_concurrent_replay_conflict_and_restart(tmp_path):
    settings = ResearchSettings(storage={'path':tmp_path/'retry.db'}, monitor={'enabled':False})
    with TestClient(create_app(settings)) as client:
        body = {'kind':'article','title':'Only once'}
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: post(client,'/items',body,'create-source'), range(8)))
        assert all(value == results[0] for value in results)
        assert len(client.get('/api/research/items').json()) == 1
        assert client.post('/api/research/items',json={**body,'title':'Different'},headers={'Idempotency-Key':'create-source'}).status_code == 409
        _, revision, bucket, claim = seed(client)
        review = {'decision':'needs_review','reviewer':'model','reviewer_kind':'processor','processor_version':'v1','processing_run_id':str(uuid4())}
        first = post(client,f"/claims/{claim['id']}/reviews",review,'review-1')
        assert post(client,f"/claims/{claim['id']}/reviews",review,'review-1') == first
        assert post(client,f"/claims/{claim['id']}/reviews",review,'review-2')['id'] != first['id']
        # Failed writes must not reserve the key.
        response = client.post(f"/api/research/revisions/{revision['id']}/annotations",json={'quote':'absent','creator':'reader'},headers={'Idempotency-Key':'annotation'})
        assert response.status_code == 422
        post(client,f"/revisions/{revision['id']}/annotations",{'quote':'Evidence','creator':'reader'},'annotation')
    with TestClient(create_app(settings)) as client:
        assert post(client,'/items',body,'create-source') == results[0]


def test_projection_pagination_and_backend_measurement(client):
    item, revision, bucket, claim = seed(client)
    post(client, f"/items/{item['id']}/revisions", {'markdown':'x'*100_000+'SECRET_TAIL'})
    for _ in range(13):
        post(client,f"/claims/{claim['id']}/evidence",{'revision_id':revision['id'],'stance':'qualifies','rationale':'Scope','creator':'reader'})
    small = client.get(f"/api/research/buckets/{bucket['id']}?view=compact")
    full = client.get(f"/api/research/buckets/{bucket['id']}")
    assert len(small.content) < len(full.content)/3
    assert int(small.headers['X-Paperflow-DB-Queries']) < int(full.headers['X-Paperflow-DB-Queries'])
    assert float(small.headers['X-Paperflow-Duration-Ms']) >= 0
    assert small.json()['claims'][0]['evidence_count'] == 13
    preview = client.get(f"/api/research/items/{item['id']}?view=compact")
    assert 'SECRET_TAIL' not in preview.text
    assert len(preview.content) < 5000
    page = client.get(f"/api/research/claims/{claim['id']}/evidence?limit=10").json()
    assert page['total'] == 13 and page['next_offset'] == 10
    second = client.get(f"/api/research/claims/{claim['id']}/evidence?offset=10&limit=10").json()
    assert len(second['records']) == 3 and second['next_offset'] is None
    assert {v['assessment']['id'] for v in page['records']}.isdisjoint(v['assessment']['id'] for v in second['records'])
    assert client.get('/api/research/buckets?limit=0').status_code == 422


def test_lifecycle_conflicts_history_and_preserved_evidence(client):
    item, revision, bucket, claim = seed(client)
    member = post(client,f"/buckets/{bucket['id']}/members",{'item_id':item['id']})
    evidence = post(client,f"/claims/{claim['id']}/evidence",{'revision_id':revision['id'],'stance':'supports','rationale':'Passage','creator':'reader'})
    path = f"/api/research/memberships/{member['id']}"
    change = {'expected_version':1,'creator':'reader','note':'Corrected','active':False}
    changed = client.patch(path,json=change)
    assert changed.status_code == 200 and changed.json()['version'] == 2
    assert client.patch(path,json=change).status_code == 409
    assert client.get(f"/api/research/buckets/{bucket['id']}/members").json()['total'] == 0
    history = client.get(path+'/history').json()['records']
    assert len(history) == 1 and history[0]['before']['note'] == ''
    assert history[0]['after']['note'] == 'Corrected'
    assert client.patch(path,json={'expected_version':2,'creator':'reader','active':True}).status_code == 200
    replacement = post(client,f"/buckets/{bucket['id']}/claims",{'statement':'Narrower finding','creator':'reader'})
    body = {'expected_version':1,'creator':'reader','replacement_claim_id':replacement['id']}
    assert client.post(f"/api/research/claims/{claim['id']}/supersede",json=body).status_code == 200
    cycle = {**body,'replacement_claim_id':claim['id']}
    assert client.post(f"/api/research/claims/{replacement['id']}/supersede",json=cycle).status_code == 422
    old = client.get(f"/api/research/claims/{claim['id']}").json()
    assert old['claim']['superseded_by'] == replacement['id']
    assert old['evidence'][0]['assessment']['id'] == evidence['id']
    assert client.patch(f"/api/research/buckets/{bucket['id']}",json={'expected_version':1,'creator':'reader','status':'archived'}).status_code == 200
    assert client.get(f"/api/research/buckets/{bucket['id']}/history").json()['total'] == 1


def test_provenance_identifiers_and_relationships(client):
    item, revision, bucket, claim = seed(client)
    processor = {'creator':'extractor','creator_kind':'processor'}
    response = client.post(f"/api/research/buckets/{bucket['id']}/claims",json={'statement':'Automated',**processor})
    assert response.status_code == 422
    response = client.post(f"/api/research/revisions/{revision['id']}/annotations",json={'quote':'Evidence',**processor})
    assert response.status_code == 422
    run = str(uuid4())
    annotation = post(client,f"/revisions/{revision['id']}/annotations",{'quote':'Evidence',**processor,'processor_version':'v2','processing_run_id':run})
    assert annotation['processing_run_id'] == run
    assert client.post(f"/api/research/claims/{claim['id']}/reviews",json={'decision':'accepted','reviewer':'agent','reviewer_kind':'processor'}).status_code == 422
    identity = post(client,f"/items/{item['id']}/identifiers",{'namespace':'DOI','value':'https://doi.org/10.1/ABC'})
    assert post(client,f"/items/{item['id']}/identifiers",{'namespace':'doi','value':'10.1/abc'}) == identity
    assert client.get('/api/research/identifiers/resolve',params={'namespace':'DOI','value':'doi:10.1/ABC'}).json()['id'] == item['id']
    paper = post(client,'/items',{'kind':'paper','title':'Research'})
    assert client.post(f"/api/research/items/{paper['id']}/identifiers",json={'namespace':'doi','value':'10.1/abc'}).status_code == 409
    relationship = {'target_item_id':paper['id'],'kind':'cites','creator':'reader','source_revision_id':revision['id']}
    post(client,f"/items/{item['id']}/relationships",relationship)
    assert client.get(f"/api/research/items/{paper['id']}/incoming-relationships").json()['total'] == 1
    assert client.post(f"/api/research/items/{paper['id']}/relationships",json={**relationship,'target_item_id':item['id']}).status_code == 422
    assert client.post(f"/api/research/items/{item['id']}/relationships",json={**relationship,'kind':'supports'}).status_code == 422


class UncheckedMemoryStorage:
    """Minimal record port with no domain rules, to test service-owned invariants."""
    def __init__(self):
        self.records = {}

    @contextmanager
    def transaction(self, **kwargs):
        original = copy.deepcopy(self.records)
        try:
            yield self
        except BaseException:
            self.records = original
            raise

    def get(self, model, record_id):
        return self.records.get((model, record_id))

    def add(self, record):
        self.records[type(record), record.id] = record
        return record


@pytest.fixture(params=['sqlite','unchecked-memory'])
def record_service(request, tmp_path):
    if request.param == 'sqlite':
        store = SQLiteResearchStorage(StorageSettings(path=tmp_path/'contract.db'))
        store.initialize()
    else:
        store = UncheckedMemoryStorage()
    return CurationService(store)


def test_shared_record_validation_contract(record_service):
    service = record_service
    item = service.add(Item(kind='article',title='A'))
    other = service.add(Item(kind='paper',title='B'))
    revision = service.add(ContentRevision.from_markdown(item_id=item.id,markdown='Exact quote'))
    second = service.add(ContentRevision.from_markdown(item_id=other.id,markdown='Different'))
    annotation = service.add(Annotation(revision_id=revision.id,quote='Exact',start=0,end=5,creator='r'))
    claim = service.add(Claim(statement='C',creator='r'))
    invalid = [ResearchBucket(title='Q',research_question='Why?',target_manuscript_id=item.id),
               Annotation(revision_id=revision.id,quote='Wrong',start=0,end=5,creator='r'),
               EvidenceAssessment(claim_id=claim.id,revision_id=second.id,annotation_id=annotation.id,stance='supports',rationale='Wrong',creator='r'),
               ItemRelationship(source_item_id=other.id,target_item_id=item.id,kind='cites',source_revision_id=revision.id,creator='r')]
    for record in invalid:
        with pytest.raises(ReferenceError):
            service.add(record)
        with service.storage.transaction() as repo:
            assert repo.get(type(record),record.id) is None


def test_api_only_process_does_not_acquire_scheduler_lock(tmp_path):
    settings = ResearchSettings(storage={'path':tmp_path/'shared.db'}, monitor={'schedule_enabled':False})
    api_only = settings.model_copy(update={'monitor':settings.monitor.model_copy(update={'enabled':False})})
    with TestClient(create_app(settings)) as owner:
        with TestClient(create_app(api_only)) as reader:
            assert reader.get('/health/ready').status_code == 200
            assert reader.get('/api/research/buckets').status_code == 200
            assert reader.get('/api/monitor/channels').status_code == 503
            post(reader,'/items',{'kind':'article','title':'Independent API'})
            assert len(owner.get('/api/research/items').json()) == 1


def test_v2_migration_preserves_records_and_marks_missing_provenance(tmp_path):
    path = tmp_path/'legacy.db'
    store = SQLiteResearchStorage(StorageSettings(path=path))
    store.initialize()
    claim_id, review_id = str(uuid4()), str(uuid4())
    created = '2026-01-01T00:00:00Z'
    with sqlite3.connect(path) as conn:
        conn.execute('UPDATE research_schema_version SET version=2')
        conn.execute('INSERT INTO research_claims VALUES (?,NULL,?)',(claim_id,json.dumps({'id':claim_id,'created_at':created,'statement':'Legacy','creator':'agent','creator_kind':'processor'})))
        conn.execute('INSERT INTO research_claim_reviews VALUES (?,?,?)',(review_id,claim_id,json.dumps({'id':review_id,'created_at':created,'claim_id':claim_id,'decision':'needs_review','reviewer':'agent'})))
    store.initialize()
    store.initialize()
    with store.transaction() as repo:
        from uuid import UUID
        from paperflow_domain import ClaimReview
        assert repo.get(Claim,UUID(claim_id)).processor_version == 'legacy-unrecorded'
        assert repo.get(ClaimReview,UUID(review_id)).reviewer_kind == 'unknown'
    with sqlite3.connect(path) as conn:
        assert conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert conn.execute('PRAGMA foreign_key_check').fetchall() == []


def test_api_otel_records_backend_work(tmp_path):
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from paperflow_monitor import IntegrationMonitor
    from paperflow_telemetry import Telemetry
    settings = ResearchSettings(storage={'path':tmp_path/'metrics.db'}, monitor={'enabled':False})
    reader, exporter = InMemoryMetricReader(), InMemorySpanExporter()
    telemetry = Telemetry(settings.telemetry, metric_readers=[reader], span_processors=[SimpleSpanProcessor(exporter)])
    monitor = IntegrationMonitor(settings, telemetry=telemetry)
    try:
        with TestClient(create_app(settings, monitor=monitor)) as client:
            response = client.get('/api/research/buckets')
            assert response.status_code == 200
            span = next(span for span in exporter.get_finished_spans() if span.name == 'http.request')
            assert span.attributes['db.query.count'] == int(response.headers['X-Paperflow-DB-Queries'])
            assert span.attributes['http.response.body.size'] == len(response.content)
            data = reader.get_metrics_data()
            names = {metric.name for resource in data.resource_metrics for scope in resource.scope_metrics for metric in scope.metrics}
            assert {'paperflow.api.duration','paperflow.api.response.size','paperflow.api.db.queries'} <= names
    finally:
        telemetry.shutdown()


def test_openapi_uses_one_path_for_collection_reads_and_writes(client):
    schema = client.get('/openapi.json').json()
    for path in ('/api/research/items/{item_id}/revisions',
                 '/api/research/revisions/{revision_id}/annotations',
                 '/api/research/buckets/{bucket_id}/members'):
        assert {'get','post'} <= schema['paths'][path].keys()
    assert not any('{parent_id}' in path for path in schema['paths'])
    assert client.get('/api/research/items/not-a-uuid/revisions').status_code == 422


def test_command_rolls_back_partial_writes_and_receipt(tmp_path):
    store = SQLiteResearchStorage(StorageSettings(path=tmp_path/'atomic.db'))
    store.initialize()
    service = CurationService(store)
    item = Item(kind='article',title='Must roll back')
    def interrupted(unit):
        unit.add(item)
        raise RuntimeError('interrupted before receipt')
    with pytest.raises(RuntimeError):
        service.command('retry-me','create',{},interrupted)
    with store.transaction() as repo:
        assert repo.get(Item,item.id) is None
        assert repo.receipt('retry-me') is None
    result = service.command('retry-me','create',{},lambda unit: unit.add(item))
    assert result['id'] == str(item.id)
