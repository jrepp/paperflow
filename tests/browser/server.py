"""Disposable real server/database for browser acceptance; never touches user data."""
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import uvicorn
from paperflow_api import create_app
from paperflow_settings import ResearchSettings
from paperflow_storage_sqlite import SQLiteResearchStorage
from paperflow_ingestion import persist_batch
from paperflow_ingest_models import ChannelConfig, SourceEntry, IngestBatch
from paperflow_curation import CurationService, AddRevision
from paperflow_domain import Item

with TemporaryDirectory(prefix='paperflow-browser-') as directory:
    settings = ResearchSettings(storage={'path':Path(directory)/'reader.db'}, monitor={'enabled':False})
    store = SQLiteResearchStorage(settings.storage)
    store.initialize()
    service = CurationService(store)
    for channel in ['technology','science']:
        batch = IngestBatch(channel=channel,config=ChannelConfig(url=f'https://{channel}.example/feed'),entries=tuple(SourceEntry(source_id=str(i),title=f'{channel.title()} reading {i}',url=f'https://example.org/{channel}/{i}',excerpt='A source excerpt with room for a careful reading.',discussion_url=f'https://example.org/discussion/{i}') for i in range(3)))
        persist_batch(store,batch)
    item=service.add(Item(kind='article',title='A quiet place to think',canonical_url='https://example.org/quiet'))
    service.add_revision(item.id,AddRevision(markdown='\n\n'.join(f'Paragraph {i}. '+('A careful reading leaves room for questions, context, and a new perspective. '*12) for i in range(30)),evidence_basis='full_text'))
    uvicorn.run(create_app(settings),host='127.0.0.1',port=8765,log_level='warning')
