import {test, expect} from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

test('browse, save, read, return, and resume in a new browser context', async ({page,browser})=>{
  await page.goto('/');
  const story=page.getByRole('link',{name:'A quiet place to think',exact:true});
  await expect(story).toBeVisible();
  await story.focus(); await page.keyboard.press('Enter');
  await expect(page.locator('#source-text')).toBeVisible();
  await page.getByRole('button',{name:'Save for later',exact:true}).click();
  await expect(page.getByRole('button',{name:'Saved',exact:true})).toHaveAttribute('aria-pressed','true');
  await page.locator('[data-offset]').nth(15).scrollIntoViewIfNeeded();
  await page.waitForTimeout(900);
  const id=page.url().split('/read/')[1];
  const state=await (await page.request.get(`/api/reader/items/${id}/state`)).json();
  expect(state.anchor.character_offset).toBeGreaterThan(0);
  await page.getByRole('link',{name:'Back to reading list'}).click();
  await expect(story).toBeFocused();
  const context=await browser.newContext(); const reopened=await context.newPage();
  await reopened.goto('http://127.0.0.1:8765/saved');
  await reopened.getByRole('link',{name:'Resume reading',exact:true}).click();
  await expect(reopened.locator('#source-text')).toBeVisible();
  await expect.poll(()=>reopened.evaluate(()=>scrollY)).toBeGreaterThan(1000);
  await context.close();
});

test('keyboard settings persist; shell and reading reflow without accessibility violations',async({page})=>{
  await page.setViewportSize({width:320,height:800});
  await page.goto('/');
  await expect(page.locator('main')).toHaveAttribute('aria-busy','false');
  await page.getByRole('button',{name:'Reading settings',exact:true}).focus();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('dialog')).toBeVisible();
  await page.getByLabel('Text size').selectOption('28');
  await page.getByLabel('Theme').selectOption('contrast');
  await page.getByRole('button',{name:'Save reading settings',exact:true}).click();
  await expect(page.getByRole('button',{name:'Reading settings',exact:true})).toBeFocused();
  await page.reload();
  await expect(page.locator('html')).toHaveAttribute('data-theme','contrast');
  for(const path of ['/','/saved','/research','/sources']){
    await page.goto(path);await expect(page.locator('main')).toHaveAttribute('aria-busy','false');
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy();
    const results=await new AxeBuilder({page}).withTags(['wcag2a','wcag2aa','wcag21aa','wcag22aa']).analyze();
    expect(results.violations).toEqual([]);
  }
  await page.goto('/');await page.getByRole('link',{name:'A quiet place to think',exact:true}).click();
  await expect(page.locator('#source-text')).toBeVisible();
  await page.addStyleTag({content:'html{font-size:200%} p{letter-spacing:.12em!important;word-spacing:.16em!important;line-height:1.5!important;margin-bottom:2em!important}'});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy();
  expect((await new AxeBuilder({page}).withTags(['wcag2a','wcag2aa','wcag21aa','wcag22aa']).analyze()).violations).toEqual([]);
});

test('channel filtering, excerpt availability and honest discussion empty state',async({page})=>{
  await page.goto('/');
  await page.getByRole('button',{name:'Latest'}).click();
  await page.getByRole('option',{name:/science/}).click();
  await expect(page.locator('.feed-row')).toHaveCount(3);
  await page.getByRole('link',{name:'Science reading 0',exact:true}).click();
  await expect(page.locator('#availability')).toContainText('not the full article');
  await page.getByRole('link',{name:'Discussion',exact:true}).click();
  await expect(page.locator('#availability')).toContainText('Comments are not available locally');
  await expect(page.getByRole('link',{name:'Open original discussion'})).toHaveAttribute('href','https://example.org/discussion/0');
});

