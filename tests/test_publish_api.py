"""Build authorization, durable receipts, concurrency, and atomic publication."""
import hashlib
import json
import secrets
import threading
import time
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from paperflow_api import create_app
from paperflow_publish_api import BuildJob, BuildService
from paperflow_settings import ResearchSettings
from paperflow_storage_sqlite import SQLiteResearchStorage


def config(tmp_path, **publishing):
    token = secrets.token_urlsafe(40)
    token_file = tmp_path / 'token'
    token_file.write_text(token)
    settings = ResearchSettings(storage={'path': tmp_path/'db.sqlite'}, monitor={'enabled': False},
        publishing={'build_api_enabled': True, 'build_token_file': token_file,
                    'releases_dir': tmp_path/'publish/releases', **publishing})
    return settings, {'Authorization': 'Bearer ' + token}


def completed(client, headers, job_id):
    for _ in range(200):
        response = client.get(f'/api/publish/builds/{job_id}', headers=headers)
        assert response.status_code == 200
        job = response.json()
        if job['status'] != 'running':
            return job
        time.sleep(0.01)
    raise AssertionError('Build did not finish')


def test_build_api_requires_explicit_enablement_and_auth(tmp_path):
    with TestClient(create_app(ResearchSettings(storage={'path': tmp_path/'disabled.db'},
                                               monitor={'enabled': False}))) as client:
        assert client.post('/api/publish/builds').status_code == 404
    settings, headers = config(tmp_path)
    with TestClient(create_app(settings)) as client:
        for unauthorized in ({}, {'Authorization': 'Bearer wrong'}):
            assert client.post('/api/publish/builds', headers=unauthorized).status_code == 401
            assert client.get(f'/api/publish/builds/{uuid4()}', headers=unauthorized).status_code == 401
        assert not settings.publishing.releases_dir.exists()
        assert client.get(f'/api/publish/builds/{uuid4()}', headers=headers).status_code == 404


def test_build_receipt_and_release_survive_restart(tmp_path):
    settings, headers = config(tmp_path)
    with TestClient(create_app(settings)) as client:
        response = client.post('/api/publish/builds', headers=headers)
        assert response.status_code == 202
        job = completed(client, headers, response.json()['id'])
        assert job['status'] == 'succeeded'
        release = settings.publishing.releases_dir/job['release_id']
        manifest = json.loads((release/'build.json').read_text())
        assert manifest['release_id'] == job['release_id']
        assert manifest['build_id'] == job['build_id']
        assert manifest['index_sha256'] == hashlib.sha256((release/'index.html').read_bytes()).hexdigest()
        assert release.stat().st_mode & 0o777 == 0o755
        assert client.post('/api/publish/builds', headers=headers).json()['id'] == job['id']
    with TestClient(create_app(settings)) as restarted:
        assert restarted.get(f"/api/publish/builds/{job['id']}", headers=headers).json() == job


def test_overlapping_requests_across_services_share_one_build(tmp_path, monkeypatch):
    import paperflow_publish_api as module
    settings, _ = config(tmp_path)
    storage = SQLiteResearchStorage(settings.storage)
    storage.initialize()
    entered, finish = threading.Event(), threading.Event()
    real = module.build_site_from_storage
    calls = []
    def slow(*args, **kwargs):
        calls.append(1)
        entered.set()
        assert finish.wait(5)
        return real(*args, **kwargs)
    monkeypatch.setattr(module, 'build_site_from_storage', slow)
    first = BuildService(storage, settings.publishing)
    other = BuildService(storage, settings.publishing)
    try:
        job = first.request()
        assert entered.wait(3)
        assert other.request().id == job.id
        assert first.request().id == job.id
        assert not list(settings.publishing.releases_dir.iterdir())
        assert len(calls) == 1
    finally:
        finish.set()
        first.close()
        other.close()
    assert other.get(job.id).status == 'succeeded'


