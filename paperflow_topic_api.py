"""Editorial topic API, independent of source ingestion and reader state."""
from uuid import UUID, NAMESPACE_URL, uuid5
from fastapi import APIRouter, Header, Query
from paperflow_topic_models import CreateTopic, EditTopic, Topic, TopicRun, TopicSummary
from paperflow_topics import TopicService
from paperflow_domain import ItemKind


def topic_router(service: TopicService):
    router=APIRouter(prefix='/api/topics',tags=['topics'])

    @router.get('',response_model=list[TopicSummary])
    def topics(editorial: bool=False, saved: bool=False, query: str=Query('',max_length=200)):
        return service.summaries(editorial=editorial,saved=saved,query=query)

    @router.get('/resolve')
    def resolve(selector: str=Query(...,min_length=1,max_length=500)):
        return service.resolve(selector)

    @router.get('/latest')
    def latest(saved: bool=False, query: str=Query('',max_length=200), kind: ItemKind | None=None):
        rows=service.summaries(saved=saved,query=query,kind=kind)
        rows=sorted(rows,key=lambda r:r.topic.last_seen_at.timestamp() if r.topic.last_seen_at else 0,reverse=True)
        return {'key':'latest','title':'Latest','persistence':'persistent','kind':'topic_index','sensitivity':'urgent',
                'window_hours':service.settings.window_hours,'topics':rows}

    @router.post('/runs',response_model=TopicRun,status_code=201)
    def curate(key: str | None=Header(None,alias='Idempotency-Key',max_length=200)):
        return service.curate(run_id=uuid5(NAMESPACE_URL,'paperflow.topic.run:'+key) if key else None)

    @router.get('/runs',response_model=list[TopicRun])
    def runs(limit: int=Query(20,ge=1,le=100)):
        return service.runs(limit)

    @router.post('',response_model=Topic,status_code=201)
    def create(body: CreateTopic):
        return service.create(body)

    @router.get('/{topic_id}/config',response_model=Topic)
    def config(topic_id: UUID):
        return service.config(topic_id)

    @router.get('/{topic_id}/brief')
    def brief(topic_id: UUID, limit: int=Query(5,ge=1,le=20), offset: int=Query(0,ge=0),
              excerpt_chars: int=Query(240,ge=0,le=1000)):
        return service.brief(topic_id,limit=limit,offset=offset,excerpt_chars=excerpt_chars)

    @router.get('/{topic_id}')
    def detail(topic_id: UUID):
        return service.detail(topic_id)

    @router.patch('/{topic_id}',response_model=Topic)
    def edit(topic_id: UUID,body: EditTopic):
        return service.edit(topic_id,body)

    return router
