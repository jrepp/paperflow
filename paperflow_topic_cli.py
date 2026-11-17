"""Progressive, API-backed topic discovery and editorial curation."""
import json
from uuid import UUID
from urllib.parse import urlencode
import typer
from paperflow_api_client import APIClient, APIError
from paperflow_settings import load_research_settings

app = typer.Typer(invoke_without_command=True, help='Brief → inspect → curate topic channels')


@app.callback()
def configure(ctx: typer.Context, api_url: str | None=typer.Option(None), config: str=typer.Option('arxiv-radar.yaml'),
              json_output: bool=typer.Option(False,'--json'), full: bool=typer.Option(False,'--full'),
              measure: bool=typer.Option(False,'--measure')):
    ctx.meta.update(json=json_output, full=full, measure=measure, requests=0, response_bytes=0, db_queries=0)
    if ctx.invoked_subcommand is None:
        typer.echo('Topic curation in three steps:\n  list                         Find current themes\n  brief TOPIC                  Read five short source excerpts\n  curate TOPIC --include ITEM   Add a source and refresh assignments\n\nTOPIC accepts an exact title, UUID, or unique UUID prefix.\nUse curate --exclude ITEM to remove a match; --reset ITEM restores automatic matching.\nUse paperflow curate items --query TEXT to find item IDs.\nAdd --measure before a command to measure context; --json for scripts; --full show TOPIC for audit history.')
        return
    settings=load_research_settings(config)
    host=settings.service.host if settings.service.host!='0.0.0.0' else '127.0.0.1'
    if ':' in host: host=f'[{host}]'
    ctx.obj=APIClient(api_url or f'http://{host}:{settings.service.port}')


def request(ctx,method,path,body=None):
    try:
        result=ctx.obj.request(method,path,body)
        ctx.meta['requests']+=1
        measurement=getattr(ctx.obj,'last_measurement',{})
        for key in ('response_bytes','db_queries'):
            ctx.meta[key]+=measurement.get(key,0)
        return result
    except (APIError, ValueError) as exc:
        typer.echo(str(exc),err=True);raise typer.Exit(1) from None


def emit(ctx, value, text):
    output=json.dumps(value,indent=2) if ctx.meta['json'] else text
    typer.echo(output)
    if ctx.meta['measure']:
        typer.echo(json.dumps({'output_bytes':len(output.encode()),'output_characters':len(output),
            'estimated_tokens':(len(output)+3)//4,'token_basis':'rough characters/4 estimate',
            **{key:ctx.meta[key] for key in ('requests','response_bytes','db_queries')}}),err=True)


def resolve(ctx, selector):
    try:
        return str(UUID(selector))
    except ValueError:
        return request(ctx,'GET','/api/topics/resolve?'+urlencode({'selector':selector}))['id']


def topic_line(t):
    return f"{t['id']}  {t['title']}  [{t.get('editorial_status',t.get('status'))}; {t['sensitivity']}; {t['persistence']}; v{t['version']}]"


def brief_text(value):
    lines=[topic_line(value['topic']), f"{value['total']} current items; {len(value['records'])} shown. Basis: {value['basis']}."]
    if value['topic'].get('editorial_summary'):
        lines.append('Editorial summary: '+value['topic']['editorial_summary']+('…' if value['topic']['summary_truncated'] else ''))
    for row in value['records']:
        lines += [f"{row['item_id']}  {row['title']} [{row['channel']}]",
                  '  '+(row['excerpt'] or '(No local excerpt)')+('…' if row['excerpt_truncated'] else ''),
                  f"  {row['reason']} | {row['url'] or 'local source'}"]
    lines.append(f"Next offset: {value['next_offset']}" if value['next_offset'] is not None else 'End of current topic.')
    lines.append('Next: curate TOPIC --include ITEM / --exclude ITEM; --full show TOPIC for audit history.')
    return '\n'.join(lines)


@app.command('run')
def run(ctx: typer.Context, request_id: str | None=typer.Option(None,help='Reuse for safe retry')):
    ctx.obj.idempotency_key=request_id
    result=request(ctx,'POST','/api/topics/runs')
    emit(ctx,result,f"Run {result['id']}: {result['input_count']} arrivals; {result['unassigned_count']} outside published topics.\nNext: list; brief TOPIC")


