import MarkdownIt from 'markdown-it';
import DOMPurify from 'dompurify';
import mermaid from 'mermaid';
import * as pdfjs from 'pdfjs-dist';

const md=new MarkdownIt({html:false,linkify:false,typographer:false});
md.renderer.rules.image=(tokens,index)=>`<span class="muted">[Image: ${md.utils.escapeHtml(tokens[index].content)}]</span>`;
mermaid.initialize({theme:'base',themeVariables:{primaryColor:'#eef1e9',primaryTextColor:'#18261d',primaryBorderColor:'#064c2a',lineColor:'#064c2a',fontFamily:'system-ui,sans-serif'},startOnLoad:false,securityLevel:'strict',suppressErrorRendering:true,maxTextSize:30000,maxEdges:300,htmlLabels:false,flowchart:{htmlLabels:false}});
pdfjs.GlobalWorkerOptions.workerSrc='/reader-assets/pdf.worker.min.mjs';
let diagramNumber=0;

export async function renderMarkdown(markdown,container,{diagramsOnly=false}={}) {
  const root=document.createElement('div');root.className='prose';root.id='source-text';container.append(root);
  const lines=markdown.split('\n'), offsets=[];let offset=0;
  for(const line of lines){offsets.push(offset);offset+=Array.from(line).length+1;}
  const tokens=md.parse(markdown,{});let diagrams=0;
  for(let i=0;i<tokens.length;) {
    const first=tokens[i];let end=i+1;
    if(first.nesting===1){let depth=1;while(end<tokens.length && depth){depth+=tokens[end].nesting;end++;}}
    const group=tokens.slice(i,end);i=end;
    const isDiagram=first.type==='fence' && first.info.trim()==='mermaid';
    if(diagramsOnly && !isDiagram)continue;
    const section=document.createElement('div');section.dataset.offset=offsets[first.map?.[0] || 0];root.append(section);
    if(isDiagram){
      diagrams++;const title=document.createElement('h2');title.textContent=`Diagram ${diagrams}`;section.append(title);
      const diagram=document.createElement('div');diagram.className='diagram-surface';diagram.setAttribute('role','img');diagram.setAttribute('aria-label',`Diagram ${diagrams}; definition follows`);section.append(diagram);
      const details=document.createElement('details'),summary=document.createElement('summary'),code=document.createElement('pre');summary.textContent='Diagram definition';code.textContent=first.content;details.append(summary,code);section.append(details);
      try{
        // Configuration belongs to the application, never to ingested diagrams.
        if(first.content.length>30000 || /%%\s*\{|^\s*---/m.test(first.content))throw new Error('Diagram directives are unsupported');
        const {svg}=await mermaid.render(`paperflow-diagram-${++diagramNumber}`,first.content);
        const clean=DOMPurify.sanitize(svg,{USE_PROFILES:{svg:true,svgFilters:true},FORBID_TAGS:['foreignObject','image','a']});
        // SVG-as-image preserves Mermaid's styling without allowing its CSS or
        // script to participate in the reader document. External SVG resources
        // are disabled by browsers in image mode.
        const image=document.createElement('img');image.alt=`Diagram ${diagrams}; definition follows`;
        image.src='data:image/svg+xml;charset=utf-8,'+encodeURIComponent(clean);
        diagram.removeAttribute('role');diagram.removeAttribute('aria-label');diagram.append(image);
      }catch{diagram.removeAttribute('role');diagram.textContent='This diagram could not be rendered. Its definition is available below.';details.open=true;}
    }else{
      for(const token of group)if(token.type==='heading_open' || token.type==='heading_close')token.tag=`h${Math.min(6,Number(token.tag.slice(1))+1)}`;
      section.innerHTML=DOMPurify.sanitize(md.renderer.render(group,md.options,{}),{USE_PROFILES:{html:true}});
      section.querySelectorAll('a').forEach(link=>link.rel='noreferrer');
    }
  }
  if(diagramsOnly && !diagrams){root.textContent='No Mermaid diagrams are present in this revision. Diagrams use fenced mermaid blocks in the source Markdown.';}
}

export async function renderPDF(url,container) {
  const toolbar=document.createElement('div');toolbar.className='flex flex-wrap gap-3 items-center py-5';
  toolbar.innerHTML='<button data-prev>Previous page</button><label>PDF page<input data-page type="number" min="1" value="1"></label><span data-count></span><button data-next>Next page</button><label>PDF zoom<select data-zoom><option value="fit">Fit width</option><option value="1">100%</option><option value="1.5">150%</option><option value="2">200%</option></select></label>';
  const status=document.createElement('p');status.setAttribute('role','status');status.textContent='Loading PDF…';
  const surface=document.createElement('div');surface.className='pdf-surface';surface.tabIndex=0;surface.setAttribute('role','region');surface.setAttribute('aria-label','PDF page; scroll to explore at larger zoom');
  const canvas=document.createElement('canvas');canvas.setAttribute('aria-hidden','true');surface.append(canvas);
  const details=document.createElement('details');details.open=true;const summary=document.createElement('summary');summary.textContent='Readable page text';const text=document.createElement('div');text.className='prose pdf-text';details.append(summary,text);
  container.append(toolbar,status,surface,details);
  const controls=[...toolbar.querySelectorAll('button,input,select')];controls.forEach(c=>c.disabled=true);
  try{
    const task=pdfjs.getDocument({url,isEvalSupported:false,disableRange:true,cMapUrl:'/reader-assets/pdf-assets/cmaps/',cMapPacked:true,standardFontDataUrl:'/reader-assets/pdf-assets/standard_fonts/',wasmUrl:'/reader-assets/pdf-assets/wasm/'});
    const pdf=await task.promise;let number=1;
    const field=toolbar.querySelector('[data-page]'),zoom=toolbar.querySelector('[data-zoom]');field.max=pdf.numPages;toolbar.querySelector('[data-count]').textContent=`of ${pdf.numPages}`;
    async function draw(){
      controls.forEach(c=>c.disabled=true);status.textContent=`Loading page ${number}…`;
      try{
        const page=await pdf.getPage(number),base=page.getViewport({scale:1});
        const scale=zoom.value==='fit'? Math.max(.1,(container.clientWidth-4)/base.width):Number(zoom.value);
        const viewport=page.getViewport({scale});const ratio=Math.min(devicePixelRatio || 1,2);
        canvas.width=Math.ceil(viewport.width*ratio);canvas.height=Math.ceil(viewport.height*ratio);canvas.style.width=`${viewport.width}px`;canvas.style.height=`${viewport.height}px`;
        await page.render({canvasContext:canvas.getContext('2d'),viewport,transform:[ratio,0,0,ratio,0,0]}).promise;
        const content=await page.getTextContent();text.textContent=content.items.map(part=>part.str+(part.hasEOL?'\n':' ')).join('') || 'No extractable text is available on this page. It may be a scanned image.';
        status.textContent=`Page ${number} of ${pdf.numPages}`;field.value=number;
      }catch{status.textContent='Unable to render this page. Try another page or open the PDF file.';}
      finally{controls.forEach(c=>c.disabled=false);toolbar.querySelector('[data-prev]').disabled=number===1;toolbar.querySelector('[data-next]').disabled=number===pdf.numPages;}
    }
    toolbar.querySelector('[data-prev]').onclick=()=>{number--;draw();};toolbar.querySelector('[data-next]').onclick=()=>{number++;draw();};
    field.onchange=()=>{number=Math.max(1,Math.min(pdf.numPages,Math.trunc(Number(field.value)) || 1));draw();};zoom.onchange=draw;
    await draw();
    // Release the worker when navigating away from this embedded view.
    if(!container.isConnected){await task.destroy();return;}
    const observer=new MutationObserver(()=>{if(!container.isConnected){observer.disconnect();task.destroy();}});observer.observe(document.querySelector('main'),{childList:true,subtree:true});
  }catch{status.textContent='Unable to load this PDF. Open the PDF file to use your browser’s viewer.';}
}
