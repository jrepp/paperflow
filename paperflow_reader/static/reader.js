const main = document.querySelector('#main');
const notice = document.querySelector('#notice');
const settings = document.querySelector('#settings');
const form = document.querySelector('#preferences-form');
let routedLocation=location.pathname+location.search;
let preferences, activeReader = null, pageGeneration = 0, saving = Promise.resolve();
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const contentTypes=[['','All types'],['paper','Papers'],['article','Articles'],['discussion','Discussions'],['note','Notes'],['manuscript','Manuscripts']];
const typeLabel=kind=>({paper:'Paper',article:'Article',discussion:'Discussion',note:'Note',manuscript:'Manuscript'}[kind] || 'Item');
const safeURL = value => { try { const url = new URL(value); return ['https:','http:'].includes(url.protocol) ? esc(url.href) : ''; } catch { return ''; } };
const external = (url, label) => safeURL(url) ? `<a class="control" href="${safeURL(url)}" rel="noreferrer">${esc(label)} ↗</a>` : '';
async function api(path, options = {}) {
  const response = await fetch(path, { ...options, headers: {'Content-Type':'application/json', ...options.headers} });
  if (!response.ok) { const body = await response.json().catch(() => ({})); throw new Error(typeof body.detail === 'string' ? body.detail : `Request failed (${response.status}). Please try again.`); }
  return response.status === 204 ? null : response.json();
}
function announce(message) { notice.textContent = message; }
function applyPreferences(value) {
  const root = document.documentElement;
  root.dataset.theme = value.theme;
  root.style.setProperty('--reader-size', `${value.font_size / 16}rem`);
  root.style.setProperty('--reader-leading', value.line_height);
  root.style.setProperty('--reader-width', `${value.measure}ch`);
  root.style.setProperty('--reader-font', value.font === 'serif' ? "Georgia,'Times New Roman',serif" : 'system-ui,sans-serif');
}
async function patchState(id, changes, state) {
  return api(`/api/reader/items/${id}/state`, {method:'PATCH', body:JSON.stringify({expected_version:state.version, ...changes})});
}
function age(value) {
  if (!value) return 'No observation yet';
  const hours = Math.max(0, Math.floor((Date.now()-new Date(value))/3600000));
  return hours < 1 ? 'Observed within the last hour' : `Observed ${hours} hours ago${hours > 24 ? ' · May be stale' : ''}`;
}
function rememberList() {
  if (!activeReader) history.replaceState({...history.state, scroll:scrollY, focus:document.activeElement.id}, '', location.href);
}
async function navigate(url) {
  rememberList(); await savePosition();
  const readerDepth=url.startsWith('/read/') ? (activeReader ? (history.state?.readerDepth ? history.state.readerDepth+1 : 0) : 1) : 0;
  history.pushState({from: activeReader ? activeReader.from : location.pathname+location.search, readerDepth}, '', url);
  await render();
}
document.addEventListener('pointerdown',event=>{if(!event.target.closest('.channel-picker')){const options=document.querySelector('#channel-options');if(options){options.hidden=true;document.querySelector('#channel-trigger').setAttribute('aria-expanded','false');}}});
document.addEventListener('click', event => {
  const link = event.target.closest('a');
  if (!link || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || link.target || link.hasAttribute('download')) return;
  const url = new URL(link.href);
  if (url.origin !== location.origin || url.hash || url.pathname.startsWith('/api/') || url.pathname === '/docs') return;
  event.preventDefault(); navigate(url.pathname+url.search);
});
window.addEventListener('popstate', () => { if(location.pathname+location.search===routedLocation)return; savePosition(); render(true); });
async function savePosition() {
  const reader = activeReader;
  if (!reader?.revision || reader.discussion || reader.state.status === 'finished') return;
  const paragraphs = [...document.querySelectorAll('#source-text [data-offset]')];
  if (!paragraphs.length || paragraphs[0].getBoundingClientRect().top > 100) return;
  const visible = paragraphs.find(p => p.getBoundingClientRect().bottom > 0) || paragraphs.at(-1);
  const offset = Number(visible.dataset.offset);
  saving = saving.catch(() => {}).then(async () => {
    reader.state = await patchState(reader.id, {status:'in_progress', anchor:{revision_id:reader.revision.id, character_offset:offset}}, reader.state);
  }).catch(error => { announce(`Reading position was not saved. ${error.message}`); });
  return saving;
}
let scrollTimer, sourcesTimer;
window.addEventListener('scroll', () => { clearTimeout(scrollTimer); if(activeReader) scrollTimer = setTimeout(savePosition, 500); }, {passive:true});
document.addEventListener('visibilitychange', () => { if(document.hidden) savePosition(); });