def test_failure_preserves_previous_release_and_hides_exception(tmp_path, monkeypatch):
    import paperflow_publish_api as module
    settings, headers = config(tmp_path, build_reuse_seconds=0)
    with TestClient(create_app(settings)) as client:
        first = completed(client, headers, client.post('/api/publish/builds', headers=headers).json()['id'])
        previous = settings.publishing.releases_dir/first['release_id']/'index.html'
        original = previous.read_bytes()
        def fail(*args, **kwargs):
            raise RuntimeError('sensitive failure detail')
        monkeypatch.setattr(module, 'build_site_from_storage', fail)
        # A completed receipt can become visible just before the worker releases
        # its lock; the documented 503 response asks clients to retry.
        for _ in range(200):
            response = client.post('/api/publish/builds', headers=headers)
            if response.status_code != 503:
                break
            time.sleep(.01)
        assert response.status_code == 202
        failed = completed(client, headers, response.json()['id'])
        assert failed['status'] == 'failed'
        assert failed['error'] == 'Static build failed.'
        assert previous.read_bytes() == original
        assert len(list(settings.publishing.releases_dir.iterdir())) == 1
        assert not list(settings.publishing.releases_dir.parent.glob('.staging-*'))


def test_abandoned_job_is_recovered_without_publishing_partial_output(tmp_path):
    from datetime import datetime, timezone
    settings, _ = config(tmp_path)
    service = BuildService(SQLiteResearchStorage(settings.storage), settings.publishing)
    lock = service._lock()
    job = BuildJob(id=uuid4(), status='running', created_at=datetime.now(timezone.utc))
    service._save(job)
    lock.close()  # Simulates a process exiting while building.
    assert service.get(job.id).status == 'failed'
    assert not settings.publishing.releases_dir.exists()
    service.close()


def test_empty_or_missing_credentials_fail_closed(tmp_path):
    with pytest.raises(ValueError):
        ResearchSettings(publishing={'build_api_enabled': True})
    settings, _ = config(tmp_path)
    settings.publishing.build_token_file.write_text('')
    with pytest.raises(ValueError, match='at least 32'):
        create_app(settings)


def test_public_snapshot_survives_recovery_and_excludes_diagnostics(tmp_path, monkeypatch):
    from paperflow_monitor import IntegrationMonitor

    settings, headers = config(tmp_path, build_reuse_seconds=0)
    settings.monitor.enabled = True
    from paperflow_telemetry import Telemetry
    monitor = IntegrationMonitor(settings, telemetry=Telemetry(settings.telemetry))
    # Exercise real publication with deterministic operator-only monitor evidence.
    monkeypatch.setattr(monitor, 'start', lambda: None)
    monkeypatch.setattr(monitor.store, 'channels', lambda: [])
    health = {'healthy': False, 'scheduler_running': True, 'scheduler_error': None,
              'overdue_runs': [], 'schedule_enabled': True, 'topics': {'error': None},
              'curation': {'enabled': True, 'healthy': False, 'error': 'CapacityExceededError',
                           'failure': {'limit': 'index_bytes', 'observed': 54216799}}}
    monkeypatch.setattr(monitor, 'health', lambda: health)
    with TestClient(create_app(settings, monitor=monitor)) as client:
        def publish():
            job = completed(client, headers, client.post('/api/publish/builds', headers=headers).json()['id'])
            assert job['status'] == 'succeeded'
            return settings.publishing.releases_dir / job['release_id']

        first = publish()
        health.update(healthy=True)
        health['curation']['healthy'] = True
        second = publish()
        assert client.get('/api/monitor/publication').json()['service_status'] == 'current'
        for release, state, message in [(first, 'curation_delayed', 'Curation is delayed'),
                                         (second, 'current', 'Updates were running normally')]:
            manifest = (release / 'build.json').read_text()
            page = (release / 'index.html').read_text()
            assert json.loads(manifest)['service_status'] == state and message in page
            assert all(value not in manifest + page for value in ('CapacityExceededError', 'index_bytes', '54216799'))
        monkeypatch.setattr(monitor, 'health', lambda: 1 / 0)
        unknown = publish()
        assert json.loads((unknown / 'build.json').read_text())['service_status'] == 'unknown'