@app.command('list')
def list_topics(ctx: typer.Context, editorial: bool=typer.Option(False), limit: int=typer.Option(10,min=1,max=100),
                offset: int=typer.Option(0,min=0)):
    rows=request(ctx,'GET',f'/api/topics?editorial={str(editorial).lower()}')
    page=rows[offset:offset+limit]
    result={'records':page,'total':len(rows),'next_offset':offset+limit if offset+limit<len(rows) else None}
    lines=['Latest — persistent index of topics emerging now']
    lines += [topic_line(r['topic'])+f" | {r['item_count']} items; {r['unread_count']} unread; {r['lifecycle']}" for r in page]
    lines.append(f"{len(page)}/{len(rows)} topics shown; next offset: {result['next_offset']}. Next: brief TOPIC")
    emit(ctx,result,'\n'.join(lines))


@app.command('brief')
def brief(ctx: typer.Context, topic: str, limit: int=typer.Option(5,min=1,max=20),
          offset: int=typer.Option(0,min=0), excerpt_chars: int=typer.Option(240,min=0,max=1000)):
    topic_id=resolve(ctx,topic)
    result=request(ctx,'GET',f'/api/topics/{topic_id}/brief?limit={limit}&offset={offset}&excerpt_chars={excerpt_chars}')
    emit(ctx,result,brief_text(result))


@app.command('show')
def show(ctx: typer.Context, topic: str):
    if not ctx.meta['full']:
        brief(ctx,topic,5,0,240)
        return
    result=request(ctx,'GET',f'/api/topics/{resolve(ctx,topic)}')
    emit(ctx,result,json.dumps(result,indent=2))


@app.command('create')
def create(ctx: typer.Context,title: str,terms: list[str]=typer.Option(...,'--term'),editor: str=typer.Option('local reader'),sensitivity: str=typer.Option('lasting')):
    result=request(ctx,'POST','/api/topics',{'title':title,'terms':terms,'editor':editor,'sensitivity':sensitivity})
    emit(ctx,result,topic_line(result)+'\nNext: run to match current arrivals; curate TOPIC --include ITEM for explicit selections.')


@app.command('edit')
def edit(ctx: typer.Context,topic: str,version: int | None=typer.Option(None),editor: str=typer.Option('local reader'),
         title: str | None=typer.Option(None),status: str | None=typer.Option(None),
         sensitivity: str | None=typer.Option(None),persistence: str | None=typer.Option(None),
         summary: str | None=typer.Option(None,help='Save an editorial summary with provenance in edit history'),
         terms: list[str] | None=typer.Option(None,'--term',help='Replace matching terms; repeat for each term'),note: str=typer.Option('')):
    topic_id=resolve(ctx,topic)
    if version is None:
        version=request(ctx,'GET',f'/api/topics/{topic_id}/brief?limit=1&excerpt_chars=0')['topic']['version']
    body={'expected_version':version,'editor':editor,'note':note}
    for key,value in [('title',title),('editorial_status',status),('sensitivity',sensitivity),('persistence',persistence),('description',summary),('terms',terms)]:
        if value is not None:body[key]=value
    result=request(ctx,'PATCH',f'/api/topics/{topic_id}',body)
    emit(ctx,result,topic_line(result))


@app.command('curate')
def curate(ctx: typer.Context,topic: str,include: list[UUID]=typer.Option([], '--include'),
           exclude: list[UUID]=typer.Option([], '--exclude'), reset: list[UUID]=typer.Option([], '--reset'),
           editor: str=typer.Option('local reader'),note: str=typer.Option(''),version: int | None=typer.Option(None)):
    if not (include or exclude or reset):
        raise typer.BadParameter('Choose --include, --exclude, or --reset ITEM_ID')
    if set(include)&set(exclude) or set(reset)&(set(include)|set(exclude)):
        raise typer.BadParameter('Each item must have only one action')
    topic_id=resolve(ctx,topic)
    current=request(ctx,'GET',f'/api/topics/{topic_id}/config')
    included=set(current['included_item_ids']);excluded=set(current['excluded_item_ids'])
    for item in reset:
        included.discard(str(item));excluded.discard(str(item))
    for item in include:
        included.add(str(item));excluded.discard(str(item))
    for item in exclude:
        excluded.add(str(item));included.discard(str(item))
    request(ctx,'PATCH',f'/api/topics/{topic_id}',{'expected_version':version or current['version'],
        'editor':editor,'note':note,'included_item_ids':sorted(included),'excluded_item_ids':sorted(excluded)})
    # Writes are optimistic; a failed refresh leaves the reviewed overrides durable.
    try:
        request(ctx,'POST','/api/topics/runs')
    except typer.Exit:
        typer.echo('Editorial choices saved; refresh failed. Retry with topics run.',err=True)
        raise
    result=request(ctx,'GET',f'/api/topics/{topic_id}/brief')
    emit(ctx,result,'Editorial choices saved and topics refreshed. Overrides respect the rolling arrival window.\n'+brief_text(result))
