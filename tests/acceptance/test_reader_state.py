"""Local reading persistence, revision anchors, and feed stability."""
from pathlib import Path
import sqlite3
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from paperflow_api import create_app
from paperflow_settings import ResearchSettings


@pytest.fixture
def settings(tmp_path):
    return ResearchSettings(storage={'path':tmp_path/'reader.db'}, monitor={'enabled':False})


def post(client, path, body):
    response = client.post('/api/research'+path, json=body)
    assert response.status_code == 201, response.text
    return response.json()


def test_saved_position_preferences_and_old_revision_survive_restart(settings):
    with TestClient(create_app(settings)) as client:
        item = post(client, '/items', {'kind':'article','title':'Read me'})
        revision = post(client, f"/items/{item['id']}/revisions", {'markdown':'First paragraph.\n\nSecond paragraph.'})
        path = f"/api/reader/items/{item['id']}/state"
        body = {'expected_version':0, 'saved':True, 'status':'in_progress', 'anchor':{'revision_id':revision['id'], 'character_offset':18}}
        first = client.patch(path,json=body)
        assert first.status_code == 200
        assert client.patch(path,json=body).json() == first.json()
        assert client.patch(path,json={**body,'saved':False}).status_code == 409
        prefs = client.put('/api/reader/preferences',json={'expected_version':0,'preferences':{'font_size':24,'theme':'night'}})
        assert prefs.status_code == 200
        latest = post(client, f"/items/{item['id']}/revisions", {'markdown':'A newer version.'})
    with TestClient(create_app(settings)) as client:
        saved = client.get('/api/reader/feed?view=saved').json()['records']
        assert [row['item']['id'] for row in saved] == [item['id']]
        assert saved[0]['state'] == first.json()
        document = client.get(f"/api/reader/items/{item['id']}").json()
        assert document['revision']['id'] == revision['id']
        assert document['latest_revision_id'] == latest['id']
        assert client.get(f"/api/reader/items/{item['id']}?latest=true").json()['revision']['id'] == latest['id']
        assert client.get('/api/reader/preferences').json()['font_size'] == 24
        assert client.get('/api/reader/preferences').json()['theme'] == 'night'
        assert client.patch(path,json={'expected_version':1,'saved':False}).status_code == 200
        assert not client.get('/api/reader/feed?view=saved').json()['records']


def test_invalid_anchor_cannot_cross_sources_or_exceed_text(settings):
    with TestClient(create_app(settings)) as client:
        item = post(client,'/items',{'kind':'article','title':'One'})
        other = post(client,'/items',{'kind':'article','title':'Two'})
        revision = post(client,f"/items/{other['id']}/revisions",{'markdown':'Short'})
        body = {'expected_version':0,'anchor':{'revision_id':revision['id'],'character_offset':0}}
        assert client.patch(f"/api/reader/items/{item['id']}/state",json=body).status_code == 422
        body['anchor']['character_offset'] = 6
        assert client.patch(f"/api/reader/items/{other['id']}/state",json=body).status_code == 422
        assert client.get(f"/api/reader/items/{other['id']}/state").json()['version'] == 0


def test_feed_keyset_does_not_repeat_rows_when_new_content_arrives(settings):
    with TestClient(create_app(settings)) as client:
        for i in range(5):
            post(client,'/items',{'kind':'article','title':f'Reading {i}'})
        page = client.get('/api/reader/feed?limit=3').json()
        assert len(page['records']) == 3
        new = post(client,'/items',{'kind':'article','title':'New arrival'})
        second = client.get(f"/api/reader/feed?limit=3&before={page['next_before']}").json()
        ids = [r['item']['id'] for r in page['records']+second['records']]
        assert len(set(ids)) == 5 and new['id'] not in ids
        assert second['next_before'] is None
        assert client.get('/api/reader/feed?limit=0').status_code == 422
        assert client.get('/api/reader/feed?channel=missing').json()['records'] == []


def test_schema_upgrade_backup_and_reader_shell(settings, tmp_path):
    with TestClient(create_app(settings)) as client:
        item = post(client,'/items',{'kind':'article','title':'Survives upgrade'})
    with sqlite3.connect(settings.storage.path) as conn:
        conn.execute('DROP TABLE reader_states')
        conn.execute('DROP TABLE reader_preferences')
        conn.execute('UPDATE research_schema_version SET version=3')
    with TestClient(create_app(settings)) as client:
        assert client.get('/api/reader/feed').json()['records'][0]['item']['id'] == item['id']
        assert client.patch(f"/api/reader/items/{item['id']}/state",json={'expected_version':0,'saved':True}).status_code == 200
        for path in ['/', '/saved', '/research', '/sources', f"/read/{item['id']}", '/reader-assets/reader.css', '/reader-assets/reader.js']:
            assert client.get(path).status_code == 200
    backup = tmp_path/'backup.db'
    with sqlite3.connect(settings.storage.path) as source, sqlite3.connect(backup) as target:
        source.backup(target)
    restored = settings.model_copy(update={'storage':settings.storage.model_copy(update={'path':backup})})
    with TestClient(create_app(restored)) as client:
        assert len(client.get('/api/reader/feed?view=saved').json()['records']) == 1


