"""Application operations over the research storage port."""
from uuid import UUID

from paperflow_curation import NotFoundError
from paperflow_domain import Item, ContentRevision
from paperflow_storage import ResearchStorage, ConflictError, ReferenceError
from paperflow_reader.models import ReaderState, ChangeReaderState, ChangePreferences


class ReaderService:
    def __init__(self, storage: ResearchStorage, topic_settings=None):
        from paperflow_topic_models import TopicSettings
        self.storage = storage
        self.topic_settings = topic_settings or TopicSettings()

    def document(self, item_id: UUID, *, latest=False):
        from paperflow_reader.models import ReaderDocument
        from paperflow_ingest_models import SourceObservation
        with self.storage.transaction() as repo:
            item = repo.get(Item, item_id)
            if not item:
                raise NotFoundError('Item not found')
            state = repo.reader_state(item_id)
            newest = repo.latest_for_item(ContentRevision, item_id)
            revision = repo.get(ContentRevision, state.anchor.revision_id) if state.anchor and not latest else newest
            return ReaderDocument(item=item, state=state, revision=revision,
                                  latest_revision_id=newest.id if newest else None,
                                  observation=repo.latest_for_item(SourceObservation, item_id), pdf=repo.reader_pdf(item_id))

    def attach_pdf(self, item_id: UUID, data: bytes):
        import hashlib
        import pymupdf
        from paperflow_reader.models import PDFAsset
        if len(data) > 20 * 1024 * 1024 or not data.startswith(b'%PDF-'):
            raise ValueError('Choose a valid PDF of at most 20 MB')
        try:
            with pymupdf.open(stream=data, filetype='pdf') as document:
                if document.needs_pass or document.page_count < 1:
                    raise ValueError('PDF must contain pages and must not require a password')
                pages = document.page_count
        except (pymupdf.FileDataError, RuntimeError) as exc:
            raise ValueError('Unable to read this PDF') from exc
        asset = PDFAsset(item_id=item_id, content_hash=hashlib.sha256(data).hexdigest(), page_count=pages, byte_count=len(data))
        with self.storage.transaction(write=True) as repo:
            if not repo.get(Item, item_id):
                raise NotFoundError('Item not found')
            return repo.add_reader_pdf(asset, data)

    def pdf(self, item_id: UUID):
        with self.storage.transaction() as repo:
            result = repo.reader_pdf(item_id, include_data=True)
            if result is None:
                raise NotFoundError('No local PDF is attached to this item')
            return result

    def state(self, item_id: UUID):
        with self.storage.transaction() as repo:
            if not repo.get(Item, item_id):
                raise NotFoundError('Item not found')
            return repo.reader_state(item_id)

    def change_state(self, item_id: UUID, change: ChangeReaderState):
        with self.storage.transaction(write=True) as repo:
            if not repo.get(Item, item_id):
                raise NotFoundError('Item not found')
            current = repo.reader_state(item_id)
            updates = change.model_dump(exclude_unset=True, exclude={'expected_version'})
            candidate = ReaderState.model_validate({**current.model_dump(), **updates})
            # Exact retries are harmless, including after restart. Different stale
            # writes never overwrite a newer reading position or save decision.
            if candidate == current:
                return current
            if current.version != change.expected_version:
                raise ConflictError('Reading state changed. Reload before retrying.')
            if candidate.anchor:
                revision = repo.get(ContentRevision, candidate.anchor.revision_id)
                if not revision or revision.item_id != item_id or candidate.anchor.character_offset > len(revision.markdown):
                    raise ReferenceError('Reading position must belong to this item and revision')
            result = candidate.model_copy(update={'version': current.version + 1})
            repo.put_reader_state(result)
            return result

    def preferences(self):
        with self.storage.transaction() as repo:
            return repo.reading_preferences()

    def change_preferences(self, change: ChangePreferences):
        with self.storage.transaction(write=True) as repo:
            current = repo.reading_preferences()
            candidate = change.preferences.model_copy(update={'version': current.version})
            if candidate == current:
                return current
            if current.version != change.expected_version:
                raise ConflictError('Reading settings changed. Reload before retrying.')
            result = candidate.model_copy(update={'version': current.version + 1})
            repo.put_reading_preferences(result)
            return result

    def feed(self, **filters):
        from paperflow_domain import now
        from datetime import timedelta
        with self.storage.transaction() as repo:
            if filters.get('topic_id'):
                if not repo.get_topic(filters['topic_id']):
                    raise NotFoundError('Topic not found')
                filters['topic_end']=now()
                filters['topic_start']=filters['topic_end']-timedelta(hours=self.topic_settings.window_hours)
            return repo.reader_feed(**filters)

    def channels(self, **filters):
        with self.storage.transaction() as repo:
            return repo.reader_channels(**filters)
