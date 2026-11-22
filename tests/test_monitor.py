from __future__ import annotations

import sqlite3
import sys
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from paperflow_api import create_app
from paperflow_ingestion import default_registry
from paperflow_monitor import IntegrationMonitor
from paperflow_monitor_store import MonitorStore
from paperflow_settings import ResearchSettings
from paperflow_telemetry import Telemetry

RSS = b'<rss version="2.0"><channel><title>Test</title><item><guid>one</guid><title>Example</title><link>https://example.org/a</link><description>Excerpt</description></item></channel></rss>'


def settings(tmp_path, **monitor):
    return ResearchSettings(storage={"path": tmp_path / "test.db"},
        monitor={"schedule_enabled": False, "poll_seconds": 0.05, **monitor},
        ingestion={"channels": {"test": {"url": "https://example.org/feed", "interval_seconds": 10}}})


def wait_for(call, predicate=lambda value: bool(value), timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = call()
        if predicate(value):
            return value
        time.sleep(0.01)
    raise AssertionError(f"condition not reached, last value: {value}")


def completed(client, run_id):
    return wait_for(lambda: client.get(f"/api/monitor/runs/{run_id}").json(),
                    lambda r: r["status"] not in {"queued", "running"})


def test_api_manual_run_persists_content_and_survives_restart(tmp_path):
    config = settings(tmp_path)
    monitor = IntegrationMonitor(config, transport=lambda url: RSS)
    with TestClient(create_app(config, monitor=monitor)) as client:
        assert client.get("/health/live").status_code == 200
        wait_for(lambda: client.get("/health/ready").status_code == 200)
        assert client.get("/api/monitor/channels").json()[0]["stale"]
        response = client.post("/api/monitor/channels/test/runs")
        assert response.status_code == 202
        run_id = response.json()["id"]
        run = completed(client, run_id)
        assert run["status"] == "succeeded"
        assert run["item_count"] == 1
        assert run["duration_seconds"] >= 0
        assert len(run["trace_id"]) == 32
        assert not client.get("/api/monitor/channels").json()[0]["stale"]
        assert client.get("/api/monitor/runs?limit=0").status_code == 422
        assert client.post("/api/monitor/channels/missing/runs").status_code == 404
    with sqlite3.connect(config.storage.path) as conn:
        assert conn.execute("SELECT count(*) FROM research_items").fetchone()[0] == 1
    replacement = IntegrationMonitor(config, transport=lambda url: RSS)
    with TestClient(create_app(config, monitor=replacement)) as client:
        assert client.get(f"/api/monitor/runs/{run_id}").json()["status"] == "succeeded"


def test_single_active_run_and_pause_state(tmp_path):
    config = settings(tmp_path)
    started, release = threading.Event(), threading.Event()
    def fetch(url):
        started.set()
        assert release.wait(5)
        return RSS
    monitor = IntegrationMonitor(config, transport=fetch)
    with TestClient(create_app(config, monitor=monitor)) as client:
        first = client.post("/api/monitor/channels/test/runs").json()
        assert started.wait(3)
        try:
            assert client.post("/api/monitor/channels/test/runs").json()["id"] == first["id"]
            assert client.patch("/api/monitor/channels/test", json={"paused": True}).status_code == 200
        finally:
            release.set()
        completed(client, first["id"])
        assert client.post("/api/monitor/channels/test/runs").status_code == 409
    second = IntegrationMonitor(config, transport=lambda url: RSS)
    with TestClient(create_app(config, monitor=second)) as client:
        assert client.get("/api/monitor/channels").json()[0]["paused"]
        client.patch("/api/monitor/channels/test", json={"paused": False})
        assert client.post("/api/monitor/channels/test/runs").status_code == 202


def test_scheduler_failure_backoff_and_recovery(tmp_path):
    config = settings(tmp_path, schedule_enabled=True, retry_seconds=1, max_retry_seconds=2)
    calls = 0
    def fetch(url):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("do not expose private body")
        return RSS
    monitor = IntegrationMonitor(config, transport=fetch)
    with TestClient(create_app(config, monitor=monitor)) as client:
        failed = wait_for(lambda: client.get("/api/monitor/runs").json(), lambda rows: rows and rows[0]["status"] == "failed")
        assert failed[0]["error"] == "OSError"
        channel = client.get("/api/monitor/channels").json()[0]
        assert channel["consecutive_failures"] == 1
        succeeded = wait_for(lambda: client.get("/api/monitor/runs").json(), lambda rows: rows and rows[0]["status"] == "succeeded")
        assert len(succeeded) == 2
        assert client.get("/api/monitor/channels").json()[0]["consecutive_failures"] == 0


def test_exclusive_owner_and_interrupted_run_recovery(tmp_path):
    config = settings(tmp_path)
    store = MonitorStore(config.storage, config.monitor)
    store.initialize(config.ingestion.channels)
    original = store.enqueue("test")
    store.claim()  # Simulate a prior process disappearing after claim.
    monitor = IntegrationMonitor(config, transport=lambda url: RSS)
    monitor.start()
    try:
        assert store.get_run(original.id).status == "interrupted"
        other = IntegrationMonitor(config, transport=lambda url: RSS)
        with pytest.raises(RuntimeError, match="Another monitor"):
            other.start()
    finally:
        monitor.close()
    assert not monitor.accepting


def test_completion_and_content_are_atomic(tmp_path, monkeypatch):
    config = settings(tmp_path)
    store = MonitorStore(config.storage, config.monitor)
    store.initialize(config.ingestion.channels)
    run = store.enqueue("test")
    store.claim()
    batch = default_registry().collect("test", config.ingestion.channels["test"], lambda url: RSS)
    from paperflow_storage_sqlite import SQLiteResearchRepository
    from paperflow_ingest_models import SourceObservation
    original = SQLiteResearchRepository.add
    def fail(self, record):
        if isinstance(record, SourceObservation):
            raise RuntimeError("failure after creating item")
        return original(self, record)
    monkeypatch.setattr(SQLiteResearchRepository, "add", fail)
    with pytest.raises(RuntimeError):
        store.finish(run.id, batch=batch, error=None, duration=1)
    assert store.get_run(run.id).status == "running"
    with sqlite3.connect(config.storage.path) as conn:
        assert conn.execute("SELECT count(*) FROM research_items").fetchone()[0] == 0


def test_otel_traces_metrics_and_degraded_enrichment(tmp_path):
    config = settings(tmp_path)
    exporter, reader = InMemorySpanExporter(), InMemoryMetricReader()
    telemetry = Telemetry(config.telemetry, span_processors=[SimpleSpanProcessor(exporter)], metric_readers=[reader])
    config.ingestion.channels["test"] = config.ingestion.channels["test"].model_copy(update={"enrichers": ("hackernews",)})
    monitor = IntegrationMonitor(config, transport=lambda url: RSS, telemetry=telemetry)
    try:
        with TestClient(create_app(config, monitor=monitor)) as client:
            run_id = client.post("/api/monitor/channels/test/runs").json()["id"]
            assert completed(client, run_id)["status"] == "degraded"
        spans = exporter.get_finished_spans()
        assert {s.name for s in spans} >= {"ingestion.run", "ingestion.fetch", "ingestion.persist"}
        root = next(s for s in spans if s.name == "ingestion.run")
        assert root.attributes["channel"] == "test"
        data = reader.get_metrics_data()
        names = {m.name for r in data.resource_metrics for scope in r.scope_metrics for m in scope.metrics}
        assert {"paperflow.ingestion.runs", "paperflow.ingestion.duration", "paperflow.ingestion.items"} <= names
    finally:
        telemetry.shutdown()


def test_deadline_prevents_late_persistence(tmp_path):
    config = settings(tmp_path, run_timeout_seconds=1)
    def slow(url):
        time.sleep(1.1)
        return RSS
    monitor = IntegrationMonitor(config, transport=slow)
    with TestClient(create_app(config, monitor=monitor)) as client:
        run_id = client.post("/api/monitor/channels/test/runs").json()["id"]
        result = completed(client, run_id)
        assert result["status"] == "failed"
        assert result["error"] == "TimeoutError"
        with sqlite3.connect(config.storage.path) as conn:
            assert conn.execute("SELECT count(*) FROM research_items").fetchone()[0] == 0


def test_serve_uses_explicit_settings_without_launching_server(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    from paperflow_cli import app
    config = tmp_path / "config.yaml"
    config.write_text("research:\n  service:\n    port: 8123\n")
    calls = []
    monkeypatch.setattr("uvicorn.run", lambda app, **kwargs: calls.append(kwargs))
    result = CliRunner().invoke(app, ["serve", "--config", str(config), "--port", "8124"])
    assert result.exit_code == 0, result.output
    assert calls == [{"host": "127.0.0.1", "port": 8124, "workers": 1}]


def test_retry_after_is_honored(tmp_path):
    import urllib.error
    from paperflow_monitor import retry_after_seconds
    assert retry_after_seconds("120") == 120
    assert retry_after_seconds("invalid") == 0
    assert retry_after_seconds("inf") == 0
    config = settings(tmp_path)
    def fetch(url):
        raise urllib.error.HTTPError(url, 429, "sensitive upstream body", {"Retry-After": "7200"}, None)
    monitor = IntegrationMonitor(config, transport=fetch)
    with TestClient(create_app(config, monitor=monitor)) as client:
        run_id = client.post("/api/monitor/channels/test/runs").json()["id"]
        assert completed(client, run_id)["error"] == "HTTP 429"
        from datetime import datetime
        channel = client.get("/api/monitor/channels").json()[0]
        assert datetime.fromisoformat(channel["next_due_at"]).timestamp() > time.time() + 7100


def test_slow_channel_does_not_block_another_channel(tmp_path):
    config = settings(tmp_path, workers=2)
    from paperflow_ingest_models import ChannelConfig
    config.ingestion.channels["fast"] = ChannelConfig(url="https://fast.example/feed")
    release, entered = threading.Event(), threading.Event()
    def fetch(url):
        if "fast.example" not in url:
            entered.set()
            assert release.wait(5)
        return RSS
    monitor = IntegrationMonitor(config, transport=fetch)
    with TestClient(create_app(config, monitor=monitor)) as client:
        slow = client.post("/api/monitor/channels/test/runs").json()["id"]
        assert entered.wait(3)
        try:
            fast = client.post("/api/monitor/channels/fast/runs").json()["id"]
            assert completed(client, fast)["status"] == "succeeded"
            assert client.get(f"/api/monitor/runs/{slow}").json()["status"] == "running"
        finally:
            release.set()
        completed(client, slow)


def test_otlp_export_to_local_receiver(tmp_path):
    pytest.importorskip("opentelemetry.exporter.otlp.proto.http.trace_exporter")
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from paperflow_settings import TelemetrySettings
    received = []
    class Receiver(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append((self.path, len(self.rfile.read(int(self.headers["Content-Length"])))))
            self.send_response(200)
            self.send_header("Content-Type", "application/x-protobuf")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Receiver)
    receiver = threading.Thread(target=server.serve_forever, daemon=True)
    receiver.start()
    telemetry = Telemetry(TelemetrySettings(otlp_endpoint=f"http://127.0.0.1:{server.server_port}"))
    try:
        config = settings(tmp_path)
        monitor = IntegrationMonitor(config, transport=lambda url: RSS, telemetry=telemetry)
        with TestClient(create_app(config, monitor=monitor)) as client:
            run_id = client.post("/api/monitor/channels/test/runs").json()["id"]
            assert completed(client, run_id)["status"] == "succeeded"
        telemetry.shutdown()
        assert {path for path, size in received if size > 0} >= {"/v1/traces", "/v1/metrics"}
    finally:
        server.shutdown()
        server.server_close()
        receiver.join()


def test_jitter_spreads_startup_preserves_future_due_and_manual_fetch_is_immediate(tmp_path, monkeypatch):
    from paperflow_ingest_models import ChannelConfig
    clock=[1000.0]
    monkeypatch.setattr('paperflow_monitor_store.time.time',lambda:clock[0])
    draws=iter([0.25,0.75,0.5,0.5])
    monkeypatch.setattr('paperflow_monitor_store.random.uniform',lambda low,high:high*next(draws))
    config=settings(tmp_path,jitter_fraction=0.2,jitter_max_seconds=3)
    config.ingestion.channels['other']=ChannelConfig(url='https://other.example/feed',interval_seconds=10)
    store=MonitorStore(config.storage,config.monitor);store.initialize(config.ingestion.channels)
    due={c.name:c.next_due_at.timestamp() for c in store.channels()}
    assert due=={'test':1000.5,'other':1001.5}
    store.schedule_due();assert store.runs()==[]
    store.initialize(config.ingestion.channels)
    assert {c.name:c.next_due_at.timestamp() for c in store.channels()}==due
    run=store.enqueue('test')
    assert store.claim()[0].id==run.id
    clock[0]=1001.5;store.schedule_due()
    assert store.claim()[0].channel=='other'


def test_jitter_applies_to_completion_retry_cooldown_recovery_and_resume(tmp_path, monkeypatch):
    from paperflow_ingestion import default_registry
    clock=[1000.0]
    monkeypatch.setattr('paperflow_monitor_store.time.time',lambda:clock[0])
    monkeypatch.setattr('paperflow_monitor_store.random.uniform',lambda low,high:high)
    config=settings(tmp_path,jitter_fraction=0.2,jitter_max_seconds=3,retry_seconds=4,max_retry_seconds=5)
    store=MonitorStore(config.storage,config.monitor);store.initialize(config.ingestion.channels)
    run=store.enqueue('test');store.claim()
    batch=default_registry().collect('test',config.ingestion.channels['test'],lambda url:RSS)
    store.finish(run.id,batch=batch,error=None,duration=1)
    assert store.channels()[0].next_due_at.timestamp()==1012
    run=store.enqueue('test');store.claim()
    store.finish(run.id,batch=None,error='OSError',duration=1)
    assert store.channels()[0].next_due_at.timestamp()==1004.8
    run=store.enqueue('test');store.claim()
    store.finish(run.id,batch=None,error='HTTP 429',duration=1,retry_after_seconds=100)
    assert store.channels()[0].next_due_at.timestamp()==1103
    run=store.enqueue('test');store.claim();store.recover()
    assert store.get_run(run.id).status=='interrupted'
    assert store.channels()[0].next_due_at.timestamp()==1002
    store.pause('test',True);clock[0]=2000;store.pause('test',False)
    assert store.channels()[0].next_due_at.timestamp()==2002


def test_jitter_can_be_disabled(tmp_path, monkeypatch):
    monkeypatch.setattr('paperflow_monitor_store.time.time',lambda:1000.0)
    config=settings(tmp_path,jitter_fraction=0)
    store=MonitorStore(config.storage,config.monitor);store.initialize(config.ingestion.channels)
    assert store.channels()[0].next_due_at.timestamp()==1000
    store.schedule_due();run,_=store.claim()
    store.finish(run.id,batch=None,error='OSError',duration=1)
    assert store.channels()[0].next_due_at.timestamp()==1060


def test_publication_status_respects_feed_pause_schedule_and_failures(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from paperflow_monitor_models import PublicationStatus

    monitor = IntegrationMonitor(settings(tmp_path))
    health = {'healthy': True, 'scheduler_running': True, 'scheduler_error': None,
              'overdue_runs': [], 'schedule_enabled': True, 'topics': {'error': None},
              'curation': {'enabled': False}}
    monkeypatch.setattr(monitor, 'health', lambda: health)
    feed = SimpleNamespace(paused=False, stale=True, consecutive_failures=0, last_run=None)
    monkeypatch.setattr(monitor.store, 'channels', lambda: [feed])
    assert monitor.publication_status() == PublicationStatus.DELAYED
    feed.paused = True
    assert monitor.publication_status() == PublicationStatus.CURRENT
    health['schedule_enabled'] = False
    feed.paused = False
    assert monitor.publication_status() == PublicationStatus.UNKNOWN
    feed.consecutive_failures = 3
    assert monitor.publication_status() == PublicationStatus.DELAYED
