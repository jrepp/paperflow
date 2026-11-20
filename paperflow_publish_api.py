"""Authenticated, single-flight static builds with durable release receipts."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import logging
import os
from pathlib import Path
import secrets
import shutil
import tempfile
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import AwareDatetime, BaseModel
from typing import Literal

from paperflow_monitor_models import PublicationStatus
from paperflow_publish_site import build_site_from_storage


class BuildJob(BaseModel):
    id: UUID
    status: Literal['running', 'succeeded', 'failed']
    created_at: AwareDatetime
    release_id: str | None = None
    build_id: str | None = None
    error: str | None = None


def _write_json(path: Path, value: dict) -> None:
    temporary = path.with_name(path.name + '.' + uuid4().hex + '.tmp')
    try:
        temporary.write_text(json.dumps(value) + '\n', encoding='utf-8')
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class BuildService:
    """One build across processes sharing a local release filesystem.

    The OS lock survives request completion and is held by the build worker.
    Status receipts survive process restarts. Interrupted jobs are recovered on
    read or the next build request without exposing incomplete staging output.
    """
    def __init__(self, storage, settings, assistant_settings=None, *, status_provider=None):
        self.status_provider = status_provider
        self.assistant_settings = assistant_settings
        self.storage = storage
        self.settings = settings
        self.releases = settings.releases_dir
        self.state = self.releases.parent / '.build-jobs'
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='publish')

    def close(self):
        self.pool.shutdown(wait=True)

    def _lock(self):
        self.state.mkdir(parents=True, exist_ok=True)
        lock = (self.state / 'build.lock').open('a+')
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock.close()
            return None
        return lock

    def _load(self, job_id):
        path = self.state / (UUID(str(job_id)).hex + '.json')
        if not path.exists():
            return None
        return BuildJob.model_validate_json(path.read_text())

    def _save(self, job):
        _write_json(self.state / (job.id.hex + '.json'), job.model_dump(mode='json'))

    def _active(self):
        path = self.state / 'active.json'
        return self._load(json.loads(path.read_text())['id']) if path.exists() else None

    def _recover(self, job):
        if job.status != 'running':
            return job
        manifest_path = self.releases / job.id.hex / 'build.json'
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text())
            job = job.model_copy(update={'status': 'succeeded', 'release_id': job.id.hex,
                                         'build_id': manifest['build_id']})
        else:
            job = job.model_copy(update={'status': 'failed', 'error': 'Build interrupted; request another build.'})
        self._save(job)
        return job

    def get(self, job_id):
        job = self._load(job_id)
        if job and job.status == 'running':
            lock = self._lock()
            if lock:
                try:
                    # Re-read after locking; a worker may have just completed.
                    job = self._recover(self._load(job_id))
                finally:
                    lock.close()
        return job

    def publication_summary(self):
        """Report the latest local build; the consumer promotes releases independently."""
        job = self._active()
        if job is None:
            return {'status': 'no_build'}
        job = self.get(job.id)
        summary = {'status': job.status, 'requested_at': job.created_at}
        if job.status == 'succeeded':
            manifest = json.loads((self.releases / job.id.hex / 'build.json').read_text())
            summary.update(built_at=manifest['created_utc'],
                           service_status=PublicationStatus(manifest.get('service_status', 'unknown')))
        return summary

    def request(self):
        lock = self._lock()
        if lock is None:
            job = self._active()
            if job and (job.status == 'running' or (job.status == 'succeeded' and
                    (datetime.now(timezone.utc) - job.created_at).total_seconds() < self.settings.build_reuse_seconds)):
                return job
            raise HTTPException(503, 'Publisher is starting; retry shortly.', headers={'Retry-After': '1'})
        try:
            previous = self._active()
            if previous:
                previous = self._recover(previous)
                age = (datetime.now(timezone.utc) - previous.created_at).total_seconds()
                if previous.status == 'succeeded' and age < self.settings.build_reuse_seconds:
                    return previous
            job = BuildJob(id=uuid4(), status='running', created_at=datetime.now(timezone.utc))
            self._save(job)
            _write_json(self.state / 'active.json', {'id': job.id.hex})
            self.pool.submit(self._build, job, lock)
            lock = None  # Worker now owns the cross-process lock.
            return job
        finally:
            if lock:
                lock.close()

    def _build(self, job, lock):
        staging = None
        try:
            self.releases.mkdir(parents=True, exist_ok=True)
            staging = Path(tempfile.mkdtemp(prefix='.staging-', dir=self.releases.parent))
            from paperflow_publish_site import _assistant_store
            assistant = _assistant_store(self.assistant_settings) if self.assistant_settings else None
            status = PublicationStatus.UNKNOWN
            if self.status_provider:
                try:
                    status = PublicationStatus(self.status_provider())
                except Exception:
                    logging.getLogger(__name__).warning('Publication status unavailable', exc_info=True)
            manifest = build_site_from_storage(self.storage, staging,
                publication_status=status, workspace_url=self.settings.workspace_url, assistant_store=assistant, releases_dir=self.settings.releases_dir)
            manifest['release_id'] = job.id.hex
            manifest['index_sha256'] = hashlib.sha256((staging / 'index.html').read_bytes()).hexdigest()
            _write_json(staging / 'build.json', manifest)
            # Releases contain only public output; read-only artifact consumers
            # must be able to traverse a directory created by mkdtemp (0700).
            staging.chmod(0o755)
            for name in ('index.html', 'build.json'):
                (staging / name).chmod(0o644)
            os.rename(staging, self.releases / job.id.hex)
            staging = None
            self._save(job.model_copy(update={'status': 'succeeded', 'release_id': job.id.hex,
                                             'build_id': manifest['build_id']}))
        except Exception:
            if staging:
                shutil.rmtree(staging, ignore_errors=True)
                staging = None
            self._save(job.model_copy(update={'status': 'failed', 'error': 'Static build failed.'}))
        finally:
            if staging:
                shutil.rmtree(staging, ignore_errors=True)
            lock.close()


def publishing_router(service, settings):
    # Read before registering any route: an absent/empty token fails closed.
    token = settings.build_token_file.read_text().strip()
    if len(token) < 32:
        raise ValueError('Publishing build token must contain at least 32 characters')
    bearer = HTTPBearer(auto_error=False)

    def authorize(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
        if credentials is None or not secrets.compare_digest(credentials.credentials.encode(), token.encode()):
            raise HTTPException(401, 'Invalid publishing credentials', headers={'WWW-Authenticate': 'Bearer'})

    router = APIRouter(prefix='/api/publish', tags=['publishing'], dependencies=[Depends(authorize)])

    @router.post('/builds', response_model=BuildJob, status_code=202)
    def request_build():
        return service.request()

    @router.get('/builds/{job_id}', response_model=BuildJob)
    def get_build(job_id: UUID):
        job = service.get(job_id)
        if job is None:
            raise HTTPException(404, 'Build not found')
        return job

    return router
