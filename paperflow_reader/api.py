"""HTTP contracts for the local reader."""
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Query, Request, HTTPException
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool

from paperflow_reader.models import ReaderState, ChangeReaderState, ReadingPreferences, ChangePreferences, ReaderDocument, PDFAsset
from paperflow_reader.service import ReaderService
from paperflow_domain import ItemKind


def reader_router(service: ReaderService):
    router = APIRouter(prefix='/api/reader', tags=['reader'])

    @router.get('/feed')
    def feed(channel: str | None = None, query: str = Query('', max_length=200),
             view: Literal['today', 'saved'] = 'today', kind: ItemKind | None = None, topic: UUID | None = None, limit: int = Query(20, ge=1, le=50),
             before: int | None = Query(None, ge=1)):
        return service.feed(channel=channel, query=query, saved=view == 'saved', kind=kind, limit=limit, before=before, topic_id=topic)

    @router.get('/channels')
    def channels(view: Literal['today', 'saved'] = 'today', query: str = Query('', max_length=200), kind: ItemKind | None = None):
        return service.channels(saved=view == 'saved', query=query, kind=kind)

    @router.get('/items/{item_id}', response_model=ReaderDocument)
    def document(item_id: UUID, latest: bool = False):
        return service.document(item_id, latest=latest)

    @router.put('/items/{item_id}/pdf', response_model=PDFAsset)
    async def attach_pdf(item_id: UUID, request: Request):
        if request.headers.get('content-type', '').split(';')[0] != 'application/pdf':
            raise HTTPException(415, 'Send a PDF with Content-Type application/pdf')
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > 20 * 1024 * 1024:
                raise HTTPException(413, 'PDF exceeds the 20 MB limit')
        return await run_in_threadpool(service.attach_pdf, item_id, bytes(data))

    @router.get('/items/{item_id}/pdf')
    def pdf(item_id: UUID):
        asset, data = service.pdf(item_id)
        return Response(data, media_type='application/pdf', headers={
            'ETag': f'"{asset.content_hash}"',
            'Content-Disposition': f'inline; filename="{item_id}.pdf"',
            'X-Content-Type-Options': 'nosniff',
            'Content-Security-Policy': 'sandbox',
        })

    @router.get('/items/{item_id}/state', response_model=ReaderState)
    def state(item_id: UUID):
        return service.state(item_id)

    @router.patch('/items/{item_id}/state', response_model=ReaderState)
    def change_state(item_id: UUID, body: ChangeReaderState):
        return service.change_state(item_id, body)

    @router.get('/preferences', response_model=ReadingPreferences)
    def preferences():
        return service.preferences()

    @router.put('/preferences', response_model=ReadingPreferences)
    def change_preferences(body: ChangePreferences):
        return service.change_preferences(body)

    return router
