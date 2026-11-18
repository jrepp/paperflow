"""Anonymous editorial feedback from the public site's trusted gateway."""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import os
import secrets
import tempfile
from pathlib import Path
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from paperflow_ingest_models import SourceObservation


class Rating(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["item", "story"]
    id: UUID
    version: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9_-]+$")
    rating: Literal["helpful", "needs_work"]


def _reader_key(path: Path) -> bytes:
    """Install the HMAC key once, including when several service workers start together."""
    if not path.exists():
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as tmp:
            tmp.write(secrets.token_bytes(32))
        try:
            os.link(tmp.name, path)
        except FileExistsError:
            pass
        finally:
            os.unlink(tmp.name)
    key = path.read_bytes()
    if len(key) != 32:
        raise ValueError("Invalid reader rating key")
    return key


def ratings_router(storage, assistant_settings, token_path: Path):
    router = APIRouter(tags=["publishing"])
    token = token_path.read_bytes().strip()
    if len(token) < 32:
        raise ValueError("The rating gateway needs a credential of at least 32 bytes")
    key = _reader_key(token_path.with_name(".rating-reader-key"))

    @router.post("/api/publish/ratings")
    def record_rating(payload: Rating, request: Request):
        credential = request.headers.get("x-paperflow-rating-gateway", "").encode()
        if not hmac.compare_digest(credential, token):
            raise HTTPException(403, "Rating gateway required")
        try:
            address = ipaddress.ip_address(request.headers.get("x-paperflow-client-ip", ""))
        except ValueError:
            raise HTTPException(400, "Missing rating gateway address") from None
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
            address = address.ipv4_mapped
        # Passive browser properties only. The gateway overwrites the IP header.
        identity = [str(address), request.headers.get("user-agent", "")[:2048],
                    request.headers.get("accept-language", "")[:512]]
        reader = hmac.new(key, json.dumps(identity, separators=(",", ":")).encode(), hashlib.sha256).hexdigest()
        if payload.kind == "item":
            from paperflow_assistant.store import AssistantStore
            store = AssistantStore(assistant_settings.path)
            if not assistant_settings.enabled or not store.path.is_file() or not store.analysis_for_run(payload.id, payload.version):
                raise HTTPException(404, "Unknown curation")
        with storage.transaction(write=True) as repo:
            if payload.kind == "story":
                if (not payload.version.isdecimal() or len(payload.version) > 12
                        or payload.version.startswith('0')):
                    raise HTTPException(404, "Unknown story version")
                known = repo.story_version_assessment_ids(payload.id, int(payload.version))
                if not known:
                    raise HTTPException(404, "Unknown story version")
            elif not repo.latest_for_item(SourceObservation, payload.id):
                raise HTTPException(404, "Unknown curation")
            repo.save_reader_rating(reader, payload.kind, payload.id, payload.version, payload.rating)
        return {"rating": payload.rating}

    return router
