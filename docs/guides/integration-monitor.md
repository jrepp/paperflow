---
title: "FastAPI integration monitor"
project_id: paperflow
doc_uuid: 018218e4-c31b-403d-a832-6539ab8b7479
tags: [guides]
---

# FastAPI integration monitor

Status: implemented local service. Validated 2026-11-15.

The FastAPI base application owns the integration monitor through its lifespan.
It exposes durable channel/run status and controls, while a bounded thread pool
executes the existing discovery → enrichment → persistence composition.
Blocking source and SQLite operations do not run on the API event loop.

## Start locally

```bash
uv run paperflow serve --config ingestion.example.yaml
```

Open `http://127.0.0.1:8000/docs` for the interactive API. The example schedules
Hacker News, Lemmy, Mbin, and Tildes and uses the separate
`artifacts/source-validation.db` database. New channels receive a short randomized startup delay;
the default interval is 900 seconds after completion plus bounded jitter. This command runs until
stopped with Ctrl-C. No background service is installed.

For ordinary use, put selected channels and settings under `research` in
`arxiv-radar.yaml` and run `uv run paperflow serve`. Existing top-level radar
source configuration is not automatically interpreted as monitor channels.
Reddit remains a separate approval-dependent probe.

The default bind address is loopback. This first service is intended for a
trusted local user; it does not add remote-user authentication. There is no
reader/dashboard UI yet: `/docs` is the initial operator interface and the JSON
API is ready for the planned frontend.

## Settings

```yaml
research:
  storage:
    path: artifacts/radar.db
  service:
    host: 127.0.0.1
    port: 8000
  monitor:
    enabled: true
    schedule_enabled: true
    workers: 2
    poll_seconds: 1
    run_timeout_seconds: 180
    retry_seconds: 60
    max_retry_seconds: 3600
    jitter_fraction: 0.1
    jitter_max_seconds: 60
  ingestion:
    channels:
      hackernews:
        url: https://news.ycombinator.com/rss
        discovery: feed
        enrichers: [hackernews]
        interval_seconds: 900
        limit: 10
        comment_limit: 3
```

Explicit CLI host/port overrides win over environment and YAML. Other nested
environment settings use the established prefix, for example
`PAPERFLOW_MONITOR__WORKERS=3` or
`PAPERFLOW_MONITOR__SCHEDULE_ENABLED=false`. Manual-only mode disables scheduling
but still accepts and processes runs submitted through the API. Fully disabling
the monitor keeps status endpoints available, rejects new runs, and reports
monitor readiness as unavailable.

## Article batch sizes

The local `arxiv-radar.yaml` allows up to 200 entries per run for Hacker News,
Lemmy, Mbin, and Tildes. Hugging Face Papers remains at 10. Channel limits accept
1–1,000 entries; unspecified channels keep the conservative default of 10, and
the example validation config deliberately uses two. The local run deadline is
600 seconds to allow larger batches with per-item enrichment.

These are ingestion caps, not upstream guarantees. RSS/Atom discovery reads the
entries supplied in one feed response; it does not backfill archives or paginate.
If a feed supplies only 30 entries, raising the cap still collects only those 30.
Comment samples and HTTP response-size limits remain bounded independently.

## Endpoints

| Method and path | Behavior |
| --- | --- |
| `GET /health/live` | Process liveness |
| `GET /health/ready` | Scheduler freshness, database availability, overdue runs; 503 if unhealthy |
| `POST /api/monitor/curation/runs` | Wake one bounded curation cycle; coalesce pending/active triggers; 202 |
| `GET /api/monitor/curation/runs` | Recent curation outcomes and validation receipts |
| `GET /api/monitor/curation` | Activity, sampled quality, token usage, and estimated spend |
| `GET /api/monitor/channels` | Schedule, pause state, freshness, consecutive failures, active/latest run |
| `POST /api/monitor/channels/{channel}/runs` | Queue a run, or return the existing active run; 202 |
| `PATCH /api/monitor/channels/{channel}` | `{"paused": true}` or `{"paused": false}` |
| `GET /api/monitor/runs?limit=50` | Recent durable run history, bounded to 1–1000 records |
| `GET /api/monitor/runs/{id}` | Stage, progress, timings, outcome, safe error, and trace ID |

For example:

```bash
curl -X POST http://127.0.0.1:8000/api/monitor/channels/hackernews/runs
curl http://127.0.0.1:8000/api/monitor/channels
curl -X PATCH http://127.0.0.1:8000/api/monitor/channels/hackernews \
  -H 'Content-Type: application/json' -d '{"paused":true}'
```