def test_pdf_attachment_is_validated_immutable_and_durable(settings):
    data=(Path(__file__).resolve().parents[1]/'fixtures/reader-two-pages.pdf').read_bytes()
    with TestClient(create_app(settings)) as client:
        item=post(client,'/items',{'kind':'paper','title':'PDF source'})
        path=f"/api/reader/items/{item['id']}/pdf"
        assert client.get(path).status_code == 404
        assert client.put(path,content=data).status_code == 415
        assert client.put(path,content=b'not a PDF',headers={'Content-Type':'application/pdf'}).status_code == 422
        response=client.put(path,content=data,headers={'Content-Type':'application/pdf'})
        assert response.status_code == 200 and response.json()['page_count'] == 2
        assert client.put(path,content=data,headers={'Content-Type':'application/pdf'}).json() == response.json()
        assert client.put(path,content=data+b'\n',headers={'Content-Type':'application/pdf'}).status_code == 409
    with TestClient(create_app(settings)) as client:
        assert client.get(path).content == data
        assert client.get(f"/api/reader/items/{item['id']}").json()['pdf']['page_count'] == 2


def test_channel_stats_follow_saved_view_search_and_dismissal(settings):
    from paperflow_storage_sqlite import SQLiteResearchStorage
    from paperflow_ingestion import persist_batch
    from paperflow_ingest_models import ChannelConfig, IngestBatch, SourceEntry
    store=SQLiteResearchStorage(settings.storage);store.initialize()
    batch=IngestBatch(channel='science',config=ChannelConfig(url='https://example.org/feed'),entries=(SourceEntry(source_id='1',title='Useful research',url='https://example.org/article'),))
    persist_batch(store,batch);persist_batch(store,batch)
    with TestClient(create_app(settings)) as client:
        stats=client.get('/api/reader/channels').json()[0]
        assert stats['item_count'] == stats['visible_count'] == stats['unread_count'] == 1
        assert client.get('/api/reader/channels?view=saved').json()[0]['visible_count'] == 0
        item=client.get('/api/reader/feed').json()['records'][0]['item']
        client.patch(f"/api/reader/items/{item['id']}/state",json={'expected_version':0,'saved':True,'status':'dismissed'})
        assert client.get('/api/reader/channels').json()[0]['visible_count'] == 0
        assert client.get('/api/reader/channels?view=saved').json()[0]['visible_count'] == 1
        assert client.get('/api/reader/channels?view=saved&query=absent').json()[0]['visible_count'] == 0


def test_content_type_filter_combines_with_source_saved_search_and_pagination(settings):
    from paperflow_ingest_models import IngestBatch, ChannelConfig, SourceEntry
    from paperflow_ingestion import persist_batch
    from paperflow_storage_sqlite import SQLiteResearchStorage
    store=SQLiteResearchStorage(settings.storage);store.initialize()
    for kind in ('paper','article','discussion'):
        persist_batch(store,IngestBatch(channel='mixed',config=ChannelConfig(url='https://example.org/feed',kind=kind),
            entries=tuple(SourceEntry(source_id=f'{kind}-{i}',title=f'{kind} research {i}',url=f'https://example.org/{kind}/{i}') for i in range(3))))
    with TestClient(create_app(settings)) as client:
        page=client.get('/api/reader/feed?kind=paper&channel=mixed&query=research&limit=2').json()
        assert len(page['records'])==2 and all(r['item']['kind']=='paper' for r in page['records'])
        second=client.get(f"/api/reader/feed?kind=paper&channel=mixed&limit=2&before={page['next_before']}").json()
        assert len(second['records'])==1
        assert not {r['item']['id'] for r in page['records']} & {r['item']['id'] for r in second['records']}
        item=page['records'][0]['item']
        client.patch(f"/api/reader/items/{item['id']}/state",json={'expected_version':0,'saved':True})
        assert len(client.get('/api/reader/feed?kind=paper&view=saved').json()['records'])==1
        assert client.get('/api/reader/feed?kind=article&view=saved').json()['records']==[]
        stats=client.get('/api/reader/channels?kind=paper&view=saved').json()[0]
        assert stats['visible_count']==stats['saved_count']==1
        run=client.post('/api/topics/runs');assert run.status_code==201
        topics=client.get('/api/topics/latest?kind=paper').json()['topics']
        for row in topics:
            feed=client.get(f"/api/reader/feed?kind=paper&topic={row['topic']['id']}").json()
            assert len(feed['records'])==row['item_count']
        assert client.get('/api/reader/feed?kind=unknown').status_code==422
        assert client.get('/api/reader/channels?kind=unknown').status_code==422
