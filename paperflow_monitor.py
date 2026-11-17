"""Single-owner local scheduler; blocking integration work runs in bounded threads."""
from __future__ import annotations

import fcntl
import json
import math
import logging
import threading
import time
import urllib.error
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from concurrent.futures import ThreadPoolExecutor

from opentelemetry.trace import Status, StatusCode

from paperflow_http import get_bytes
from paperflow_ingestion import default_registry
from paperflow_monitor_store import MonitorStore
from paperflow_settings import ResearchSettings
from paperflow_telemetry import Telemetry

logger = logging.getLogger("paperflow.monitor")


def retry_after_seconds(value: str | None) -> float:
    if not value:
        return 0
    try:
        seconds = float(value)
        return max(0, seconds) if math.isfinite(seconds) else 0
    except ValueError:
        try:
            return max(0, (parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds())
        except (ValueError, TypeError, OverflowError):
            return 0


class IntegrationMonitor:
    def __init__(self, settings: ResearchSettings, *, registry=None, transport=get_bytes, telemetry=None):
        self.settings = settings
        self.store = MonitorStore(settings.storage, settings.monitor)
        self.registry = registry or default_registry()
        self.transport = transport
        self.telemetry = telemetry
        self._owns_telemetry = telemetry is None
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._curation_lock = threading.Lock()
        self._curation_requested = False
        self._thread = None
        self._lock = None
        self._pool = None
        self._futures = {}
        self._topic_future = None
        self._next_topic_at = 0
        self.topic_error = None
        self.last_topic_run = None
        self.last_tick = None
        self.last_error = None
        self.assistant = None
        self._assistant_pool = None
        self._assistant_future = None
        self._next_assistant_at = 0
        self.assistant_error = None
        self.last_assistant_run = None
        self._heartbeat_pool = None
        self._heartbeat_future = None
        self._next_heartbeat_at = 0
        self.heartbeat_status = {'configured': bool(settings.monitor.heartbeat_url),
                                 'last_acknowledged_at': None, 'signal': None, 'error': None}

    def start(self):
        path = self.settings.storage.path.resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = open(str(path) + ".monitor.lock", "a")
        try:
            fcntl.flock(self._lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self._lock.close()
            self._lock = None
            raise RuntimeError("Another monitor owns this database; run one FastAPI worker") from None
        try:
            self.store.initialize(self.settings.ingestion.channels)
            self.store.recover()
            if self.telemetry is None:
                self.telemetry = Telemetry(self.settings.telemetry)
            if self.settings.monitor.enabled:
                if self.settings.monitor.heartbeat_url:
                    self._heartbeat_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='paperflow-heartbeat')
                if self.settings.assistant.enabled:
                    from paperflow_assistant.runtime import AssistantWorker
                    self.assistant = AssistantWorker(self.settings)
                    self.assistant.initialize()
                    self._assistant_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="paperflow-curator")
                self._pool = ThreadPoolExecutor(max_workers=self.settings.monitor.workers, thread_name_prefix="paperflow-ingest")
                self._thread = threading.Thread(target=self._loop, name="paperflow-monitor", daemon=True)
                self._thread.start()
        except BaseException:
            self.close()
            raise

    def close(self):
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join()
        if self._pool:
            self._pool.shutdown(wait=True)
        if self._assistant_pool:
            self._assistant_pool.shutdown(wait=True)
        if self._heartbeat_pool:
            self._heartbeat_pool.shutdown(wait=True)
        if self._owns_telemetry and self.telemetry:
            self.telemetry.shutdown()
        if self._lock:
            fcntl.flock(self._lock, fcntl.LOCK_UN)
            self._lock.close()
            self._lock = None

    @property
    def accepting(self):
        return bool(self._thread and self._thread.is_alive() and not self._stop.is_set())

    def trigger_curation(self):
        """Wake one bounded cycle; duplicate triggers share pending or active work."""
        with self._curation_lock:
            if not self.accepting or not self.assistant:
                raise RuntimeError('Curation is disabled or the monitor is stopping')
            running = bool(self._assistant_future and not self._assistant_future.done())
            coalesced = running or self._curation_requested
            if not running:
                self._curation_requested = True
            self._wake.set()
            return {'status': 'running' if running else 'requested', 'coalesced': coalesced,
                    'runs_url': '/api/monitor/curation/runs',
                    'metrics_url': '/api/monitor/curation'}

    def _schedule_assistant(self):
        with self._curation_lock:
            if self._assistant_future and self._assistant_future.done():
                try:
                    result = self._assistant_future.result()
                    if result:
                        self.last_assistant_run = result['id']
                        self.assistant_error = result.get('error')
                except Exception as exc:
                    self.assistant_error = type(exc).__name__
                self._assistant_future = None
            due = self.settings.monitor.schedule_enabled and time.time() >= self._next_assistant_at
            if self.assistant and self._assistant_future is None and not self._stop.is_set() and (due or self._curation_requested):
                self._assistant_future = self._assistant_pool.submit(self._run_assistant)
                self._curation_requested = False
                self._next_assistant_at = time.time() + self.settings.assistant.interval_seconds

    def health(self):
        overdue = [str(run.id) for run in self.store.running()
                   if run.status == "running" and run.started_at and
                   time.time() - run.started_at.timestamp() > self.settings.monitor.run_timeout_seconds]
        tick_recent = self.last_tick is not None and time.time() - self.last_tick < max(
            5, self.settings.monitor.poll_seconds * 3 + self.settings.storage.busy_timeout_seconds)
        healthy = self.accepting and tick_recent and not self.last_error and not self.topic_error and not self.assistant_error and not overdue
        return {"healthy": healthy, "enabled": self.settings.monitor.enabled,
                "scheduler_running": self.accepting, "schedule_enabled": self.settings.monitor.schedule_enabled,
                "telemetry": {"otlp_configured": bool(self.settings.telemetry.otlp_endpoint)}, "last_tick": self.last_tick,
                "scheduler_error": self.last_error, "overdue_runs": overdue,
                "heartbeat": dict(self.heartbeat_status),
                "curation": {"enabled": self.settings.assistant.enabled,
                             "running": bool(self._assistant_future and not self._assistant_future.done()),
                             "last_run": self.last_assistant_run, "error": self.assistant_error},
                "topics": {"enabled": self.settings.topics.enabled, "running": bool(self._topic_future and not self._topic_future.done()),
                           "last_run_id": self.last_topic_run, "error": self.topic_error}}

    def _loop(self):
        while not self._stop.is_set():
            self._wake.clear()
            try:
                self._schedule_assistant()
                if self._topic_future and self._topic_future.done():
                    try:
                        self.last_topic_run=str(self._topic_future.result().id)
                        self.topic_error=None
                    except Exception as exc:
                        self.topic_error=type(exc).__name__
                        self._next_topic_at=time.time()+self.settings.monitor.retry_seconds
                        logger.error(json.dumps({"event":"topic_curator_error","error_type":self.topic_error}))
                    self._topic_future=None
                if (self.settings.monitor.schedule_enabled and self.settings.topics.enabled
                    and self._topic_future is None and time.time()>=self._next_topic_at
                    and len(self._futures)<self.settings.monitor.workers):
                    self._topic_future=self._pool.submit(self._run_topics)
                    self._next_topic_at=time.time()+self.settings.topics.interval_seconds
                for future in tuple(self._futures):
                    if future.done():
                        try:
                            future.result()
                        except Exception as exc:
                            # A database outage can prevent worker completion.
                            # Keep the future until its failure can be recorded.
                            run_id = self._futures[future]
                            current = self.store.get_run(run_id)
                            if current and current.status == "running":
                                self.store.finish(run_id, batch=None, error=type(exc).__name__, duration=0)
                        del self._futures[future]
                if self.settings.monitor.schedule_enabled:
                    self.store.schedule_due()
                while len(self._futures) + int(self._topic_future is not None) < self.settings.monitor.workers and not self._stop.is_set():
                    claimed = self.store.claim()
                    if claimed is None:
                        break
                    self._futures[self._pool.submit(self._run, *claimed)] = claimed[0].id
                self.last_tick = time.time()
                self.last_error = None
            except Exception as exc:
                self.last_error = type(exc).__name__
                logger.error(json.dumps({"event": "scheduler_error", "error_type": type(exc).__name__}))
            self._schedule_heartbeat()
            self._wake.wait(self.settings.monitor.poll_seconds)

    def _schedule_heartbeat(self):
        if not self._heartbeat_pool or self._stop.is_set():
            return
        if self._heartbeat_future and self._heartbeat_future.done():
            try:
                signal = self._heartbeat_future.result()
                self.heartbeat_status.update(last_acknowledged_at=time.time(), signal=signal, error=None)
            except Exception as exc:
                self.heartbeat_status['error'] = type(exc).__name__
            self._heartbeat_future = None
        if self._heartbeat_future is None and time.monotonic() >= self._next_heartbeat_at:
            self._heartbeat_future = self._heartbeat_pool.submit(self._send_heartbeat)
            self._next_heartbeat_at = time.monotonic() + self.settings.monitor.heartbeat_interval_seconds

    def _send_heartbeat(self):
        from paperflow_heartbeat import ping
        try:
            healthy = self.health()['healthy']
        except Exception:
            healthy = False
        ping(self.settings.monitor.heartbeat_url.get_secret_value(), healthy)
        return 'success' if healthy else 'failure'

    def _run_assistant(self):
        started = time.monotonic()
        with self.telemetry.tracer.start_as_current_span('curation.run', record_exception=False) as span:
            last_result = None
            for _ in range(self.settings.assistant.items_per_cycle):
                if self._stop.is_set():
                    break
                result = self.assistant.tick()
                if not result:
                    break
                last_result = result
                span.set_attribute('curation.run_id', result['id'])
                span.set_attribute('curation.status', result['status'])
                self.telemetry.curation_runs.add(1, {'status': result['status']})
                for request in self.assistant.store.request_metrics(result['id'], result['attempt']):
                    labels = {'role': request['role'], 'model': request['model']}
                    usage = request.get('usage') or {}
                    for direction in ('input', 'output'):
                        self.telemetry.curation_tokens.add(usage.get(direction+'_tokens', 0), {**labels, 'direction': direction})
                    self.telemetry.curation_cached_tokens.add(usage.get('cache_read_tokens', 0), labels)
                    if request.get('estimated_usd') is None:
                        self.telemetry.curation_unpriced.add(1, labels)
                    else:
                        self.telemetry.curation_spend.add(float(request['estimated_usd']), labels)
            if last_result:
                self.telemetry.curation_duration.record(time.monotonic()-started)
            return last_result

    def _run_topics(self):
        from paperflow_storage_sqlite import SQLiteResearchStorage
        from paperflow_topics import TopicService
        from uuid import uuid5, NAMESPACE_URL
        curator = None
        if self.assistant:
            from paperflow_assistant.topics import AssistantTopicCurator
            curator = AssistantTopicCurator(self.assistant.store)
        service=TopicService(SQLiteResearchStorage(self.settings.storage),self.settings.topics,curator)
        interval=self.settings.topics.interval_seconds
        slot=int(time.time()//interval)*interval
        at=datetime.fromtimestamp(slot,UTC)
        recent=service.runs(1)
        if recent and recent[0].window_end>=at:
            return recent[0]
        run_id=uuid5(NAMESPACE_URL,f'paperflow.topic.scheduled:{slot}:{self.settings.topics.model_dump_json()}:{service.curator.version}')
        started=time.monotonic();status='failed'
        with self.telemetry.tracer.start_as_current_span('topics.curate') as span:
            try:
                run=service.curate(at=at,run_id=run_id)
                span.set_attribute('topics.candidates',run.input_count)
                span.set_attribute('topics.editions',len(run.editions))
                span.set_attribute('topics.run_id',str(run.id))
                self.telemetry.topic_candidates.add(run.input_count)
                status='succeeded'
                return run
            finally:
                self.telemetry.topic_runs.add(1,{'status':status})
                self.telemetry.topic_duration.record(time.monotonic()-started,{'status':status})

    def _run(self, run, channel):
        started = time.monotonic()
        labels = {"channel": run.channel, "discovery": channel.discovery}
        status = "failed"
        count = 0
        retry_after = 0
        telemetry = self.telemetry

        def check_deadline():
            if self._stop.is_set():
                raise InterruptedError("monitor shutdown")
            if time.monotonic() - started > self.settings.monitor.run_timeout_seconds:
                raise TimeoutError("run deadline exceeded")

        with telemetry.tracer.start_as_current_span("ingestion.run", attributes=labels) as span:
            trace_id = format(span.get_span_context().trace_id, "032x")
            span.set_attribute("run.id", str(run.id))
            telemetry.active.add(1, labels)

            def progress(stage, completed, total):
                check_deadline()
                self.store.progress(run.id, stage, completed, total, trace_id)
                span.add_event(stage, {"completed": completed, "total": total})

            def fetch(url):
                nonlocal retry_after
                check_deadline()
                if retry_after > 0:
                    raise OSError("upstream requested a cooldown")
                with telemetry.tracer.start_as_current_span("ingestion.fetch", record_exception=False, set_status_on_exception=False):
                    try:
                        return self.transport(url)
                    except urllib.error.HTTPError as exc:
                        retry_after = max(retry_after, retry_after_seconds(exc.headers.get("Retry-After")))
                        raise

            try:
                batch = self.registry.collect(run.channel, channel, fetch, progress=progress)
                check_deadline()
                progress("persisting", len(batch.entries), len(batch.entries))
                with telemetry.tracer.start_as_current_span("ingestion.persist", record_exception=False, set_status_on_exception=False):
                    self.store.finish(run.id, batch=batch, error=None, duration=time.monotonic() - started,
                                      retry_after_seconds=retry_after)
                count = len(batch.entries)
                status = "degraded" if batch.warnings or any(e.comments_status == "unavailable" for e in batch.entries) else "succeeded"
            except Exception as exc:
                status = "interrupted" if isinstance(exc, InterruptedError) else "failed"
                # Persist only an error type; upstream messages may contain private URLs or bodies.
                error = f"HTTP {exc.code}" if isinstance(exc, urllib.error.HTTPError) else type(exc).__name__
                self.store.finish(run.id, batch=None, error=error, duration=time.monotonic() - started,
                                  retry_after_seconds=retry_after)
                span.set_status(Status(StatusCode.ERROR, type(exc).__name__))
            finally:
                elapsed = time.monotonic() - started
                telemetry.active.add(-1, labels)
                telemetry.runs.add(1, {**labels, "status": status})
                telemetry.duration.record(elapsed, {**labels, "status": status})
                telemetry.items.add(count, labels)
                logger.info(json.dumps({"event": "ingestion_finished", "run_id": str(run.id),
                    "channel": run.channel, "status": status, "item_count": count,
                    "duration_seconds": elapsed, "trace_id": trace_id}))