test('safe source text, dismissed items and service errors stay recoverable',async({page})=>{
  const item=await (await page.request.post('/api/research/items',{data:{kind:'article',title:'Safe rendering check'}})).json();
  await page.request.post(`/api/research/items/${item.id}/revisions`,{data:{markdown:'<img src=x onerror="window.paperflowUnsafeExecuted=true">\n\n<script>window.paperflowUnsafeExecuted=true</script>',evidence_basis:'excerpt'}});
  await page.goto(`/read/${item.id}`);
  await expect(page.locator('#source-text')).toContainText('<script>');
  expect(await page.evaluate(()=>window.paperflowUnsafeExecuted)).toBeUndefined();
  expect(await page.locator('#source-text img').count()).toBe(0);
  await page.goto('/');
  const row=page.locator('.feed-row').filter({hasText:'Safe rendering check'});
  await row.getByRole('button',{name:/All set — clear/}).click();
  await expect(row.getByRole('button',{name:/Undo — restore/})).toBeVisible();
  await expect(row.locator('h2')).toBeHidden();
  await expect(row.locator('.clear-receipt')).toContainText('Cleared');
  await row.getByRole('button',{name:/Undo — restore/}).click();
  await page.reload(); await expect(row).toBeVisible();
  await page.route('**/api/reader/feed?**',route=>route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({detail:'Reader temporarily unavailable'})}));
  await page.goto('/');await expect(page.getByRole('alert')).toContainText('Reader temporarily unavailable');
  await page.unroute('**/api/reader/feed?**');await page.getByRole('button',{name:'Retry',exact:true}).click();
  await expect(row).toBeVisible();
});

test('later batches and channel context survive source and discussion navigation',async({page})=>{
  for(let i=0;i<22;i++)await page.request.post('/api/research/items',{data:{kind:'article',title:`Paging check ${i}`}});
  await page.goto('/?query=Paging');
  await expect(page.locator('.feed-row')).toHaveCount(20);
  await page.getByRole('button',{name:'Read the next batch'}).click();
  await expect(page.locator('.feed-row')).toHaveCount(22);
  const link=page.getByRole('link',{name:'Paging check 0',exact:true});
  await link.click();await page.getByRole('link',{name:'Discussion',exact:true}).click();
  await page.getByRole('link',{name:'Back to reading list'}).click();
  await expect(page.locator('.feed-row')).toHaveCount(22);
  await expect(link).toBeFocused();
  await expect(page.getByRole('searchbox')).toHaveValue('Paging');
});

test('source operations show failed and paused states with working recovery controls',async({page})=>{
  const channel={name:'fixture',paused:false,interval_seconds:900,next_due_at:new Date().toISOString(),last_success_at:null,consecutive_failures:1,active_run_id:null,last_run:{status:'failed',error:'Upstream unavailable'},stale:true};
  await page.route('**/api/monitor/channels',route=>route.fulfill({json:[channel]}));
  await page.route('**/api/monitor/channels/fixture',async route=>{
    expect(route.request().method()).toBe('PATCH'); channel.paused=route.request().postDataJSON().paused;await route.fulfill({json:[channel]});
  });
  await page.route('**/api/monitor/channels/fixture/runs',route=>route.fulfill({status:202,json:{id:'queued-run'}}));
  await page.goto('/sources');await expect(page.getByText('Upstream unavailable',{exact:true})).toBeVisible();
  await page.getByRole('button',{name:'Pause ingestion',exact:true}).click();
  await expect(page.getByRole('button',{name:'Fetch now',exact:true})).toBeDisabled();
  await page.getByRole('button',{name:'Resume ingestion',exact:true}).click();
  await page.getByRole('button',{name:'Fetch now',exact:true}).click();
  await expect(page.getByRole('status')).toContainText('Fetch queued');
});

test('forced colors and reduced motion preserve focus and readable navigation',async({page})=>{
  await page.emulateMedia({forcedColors:'active',reducedMotion:'reduce'});
  await page.setViewportSize({width:320,height:800});
  await page.goto('/');await expect(page.locator('main')).toHaveAttribute('aria-busy','false');
  await page.getByRole('link',{name:'Skip to content'}).focus();
  await page.keyboard.press('Enter');await expect(page.locator('main')).toBeFocused();
  await page.getByRole('button',{name:'Reading settings',exact:true}).focus();await page.keyboard.press('Enter');
  await expect(page.getByRole('dialog')).toBeVisible();await page.keyboard.press('Escape');
  await expect(page.getByRole('button',{name:'Reading settings',exact:true})).toBeFocused();
  expect(await page.evaluate(()=>getComputedStyle(document.activeElement).outlineStyle)).not.toBe('none');
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy();
});