async function feed(view, generation, restoring) {
  const params = new URLSearchParams(location.search);
  const channel = params.get('channel') || '', query = params.get('query') || '', topicId=params.get('topic') || '', kind=params.get('kind') || '';
  const typeQuery=kind?`&kind=${encodeURIComponent(kind)}`:'';
  const snapshot=restoring ? history.state?.feedSnapshot : null;
  const [data, channels, latest] = snapshot ? [snapshot.data,snapshot.channels,snapshot.latest || {topics:[]}] : await Promise.all([api(`/api/reader/feed?view=${view}&${params}`), api(`/api/reader/channels?view=${view}&query=${encodeURIComponent(query)}${typeQuery}`), api(`/api/topics/latest?saved=${view==='saved'}&query=${encodeURIComponent(query)}${typeQuery}`)]);
  const selectedTopic=latest.topics.find(row=>row.topic.id===topicId);
  if(snapshot) await Promise.all(data.records.map(async record=>{record.state=await api(`/api/reader/items/${record.item.id}/state`);}));
  if(generation !== pageGeneration) return;
  document.querySelector('#global-search input').value=query;
  main.innerHTML = `<div class="feed-toolbar"><h1>${view==='saved'?'Saved':'Today'}</h1><div class="channel-picker"><span class="filter-label" id="channel-label">Topic or source</span><button id="channel-trigger" aria-haspopup="listbox" aria-expanded="false" aria-controls="channel-options">${esc(selectedTopic?.topic.title || (topicId?'Expired topic':channel || 'Latest'))} <span aria-hidden="true">⌄</span></button><div id="channel-options" class="channel-options" role="listbox" aria-label="Topics and source feeds" hidden></div></div>${query?`<span class="meta">Titles matching “${esc(query)}”</span>`:''}<a class="meta" href="/topics">Review topics</a></div><div class="type-filters" role="group" aria-label="Content type"><span class="filter-label">Type</span>${contentTypes.map(([value,label])=>`<button type="button" data-kind="${value}" aria-pressed="${kind===value}">${label}</button>`).join('')}</div>${!topicId && !channel && latest.topics.some(row=>row.item_count>0)?`<nav class="topic-strip" aria-label="Topics emerging now">${latest.topics.filter(row=>row.item_count>0).slice(0,8).map(row=>`<a href="${view==='saved'?'/saved':'/'}?topic=${row.topic.id}${typeQuery}&query=${encodeURIComponent(query)}">${esc(row.topic.title)} <span class="topic-count" aria-label="${row.item_count} items">${row.item_count}</span></a>`).join('')}</nav>`:''}${topicId?`<p class="meta">${selectedTopic?`${esc(selectedTopic.topic.sensitivity)} · ${selectedTopic.item_count} items in the rolling window`:'This topic has no published content in the current window. Saved items remain in your Saved list.'}</p>`:''}<ol class="feed-list" id="feed-list"></ol><div id="batch-end" class="py-8"></div>`;
  document.querySelectorAll('[data-kind]').forEach(button=>button.onclick=()=>{const search=new URLSearchParams(params);search.delete('before');if(button.dataset.kind)search.set('kind',button.dataset.kind);else search.delete('kind');navigate(`${view==='saved'?'/saved':'/'}?${search}`);});
  const trigger=document.querySelector('#channel-trigger'),options=document.querySelector('#channel-options');
  const choices=[{kind:'latest',label:'Latest',stats:`${latest.topics.length} current topics · matching incoming items`,selected:!channel&&!topicId},
    ...latest.topics.map(row=>({kind:'topic',id:row.topic.id,label:row.topic.title,stats:`${row.item_count} items · ${row.unread_count} unread · ${row.topic.sensitivity} · ${row.topic.persistence}`,selected:topicId===row.topic.id})),
    ...channels.map(c=>({kind:'source',id:c.channel,label:c.channel,stats:`Source · ${c.visible_count} in ${view==='saved'?'Saved':'Today'} · ${c.unread_count} unread · ${c.saved_count} saved`,freshness:age(c.observed_at),selected:channel===c.channel&&!topicId}))];
  choices.forEach(c=>{
    const option=document.createElement('button');option.type='button';option.setAttribute('role','option');option.setAttribute('aria-selected',String(c.selected));
    option.innerHTML=`<strong>${esc(c.label)}</strong><span class="picker-kind">${c.kind==='topic'?'Topic':c.kind==='source'?'Source feed':'All sources and topics'}</span><span class="channel-stats">${esc(c.stats)}</span>${c.freshness?`<span class="meta">${esc(c.freshness)}</span>`:''}`;
    // Safari blurs the focused option on mouse-down without focusing the clicked
    // button. Keep focus inside the list until click commits the selection.
    option.onmousedown=event=>{event.preventDefault();option.focus({preventScroll:true});};
    option.onclick=()=>{const search=new URLSearchParams({query});if(kind)search.set('kind',kind);if(c.kind==='topic')search.set('topic',c.id);if(c.kind==='source')search.set('channel',c.id);navigate(`${view==='saved'?'/saved':'/'}?${search}`);};options.append(option);
  });
  function closePicker(){options.hidden=true;trigger.setAttribute('aria-expanded','false');}
  trigger.onclick=()=>{const opening=options.hidden;options.hidden=!opening;trigger.setAttribute('aria-expanded',String(opening));if(opening)(options.querySelector('[aria-selected=true]') || options.firstElementChild).focus();};
  options.onkeydown=event=>{const items=[...options.children];let index=items.indexOf(document.activeElement);if(event.key==='Escape'){event.preventDefault();closePicker();trigger.focus();return;}if(['ArrowDown','ArrowUp','Home','End'].includes(event.key)){event.preventDefault();index=event.key==='Home'?0:event.key==='End'?items.length-1:(index+(event.key==='ArrowDown'?1:-1)+items.length)%items.length;items[index].focus();}};
  const picker=trigger.parentElement;picker.addEventListener('focusout',()=>setTimeout(()=>{if(!picker.contains(document.activeElement))closePicker();},0));
  let index = 0; const allRecords=[];
  function append(page) {
    const list = document.querySelector('#feed-list');
    allRecords.push(...page.records);
    for(const record of page.records) {
      index++; const {item,state,revision,observation} = record;
      const row = document.createElement('li'); row.className='feed-row';row.dataset.itemKind=item.kind;
      row.innerHTML = `<span class="row-number" aria-hidden="true">${String(index).padStart(2,'0')}</span><article><div class="meta item-meta"><span class="item-type" data-type="${esc(item.kind)}">${typeLabel(item.kind)}</span><span>${esc(observation?.channel || 'Local library')}</span> · ${esc(state.status.replace('_',' '))}${observation?.entry.signals.score != null ? ` · ${observation.entry.signals.score} votes` : ''}</div><h2><a id="item-${item.id}" href="/read/${item.id}">${esc(item.title)}</a></h2>${item.kind==='paper' && (item.authors.length || item.published_at)?`<p class="meta paper-byline">${esc([item.authors.slice(0,3).join(', ')+(item.authors.length>3?' et al.':''),item.published_at?.slice(0,10)].filter(Boolean).join(' · '))}</p>`:''}<p class="muted">${esc(revision?.preview_text || 'Local text is not available. Open the original source to read.')}</p><p class="meta">${esc(revision?.evidence_basis?.replace('_',' ') || 'No local text')} · ${esc(age(observation?.observed_at))}</p><div class="row-actions flex flex-wrap gap-2"><span class="clear-receipt">✓ Cleared · ${esc(item.title)}</span><a id="read-${item.id}" class="control" href="/read/${item.id}">${state.anchor ? 'Resume reading' : item.kind==='paper'?'Read paper':'Read source'}</a><a class="control" href="/read/${item.id}?view=discussion">Discussion</a><button type="button" data-save aria-pressed="${state.saved}" aria-label="${state.saved ? 'Unsave' : 'Save'} ${esc(item.title)}">${state.saved ? 'Saved' : 'Save for later'}</button><button type="button" data-dismiss aria-label="${state.status==='dismissed'?'Undo — restore':'All set — clear'} ${esc(item.title)} ${state.status==='dismissed'?'to':'from'} the Today inbox" title="Clear from the Today inbox; saved reading is kept">${state.status==='dismissed'?'↶ Undo':'✓ All set'}</button></div></article>`;
      let current = state; const beforeDismiss=state.status==='dismissed'?'unread':state.status;
      row.querySelector('[data-save]').onclick = async e => {
        const button=e.currentTarget; button.disabled=true;
        try { current=await patchState(item.id,{saved:!current.saved},current); record.state=current; if(generation!==pageGeneration)return; history.replaceState({...history.state,feedSnapshot:{data:{records:allRecords,next_before:history.state.feedSnapshot.data.next_before},channels,latest}},"",location.href); button.textContent=current.saved?'Saved':'Save for later'; button.setAttribute('aria-pressed',current.saved); button.setAttribute('aria-label',`${current.saved?'Unsave':'Save'} ${item.title}`); announce(current.saved ? 'Saved for later.' : 'Removed from Saved. Select Save for later to undo.'); }
        catch(error) { announce(error.message); } finally { button.disabled=false; }
      };
      row.querySelector('[data-dismiss]').onclick=async e=>{
        const button=e.currentTarget;button.disabled=true;
        try{current=await patchState(item.id,{status:current.status==='dismissed'?beforeDismiss:'dismissed'},current);record.state=current;button.textContent=current.status==='dismissed'?'↶ Undo':'✓ All set';button.setAttribute('aria-label',`${current.status==='dismissed'?'Undo — restore':'All set — clear'} ${item.title} ${current.status==='dismissed'?'to':'from'} the Today inbox`);button.title=current.status==='dismissed'?'Bring this item back to your inbox':'Clear from the Today inbox; saved reading is kept';row.dataset.cleared=String(current.status==='dismissed' && view==='today');announce(current.status==='dismissed'?'All set. Cleared from your Today inbox; saved reading is kept. Undo is available here.':'Back in your Today inbox.');}
        catch(error){announce(error.message);}finally{button.disabled=false;}
      };
      list.append(row);
    }
    history.replaceState({...history.state,feedSnapshot:{data:{records:allRecords,next_before:page.next_before},channels,latest}},'',location.href);
    const end = document.querySelector('#batch-end');
    end.innerHTML = page.next_before ? '<button type="button">Read the next batch</button>' : `<h2>${index ? 'A good place to pause.' : view === 'saved' ? 'Your shelf is ready.' : 'Nothing here yet.'}</h2><p class="muted">${index ? 'You’ve reached the end of this batch. Source freshness is shown above; this does not mean every source is up to date.' : view === 'saved' ? 'Save something from Today and it will be here when you return.' : 'No items match these filters. Choose All types, another topic or source, or clear your search.'}</p>`;
    if(page.next_before) end.querySelector('button').onclick=async e=>{
      e.target.disabled=true;
      try { const next=await api(`/api/reader/feed?view=${view}&${params}&before=${page.next_before}`); if(generation===pageGeneration) { append(next); announce(`${next.records.length} more items loaded.`); } }
      catch(error){ announce(error.message); e.target.disabled=false; }
    };
  }
  append(data);
}
async function reader(id, generation) {
  const view=new URLSearchParams(location.search).get('view') || 'markdown';
  const discussion = view === 'discussion';
  const latestRequested = new URLSearchParams(location.search).has('latest');
  const {item,state,revision,latest_revision_id,observation,pdf} = await api(`/api/reader/items/${id}?latest=${latestRequested}`);
  if(generation!==pageGeneration) return;
  const from=history.state?.from && !history.state.from.startsWith('/read/') ? history.state.from : '/';
  activeReader={id,state,revision,discussion:discussion || view==='analysis' || view==='pdf' || view==='diagrams',from};
  document.title=`${item.title} · Paperflow`;
  main.innerHTML=`<a class="control" id="back-feed" href="${esc(from)}">← Back to reading list</a><div class="reader-top"><p class="kicker"><span class="item-type" data-type="${esc(item.kind)}">${typeLabel(item.kind)}</span> ${esc(observation?.channel || 'Local library')}</p><h1>${esc(item.title)}</h1><p class="meta">${esc(age(observation?.observed_at))}</p><div class="flex flex-wrap gap-2"><button id="save-reader" aria-pressed="${state.saved}">${state.saved?'Saved':'Save for later'}</button><button id="finish-reader">${state.status==='finished'?'Mark unread':'Mark finished'}</button>${external(observation?.entry.url || item.canonical_url,item.kind==='paper'?'Open original paper':'Open original')}</div><nav class="reader-tabs flex flex-wrap gap-2" aria-label="Item views"><a href="/read/${id}?view=analysis" ${view==='analysis'?'aria-current="page"':''}>Analysis</a><a href="/read/${id}" ${view==='markdown'?'aria-current="page"':''}>Markdown</a><a href="/read/${id}?view=pdf" ${view==='pdf'?'aria-current="page"':''}>PDF</a><a href="/read/${id}?view=diagrams" ${view==='diagrams'?'aria-current="page"':''}>Diagrams</a><a href="/read/${id}?view=discussion" ${discussion?'aria-current="page"':''}>Discussion</a></nav><div id="availability" class="panel"></div></div><section id="reading-content" aria-label="${discussion?'Discussion samples':'Source text'}"></section>`;
  const back=document.querySelector('#back-feed');
  back.onclick=async e=>{if(history.state?.readerDepth > 0 && history.state?.from && !history.state.from.startsWith('/read/')) { e.preventDefault(); e.stopPropagation(); await savePosition(); history.go(-(history.state.readerDepth || 1)); }};
  document.querySelector('#save-reader').onclick=async e=>{
    const button=e.currentTarget; button.disabled=true;
    try { await saving; activeReader.state=await patchState(id,{saved:!activeReader.state.saved},activeReader.state); button.textContent=activeReader.state.saved?'Saved':'Save for later'; button.setAttribute('aria-pressed',activeReader.state.saved); announce(activeReader.state.saved?'Saved for later.':'Removed from Saved. Select Save for later to undo.'); }
    catch(error){announce(error.message);}finally{button.disabled=false;}
  };
  document.querySelector('#finish-reader').onclick=async e=>{
    const button=e.currentTarget; button.disabled=true;
    try { await saving; const status=activeReader.state.status==='finished'?'unread':'finished'; activeReader.state=await patchState(id,{status, ...(status==='unread'?{anchor:null}:{})},activeReader.state); button.textContent=status==='finished'?'Mark unread':'Mark finished'; announce(status==='finished'?'Marked finished.':'Marked unread.'); }
    catch(error){announce(error.message);}finally{button.disabled=false;}
  };
  const availability=document.querySelector('#availability'), content=document.querySelector('#reading-content');
  if(view==='analysis') {
    content.setAttribute('aria-label','Research analysis');
    availability.textContent='Model analysis with cited evidence. Inferences and downstream effects require independent review.';
    try{
      const result=await api(`/api/reader/items/${id}/analysis`);
      if(generation!==pageGeneration)return;
      const a=result.analysis;
      const refs=values=>values.map(c=>`<blockquote><p>${esc(c.quote)}</p><footer>Revision ${esc(c.revision_id)} · character ${c.start}</footer></blockquote>`).join('');
      const findings=values=>values.map(f=>`<article><p><strong>${esc(f.kind.replaceAll('_',' '))}</strong>: ${esc(f.statement)}</p><p>${esc(f.uncertainty)}</p><details><summary>Source evidence</summary>${refs(f.citations)}</details></article>`).join('');
      content.className='prose';
      content.innerHTML=`<p class="meta">${esc(a.category)} · Evidence: ${esc(a.evidence_basis)} · Source revision ${esc(result.revision_id)}</p><h2>Summary</h2><p>${esc(a.summary)}</p><h2>Findings</h2>${findings(a.findings)}<h2>Limits</h2><ul>${a.limitations.map(x=>`<li>${esc(x)}</li>`).join('')}</ul>${a.biases.length?`<h2>Bias assessments</h2>${findings(a.biases)}`:''}${a.effects.length?`<h2>Conditional effects</h2>${a.effects.map(e=>`<article><h3>Order ${e.order}: ${esc(e.hypothesis)}</h3><ol>${e.mechanism.map(x=>`<li>${esc(x)}</li>`).join('')}</ol><p>Assumptions: ${esc(e.assumptions.join(' '))}</p><p>Time horizon: ${esc(e.time_horizon)}</p><p>Falsifier: ${esc(e.falsifier)}</p><p>${esc(e.uncertainty)}</p><details><summary>Source evidence</summary>${refs(e.citations)}</details></article>`).join('')}`:''}<h2>Research gaps</h2><ul>${a.research_gaps.map(x=>`<li>${esc(x)}</li>`).join('')}</ul>`;
    }catch(error){if(generation===pageGeneration)content.textContent='No analysis is available for this item.';}
  } else if(view==='pdf') {
    content.setAttribute('aria-label','PDF viewer');
    if(pdf){
      availability.innerHTML=`<p>Local PDF · ${pdf.page_count} pages. PDF page position is separate from Markdown reading progress.</p><a class="control" href="/api/reader/items/${id}/pdf" target="_blank" rel="noopener">Open PDF file ↗</a>`;
      const {renderPDF}=await import('/reader-assets/viewers.js');if(generation!==pageGeneration)return;await renderPDF(`/api/reader/items/${id}/pdf`,content);
    }else{
      availability.textContent='No PDF is attached. Choose a local PDF to read it here (maximum 20 MB). Existing source Markdown is kept separately.';
      const upload=document.createElement('form');upload.innerHTML='<label>PDF file<input name="pdf" type="file" accept="application/pdf,.pdf" required></label><button type="submit">Attach and read PDF</button><p role="alert"></p>';content.append(upload);
      upload.onsubmit=async event=>{event.preventDefault();const button=upload.querySelector('button');button.disabled=true;try{const file=upload.elements.pdf.files[0];if(!file || file.size>20*1024*1024)throw new Error('Choose a PDF of at most 20 MB');await api(`/api/reader/items/${id}/pdf`,{method:'PUT',headers:{'Content-Type':'application/pdf'},body:file});if(generation===pageGeneration)await render();}catch(error){upload.querySelector('[role=alert]').textContent=error.message;}finally{button.disabled=false;}};
    }
  } else if(discussion) {
    availability.innerHTML=`<p>${observation?.entry.comments_status==='sampled'?'A sample of the conversation, not the complete thread.':'Comments are not available locally for this observation.'}</p>${external(observation?.entry.discussion_url,'Open original discussion')}`;
    for(const comment of observation?.entry.comments || []) {
      const article=document.createElement('article');article.className='prose py-5';
      const p=document.createElement('p');p.textContent=comment.text;article.append(p);
      const link=document.createElement('a'); if(safeURL(comment.url)) {link.href=comment.url;link.textContent='View original comment ↗';article.append(link);}
      content.append(article);
    }
  } else {
    availability.textContent=revision ? `${revision.evidence_basis.replace('_',' ')} available locally. ${revision.evidence_basis==='excerpt'?'This is a source excerpt, not the full article.':''}` : 'No source text is available locally. Use Open original to read at the source.';
    if(state.anchor && latest_revision_id!==revision?.id) { availability.append(' You’re resuming the version you were reading. A newer source version is available. '); const link=document.createElement('a');link.href=`/read/${id}?latest=1`;link.textContent='Read the latest version';availability.append(link); }
    if(revision){const {renderMarkdown}=await import('/reader-assets/viewers.js');if(generation!==pageGeneration)return;await renderMarkdown(revision.markdown,content,{diagramsOnly:view==='diagrams'});}
  }
}
async function topicDesk(generation) {
  const rows=await api('/api/topics?editorial=true');if(generation!==pageGeneration)return;
  main.innerHTML=`<h1>Topic editorial</h1><p class="muted">Themes are published automatically from a rolling window of new arrivals. Editorial names and suppression survive future runs. Latest is a permanent index of current topics.</p><div class="feed-toolbar"><button id="curate-now">Curate now</button><a href="/">Read Latest</a></div><form id="new-topic" class="panel"><h2>Keep a topic around</h2><label>Topic title<input name="title" required maxlength="160"></label><label>Matching terms (comma-separated)<input name="terms" required placeholder="machine learning, inference"></label><label>Editor name<input name="editor" required value="local reader"></label><button>Create persistent topic</button></form><div id="editorial-topics" class="source-stats-grid"></div>`;
  document.querySelector('#curate-now').onclick=async e=>{e.target.disabled=true;try{const run=await api('/api/topics/runs',{method:'POST'});await render();announce(`Curated ${run.input_count} incoming items; ${run.unassigned_count} remain outside published themes.`);}catch(error){announce(error.message);e.target.disabled=false;}};
  document.querySelector('#new-topic').onsubmit=async e=>{e.preventDefault();const values=Object.fromEntries(new FormData(e.target));values.terms=values.terms.split(',').map(v=>v.trim()).filter(Boolean);try{await api('/api/topics',{method:'POST',body:JSON.stringify(values)});await render();announce('Persistent topic created. Curate now to assign current items.');}catch(error){announce(error.message);}};
  for(const row of rows){const t=row.topic;const card=document.createElement('article');card.className='panel';card.innerHTML=`<h2>${esc(t.title)}</h2><p>${row.item_count} items · ${esc(row.lifecycle)} · ${esc(t.editorial_status)}</p><a href="/?topic=${t.id}">Read topic</a><details><summary>Edit topic</summary><form><label>Title<input name="title" value="${esc(t.title)}" required></label><label>Description<input name="description" value="${esc(t.description)}"></label><label>Time sensitivity<select name="sensitivity">${['urgent','timely','lasting'].map(v=>`<option ${v===t.sensitivity?'selected':''}>${v}</option>`).join('')}</select></label><label>Persistence<select name="persistence">${['dynamic','persistent'].map(v=>`<option ${v===t.persistence?'selected':''}>${v}</option>`).join('')}</select></label><label>Editorial status<select name="editorial_status">${['published','draft','suppressed'].map(v=>`<option ${v===t.editorial_status?'selected':''}>${v}</option>`).join('')}</select></label><label>Matching terms (comma-separated)<input name="terms" value="${esc(t.terms.join(', '))}"></label><label>Editor name<input name="editor" value="local reader" required></label><label>Editorial note<input name="note"></label><button>Save editorial changes</button></form></details><details><summary>Why these items?</summary><div data-assignments></div></details>`;
    card.querySelector('form').onsubmit=async e=>{e.preventDefault();const values=Object.fromEntries(new FormData(e.target));values.terms=values.terms.split(',').map(v=>v.trim()).filter(Boolean);values.expected_version=t.version;try{await api(`/api/topics/${t.id}`,{method:'PATCH',body:JSON.stringify(values)});await render();announce('Editorial changes saved.');}catch(error){announce(error.message);}};
    const details=card.querySelectorAll('details')[1];details.ontoggle=async()=>{if(!details.open)return;const target=card.querySelector('[data-assignments]');target.textContent='Loading assignments…';try{const detail=await api(`/api/topics/${t.id}`);target.innerHTML=detail.assignments.map(a=>`<p><a href="/read/${a.item_id}">Read source</a> · ${esc(a.reason)}</p>`).join('') || 'No items in the current window.';}catch(error){target.textContent=error.message;}};
    document.querySelector('#editorial-topics').append(card);
  }
}
async function about(generation) {
  const channels=await api('/api/reader/channels');if(generation!==pageGeneration)return;
  main.innerHTML=`<article class="about-page"><p class="kicker">A finite batch. An open mind.</p><h1>A good place to begin.</h1><p class="about-intro">Ideas from your sources, with room to think. Read a little. Follow what interests you.</p><h2>How Paperflow works</h2><ol><li>Configured feeds bring articles, papers and discussions into your local library.</li><li>Today offers finite batches. Latest highlights themes from the last 24 hours; the picker also lets you browse stable source feeds.</li><li>Topics publish automatically and can be reviewed in <a href="/topics">Topic editorial</a>. Their sensitivity helps rank timely reading.</li><li>Read available Markdown, PDFs and diagrams; inspect discussion samples separately and follow the original source.</li><li>Save without choosing a research bucket. Return to Saved to resume your Markdown reading.</li><li>Use Research to collect sources around a question and review evidence through the curator API and CLI.</li></ol><h2>Sources in this workspace</h2><p>These counts come from stored observations, not subscription promises. Repeated observations of an item count once per channel. An item can appear in more than one channel. Reddit is not connected unless approved access is configured.</p><div class="source-stats-grid">${channels.map(c=>`<article class="panel"><h3>${esc(c.channel)}</h3><dl><div><dt>Ingested items</dt><dd>${c.item_count}</dd></div><div><dt>Available in Today</dt><dd>${c.visible_count}</dd></div><div><dt>Unread in Today</dt><dd>${c.unread_count}</dd></div></dl><p class="meta">${esc(age(c.observed_at))}</p><a href="/?channel=${encodeURIComponent(c.channel)}">Read channel</a></article>`).join('') || '<p>No source observations have been stored yet. Configure ingestion in Sources to begin.</p>'}</div><p>Everything shown here is stored locally. Popularity signals and sampled comments provide context, not evidence that a claim is true. <a href="/sources">Inspect source health</a>.</p></article>`;
}
async function researchBrief(id,generation) {
  const response=await fetch(`/api/research/buckets/${id}/export`);
  if(!response.ok)throw new Error(`Unable to load research brief (${response.status}).`);
  const markdown=await response.text();if(generation!==pageGeneration)return;
  document.title='Research brief · Paperflow';
  main.innerHTML=`<div class="feed-toolbar"><a class="control" href="/research">← Research</a><a class="control" href="/api/research/buckets/${encodeURIComponent(id)}/export" download="research-brief.md">Download Markdown</a></div><section id="research-brief" aria-label="Research brief"></section>`;
  const {renderMarkdown}=await import('/reader-assets/viewers.js');if(generation!==pageGeneration)return;
  await renderMarkdown(markdown,document.querySelector('#research-brief'));
  const heading=main.querySelector('#research-brief h2');if(heading){const title=document.createElement('h1');title.textContent=heading.textContent;heading.replaceWith(title);}
}
async function research(generation) {
  const data=await api('/api/research/buckets?limit=50'); if(generation!==pageGeneration)return;
  main.innerHTML='<p class="kicker">Follow a question</p><h1>Room for a deeper look.</h1><p class="muted">Your research collections live here. Evidence review and editing are available through the curator CLI while the workspace takes shape.</p><div id="buckets" class="grid gap-4"></div>';
  const container=document.querySelector('#buckets');
  if(!data.records.length) container.innerHTML='<div class="panel"><h2>Start with curiosity.</h2><p>Save interesting reading first. Research collections are separate from your everyday reading shelf.</p></div>';
  for(const bucket of data.records) {const el=document.createElement('article');el.className='panel';el.innerHTML=`<h2>${esc(bucket.title)}</h2><p>${esc(bucket.research_question)}</p><a class="control" href="/research/${bucket.id}">Read research brief</a>`;container.append(el);}
}
async function sources(generation) {
  main.innerHTML=`<div class="sources-heading"><div><p class="kicker">Ingestion monitor</p><h1>Sources</h1><p class="muted">From incoming feeds to your reading space.</p></div><div class="sources-refresh"><button id="refresh-sources">Refresh source status</button><span id="sources-updated" class="meta">Checking…</span></div></div><div id="sources-alert" aria-live="polite"></div><section id="monitor-overview" aria-label="Ingestion overview"></section><section id="curation-overview" class="panel" aria-label="Research curation"></section><div class="sources-section-heading"><h2>Source feeds</h2><span class="meta">Freshness reflects collection, not publication date.</span></div><div id="source-list" class="source-monitor-grid"></div><details id="recent-runs" class="run-history"><summary>Recent ingestion runs</summary><div id="run-history"></div></details>`;
  let loading=false,operating=false;
  const sourceName=name=>({'hackernews':'Hacker News','huggingface-papers':'Hugging Face Papers','lemmy-technology':'Lemmy · Technology','tildes-computing':'Tildes · Computing','mbin':'Mbin'}[name] || name);
  const stamp=value=>value?new Date(value).toLocaleString(): 'Not yet';
  const relative=value=>{if(!value)return 'Never collected';const seconds=Math.max(0,Math.floor((Date.now()-new Date(value))/1000));return seconds<60?'Just now':seconds<3600?`${Math.floor(seconds/60)}m ago`:seconds<86400?`${Math.floor(seconds/3600)}h ago`:`${Math.floor(seconds/86400)}d ago`;};
  const duration=value=>value==null?'—':value<60?`${value.toFixed(1)}s`:`${(value/60).toFixed(1)}m`;
  const statusOf=c=>!c?'Stored only':c.paused?'Paused':c.active_run_id?(c.last_run?.status==='queued'?'Queued':'Running'):['failed','interrupted'].includes(c.last_run?.status)?'Failed':c.last_run?.status==='degraded'?'Warnings':!c.last_success_at?'Not run yet':c.stale?'Stale':'Fresh';
  async function update(manual=false){
    if(loading || (operating&&!manual) || generation!==pageGeneration)return;
    loading=true;
    try{
      const [observedResult,channelsResult,runsResult,healthResult,curationResult]=await Promise.allSettled([
        api('/api/reader/channels'),api('/api/monitor/channels'),api('/api/monitor/runs?limit=10'),
        fetch('/health/ready').then(async r=>{const value=await r.json();if(!r.ok&&!('healthy' in value))throw new Error('Monitor health unavailable');return value;}),api('/api/monitor/curation')]);
      if(generation!==pageGeneration)return;
      if(observedResult.status==='rejected')throw observedResult.reason;
      const observed=observedResult.value,channels=channelsResult.status==='fulfilled'?channelsResult.value:[],runs=runsResult.status==='fulfilled'?runsResult.value:[],health=healthResult.status==='fulfilled'?healthResult.value:null;
      const curation=curationResult.status==='fulfilled'?curationResult.value:null;
      const curator=document.querySelector('#curation-overview');
      if(!curation){curator.innerHTML='<h2>Research curation</h2><p>Status unavailable.</p>';}
      else if(!curation.enabled){curator.innerHTML='<h2>Research curation</h2><p>Model curation is disabled.</p>';}
      else{
        const counts=curation.counts||{},quality=curation.quality||{};
        const groups=Object.entries(quality.groups||{}).map(([name,g])=>{
          const bounds=g.pass_rate_interval_95;
          return `<li>${esc(name)}: ${g.passed} / ${g.n} passed (${Math.round(g.pass_rate*100)}%). Estimated interval: ${Math.round(bounds[0]*100)}–${Math.round(bounds[1]*100)}%.</li>`;
        }).join('');
        curator.innerHTML=`<h2>Research curation</h2><dl class="monitor-metrics"><div><dt>Completed / eligible</dt><dd>${counts.succeeded||0} / ${curation.eligible_jobs||0}</dd></div><div><dt>Queued / running</dt><dd>${counts.queued||0} / ${counts.running||0}</dd></div><div><dt>Failed / retries</dt><dd>${counts.failed||0} / ${curation.retries||0}</dd></div><div><dt>Open follow-ups</dt><dd>${curation.active_memory?.follow_up||0}</dd></div></dl><p>Counts refer to source revisions and harness configurations.</p><p>${curation.valid_quote_citations||0} exact citation matches. Matches establish provenance, not factual correctness.</p><p>Quality sample: ${quality.sample_evaluated||0} evaluated / ${quality.sample_selected||0} selected. Evaluation errors: ${quality.evaluation_errors||0}.</p>${groups?`<ul>${groups}</ul>`:'<p>Quality estimate unavailable: no completed evaluations.</p>'}<p class="muted">Model-estimated quality. Human-calibrated accuracy is unknown.</p>`;
        const diagnostic=curation.diagnostics;
        if(diagnostic){
          const failures=Object.entries(diagnostic.failure_groups||{}).map(([key,count])=>`<li>${esc(key)}: ${count}</li>`).join('');
          const latest=diagnostic.recent_failures?.[0],detail=latest?.failure;
          const lastSuccess=diagnostic.last_success_at?new Date(diagnostic.last_success_at*1000).toISOString():'none recorded';
          const limit=detail?`${esc(detail.phase)} · ${esc(detail.limit||'unknown limit')}: ${detail.observed??'unknown'} / ${detail.configured_limit??'unknown'} (${esc(detail.measurement)})`:'Exact limit unavailable for this historical failure.';
          curator.insertAdjacentHTML('beforeend',`<h3>Execution diagnostics</h3><p>Last 24 hours: ${diagnostic.failed_attempts} failed / ${diagnostic.completed_attempts} finished attempts. Consecutive failures: ${diagnostic.consecutive_failures_in_window}.</p><p>Daily attempts: ${diagnostic.daily_attempts_used} / ${diagnostic.daily_attempt_limit}. Queue: ${esc(diagnostic.queue_blocked_reason||'eligible work available')}. Last successful analysis: ${esc(lastSuccess)}.</p>${failures?`<ul>${failures}</ul>`:''}${latest?`<p>Latest failure: ${esc(latest.error_type)}. ${limit}</p>`:''}<p class="muted">Token caps count cumulative input and output across turns, including repeated context. Budget failures degrade curation; service readiness is reported separately.</p>`);
        }
        const spend=curation.spend;
        if(spend){
          const money=value=>value==null?'Unknown':`$${Number(value).toFixed(6)}`;
          const groups=Object.entries(spend.by_role_and_model||{}).map(([name,g])=>`<tr><th scope="row">${esc(name)}</th><td>${g.requests}</td><td>${g.input_tokens}</td><td>${g.output_tokens}</td><td>${g.cache_read_tokens}</td><td>${money(g.estimated_usd)}</td></tr>`).join('');
          curator.insertAdjacentHTML('beforeend',`<h3>Usage and spend · last ${spend.window_days} days</h3><dl class="monitor-metrics"><div><dt>Estimated spend</dt><dd>${money(spend.estimated_usd)}</dd></div><div><dt>Per completed analysis</dt><dd>${money(spend.estimated_usd_per_completed_job)}</dd></div><div><dt>Input / output tokens</dt><dd>${spend.input_tokens} / ${spend.output_tokens}</dd></div><div><dt>Retry spend</dt><dd>${money(spend.retry_estimated_usd)}</dd></div></dl><p>${spend.priced_requests} priced requests. ${spend.unpriced_requests} requests have unknown cost, including ${spend.pending_requests} pending.</p><p>Current schedule: ${curation.schedule?.items_per_cycle||1} item(s) per cycle, every ${curation.schedule?.interval_seconds||60} seconds. Daily attempt limit: ${curation.schedule?.daily_attempt_limit||100}.</p><details><summary>Usage by model role</summary><div class="overflow-x-auto"><table><thead><tr><th>Role and model</th><th>Requests</th><th>Input</th><th>Output</th><th>Cached input</th><th>Estimated USD</th></tr></thead><tbody>${groups}</tbody></table></div></details><p class="muted">Token-price estimates, not billed spend. Unknown usage is not free. Prices and request timing affect the estimate.</p>`);
        }
      }
      const mode=health?.enabled===false?'Monitor disabled':health?.schedule_enabled===false?'Manual collection':health?.healthy?'Automatic collection':'Monitor needs attention';
      const running=channels.filter(c=>c.active_run_id).length,paused=channels.filter(c=>c.paused).length;
      const attention=channels.filter(c=>['Stale','Failed','Warnings','Not run yet'].includes(statusOf(c))).length;
      const error=channelsResult.status==='rejected'?channelsResult.reason.message:health?.error || health?.scheduler_error || health?.topics?.error || (!health?'Monitor health unavailable':'');
      document.querySelector('#sources-alert').innerHTML=error?`<p class="monitor-callout">${esc(error)}. Stored reading remains available.</p>`:'';
      document.querySelector('#monitor-overview').dataset.health=health?.enabled===false?'disabled':health?.healthy?'healthy':'attention';
      document.querySelector('#monitor-overview').innerHTML=`<div class="monitor-mode"><span class="health-dot" aria-hidden="true"></span><strong>${mode}</strong><span class="meta">${health?.enabled===false?'Local library available':health?.healthy?'Worker available':'Check source details below'}</span></div><dl class="monitor-metrics"><div><dt>Source feeds</dt><dd>${new Set([...observed.map(c=>c.channel),...channels.map(c=>c.name)]).size}</dd></div><div><dt>Running / queued</dt><dd>${running}</dd></div><div><dt>Need attention</dt><dd>${attention}</dd></div><div><dt>Paused</dt><dd>${paused}</dd></div></dl><div class="monitor-footnotes"><span>Topic curator: ${health?.topics?.running?'running':health?.topics?.error?'needs attention':health?.topics?.enabled===false?'disabled':health?.enabled===false?'not running':health?.schedule_enabled===false?'on request':'scheduled'}</span><span>Telemetry: ${health?.telemetry?.otlp_configured?'OTLP configured · delivery not verified':'export not configured or unavailable'}</span></div>`;
      const focused=document.activeElement.id;
      const open=new Set([...document.querySelectorAll('#source-list details[open]')].map(e=>e.id));
      const names=[...new Set([...channels.map(c=>c.name),...observed.map(c=>c.channel)])];
      names.sort((a,b)=>{const rank=n=>({Failed:0,Warnings:1,Running:2,Queued:2,Stale:3,'Not run yet':4,Fresh:5,Paused:6,'Stored only':7}[statusOf(channels.find(c=>c.name===n))]);return rank(a)-rank(b)||a.localeCompare(b);});
      const list=document.querySelector('#source-list');list.innerHTML='';
      for(const name of names){
        const c=channels.find(c=>c.name===name),stats=observed.find(c=>c.channel===name),state=statusOf(c),run=c?.last_run;
        const card=document.createElement('article');card.className='source-monitor-card';card.dataset.status=state.toLowerCase().replaceAll(' ','-');
        const id=encodeURIComponent(name), detailsId=`source-details-${id}`;
        card.innerHTML=`<div class="source-card-heading"><h3>${esc(sourceName(name))}</h3><span class="source-status">${state}</span></div><dl class="source-numbers"><div><dt>In library</dt><dd>${stats?.item_count || 0}</dd></div><div><dt>Unread in Today</dt><dd>${stats?.unread_count || 0}</dd></div><div><dt>Saved</dt><dd>${stats?.saved_count || 0}</dd></div></dl><div class="source-freshness"><span>Collected <strong title="${esc(stamp(c?.last_success_at || stats?.observed_at))}">${relative(c?.last_success_at || stats?.observed_at)}</strong></span><span>${c?.paused?'Collection paused':!c?'No configured worker':health?.schedule_enabled===false?'Next: on request':`Next: ${new Date(c.next_due_at)>new Date()?esc(stamp(c.next_due_at)):'due now'}`}</span></div>${c?.active_run_id?`<p class="source-progress" role="status">${esc(run?.stage || 'queued')}${run?.total?` · ${run.completed} / ${run.total} in this stage`:''}</p>`:''}${run?.error?`<p class="source-error">${esc(run.error)}</p>`:''}${run?.warning_count?`<p class="source-error">${run.warning_count} warnings · usable content retained</p>`:''}<div class="source-actions"><a class="control" href="/?channel=${id}">Read feed</a>${c?`<button id="fetch-${id}" data-run ${c.paused || c.active_run_id?'disabled':''}>Fetch now</button><button id="pause-${id}" data-pause>${c.paused?'Resume ingestion':'Pause ingestion'}</button>`:''}</div><details id="${detailsId}" ${open.has(detailsId)?'open':''}><summary>Run details</summary><dl class="run-details"><div><dt>Last result</dt><dd>${esc(run?.status || 'No run recorded')}</dd></div><div><dt>Batch items</dt><dd>${run?.item_count ?? '—'}</dd></div><div><dt>Duration</dt><dd>${duration(run?.duration_seconds)}</dd></div><div><dt>Consecutive failures</dt><dd>${c?.consecutive_failures ?? '—'}</dd></div>${run?.trace_id?`<div><dt>Trace ID</dt><dd><code>${esc(run.trace_id)}</code></dd></div>`:''}<div><dt>Finished</dt><dd>${esc(stamp(run?.finished_at))}</dd></div></dl></details>`;
        async function operate(button,method,path,body){
          operating=true;button.disabled=true;
          try{await api(path,{method,...(body?{body:JSON.stringify(body)}:{})});await update(true);announce(method==='POST'?'Fetch queued. Progress updates automatically.':c.paused?'Ingestion resumed.':'Ingestion paused.');}
          catch(error){announce(error.message);button.disabled=false;}
          finally{operating=false;}
        }
        if(c){card.querySelector('[data-pause]').onclick=e=>operate(e.currentTarget,'PATCH',`/api/monitor/channels/${id}`,{paused:!c.paused});card.querySelector('[data-run]').onclick=e=>operate(e.currentTarget,'POST',`/api/monitor/channels/${id}/runs`);}
        list.append(card);
      }
      if(!names.length)list.innerHTML='<p class="panel">No source feeds yet. Configure ingestion to begin.</p>';
      if(focused && document.getElementById(focused))document.getElementById(focused).focus({preventScroll:true});
      document.querySelector('#run-history').innerHTML=runs.length?`<ol class="run-list">${runs.map(r=>`<li><div><strong>${esc(sourceName(r.channel))}</strong><span class="source-status">${esc(r.status)}</span></div><p>${r.item_count} items · ${duration(r.duration_seconds)} · ${esc(stamp(r.finished_at || r.started_at || r.queued_at))}${r.warning_count?` · ${r.warning_count} warnings`:''}</p></li>`).join('')}</ol>`:`<p class="muted">${runsResult.status==='rejected'?'Run history unavailable.':'No runs recorded yet.'}</p>`;
      document.querySelector('#sources-updated').textContent=`Updated ${new Date().toLocaleTimeString()} · refreshes every 5s`;
    }catch(error){if(generation===pageGeneration){document.querySelector('#sources-alert').textContent=`Could not refresh: ${error.message}. Showing the last available status.`;}}
    finally{loading=false;}
  }
  document.querySelector('#refresh-sources').onclick=()=>update(true);
  await update();
  function schedule(){sourcesTimer=setTimeout(async()=>{if(generation!==pageGeneration)return;if(!document.hidden)await update();schedule();},5000);}
  if(generation===pageGeneration)schedule();
}

