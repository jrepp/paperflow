"""Durable integration-monitor records exposed by the service API."""
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict

RunStatus = Literal["queued", "running", "succeeded", "degraded", "failed", "interrupted"]


class MonitorRun(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)
    id: UUID
    channel: str
    status: RunStatus
    stage: str
    queued_at: AwareDatetime
    started_at: AwareDatetime | None = None
    finished_at: AwareDatetime | None = None
    heartbeat_at: AwareDatetime | None = None
    completed: int = 0
    total: int = 0
    item_count: int = 0
    warning_count: int = 0
    duration_seconds: float | None = None
    error: str | None = None
    trace_id: str | None = None


class MonitoredChannel(BaseModel):
    name: str
    paused: bool
    interval_seconds: int
    next_due_at: AwareDatetime
    last_success_at: AwareDatetime | None
    consecutive_failures: int
    active_run_id: UUID | None
    last_run: MonitorRun | None
    stale: bool
