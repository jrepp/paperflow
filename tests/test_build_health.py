import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from fastapi.testclient import TestClient
import pytest

from paperflow_api import create_app
from paperflow_settings import ResearchSettings
from paperflow_monitor import IntegrationMonitor
from paperflow_heartbeat import ping


def test_version_matches_package_and_build_without_host_details(tmp_path, monkeypatch):
    monkeypatch.setenv('PAPERFLOW_BUILD_COMMIT', 'a'*40)
    monkeypatch.setenv('PAPERFLOW_BUILD_CHANNEL', 'nightly')
    settings = ResearchSettings(storage={'path': tmp_path/'db'}, monitor={'enabled': False})
    with TestClient(create_app(settings)) as client:
        info = client.get('/api/version').json()
        assert info['commit'] == 'a'*40 and info['channel'] == 'nightly'
        assert info['version'] == client.get('/openapi.json').json()['info']['version']
        assert set(info) == {'version', 'commit', 'channel', 'built_at'}
        assert client.get('/health/live').status_code == 200
        assert client.get('/health/ready').json()['healthy']


def test_heartbeat_reports_monitor_health_without_exposing_secret(tmp_path, monkeypatch):
    settings = ResearchSettings(storage={'path': tmp_path/'db'}, monitor={
        'heartbeat_url': 'https://example.org/private-token', 'schedule_enabled': False})
    monitor = IntegrationMonitor(settings)
    calls = []
    monkeypatch.setattr('paperflow_heartbeat.ping', lambda url, healthy: calls.append((url, healthy)))
    monkeypatch.setattr(monitor, 'health', lambda: {'healthy': True})
    assert monitor._send_heartbeat() == 'success'
    monkeypatch.setattr(monitor, 'health', lambda: {'healthy': False})
    assert monitor._send_heartbeat() == 'failure'
    assert [value[1] for value in calls] == [True, False]
    assert 'private-token' not in str(settings.model_dump())
    assert 'private-token' not in str(monitor.heartbeat_status)


def test_heartbeat_does_not_follow_redirects_or_accept_false_acknowledgments():
    paths = []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            paths.append(self.path)
            self.send_response(302 if self.path == '/redirect' else 200)
            if self.path == '/redirect':
                self.send_header('Location', '/leaked')
            self.end_headers()
            self.wfile.write(b'OK (not found)' if self.path == '/missing' else b'OK')
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    base = 'http://127.0.0.1:'+str(server.server_port)
    try:
        ping(base+'/check', True)
        ping(base+'/check', False)
        with pytest.raises(Exception):
            ping(base+'/redirect', True)
        with pytest.raises(RuntimeError):
            ping(base+'/missing', True)
        assert paths == ['/check', '/check/fail', '/redirect', '/missing']
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def test_flat_heartbeat_environment_is_private_and_optional(monkeypatch):
    monkeypatch.setenv('PAPERFLOW_HEARTBEAT_URL', 'https://example.org/private-check')
    settings = ResearchSettings()
    assert settings.monitor.heartbeat_url.get_secret_value().endswith('/private-check')
    assert 'private-check' not in str(settings.model_dump())
    monkeypatch.delenv('PAPERFLOW_HEARTBEAT_URL')
    assert ResearchSettings().monitor.heartbeat_url is None
