/* SPDX-License-Identifier: AGPL-3.0-or-later */
/* All durable notes use Zotero's native annotation API, never its database. */
var PaperBridgeReaderNotes = {
  dialogs: new Map(), handlers: [],
  clone(value) { return JSON.parse(JSON.stringify(value)); },
  capture(reader, annotation) {
    try {
      if (!annotation?.text || !annotation?.position?.rects?.length) {
        annotation=null;
        for (const view of PaperBridgeReaderSync.views(reader)) {
          if (!view.getSelectionPosition?.()?.rects?.length) continue;
          if (view._getAnnotationFromSelectionRanges && view._selectionRanges?.length) {
            annotation=view._getAnnotationFromSelectionRanges(view._selectionRanges,'highlight','#ffd400');
          }
          if (annotation?.text) break;
        }
      }
      if (!annotation?.text?.trim() || !annotation.position?.rects?.length) return null;
      return {reader, annotation:this.clone(annotation), attachmentID:reader.itemID};
    } catch(e) {Zotero.debug('PaperBridge selection snapshot: '+e);return null;}
  },
  async counterpart(snapshot) {
    const result=await PaperBridgeReaderSync.lookup(snapshot.reader,snapshot.annotation.position);
    const sourceSide=result.found[0]?.side!=='target';
    return {text:result.found.map(x=>x.pair[sourceSide ? 'translation' : 'text']).join('\n\n'),
      label:sourceSide ? '对应译文（完整段落）' : '对应原文（完整段落）',
      pending:!result.found.length};
  },
  async copy(snapshot) {
    const other=await this.counterpart(snapshot);
    const text=snapshot.annotation.text+'\n\n'+other.label+'\n'+(other.text||'对应待核对')+
      (other.pending && other.text ? '\n【部分对应待核对】' : '');
    Components.classes['@mozilla.org/widget/clipboardhelper;1']
      .getService(Components.interfaces.nsIClipboardHelper).copyString(text);
  },
  positionKey(position) {
    return JSON.stringify({pageIndex:position.pageIndex,
      rects:position.rects.map(r=>r.map(n=>Math.round(n*10)/10)),
      nextPageRects:position.nextPageRects?.map(r=>r.map(n=>Math.round(n*10)/10))||[]});
  },
  async existing(attachment,snapshot) {
    await attachment.loadAllData();
    const key=this.positionKey(snapshot.annotation.position);
    return attachment.getAnnotations().find(a=>{
      try {return !a.annotationIsExternal && a.annotationType==='highlight' &&
        this.positionKey(JSON.parse(a.annotationPosition))===key;}
      catch(e) {return false;}
    });
  },
  async save(snapshot,comment,tags) {
    const attachment=await Zotero.Items.getAsync(snapshot.attachmentID);
    if (!attachment || attachment.deleted) throw new Error('原 PDF 附件已不存在；输入内容已保留。');
    const library=Zotero.Libraries.get(attachment.libraryID);
    if (!library?.editable) throw new Error('此文献库为只读，无法保存批注。');
    const previous=await this.existing(attachment,snapshot);
    const json=previous ? await Zotero.Annotations.toJSON(previous) : this.clone(snapshot.annotation);
    json.key=previous?.key || Zotero.DataObjectUtilities.generateKey();
    json.type='highlight';json.color=json.color||'#ffd400';json.text=snapshot.annotation.text;
    json.position=this.clone(snapshot.annotation.position);json.comment=comment;
    json.pageLabel=json.pageLabel||String(json.position.pageIndex+1);
    json.sortIndex=json.sortIndex || String(json.position.pageIndex).padStart(5,'0')+'|000000|00000';
    json.tags=[...new Set(tags.map(t=>t.trim()).filter(Boolean))].map(name=>({name}));
    return Zotero.Annotations.saveFromJSON(attachment,json);
  },
  async edit(snapshot,tagsOnly=false) {
    const dialogKey=snapshot.attachmentID+':'+this.positionKey(snapshot.annotation.position);
    const old=this.dialogs.get(dialogKey);
    if (old && !old.closed) {old.focus();return;}
    const attachment=await Zotero.Items.getAsync(snapshot.attachmentID);
    if (!attachment || !Zotero.Libraries.get(attachment.libraryID)?.editable) {
      Services.prompt.alert(Zotero.getMainWindow(),'PaperBridge','此 PDF 不可写入批注。');return;
    }
    const [other,previous]=await Promise.all([this.counterpart(snapshot),this.existing(attachment,snapshot)]);
    // Check again after asynchronous lookup: two rapid clicks must share a dialog.
    const open=this.dialogs.get(dialogKey);
    if (open && !open.closed) {open.focus();return;}
    const owner=snapshot.reader._window||Zotero.getMainWindow();
    const win=owner.openDialog('about:blank','', 'chrome,dialog,resizable,centerscreen,width=660,height=720');
    this.dialogs.set(dialogKey,win);
    const render=async()=>{
      const doc=win.document;
      const el=(name,text)=>{const n=doc.createElementNS('http://www.w3.org/1999/xhtml',name);if(text!==undefined)n.textContent=text;return n;};
      doc.title=tagsOnly ? 'PaperBridge · 添加标签' : 'PaperBridge · 灵感批注';
      const body=doc.body||doc.documentElement.appendChild(el('body'));
      body.replaceChildren();body.style.cssText='padding:20px;font:14px/1.5 system-ui;background:#f5f8fa;color:#173344';
      body.append(el('h2',doc.title));
      body.append(el('div','所选摘录（高亮保留实际选区）'));
      const quote=el('pre',snapshot.annotation.text);quote.style.cssText='white-space:pre-wrap;max-height:130px;overflow:auto;background:white;padding:10px';body.append(quote);
      const label=el('label','批注：对应译文与灵感');body.append(label);
      const comment=el('textarea');comment.rows=10;comment.style.cssText='box-sizing:border-box;width:100%;font:inherit;resize:vertical';
      comment.value=previous?.annotationComment || other.label+'\n'+(other.text||'对应待核对')+
        (other.pending && other.text ? '\n【部分对应待核对】' : '')+'\n\n灵感：\n';
      label.append(comment);
      const tagLabel=el('label','标签（逗号或换行分隔）');body.append(tagLabel);
      const tags=el('textarea');tags.rows=2;tags.style.cssText='box-sizing:border-box;width:100%;font:inherit';
      tags.value=previous?.getTags().map(t=>t.tag).join(', ')||'';tagLabel.append(tags);
      const choose=el('input');choose.placeholder='选择已有标签或输入新标签';choose.setAttribute('list','paperbridge-tags');
      choose.style.cssText='width:70%;padding:5px';
      const list=el('datalist');list.id='paperbridge-tags';body.append(choose,list);
      const add=el('button','添加到标签');body.append(add);
      const addTag=()=>{if(choose.value.trim()){tags.value+=(tags.value.trim() ? ', ' : '')+choose.value.trim();choose.value='';}};
      add.addEventListener('click',addTag);choose.addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();addTag();}});
      try {for(const t of (await Zotero.Tags.getAll(attachment.libraryID)).slice(0,1500)){const o=el('option');o.value=t.tag;list.append(o);}}catch(e){}
      const error=el('div');error.setAttribute('role','alert');error.style.cssText='color:#ad2929;white-space:pre-wrap;margin:10px 0';body.append(error);
      const cancel=el('button','取消'),save=el('button','保存到 Zotero');body.append(cancel,save);
      save.style.cssText='margin-left:12px;padding:8px 15px';cancel.addEventListener('click',()=>win.close());
      save.addEventListener('click',async()=>{
        save.disabled=true;error.textContent='';
        try {
          addTag();
          await this.save(snapshot,comment.value,tags.value.split(/[,，;；\n]/));
          win.close();
        } catch(e) {error.textContent='保存失败：'+String(e.message||e)+'\n内容已保留，请修改后重试。';save.disabled=false;}
      });
      (tagsOnly ? choose : comment).focus();
    };
    if(win.document.readyState==='complete')render();else win.addEventListener('load',render,{once:true});
    win.addEventListener('unload',()=>this.dialogs.delete(dialogKey),{once:true});
  },
  appendActions(event,snapshot) {
    const row=event.doc.createElement('div');row.style.cssText='display:flex;gap:6px;padding:4px;flex-wrap:wrap';
    for(const [label,action] of [['灵感批注',()=>this.edit(snapshot)],['添加标签',()=>this.edit(snapshot,true)],['复制中英对照',()=>this.copy(snapshot)]]) {
      const button=event.doc.createElement('button');button.textContent=label;
      button.addEventListener('click',()=>Promise.resolve(action()).catch(e=>Services.prompt.alert(Zotero.getMainWindow(),'PaperBridge',String(e))));row.append(button);
    }
    event.append(row);
  },
  start(id) {
    const handler=event=>{
      let snapshot=this.capture(event.reader);
      if(event.params?.ids?.length===1) {
        const attachment=Zotero.Items.get(event.reader.itemID);
        const item=Zotero.Items.getByLibraryAndKey(attachment.libraryID,event.params.ids[0]);
        if(item?.annotationType==='highlight')snapshot={reader:event.reader,attachmentID:attachment.id,
          annotation:{type:'highlight',text:item.annotationText,position:JSON.parse(item.annotationPosition),
            sortIndex:item.annotationSortIndex,pageLabel:item.annotationPageLabel,color:item.annotationColor}};
      }
      if(!snapshot)return;
      for(const [label,action] of [['PaperBridge · 添加灵感批注',()=>this.edit(snapshot)],
        ['PaperBridge · 添加标签',()=>this.edit(snapshot,true)],['PaperBridge · 复制中英对照',()=>this.copy(snapshot)]]) {
        event.append({label,onCommand:()=>Promise.resolve(action()).catch(e=>Services.prompt.alert(Zotero.getMainWindow(),'PaperBridge',String(e)))});
      }
    };
    for(const type of ['createViewContextMenu','createAnnotationContextMenu']) {
      Zotero.Reader.registerEventListener(type,handler,id);this.handlers.push([type,handler]);
    }
  },
  stop() {
    for(const [type,handler] of this.handlers)Zotero.Reader.unregisterEventListener(type,handler);
    this.handlers=[];
  }
};