Pausing stops future scheduling and queued work. A running job finishes normally.
Resume allows due/queued work to proceed. Paused settings survive restarts.
Unknown channels return 404; triggering a paused channel without an active run
returns 409. Repeated triggers never create concurrent runs for the same channel.

## Scheduling jitter

Automatic pulls add a uniform random delay from zero to
`min(base_delay * jitter_fraction, jitter_max_seconds)`. Defaults are 10 percent
and a 60-second cap. For a 900-second interval, the next pull occurs 900–960 seconds
after completion. This positive-only delay never polls sooner than the configured
interval or an upstream Retry-After deadline. Retry backoff is capped before jitter
is added; a longer Retry-After still takes precedence.

New channels, overdue channels at startup, interrupted-run recovery, and overdue
channels on resume receive a zero-to-cap delay based on their normal interval.
Future due times are persisted and preserved across restarts. Already queued runs
are kept. Manual Fetch now remains immediate (subject to worker availability,
pause state, and existing active-run deduplication). Jitter changes when scheduling
queues the next run, not the timing of individual HTTP requests within a run.
Set either `jitter_fraction` or `jitter_max_seconds` to zero to disable it.
Environment overrides are `PAPERFLOW_MONITOR__JITTER_FRACTION` and
`PAPERFLOW_MONITOR__JITTER_MAX_SECONDS`.

## Lifecycle and recovery

Runs move through queued → running → succeeded, degraded, failed, or interrupted.
Active stages are starting, discovery, enrichment, and persisting. Progress
counts describe the current stage, not a fabricated percentage of the whole
pipeline. Heartbeat timestamps record stage progress; they are not evidence
that a remote request is making progress.

Each run snapshots its channel configuration. Successful content writes and
terminal status commit in one SQLite transaction. A failed persistence attempt
rolls back the content, then records failure separately. Source warnings and
unavailable comment samples produce degraded outcomes while retaining usable
content. Empty successful feeds remain successful and refresh channel freshness.
Channel freshness means a completed ingestion within twice its configured
interval; it does not assert that upstream publications are recent.

Failures retry on later scheduling ticks with capped exponential backoff.
`Retry-After` seconds or HTTP-date headers can extend that delay beyond the
configured cap. A cooldown also stops further network calls in the current run.
Manual-only mode requires explicit retries. Degraded runs normally use the
regular channel interval, extended by any upstream cooldown.

The service takes an exclusive OS file lock beside the database before recovery
or scheduling. Run exactly one FastAPI/Uvicorn process per database; a second
owner fails startup rather than stealing work. This implementation uses POSIX
`flock` and targets the project's macOS/Linux environments. The lock file may
remain on disk; ownership is the OS lock, not the file's existence.

After process death, the OS releases the lock. The next owner marks abandoned
running jobs interrupted and staggers their next attempts with startup jitter. Queued work
survives. Removing a channel from configuration disables its scheduling and
interrupts its queued work while retaining history.

Deadlines are cooperative: the runner checks before stage changes and network
requests and before persistence. Built-in HTTP calls also have a 20-second
timeout. An overdue custom component is reported unhealthy, but threads are not
force-killed or duplicated. Custom components must return in bounded time.
Shutdown stops new claims and waits for active workers; those workers stop at
their next checkpoint. Use an external process supervisor if automatic process
restart is desired. Distributed leases and multiple service replicas are not
part of this local implementation.

## OpenTelemetry

Instrumentation uses app-owned providers, avoiding global provider replacement.
Run IDs and trace IDs are stored with durable history. Instrumentation includes:

- HTTP request spans with route templates, method, status, and duration.
- `ingestion.run`, `ingestion.fetch`, and `ingestion.persist` spans, plus stage
  events showing counts.
- `paperflow.ingestion.runs`, `paperflow.ingestion.items`,
  `paperflow.ingestion.duration`, and `paperflow.ingestion.active` metrics.
- Structured completion logs containing channel, run ID, trace ID, outcome,
  item count, and duration. Logs use standard Python logging; `paperflow serve`
  enables informational output.

Metric labels are configured channel/component names and outcomes, not item IDs
or URLs. Upstream exception bodies, URLs, and content are excluded from stored
errors and custom telemetry. Durable state remains available without a Collector.

To export traces and metrics to a local OTLP/HTTP Collector:

```bash
PAPERFLOW_TELEMETRY__OTLP_ENDPOINT=http://127.0.0.1:4318 \
  uv run --extra telemetry paperflow serve --config ingestion.example.yaml
```