test('resuming a changed source preserves the original revision and offers the new one',async({page})=>{
  const item=await (await page.request.post('/api/research/items',{data:{kind:'article',title:'Revision continuity'}})).json();
  const revision=await (await page.request.post(`/api/research/items/${item.id}/revisions`,{data:{markdown:'Original passage.\n\nOriginal ending.'}})).json();
  await page.request.patch(`/api/reader/items/${item.id}/state`,{data:{expected_version:0,saved:true,status:'in_progress',anchor:{revision_id:revision.id,character_offset:19}}});
  await page.request.post(`/api/research/items/${item.id}/revisions`,{data:{markdown:'A revised passage with new context.'}});
  await page.goto(`/read/${item.id}`);
  await expect(page.locator('#source-text')).toContainText('Original passage');
  await expect(page.locator('#availability')).toContainText('A newer source version is available');
  await page.getByRole('link',{name:'Read the latest version',exact:true}).click();
  await expect(page.locator('#source-text')).toContainText('A revised passage with new context.');
});

test('conflicting tabs explain stale state and recover after reload',async({page,context})=>{
  const item=await (await page.request.post('/api/research/items',{data:{kind:'article',title:'Concurrent reading'}})).json();
  await page.request.post(`/api/research/items/${item.id}/revisions`,{data:{markdown:'A small excerpt.'}});
  await page.goto(`/read/${item.id}`);await expect(page.locator('main')).toHaveAttribute('aria-busy','false');
  const other=await context.newPage();await other.goto(`/read/${item.id}`);await expect(other.locator('main')).toHaveAttribute('aria-busy','false');
  await page.getByRole('button',{name:'Save for later',exact:true}).click();
  await other.getByRole('button',{name:'Mark finished',exact:true}).click();
  await expect(other.getByRole('status')).toContainText('Reading state changed');
  await other.reload();await other.getByRole('button',{name:'Mark finished',exact:true}).click();
  await expect(other.getByRole('status')).toContainText('Marked finished');
  await other.close();
});

test('a bookmarked source returns to Today after switching item views',async({page})=>{
  const item=await (await page.request.post('/api/research/items',{data:{kind:'article',title:'Bookmarked reading'}})).json();
  await page.goto('/sources');
  // A full document navigation has no in-app list history to return through.
  await page.goto(`/read/${item.id}`);
  await page.getByRole('link',{name:'Discussion',exact:true}).click();
  await page.getByRole('link',{name:'Back to reading list'}).click();
  await expect(page).toHaveURL('http://127.0.0.1:8765/');
  await expect(page.getByRole('heading',{name:'Today',exact:true})).toBeVisible();
});

test('Markdown and Mermaid render locally with readable error fallbacks',async({page})=>{
  const item=await (await page.request.post('/api/research/items',{data:{kind:'article',title:'Rich source'}})).json();
  await page.request.post(`/api/research/items/${item.id}/revisions`,{data:{markdown:'# Methods\n\n**Strong evidence** and [source](https://example.org).\n\n- First result\n- Second result\n\n```mermaid\ngraph TD\n A[Read] --> B[Research]\n```\n\n```mermaid\nnot a valid diagram\n```'}});
  await page.goto(`/read/${item.id}`);
  await expect(page.locator('#source-text strong')).toHaveText('Strong evidence');
  await expect(page.locator('#source-text li')).toHaveCount(2);
  await expect(page.locator('.diagram-surface img')).toHaveCount(1);
  await expect.poll(()=>page.locator('.diagram-surface img').evaluate(img=>img.complete && img.naturalWidth>0)).toBeTruthy();
  await expect(page.getByText('This diagram could not be rendered. Its definition is available below.')).toBeVisible();
  await page.getByRole('link',{name:'Diagrams',exact:true}).click();
  await expect(page.locator('.diagram-surface img')).toHaveCount(1);
  await expect.poll(()=>page.locator('.diagram-surface img').evaluate(img=>img.complete && img.naturalWidth>0)).toBeTruthy();
  await expect(page.locator('#source-text strong')).toHaveCount(0);
});

