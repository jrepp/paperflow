"""FastAPI application composition root for the local Paperflow service."""
from contextlib import asynccontextmanager
from uuid import UUID
import time

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from starlette.concurrency import run_in_threadpool

from paperflow_build import build_info
from paperflow_monitor import IntegrationMonitor
from paperflow_monitor_models import CurationTrigger, MonitoredChannel, MonitorRun
from paperflow_settings import ResearchSettings, load_research_settings
from paperflow_curation import CurationService, NotFoundError
from paperflow_curation_api import curation_router
from paperflow_storage import ConflictError, ReferenceError, StorageError
from paperflow_storage_sqlite import SQLiteResearchStorage, query_measurement
from paperflow_telemetry import Telemetry
from paperflow_domain import ResearchBucket


class PauseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    paused: bool


def create_app(settings: ResearchSettings | None = None, *, monitor: IntegrationMonitor | None = None) -> FastAPI:
    settings = settings or load_research_settings()
    monitor = monitor or IntegrationMonitor(settings)

    storage = SQLiteResearchStorage(settings.storage)
    publisher = None
    if settings.publishing.build_api_enabled:
        from paperflow_publish_api import BuildService
        publisher = BuildService(storage, settings.publishing)

    @asynccontextmanager
    async def lifespan(app):
        await run_in_threadpool(storage.initialize)
        if settings.monitor.enabled:
            await run_in_threadpool(monitor.start)
        elif monitor.telemetry is None:
            monitor.telemetry = Telemetry(settings.telemetry)
        app.state.monitor = monitor
        try:
            yield
        finally:
            if publisher:
                await run_in_threadpool(publisher.close)
            await run_in_threadpool(monitor.close)

    identity = build_info()
    app = FastAPI(title="Paperflow", version=identity["version"], lifespan=lifespan,
                  description="Local research service and integration monitor")
    if publisher:
        from paperflow_publish_api import publishing_router
        app.include_router(publishing_router(publisher, settings.publishing))
    app.include_router(curation_router(CurationService(storage)))
    from paperflow_reader.api import reader_router
    from paperflow_reader.service import ReaderService
    app.include_router(reader_router(ReaderService(storage, settings.topics)))
    from paperflow_topics import TopicService
    from paperflow_topic_api import topic_router
    topic_curator = None
    if settings.assistant.enabled and settings.monitor.enabled:
        from paperflow_assistant.topics import AssistantTopicCurator
        from paperflow_assistant.store import AssistantStore
        topic_curator = AssistantTopicCurator(AssistantStore(settings.assistant.path))
    app.include_router(topic_router(TopicService(storage, settings.topics, topic_curator)))

    @app.exception_handler(NotFoundError)
    async def not_found(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=404)

    @app.exception_handler(ConflictError)
    async def conflict(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(ReferenceError)
    @app.exception_handler(ValueError)
    async def invalid_operation(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=422)

    @app.exception_handler(StorageError)
    async def storage_unavailable(request, exc):
        return JSONResponse({"detail": "Research storage unavailable"}, status_code=503)

    @app.middleware("http")
    async def trace_request(request, call_next):
        from opentelemetry.propagate import extract
        from opentelemetry.trace import Status, StatusCode
        started = time.monotonic()
        stats = {"queries": 0}
        token = query_measurement.set(stats)
        with monitor.telemetry.tracer.start_as_current_span(
            "http.request", context=extract(request.headers),
            record_exception=False, set_status_on_exception=False,
        ) as span:
            status, size = 500, 0
            try:
                response = await call_next(request)
                status = response.status_code
                size = int(response.headers.get("content-length", 0))
                response.headers["X-Paperflow-DB-Queries"] = str(stats["queries"])
                response.headers["X-Paperflow-Duration-Ms"] = f"{(time.monotonic() - started) * 1000:.3f}"
                return response
            finally:
                elapsed = time.monotonic() - started
                route = request.scope.get("route")
                labels = {"http.request.method": request.method,
                          "http.route": route.path if route else "unmatched",
                          "http.response.status_code": status}
                for key, value in labels.items():
                    span.set_attribute(key, value)
                span.set_attribute("db.query.count", stats["queries"])
                span.set_attribute("http.response.body.size", size)
                if status >= 500:
                    span.set_status(Status(StatusCode.ERROR))
                monitor.telemetry.api_duration.record(elapsed, labels)
                monitor.telemetry.api_bytes.record(size, labels)
                monitor.telemetry.api_queries.record(stats["queries"], labels)
                query_measurement.reset(token)

    @app.get("/api/version", tags=["health"])
    def running_version():
        return identity

    @app.get("/health/live")
    def live():
        return {"status": "ok"}

    @app.get("/health/ready")
    def ready():
        if not settings.monitor.enabled:
            try:
                with storage.transaction() as repo:
                    repo.page(ResearchBucket, limit=1)
                return {"healthy": True, "enabled": False, "scheduler_running": False}
            except StorageError:
                return JSONResponse({"healthy": False, "error": "Research storage unavailable"}, status_code=503)
        try:
            health = monitor.health()
        except Exception:
            return JSONResponse({"healthy": False, "error": "Monitor storage unavailable"}, status_code=503)
        return JSONResponse(health, status_code=200 if health["healthy"] else 503)

    @app.get("/api/monitor/channels", response_model=list[MonitoredChannel])
    def channels():
        if not settings.monitor.enabled:
            raise HTTPException(503, "Monitor is disabled in this API process")
        return monitor.store.channels()

    @app.get("/api/monitor/runs", response_model=list[MonitorRun])
    def runs(limit: int = Query(default=50, ge=1, le=1000)):
        if not settings.monitor.enabled:
            raise HTTPException(503, "Monitor is disabled in this API process")
        return monitor.store.runs(limit)

    @app.get("/api/monitor/curation")
    def curation_metrics(days: int = Query(default=7, ge=1, le=365),
                         planned_items_per_day: int | None = Query(default=None, ge=1, le=10000)):
        if not monitor.assistant:
            return {"enabled": False}
        return {"enabled": True, **monitor.assistant.store.metrics(),
                "spend": monitor.assistant.store.spend(days, planned_items_per_day),
                "schedule": {"interval_seconds": settings.assistant.interval_seconds,
                             "items_per_cycle": settings.assistant.items_per_cycle,
                             "daily_attempt_limit": settings.assistant.daily_attempt_limit}}

    @app.post('/api/monitor/curation/runs', response_model=CurationTrigger, status_code=202,
              operation_id='trigger_curation', tags=['curation'],
              summary='Trigger a bounded curation cycle',
              description='Wake the curator even when automatic scheduling is off. Repeated requests '
              'coalesce with pending or active work. Retry backoff, daily attempt limits, and per-cycle '
              'intake still apply. This is an in-memory wake-up, not a durable job receipt. '
              'Poll the returned run-history and metrics URLs for outcomes and spend. '
              'An empty queue or exhausted daily budget may produce no new run.')
    def trigger_curation():
        try:
            return monitor.trigger_curation()
        except RuntimeError as exc:
            raise HTTPException(503, str(exc)) from None

    @app.get("/api/monitor/curation/runs")
    def curation_runs(limit: int = Query(default=20, ge=1, le=100)):
        if not monitor.assistant:
            return []
        # Output and private memory never enter the public monitor projection.
        return monitor.assistant.store.runs(limit)

    @app.get("/api/reader/items/{item_id}/analysis")
    def item_analysis(item_id: UUID):
        result = monitor.assistant.store.analysis(item_id) if monitor.assistant else None
        if result is None:
            raise HTTPException(404, 'No completed analysis')
        return result

    @app.get("/api/monitor/runs/{run_id}", response_model=MonitorRun)
    def run(run_id: UUID):
        if not settings.monitor.enabled:
            raise HTTPException(503, "Monitor is disabled in this API process")
        result = monitor.store.get_run(run_id)
        if result is None:
            raise HTTPException(404, "Run not found")
        return result

    @app.post("/api/monitor/channels/{channel}/runs", response_model=MonitorRun, status_code=202)
    def trigger(channel: str):
        if not monitor.accepting:
            raise HTTPException(503, "Monitor is not accepting runs")
        try:
            return monitor.store.enqueue(channel)
        except KeyError:
            raise HTTPException(404, "Channel not found") from None
        except ValueError:
            raise HTTPException(409, "Channel is paused") from None

    @app.patch("/api/monitor/channels/{channel}", response_model=list[MonitoredChannel])
    def pause(channel: str, body: PauseRequest):
        if not settings.monitor.enabled:
            raise HTTPException(503, "Monitor is disabled in this API process")
        try:
            monitor.store.pause(channel, body.paused)
        except KeyError:
            raise HTTPException(404, "Channel not found") from None
        return monitor.store.channels()

    from pathlib import Path
    from fastapi.staticfiles import StaticFiles
    from fastapi.responses import FileResponse
    static = Path(__file__).parent / 'paperflow_reader' / 'static'
    app.mount('/reader-assets', StaticFiles(directory=static), name='reader-assets')

    @app.get('/', include_in_schema=False)
    @app.get('/saved', include_in_schema=False)
    @app.get('/research', include_in_schema=False)
    @app.get('/research/{bucket_id}', include_in_schema=False)
    @app.get('/sources', include_in_schema=False)
    @app.get('/about', include_in_schema=False)
    @app.get('/topics', include_in_schema=False)
    @app.get('/read/{item_id}', include_in_schema=False)
    def reader_shell():
        return FileResponse(static / 'index.html', headers={'Cache-Control': 'no-cache'})

    return app