async function render(restoring=false) {
  routedLocation=location.pathname+location.search;
  clearTimeout(scrollTimer); clearTimeout(sourcesTimer); activeReader=null; const generation=++pageGeneration;
  main.setAttribute('aria-busy','true'); main.innerHTML='<p role="status">Opening your reading space…</p>';
  const path=location.pathname; const view=path==='/saved'?'saved':path.startsWith('/research')?'research':'today';
  document.querySelectorAll('[data-nav]').forEach(a=>{if(a.dataset.nav===view && !['/sources','/about','/topics'].includes(path))a.setAttribute('aria-current','page');else a.removeAttribute('aria-current');});
  document.title=`${path==='/sources'?'Sources':path==='/about'?'About':path==='/topics'?'Topic editorial':view[0].toUpperCase()+view.slice(1)} · Paperflow`;
  try {
    if(path.startsWith('/read/')) await reader(path.split('/')[2],generation);
    else if(path==='/research') await research(generation);
    else if(path.startsWith('/research/')) await researchBrief(path.split('/')[2],generation);
    else if(path==='/sources') await sources(generation);
    else if(path==='/about') await about(generation);
    else if(path==='/topics') await topicDesk(generation);
    else await feed(view,generation,restoring);
    if(generation!==pageGeneration)return;
    if (!settings.open && !(generation===1 && document.activeElement!==document.body)) main.focus({preventScroll:true});
    if(restoring) { window.scrollTo(0,history.state?.scroll || 0); const element=document.getElementById(history.state?.focus); element?.focus({preventScroll:true}); }
    else if(activeReader?.state.anchor && activeReader.state.anchor.revision_id===activeReader.revision?.id && !activeReader.discussion) { const offset=activeReader.state.anchor.character_offset; const paragraphs=[...document.querySelectorAll('[data-offset]')]; const target=paragraphs.filter(p=>Number(p.dataset.offset)<=offset).at(-1);target?.scrollIntoView(); }
    else window.scrollTo(0,0);
  }catch(error){ if(generation!==pageGeneration)return;main.innerHTML=`<h1>Let’s try that again.</h1><p role="alert">${esc(error.message)}</p><button id="retry">Retry</button>`;document.querySelector('#retry').onclick=()=>render();main.focus(); }
  finally {if(generation===pageGeneration)main.setAttribute('aria-busy','false');}
}
document.querySelector('#global-search').onsubmit=event=>{event.preventDefault();const params=new URLSearchParams(location.search);params.delete('before');params.delete('view');params.set('query',event.target.elements.query.value);navigate(`${location.pathname==='/saved'?'/saved':'/'}?${params}`);};
document.querySelector('#settings-open').onclick=async()=>{
  try {preferences=await api('/api/reader/preferences'); for(const [key,value] of Object.entries(preferences))if(form.elements[key])form.elements[key].value=value;document.querySelector('#settings-error').textContent='';settings.showModal();}
  catch(error){announce(error.message);}
};
document.querySelector('#settings-close').onclick=()=>settings.close();
settings.addEventListener('close',()=>document.querySelector('#settings-open').focus());
form.onsubmit=async event=>{
  event.preventDefault(); const button=form.querySelector('[type=submit]');button.disabled=true;
  const values=Object.fromEntries(new FormData(form));for(const key of ['font_size','line_height','measure'])values[key]=Number(values[key]);
  try{preferences=await api('/api/reader/preferences',{method:'PUT',body:JSON.stringify({expected_version:preferences.version,preferences:values})});applyPreferences(preferences);settings.close();announce('Reading settings saved.');}
  catch(error){document.querySelector('#settings-error').textContent=error.message;}
  finally{button.disabled=false;}
};
try {preferences=await api('/api/reader/preferences');applyPreferences(preferences);}catch(error){announce(`Reading settings could not be loaded. ${error.message}`);}
render();