test('local PDF attachment opens an embedded paged viewer',async({page})=>{
  const item=await (await page.request.post('/api/research/items',{data:{kind:'paper',title:'Embedded PDF'}})).json();
  await page.goto(`/read/${item.id}?view=pdf`);
  await page.getByLabel('PDF file',{exact:true}).setInputFiles('tests/fixtures/reader-two-pages.pdf');
  await page.getByRole('button',{name:'Attach and read PDF'}).click();
  await expect(page.getByText('Page 1 of 2',{exact:true})).toBeVisible();
  await expect(page.locator('.pdf-text')).toContainText('First PDF page');
  await page.getByRole('button',{name:'Next page',exact:true}).click();
  await expect(page.locator('.pdf-text')).toContainText('Second PDF page');
  await page.reload();await expect(page.locator('.pdf-surface canvas')).toBeVisible();
});

test('compact channel picker provides contextual counts and About owns the introduction',async({page})=>{
  await page.goto('/');
  await expect(page.getByText('A finite batch. An open mind.',{exact:true})).toHaveCount(0);
  const trigger=page.getByRole('button',{name:'Latest'});await trigger.click();
  await expect(page.getByRole('listbox',{name:'Topics and source feeds'})).toBeVisible();
  await expect(page.getByRole('option',{name:/technology/})).toContainText('in Today');
  await page.keyboard.press('Escape');await expect(trigger).toBeFocused();
  await page.getByRole('link',{name:'About Paperflow'}).click();
  await expect(page.getByRole('heading',{name:'How Paperflow works'})).toBeVisible();
  await expect(page.getByText('Ingested items',{exact:true})).toHaveCount(2);
  await expect(page.getByText('A finite batch. An open mind.',{exact:true})).toBeVisible();
});

test('research briefs open in the embedded Markdown reader with a download fallback',async({page})=>{
  const bucket=await (await page.request.post('/api/research/buckets',{data:{title:'Rendered research',research_question:'What does the evidence say?'}})).json();
  await page.goto('/research');
  await page.locator('article').filter({has:page.getByRole('heading',{name:'Rendered research',exact:true})}).getByRole('link',{name:'Read research brief'}).click();
  await expect(page).toHaveURL(new RegExp(`/research/${bucket.id}$`));
  await expect(page.locator('#research-brief h1')).toContainText('Rendered research');
  await expect(page.getByRole('link',{name:'Download Markdown'})).toHaveAttribute('download','research-brief.md');
});