The configured endpoint is a base URL; `/v1/traces` and `/v1/metrics` are appended.
Exporters run asynchronously with short request timeouts, and exporter failures
do not determine ingestion success. No telemetry is exported by default.
Logs remain local JSON messages; OTLP log export is not configured in this slice.

## Validation

Automated tests exercise API lifecycle, independent workers, deduplicated manual
triggers, persistent pause state, scheduled failure/retry, process ownership,
abandoned-run recovery, transaction rollback, deadline enforcement, Retry-After,
and in-memory trace/metric output.

The full unit suite passes 56 tests, including OTLP traces and metrics delivered
to a local HTTP receiver. The existing Starlette/httpx test client emits a
dependency deprecation warning; it does not affect service behavior.

A real local service smoke test completed all four example channels with eight
total entries and readiness HTTP 200. The test service was stopped afterward.
Its configuration, database, and log are under `artifacts/monitor-validation*`.

Implementation: `paperflow_api.py` composes the app, `paperflow_monitor.py` owns
execution, `paperflow_monitor_store.py` isolates SQLite state, and
`paperflow_monitor_models.py` defines the API records. Reader and curation routes
are now available in this same FastAPI base application; see the
[curator workflow](curator-workflow.md).


## Running the API independently

`research.monitor.enabled: false` (or `PAPERFLOW_MONITOR__ENABLED=false`) starts
the research API and its telemetry without acquiring the scheduler lock,
recovering runs, or changing monitor channel configuration. Research storage
initialization is independent of the monitor. Readiness checks research storage
in this mode; monitor endpoints return 503 because this process owns no monitor.
It can run alongside the single scheduler-owning service on a different port.
Combined local API + monitor mode remains the default. Setting only
`schedule_enabled: false` retains scheduler ownership and manual run support.

HTTP telemetry also measures research SQL statement counts, response body bytes,
and response-ready latency through `paperflow.api.db.queries`,
`paperflow.api.response.size`, and `paperflow.api.duration`. These metrics remain
available in API-only mode. Research query counts do not include MonitorStore SQL.


## Agent-triggered research curation

Agents can discover `trigger_curation` in `/openapi.json` or `/docs` and request
one cycle without restarting the service:

```bash
curl -X POST http://127.0.0.1:8000/api/monitor/curation/runs
curl http://127.0.0.1:8000/api/monitor/curation/runs
curl 'http://127.0.0.1:8000/api/monitor/curation?days=7&planned_items_per_day=50'
```

The POST returns HTTP 202 with `status` (`requested` or `running`), `coalesced`,
`runs_url`, and `metrics_url`. It wakes the monitor immediately, including when
`monitor.schedule_enabled` is false. The monitor and assistant must both be
running; otherwise it returns HTTP 503. The endpoint uses the same trusted-local
access boundary as other monitor controls. Keep it behind the deployment's
private network or authenticated gateway.

This is an in-memory wake-up, not a durable job receipt. A restart can discard a
pending wake-up; agents may safely retry. Repeated requests share pending or active
work and never create concurrent curator workers or queue extra cycles behind an
active cycle. No SIGHUP handler is installed. The configured `items_per_cycle`,
daily attempt cap, model budgets, and retry backoff still apply. An empty eligible
queue or exhausted daily cap produces no new run. Completed revisions are not
reprocessed. Poll the returned URLs for durable outcomes, quality flags, and
estimated spend; HTTP 202 does not assert that an analysis completed or passed
quality checks. Internal research notes remain private.


## Build identity and external heartbeat

`GET /api/version` reports the installed package version, immutable commit,
build timestamp, and build channel. Local builds can have unknown commit/time.
`GET /health/live` checks process liveness. `GET /health/ready` checks storage,
scheduler freshness, overdue runs, and current ingestion/curation errors.

Set `PAPERFLOW_MONITOR__HEARTBEAT_URL` in private runtime configuration to enable
Healthchecks-compatible POST pings. The monitor sends an empty success ping when
ready or a `/fail` ping when unhealthy, every 60 seconds by default. Configure the
external check for a one-minute period with suitable startup/deployment grace.
The URL is a secret and is excluded from settings serialization and health output.
A dedicated worker bounds requests to five seconds; network failures do not block
curation or ingestion. Redirects are rejected. Readiness includes the last
acknowledged heartbeat time, signal, and sanitized error type. Stalled or stopped
monitors stop pinging. Quality estimates remain separate from process health.
