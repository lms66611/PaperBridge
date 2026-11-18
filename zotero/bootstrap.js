/* SPDX-License-Identifier: AGPL-3.0-or-later */
var PaperBridge;
var { classes: Cc, interfaces: Ci } = Components;
var Services = globalThis.Services || ChromeUtils.importESModule('resource://gre/modules/Services.sys.mjs').Services;

function install() {}
function uninstall() {}

async function startup({ id, version, rootURI }) {
  await Zotero.initializationPromise;
  try {
    Services.scriptloader.loadSubScriptWithOptions(String(rootURI.spec || rootURI)+'reader-alignment.js', {target:globalThis,ignoreCache:true});
    Services.scriptloader.loadSubScriptWithOptions(String(rootURI.spec || rootURI)+'reader-notes.js', {target:globalThis,ignoreCache:true});
    PaperBridgeReaderSync.start(id);
    PaperBridgeReaderNotes.start(id);
  } catch (e) { Zotero.debug('PaperBridge reader startup: '+e); }
  // Zotero 10 requires an HTTPS update URL even for manually distributed plugins.
  // This portable release has no update server; disable background updates for itself.
  try {
    const { AddonManager } = ChromeUtils.importESModule('resource://gre/modules/AddonManager.sys.mjs');
    const self = await AddonManager.getAddonByID(id);
    if (self) self.applyBackgroundUpdates = AddonManager.AUTOUPDATE_DISABLE;
  } catch (e) { Zotero.debug('PaperBridge: ' + e); }
  PaperBridge = {
    id, active: true, timer: null, busy: false, interval: 150, importing: new Set(),
    inFlight: new Map(), importTail: Promise.resolve(), lastHello: 0,
    async discovery(allowStopped = false) {
      const env = Cc['@mozilla.org/process/environment;1'].getService(Ci.nsIEnvironment);
      const base = env.get('LOCALAPPDATA');
      if (!base) throw new Error('未找到 Windows 本机数据目录');
      const path = PathUtils.join(base, 'PaperBridge', 'bridge.json');
      const d = JSON.parse(await IOUtils.readUTF8(path));
      if (!allowStopped && !/^http:\/\/127\.0\.0\.1:\d+$/.test(d.url || '')) throw new Error('请先双击启动 PaperBridge');
      return d;
    },
    async ensureDesktop() {
      try { await this.request('/status'); return; } catch (e) { /* Start the paired portable program. */ }
      const d = await this.discovery(true);
      const command = d.command;
      if (!Array.isArray(command) || command.length !== 1 || !/[\\/]PaperBridge\.exe$/i.test(command[0]) || !(await IOUtils.exists(command[0]))) {
        throw new Error('请先双击本机的 PaperBridge.exe 完成首次关联；移动程序目录后也需先启动一次');
      }
      const file = Cc['@mozilla.org/file/local;1'].createInstance(Ci.nsIFile);file.initWithPath(command[0]);
      const process = Cc['@mozilla.org/process/util;1'].createInstance(Ci.nsIProcess);process.init(file);
      process.runwAsync([],0);
      const deadline = Date.now()+25000;
      while (Date.now()<deadline) {
        await new Promise(resolve => setTimeout(resolve,500));
        try { await this.request('/status'); return; } catch(e) { /* New port/token may still be starting. */ }
      }
      throw new Error('PaperBridge 尚未启动成功，请查看程序目录中的 startup-error.log');
    },
    async request(endpoint, payload) {
      const d = await this.discovery();
      const options = { responseType: 'json', headers: { Authorization: 'Bearer ' + d.token }, timeout: 8000 };
      if (payload !== undefined) {
        options.body = JSON.stringify(payload); options.headers['Content-Type'] = 'application/json';
      }
      const r = await Zotero.HTTP.request(payload === undefined ? 'GET' : 'POST', d.url + endpoint, options);
      return r.response;
    },
    locator(item) {
      const attachmentKey = item.isAttachment() ? item.key : null;
      if (item.isAttachment() && item.parentID) item = Zotero.Items.get(item.parentID);
      const lib = Zotero.Libraries.get(item.libraryID);
      return { key: item.key, attachmentKey, libraryType: lib.libraryType,
        groupID: lib.libraryType === 'group' ? Zotero.Groups.getGroupIDFromLibraryID(item.libraryID) : null,
        doi: item.isRegularItem() ? item.getField('DOI') : '' };
    },
    resolve(locator) {
      if (!locator || !locator.key) return null;
      let libraryID = Zotero.Libraries.userLibraryID;
      if (locator.libraryType === 'group') {
        libraryID = Zotero.Groups.getLibraryIDFromGroupID(locator.groupID);
        if (!libraryID) return null;
      }
      return Zotero.Items.getByLibraryAndKey(libraryID, locator.key);
    },
    async translate(command) {
      let api = Zotero.PDFTranslate && Zotero.PDFTranslate.api;
      const deadline = Date.now() + 18000;
      while ((!api || typeof api.translate !== 'function') && Date.now() < deadline && this.active) {
        await new Promise(resolve => setTimeout(resolve,300));
        api = Zotero.PDFTranslate && Zotero.PDFTranslate.api;
      }
      if (!api || typeof api.translate !== 'function') throw new Error('请安装并启用 Translate for Zotero 插件');
      const item = this.resolve(command.locator);
      const result = await api.translate(command.text, { pluginID: this.id,
        service: command.service || 'bing', itemID: item ? item.id : undefined,
        langfrom: command.from || 'en-US', langto: command.to || 'zh-CN' });
      if (result.status !== 'success' || !result.result || /^\s*\[(?:请求错误|request error)\]/i.test(result.result)) {
        throw new Error(String(result.error || result.result || result.status || '翻译服务返回空结果').slice(0,550));
      }
      return { text: result.result, service: result.service };
    },
    async chooseItem(items, title) {
      const win = Zotero.getMainWindow();
      const prompts = Services.prompt;
      const out = {};
      const labels = items.map(i => i.getField('title') || i.key);
      if (!prompts.select(win, '选择归属文献', title, labels, out)) throw new Error('已取消归档，译文文件已保留');
      return items[out.value];
    },
    async importFile(command) {
      if (!command.path || !(await IOUtils.exists(command.path))) throw new Error('译文文件不存在');
      const revision = Number(command.revision || 1);
      if (!Number.isSafeInteger(revision) || revision < 1) throw new Error('双语 PDF 版本号无效');
      const versionPrefix = 'paperbridge:' + command.fingerprint + ':' + command.job_id + ':v';
      const tag = versionPrefix + revision;
      const attachmentTitle = '双语翻译版 v' + revision;
      const attachmentFile = '双语翻译版_v' + revision + '.pdf';
      let parent = this.resolve(command.locator);
      const standalone = parent && parent.isAttachment() && !parent.parentID ? parent : null;
      if (parent && parent.isAttachment() && parent.parentID) parent = Zotero.Items.get(parent.parentID);
      if (!parent || !parent.isRegularItem()) {
        const doi = (command.metadata && (command.metadata.doi || command.metadata.DOI)) || '';
        if (doi) {
          const search = new Zotero.Search(); search.addCondition('DOI', 'contains', doi);
          const ids = await search.search();
          const items = (await Zotero.Items.getAsync(ids)).filter(i => i.isRegularItem() && !i.deleted && String(i.getField('DOI')).toLowerCase() === doi.toLowerCase());
          if (items.length === 1) parent = items[0];
          else if (items.length > 1) parent = await this.chooseItem(items, '找到多个 DOI 相同的条目，请选择本次译文的归属。');
        }
        if (!parent || !parent.isRegularItem()) {
          parent = new Zotero.Item('journalArticle');
          parent.libraryID = Zotero.Libraries.userLibraryID;
          parent.setField('title', command.title || 'PDF 文献');
          if (doi) parent.setField('DOI', doi);
          await parent.saveTx();
        }
      }
      if (standalone) {
        standalone.parentID = parent.id;
        await standalone.saveTx();
      }
      const attachments = await Zotero.Items.getAsync(parent.getAttachments());
      if (command.source_path && await IOUtils.exists(command.source_path) && !attachments.some(a => a.isPDFAttachment())) {
        const original = await Zotero.Attachments.importFromFile({file:command.source_path,parentItemID:parent.id,
          title:command.source_name || '原始文献 PDF',contentType:'application/pdf'});
        original.addTag('paperbridge:original:' + command.fingerprint,1); await original.saveTx();
      }
      for (const item of attachments) {
        if (item.deleted || !item.isPDFAttachment()) continue;
        const previous = item.getTags().map(t => t.tag).find(t => t.startsWith(versionPrefix) &&
          /^[1-9]\d*$/.test(t.slice(versionPrefix.length)));
        if (!previous) continue;
        const currentTitle = item.getField('title');
        const numberedTitle = '双语翻译版 v' + previous.slice(versionPrefix.length);
        if ((currentTitle === '双语翻译版' || /^双语翻译版 v\d+$/.test(currentTitle)) &&
            currentTitle !== numberedTitle) {
          item.setField('title', numberedTitle);
          await item.saveTx();
        }
      }
      for (const item of attachments) {
        if (item.getTags().some(t => t.tag === tag)) {
          const currentTitle = item.getField('title');
          if (currentTitle === '双语翻译版' || /^双语翻译版 v\d+$/.test(currentTitle)) {
            item.setField('title', attachmentTitle);
            await item.saveTx();
          }
          if (typeof item.renameAttachmentFile === 'function') {
            const renamed = await item.renameAttachmentFile(attachmentFile);
            if (renamed === false || typeof renamed === 'number') throw new Error('双语 PDF 已附回，但文件重命名未完成，请重试归档');
          }
          return { key: item.key, parentKey: parent.key, duplicate: true };
        }
      }
      const attachment = await Zotero.Attachments.importFromFile({ file: command.path,
        parentItemID: parent.id, title: attachmentTitle, contentType: 'application/pdf' });
      attachment.addTag(tag, 1); await attachment.saveTx();
      if (typeof attachment.renameAttachmentFile === 'function') {
        const renamed = await attachment.renameAttachmentFile(attachmentFile);
        if (renamed === false || typeof renamed === 'number') throw new Error('双语 PDF 已附回，但文件重命名未完成，请重试归档');
      }
      return { key: attachment.key, parentKey: parent.key, duplicate: false };
    },
    async executeCommand(command) {
      let result=null,error=null;
      try {
        if (!this.active || command.expires && command.expires*1000<Date.now()) throw new Error('任务请求已过期，请在桌面程序重试');
        if (command.type==='translate') result=await this.translate(command);
        else if (command.type==='import') {
          const run=this.importTail.catch(()=>{}).then(()=>{
            if (!this.active || command.expires*1000<Date.now()) throw new Error('归档请求已过期，请重试');
            return this.importFile(command);
          });
          this.importTail=run;result=await run;
        } else throw new Error('未知桥接任务');
      } catch(e) {error=String(e.message||e);}
      // Retry delivery only. Never repeat translation, import or paid review.
      for(let attempt=0;attempt<3 && this.active;attempt++) {
        try {await this.request('/bridge/result',{id:command.id,result,error});break;}
        catch(e) {if(attempt<2)await new Promise(resolve=>setTimeout(resolve,500*(attempt+1)));}
      }
    },
    dispatch(command) {
      if(this.inFlight.has(command.id))return;
      const task=this.executeCommand(command).catch(e=>Zotero.debug('PaperBridge command: '+e))
        .finally(()=>this.inFlight.delete(command.id));
      this.inFlight.set(command.id,task);
    },
    async poll() {
      if (!this.active || this.busy) return;
      this.busy = true;
      try {
        if(Date.now()-this.lastHello>10000) {
          await this.request('/bridge/hello', { zoteroVersion: Zotero.version,
            translationVersion: Zotero.PDFTranslate?.api?.getVersion?.() || null, pluginVersion: version,
            parallelCommands:3 });
          this.lastHello=Date.now();
        }
        if(this.inFlight.size<3) {
          const response=await this.request('/bridge/poll');
          if(response.command)this.dispatch(response.command);
        }
      } catch (e) { /* Desktop app may be closed; don't disturb reading. */ }
      finally {
        this.busy = false;
        if (this.active) this.timer = setTimeout(() => this.poll(), this.interval);
      }
    },
    async submitSelected() {
      try {
        const pane = Zotero.getActiveZoteroPane();
        const selected = pane.getSelectedItems();
        const files = [];
        const seen = new Set();
        const generated = a => a.getTags().some(t => t.tag.startsWith('paperbridge:') && !t.tag.startsWith('paperbridge:original:'));
        for (let item of selected) {
          let attachment = item;
          if (item.isRegularItem()) {
            attachment = await item.getBestAttachment();
            if (!attachment || generated(attachment)) {
              const attachments = await Zotero.Items.getAsync(item.getAttachments());
              attachment = attachments.find(a => a.isPDFAttachment() && !a.deleted && !generated(a));
            }
          }
          if (!attachment || !attachment.isPDFAttachment()) continue;
          if (generated(attachment)) continue;
          const path = await attachment.getFilePathAsync();
          if (!path || !(await IOUtils.exists(path))) continue;
          if (seen.has(path)) continue;
          seen.add(path);
          const parent = item.isAttachment() && item.parentID ? Zotero.Items.get(item.parentID) : item;
          files.push({ path, locator: this.locator(attachment), metadata: {
            title: parent.getField('title'), doi: parent.isRegularItem() ? parent.getField('DOI') : '' } });
        }
        if (!files.length) throw new Error('请选中带原始 PDF 的文献或原始 PDF 附件；已生成的双语版不重复翻译');
        await this.ensureDesktop();
        await this.request('/jobs', { files });
        const window = new Zotero.ProgressWindow();
        window.changeHeadline('PaperBridge'); window.addDescription('已加入翻译队列；完成后会附回带版本号的双语 PDF。'); window.show(); window.startCloseTimer(6000);
      } catch (e) { Services.prompt.alert(Zotero.getMainWindow(), 'PaperBridge', String(e.message || e)); }
    },
    addMenu(window) {
      const document = window.document;
      for (const [popupID, suffix] of [['zotero-itemmenu','item'],['menu_ToolsPopup','tools']]) {
        const popup = document.getElementById(popupID);
        if (!popup || document.getElementById('paperbridge-'+suffix)) continue;
        const menu = document.createXULElement('menuitem');
        menu.id='paperbridge-'+suffix; menu.setAttribute('label','PaperBridge · 生成双语翻译版');
        menu.addEventListener('command', () => this.submitSelected()); popup.appendChild(menu);
        const open=document.createXULElement('menuitem');
        open.id='paperbridge-open-'+suffix;open.setAttribute('label','PaperBridge · 打开最新双语版');
        open.addEventListener('command',async()=>{
          try {
            const selected=Zotero.getActiveZoteroPane().getSelectedItems()[0];
            if (!selected) throw new Error('请先选择一篇文献。');
            const parent=selected.parentID ? Zotero.Items.get(selected.parentID) : selected;
            const attachments=parent.isRegularItem() ? await Zotero.Items.getAsync(parent.getAttachments()) : [parent];
            const versionOf = item => {
              const tag=item.getTags().map(t=>t.tag).find(t=>/^paperbridge:.*:v[1-9]\d*$/.test(t));
              return tag ? Number(tag.match(/:v(\d+)$/)[1]) : 0;
            };
            const candidates=attachments.filter(a=>!a.deleted&&a.isPDFAttachment()&&
              (versionOf(a)>0 || /^双语翻译版(?: v\d+)?$/.test(a.getField('title'))));
            candidates.sort((a,b)=>versionOf(b)-versionOf(a)||
              String(b.dateModified||'').localeCompare(String(a.dateModified||'')));
            if (!candidates.length) throw new Error('此条目尚无“双语翻译版”附件，请先翻译或附回新版 PDF。');
            await Zotero.Reader.open(candidates[0].id);
          } catch(e) {Services.prompt.alert(window,'PaperBridge',String(e.message||e));}
        });popup.appendChild(open);
      }
    },
    removeMenu(window) {
      for (const suffix of ['item','tools']) {
        window.document.getElementById('paperbridge-'+suffix)?.remove();
        window.document.getElementById('paperbridge-open-'+suffix)?.remove();
      }
    }
  };
  for (const window of Zotero.getMainWindows()) PaperBridge.addMenu(window);
  PaperBridge.poll();
}

function onMainWindowLoad({ window }) { PaperBridge?.addMenu(window); }
function onMainWindowUnload({ window }) { PaperBridge?.removeMenu(window); }
function shutdown() {
  if (typeof PaperBridgeReaderNotes !== 'undefined') PaperBridgeReaderNotes.stop();
  if (typeof PaperBridgeReaderSync !== 'undefined') PaperBridgeReaderSync.stop();
  if (!PaperBridge) return;
  PaperBridge.active = false;
  if (PaperBridge.timer) clearTimeout(PaperBridge.timer);
  for (const window of Zotero.getMainWindows()) PaperBridge.removeMenu(window);
  PaperBridge = null;
}