test('automatic topics appear in Latest and editorial overrides survive recuration',async({page})=>{
  await page.request.post('/api/topics/runs');
  const latest=await (await page.request.get('/api/topics/latest')).json();
  const topic=latest.topics.find(row=>row.topic.title==='Technology Reading').topic;
  await page.goto('/');
  await page.getByRole('button',{name:'Latest'}).click();
  await page.getByRole('option',{name:/^Technology Reading/}).click();
  await expect(page.locator('.feed-row')).toHaveCount(3);
  await page.getByRole('link',{name:'Review topics',exact:true}).click();
  const card=page.locator('#editorial-topics article').filter({has:page.getByRole('heading',{name:'Technology Reading',exact:true})});
  await card.getByText('Edit topic',{exact:true}).click();
  await card.getByLabel('Title',{exact:true}).fill('Computing today');
  await card.getByRole('combobox',{name:'Time sensitivity',exact:true}).selectOption('urgent');
  await card.getByRole('button',{name:'Save editorial changes'}).click();
  await expect(page.getByRole('heading',{name:'Computing today',exact:true})).toBeVisible();
  await page.getByRole('button',{name:'Curate now',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Computing today',exact:true})).toBeVisible();
  await page.setViewportSize({width:320,height:800});
  await page.getByText('Edit topic',{exact:true}).last().click();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy();
  expect((await new AxeBuilder({page}).withTags(['wcag2a','wcag2aa','wcag21aa','wcag22aa']).analyze()).violations).toEqual([]);
  const detail=await (await page.request.get(`/api/topics/${topic.id}`)).json();
  expect(detail.topic.sensitivity).toBe('urgent');expect(detail.edits).toHaveLength(1);
});

test('paper affordances and type filters stay clear across navigation and empty results',async({page})=>{
  const paper=await (await page.request.post('/api/research/items',{data:{kind:'paper',title:'A paper about reader clarity',authors:['Ada Example'],canonical_url:'https://example.org/paper'}})).json();
  await page.request.post(`/api/research/items/${paper.id}/revisions`,{data:{markdown:'A bounded abstract for this research paper.',evidence_basis:'abstract'}});
  await page.goto('/');
  const types=page.getByRole('group',{name:'Content type'});
  await types.getByRole('button',{name:'Papers',exact:true}).click();
  await expect(page).toHaveURL(/kind=paper/);
  const row=page.locator('.feed-row').filter({has:page.getByRole('link',{name:'A paper about reader clarity',exact:true})});
  await expect(row).toBeVisible();
  await expect(page.locator('.feed-row:not([data-item-kind=paper])')).toHaveCount(0);
  await expect(row.locator('.item-type')).toHaveText('Paper');
  await expect(row.locator('.paper-byline')).toContainText('Ada Example');
  await row.getByRole('link',{name:'Read paper',exact:true}).click();
  await expect(page.getByRole('link',{name:'Open original paper'})).toHaveAttribute('href','https://example.org/paper');
  await page.getByRole('link',{name:'Back to reading list'}).click();
  await expect(types.getByRole('button',{name:'Papers',exact:true})).toHaveAttribute('aria-pressed','true');
  await page.getByRole('button',{name:'Latest',exact:true}).click();
  await page.getByRole('option',{name:/^science /}).click();
  await expect(page).toHaveURL(/kind=paper/);
  await expect(page).toHaveURL(/channel=science/);
  await expect(page.locator('.feed-row')).toHaveCount(0);
  await expect(page.locator('#batch-end')).toContainText('No items match these filters');
  await types.getByRole('button',{name:'All types',exact:true}).click();
  await expect(page.locator('.feed-row')).toHaveCount(3);
  await expect(page.locator('.item-type')).toHaveText(['Discussion','Discussion','Discussion']);
  await page.setViewportSize({width:320,height:800});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy();
  expect((await new AxeBuilder({page}).withTags(['wcag2a','wcag2aa','wcag21aa','wcag22aa']).analyze()).violations).toEqual([]);
});

test('source heads-up merges feeds and updates progress without losing focus',async({page})=>{
  const channel={name:'science',paused:false,interval_seconds:900,next_due_at:new Date().toISOString(),last_success_at:new Date().toISOString(),consecutive_failures:0,active_run_id:'active',last_run:{id:'active',status:'running',stage:'enrichment',completed:2,total:5,item_count:0,warning_count:0},stale:false};
  let polls=0;
  await page.route('**/api/monitor/channels',route=>{polls++;return route.fulfill({json:[channel]});});
  await page.route('**/api/monitor/runs?limit=10',route=>route.fulfill({json:[]}));
  await page.route('**/health/ready',route=>route.fulfill({json:{healthy:true,enabled:true,schedule_enabled:false,telemetry:{otlp_configured:true},topics:{enabled:true,running:false}}}));
  await page.goto('/sources');
  const card=page.locator('.source-monitor-card').filter({has:page.getByRole('heading',{name:'science',exact:true})});
  await expect(card).toHaveCount(1);
  await expect(page.getByText('Manual collection',{exact:true})).toBeVisible();
  await expect(card).toContainText('2 / 5 in this stage');
  await expect(card.getByRole('button',{name:'Fetch now',exact:true})).toBeDisabled();
  await card.getByText('Run details',{exact:true}).click();
  const pause=card.getByRole('button',{name:'Pause ingestion',exact:true});await pause.focus();
  channel.active_run_id=null;channel.last_run={...channel.last_run,status:'succeeded',stage:'finished',completed:5,item_count:5,duration_seconds:1.2};
  const before=polls;
  await expect.poll(()=>polls,{timeout:8000}).toBeGreaterThan(before);
  await expect(card).toContainText('Fresh');
  await expect(pause).toBeFocused();
  await expect(card.locator('details')).toHaveAttribute('open','');
  await expect(card.getByRole('button',{name:'Fetch now',exact:true})).toBeEnabled();
  await page.setViewportSize({width:320,height:800});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy();
  expect((await new AxeBuilder({page}).withTags(['wcag2a','wcag2aa','wcag21aa','wcag22aa']).analyze()).violations).toEqual([]);
  await page.getByRole('link',{name:'Today',exact:true}).click();
  const after=polls;await page.waitForTimeout(5200);expect(polls).toBe(after);
});
