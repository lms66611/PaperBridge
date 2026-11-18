/* SPDX-License-Identifier: AGPL-3.0-or-later */
/* Paragraph correspondence is embedded in the PDF; temporary overlays are not annotations. */
var PaperBridgeReaderSync = {
  states: new Map(), active: false,
  unwrap(value) { return value?.wrappedJSObject || value; },
  views(reader) {
    const r = this.unwrap(reader._internalReader);
    return [r?._primaryView, r?._secondaryView].filter(Boolean).map(v => this.unwrap(v));
  },
  app(view) { return this.unwrap(view._iframeWindow)?.PDFViewerApplication; },
  selection(reader) {
    for (const v of this.views(reader)) {
      const p=v.getSelectionPosition?.();
      if (p?.rects?.length) return p;
    }
    return null;
  },
  state(reader) {
    if (this.states.has(reader)) return this.states.get(reader);
    const s = {reader, map: null, overlays: [], listeners: [], selected: null, signature: '', sequence: 0, citationViews: new Set()};
    this.states.set(reader, s);
    s.ready = (async () => {
      await reader._initPromise;
      for (let i = 0; i < 40 && this.active; i++) {
        const app = this.app(this.views(reader)[0]);
        if (app?.pdfDocument) {
          s.fingerprint = app.pdfDocument.fingerprints?.[0]?.toLowerCase();
          const attachments = await app.pdfDocument.getAttachments();
          const entry = Object.values(attachments || {}).find(a => a.filename === 'paperbridge-alignment.json');
          if (entry && entry.content.length < 15000000) {
            const m = JSON.parse(new TextDecoder().decode(entry.content));
            if ([1,2].includes(m.version) && Array.isArray(m.pairs) && m.pairs.length < 100000 &&
                m.pairs.every(p => ['source','target','original'].every(k => Array.isArray(p[k]) && p[k].every(a =>
                  Number.isInteger(a.page) && a.page >= 0 && Array.isArray(a.rects) &&
                  a.rects.every(r => r.length === 4 && r.every(Number.isFinite)))))) s.map = m;
          }
          for (const view of this.views(reader)) {
            const bus = this.app(view)?.eventBus;
            if (!bus) continue;
            for (const type of ['pagerendered','scalechanging','rotationchanging']) {
              const fn = () => this.draw(s);
              bus.on(type, fn); s.listeners.push(() => bus.off(type, fn));
            }
          }
          this.attachCitationHover(s);
          return s;
        }
        await new Promise(resolve => setTimeout(resolve, 150));
      }
      return s;
    })().catch(e => { Zotero.debug('PaperBridge alignment: '+e); return s; });
    return s;
  },
  intersects(a,b) {
    return Math.min(a[2],b[2])-Math.max(a[0],b[0]) > .5 &&
      Math.min(a[3],b[3])-Math.max(a[1],b[1]) > .5;
  },
  citationAt(map,page,point) {
    return (map?.citations || []).find(c => c.page===page && Array.isArray(c.rect) && c.rect.length===4 &&
      c.rect.every(Number.isFinite) && typeof c.reference==='string' && c.reference.length<=12000 &&
      point[0]>=c.rect[0]-1 && point[0]<=c.rect[2]+1 && point[1]>=c.rect[1]-1 && point[1]<=c.rect[3]+1);
  },
  clearCitation(s) { if (s.citationTooltip) s.citationTooltip.remove(); s.citationTooltip=null;s.hoverCitation=null; },
  showCitation(s,view,event) {
    const target=this.unwrap(event.target);
    const pageElement=target?.closest?.('.page');
    if (!pageElement || event.buttons) {this.clearCitation(s);return;}
    const pageIndex=Number(pageElement.dataset.pageNumber)-1;
    const page=this.app(view)?.pdfViewer?.getPageView(pageIndex);
    if (!page?.viewport) return;
    // The page div includes a border; the canvas is the actual PDF viewport.
    const canvas=page.canvas || pageElement.querySelector('canvas');
    const box=(canvas || pageElement).getBoundingClientRect();
    if (!box.width || !box.height) return;
    const coordinates=page.viewport.convertToPdfPoint(
      (event.clientX-box.left)*page.viewport.width/box.width,
      (event.clientY-box.top)*page.viewport.height/box.height);
    const citation=this.citationAt(s.map,pageIndex,coordinates);
    if (!citation) {this.clearCitation(s);return;}
    if (s.hoverCitation===citation && s.citationTooltip) return;
    this.clearCitation(s);
    const doc=pageElement.ownerDocument,win=doc.defaultView;
    const tooltip=doc.createElement('div');
    tooltip.className='paperbridge-citation-tooltip';tooltip.setAttribute('role','tooltip');
    tooltip.textContent=citation.reference;
    Object.assign(tooltip.style,{position:'fixed',zIndex:'2147483647',pointerEvents:'none',
      maxWidth:'min(520px,80vw)',maxHeight:'55vh',overflow:'hidden',padding:'12px 14px',
      border:'1px solid #b8c6d2',borderRadius:'6px',background:'#fff',color:'#1b2c38',
      boxShadow:'0 4px 18px #0003',font:'13px/1.5 system-ui,sans-serif',whiteSpace:'pre-wrap',
      overflowWrap:'anywhere',left:'0px',top:'0px'});
    doc.body.appendChild(tooltip);
    const rect=tooltip.getBoundingClientRect();
    tooltip.style.left=Math.max(8,Math.min(event.clientX+12,win.innerWidth-rect.width-8))+'px';
    tooltip.style.top=Math.max(8,Math.min(event.clientY+18,win.innerHeight-rect.height-8))+'px';
    s.citationTooltip=tooltip;s.hoverCitation=citation;
  },
  attachCitationHover(s) {
    if (!Array.isArray(s.map?.citations)) return;
    for (const view of this.views(s.reader)) {
      // Use the page view's current document. Zotero can replace its iframe
      // document while initialization is still resolving.
      const doc=this.app(view)?.pdfViewer?.getPageView(0)?.div?.ownerDocument;
      if (!doc || s.citationViews.has(doc)) continue;
      s.citationViews.add(doc);
      const over=e=>{try{this.showCitation(s,view,e);}catch(err){Zotero.debug('PaperBridge citation: '+err);}};
      const out=e=>{if(!e.relatedTarget?.closest?.('.page'))this.clearCitation(s);};
      const clear=()=>this.clearCitation(s);
      doc.addEventListener('pointermove',over,true,true);doc.addEventListener('pointerout',out,true,true);
      doc.addEventListener('scroll',clear,true);doc.addEventListener('pointerdown',clear,true);doc.addEventListener('click',clear,true);
      s.listeners.push(()=>{doc.removeEventListener('pointermove',over,true);doc.removeEventListener('pointerout',out,true);
        doc.removeEventListener('scroll',clear,true);doc.removeEventListener('pointerdown',clear,true);doc.removeEventListener('click',clear,true);});
    }
  },
  match(map, position, original = false) {
    const selections = [{page: position.pageIndex, rects: position.rects || []}];
    if (position.nextPageRects?.length) selections.push({page: position.pageIndex+1, rects: position.nextPageRects});
    const found = [];
    for (const parent of map.pairs) {
      for (const side of original ? ['original'] : ['source','target']) {
        if ((parent[side] || []).some(a => selections.some(sel => a.page === sel.page &&
            a.rects.some(r => sel.rects.some(t => this.intersects(r,t)))))) {
          found.push({pair:parent, side: side === 'original' ? 'source' : side}); break;
        }
      }
    }
    return found;
  },
  clear(s) { for (const el of s.overlays) el.remove(); s.overlays = []; },
  draw(s) {
    this.clear(s);
    if (!this.active || !s.selected) return;
    for (const view of this.views(s.reader)) {
      const viewer = this.app(view)?.pdfViewer;
      if (!viewer) continue;
      for (const anchor of s.selected) {
        const page = viewer.getPageView(anchor.page);
        if (!page?.div || !page.viewport) continue;
        for (const rect of anchor.rects) {
          const coordinates=Components.utils.cloneInto(Array.from(rect),Components.utils.getGlobalForObject(page.viewport));
          const r = page.viewport.convertToViewportRectangle(coordinates);
          const el = page.div.ownerDocument.createElement('div');
          el.className = 'paperbridge-correspondence';
          el.setAttribute('aria-hidden','true');
          Object.assign(el.style, {position:'absolute',pointerEvents:'none',zIndex:'9',
            background:'rgba(0,173,181,.22)', outline:'1px dashed rgba(0,120,130,.7)',
            left:Math.min(r[0],r[2])+'px',top:Math.min(r[1],r[3])+'px',
            width:Math.abs(r[2]-r[0])+'px',height:Math.abs(r[3]-r[1])+'px'});
          page.div.appendChild(el); s.overlays.push(el);
        }
      }
    }
  },
  async related(reader) {
    const item = Zotero.Items.get(reader.itemID);
    if (!item?.parentID) return null;
    const parent = Zotero.Items.get(item.parentID);
    const attachments = (await Zotero.Items.getAsync(parent.getAttachments())).filter(a =>
      a.id !== item.id && !a.deleted && a.isPDFAttachment() &&
      (a.getField('title') === '双语翻译版' || a.getTags().some(t => t.tag.startsWith('paperbridge:'))));
    attachments.sort((a,b) => String(b.dateModified || '').localeCompare(String(a.dateModified || '')));
    // Prefer a reader already open. A fingerprint check below prevents false correspondence.
    return attachments[0] ? Zotero.Reader.open(attachments[0].id) : null;
  },
  async lookup(reader,position) {
    const origin = await this.state(reader); await origin.ready;
    let dest = origin, original = false;
    if (!origin.map) {
      const other = await this.related(reader);
      if (other) { dest = await this.state(other); await dest.ready; original = true; }
      if (!dest.map || dest.map.source_fingerprint !== origin.fingerprint) {
        return {origin,dest,found:[],error:'此 PDF 尚无可验证的段落对应。请在 PaperBridge 中关联原任务并重新导出。'};
      }
    }
    const found = this.match(dest.map, position, original);
    return {origin,dest,found,original,error:found.length ? '' : '此处没有段落对应，可能属于图内文字或未翻译区域。'};
  },
  async select(event) {
    const {reader,params,doc,append} = event;
    const position = params.annotation?.position;
    if (!position?.rects?.length) return;
    // Zotero collects popup nodes synchronously. Append before the first await.
    const origin=this.state(reader), sequence=++origin.sequence;
    origin.signature=JSON.stringify(position);
    if (origin.target) {origin.target.selected=null;this.clear(origin.target);}
    const notice = doc.createElement('div');
    notice.style.cssText = 'max-width:440px;max-height:300px;overflow:auto;padding:6px;white-space:pre-wrap;font-size:12px';
    notice.textContent = 'PaperBridge · 正在定位对应段落…'; append(notice);
    const snapshot=typeof PaperBridgeReaderNotes!=='undefined' ? PaperBridgeReaderNotes.capture(reader,params.annotation) : null;
    if (snapshot) PaperBridgeReaderNotes.appendActions(event,snapshot);
    const result=await this.lookup(reader,position);
    if (!this.active || origin.sequence!==sequence || !this.states.has(reader)) return;
    const {dest,found,original,error}=result;
    if (error) {notice.textContent=error;return;}
    if (origin.target && origin.target !== dest) { origin.target.selected = null; this.clear(origin.target); }
    origin.target = dest;
    dest.selected = found.flatMap(x => x.pair[x.side === 'source' ? 'target' : 'source']);
    notice.textContent = '段落对应 · '+found.map(x =>
      x.pair[x.side === 'source' ? 'translation' : 'text']).join('\n\n');
    if (dest.selected.length && !original && !dest.selected.some(a=>a.page===position.pageIndex)) {
      const button=doc.createElement('button');button.textContent='跳到对应译文 / 原文';
      button.addEventListener('click',()=>{const a=dest.selected[0];dest.reader.navigate({position:{pageIndex:a.page,rects:a.rects}});});
      notice.appendChild(button);
    }
    if (original && dest.selected[0]) {
      const a = dest.selected[0]; dest.reader.navigate({position:{pageIndex:a.page,rects:a.rects}});
    }
    this.draw(dest);
  },
  start(id) {
    this.active = true;
    this.handler = e => this.select(e).catch(err => Zotero.debug('PaperBridge alignment: '+err+'\n'+err.stack));
    Zotero.Reader.registerEventListener('renderTextSelectionPopup',this.handler,id);
    this.timer = setInterval(async () => {
      if (this.polling) return;this.polling=true;
      try {
      for (const reader of Zotero.Reader._readers || []) {
        if (!this.states.has(reader) && reader._type === 'pdf') await this.state(reader);
      }
      for (const [reader,s] of this.states) {
        if (!(Zotero.Reader._readers || []).includes(reader)) {
          this.clear(s); this.clearCitation(s); s.listeners.forEach(f => f()); this.states.delete(reader); continue;
        }
        this.attachCitationHover(s);
        const pos = this.selection(reader);
        if (pos && JSON.stringify(pos)!==s.signature) {
          s.signature=JSON.stringify(pos);
          await this.select({reader,params:{annotation:{position:pos}},
            doc:reader._iframeWindow.document,append(){}});
        }
        if (!s.signature) continue;
        if (!pos) {
          s.signature = ''; ++s.sequence; if (s.target) {s.target.selected=null;this.clear(s.target);}
        }
      }
      } catch(e) {Zotero.debug('PaperBridge selection watcher: '+e);}
      finally {this.polling=false;}
    },350);
  },
  stop() {
    this.active=false;clearInterval(this.timer);
    if (this.handler) Zotero.Reader.unregisterEventListener('renderTextSelectionPopup',this.handler);
    for (const s of this.states.values()) {this.clear(s);this.clearCitation(s);s.listeners.forEach(f=>f());}
    this.states.clear();
  }
};
