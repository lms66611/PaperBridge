from __future__ import annotations
import collections, copy, hashlib, json, pathlib, queue, re, shutil, threading, time, traceback
from .storage import atomic_json, read_json
from .engines import LocalModel,JobLocalModel,TranslationGate,FreeTranslator,parse_json,split_text
from .extract import PDFExtractor,language
from .export import export_document
from .usage import start_processing,checkpoint_processing,finish_processing

def protected_facts(text):
    normalized=text.translate(str.maketrans('０１２３４５６７８９．％','0123456789.%'))
    return collections.Counter(re.findall(r'(?<![A-Za-z0-9_])[+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|PB[A-Z]+\d+PB',normalized))

def check_translation(source,target):
    issues=[]
    if not target.strip(): return ['译文为空']
    if protected_facts(source)!=protected_facts(target): issues.append('数字或保护标记不一致，请核对')
    if len(target)<max(3,len(source)*.10): issues.append('译文异常简短，可能漏译')
    if source.strip()==target.strip() and len(source)>80: issues.append('长段落与原文完全相同，可能未翻译')
    if re.search(r'ignore (?:all|previous)|system prompt|忽略.*指令',source,re.I): issues.append('原文含指令式内容，仅按资料翻译')
    return issues

class Paused(Exception): pass

class Controller:
    def __init__(self,paths,store):
        self.paths,self.store=paths,store
        self.bridge=None
        self.local=LocalModel(paths,store.settings)
        self.extractor=PDFExtractor(paths)
        self.work=queue.Queue(); self.pauses=set(); self.active=set(); self.closed=False
        self.lock=threading.RLock()
        self.wake=threading.Condition(self.lock)
        self.pdf_lock=threading.Lock()
        self.translation_gate=TranslationGate()
        self.maintenance=threading.Lock()
        self.jobs={j['id']:j for j in store.list_jobs()}
        self.job_views={}
        for j in self.jobs.values():
            if j.pop('processing_checkpoint_at',None) is not None:
                j['timing_partial']=True
                self.save_job(j)
            if j['settings'].get('workflow_version',0)<2:
                for key in ('mode','gpt_enabled','local_review','local_fallback','auto_import','workflow_version','pdf_layout'):
                    j['settings'][key]=store.settings[key]
            if j.get('status') in ['running','queued']:
                j['status']='paused'; j['stage']='上次中断，可继续'; self.save_job(j)
        self.job_views=copy.deepcopy(self.jobs)
        self.threads=[threading.Thread(target=self.run_worker,name=f'PaperBridge-{i+1}',daemon=True) for i in range(3)]
        for thread in self.threads:thread.start()
        self.thread=self.threads[0]

    def parallel_limit(self):
        try:return max(1,min(3,int(self.store.settings.get('parallel_documents',2))))
        except (ValueError,TypeError):return 2

    def model_for(self,job):
        return JobLocalModel(self.local,job['settings'],lambda:self.cancel_check(job))

    def pdf_work(self,job,action):
        while not self.pdf_lock.acquire(timeout=.2):self.cancel_check(job)
        try:
            self.checkpoint(job)
            return action()
        finally:self.pdf_lock.release()

    def save_job(self,job):
        # Publish only at saved checkpoints; UI readers never traverse live worker data.
        with self.lock:
            checkpoint_processing(job)
            self.store.save_job(job)
            self.job_views[job['id']]=copy.deepcopy(job)

    def list_jobs(self):
        with self.lock: return copy.deepcopy(sorted(self.job_views.values(),key=lambda j:j['created']))

    def submit(self,source,locator=None,metadata=None,sequence=None):
        source=pathlib.Path(source).resolve()
        if source.suffix.lower()!='.pdf' or not source.is_file(): raise ValueError('请选择存在的 PDF 文件。')
        digest=hashlib.file_digest(source.open('rb'),'sha256').hexdigest()
        settings=copy.deepcopy(self.store.settings)
        identity={k:settings[k] for k in ['mode','service','gpt_enabled','gpt_model','local_review','workflow_version','pdf_layout']}
        ident=hashlib.sha256((digest+json.dumps(identity,sort_keys=True)).encode()).hexdigest()[:24]
        with self.lock:
            if ident in self.jobs:
                old=self.jobs[ident]
                if locator:
                    old['locator']=locator
                    if ident not in self.active:self.save_job(old)
                if locator and old['status'] not in ('running','queued'): self.enqueue(ident)
                return ident
            folder=self.paths.jobs/ident; folder.mkdir(exist_ok=True)
            shutil.copy2(source,folder/'source.pdf')
            j={'id':ident,'fingerprint':digest,'source_name':source.name,'original_path':str(source),
               'title':(metadata or {}).get('title') or source.stem,'created':time.time(),'status':'ready','stage':'已添加',
               'progress':0,'settings':settings,'locator':locator,'metadata':metadata or {},'segments':[],
               'review_flags':[],'gpt_cost':0,'revision':1,'sequence':sequence,
               'processing_seconds':0.0,'free_usage':[]}
            self.jobs[ident]=j; self.save_job(j)
            # Zotero right-click means start; desktop adding stays ready until Start is clicked.
            if locator: self.enqueue(ident)
        return ident

    def enqueue(self,ident):
        with self.lock:
            if self.closed:raise RuntimeError('程序正在退出，请重新启动后继续。')
            if self.maintenance.locked(): raise RuntimeError('正在导出 PDF，请完成后再开始翻译。')
            j=self.jobs[ident]
            if j['status'] in ['running','queued']: return
            self.pauses.discard(ident); j['status']='queued'; j['stage']='等待处理'; j.pop('error',None)
            self.save_job(j); self.work.put(ident)
            self.wake.notify_all()

    def pause(self,ident):
        with self.wake:
            self.pauses.add(ident)
            job=self.jobs[ident]
            if job['status']=='queued':
                job['status']='paused';job['stage']='已暂停，尚未开始';self.save_job(job)
            self.wake.notify_all()
    def checkpoint(self,job):
        self.save_job(job)
        self.cancel_check(job)

    def cancel_check(self,job):
        if self.closed or job['id'] in self.pauses: raise Paused()
    def update(self,job,stage,progress=None):
        job['stage']=stage
        if progress is not None: job['progress']=round(progress,3)
        self.checkpoint(job)

    def run_worker(self):
        while True:
            with self.wake:
                while not self.closed:
                    if len(self.active)<self.parallel_limit() and not self.maintenance.locked():
                        try:ident=self.work.get_nowait()
                        except queue.Empty:pass
                        else:
                            job=self.jobs[ident]
                            if job['status']!='queued' or ident in self.active:
                                self.work.task_done();continue
                            self.active.add(ident);job['status']='running';break
                    self.wake.wait(.25)
                else:return
            start_processing(job);self.save_job(job)
            try: self.process(job)
            except Paused: job['status']='paused'; job['stage']='已暂停，进度已保存'
            except Exception as exc:
                job['status']='failed'; job['error']=str(exc); job['stage']='需要处理后重试'
                (self.paths.jobs/ident/'error.log').write_text(traceback.format_exc(),'utf-8')
            finally:
                finish_processing(job)
                try:self.save_job(job)
                finally:
                    with self.wake:
                        self.active.discard(ident);self.work.task_done();self.wake.notify_all()

    def build_context(self,job):
        if job['settings']['mode']=='free':
            # No model, paid API or model download is needed for free service
            # translation; source definitions remain in adjacent-paragraph context.
            return {'domain':'当前论文','topic':job['title'],'terms':[], 'definitions':[], 'confirmed':[]}
        source='\n'.join(s['source'] for s in job['segments'] if s['type']!='graphic' and s['status']!='reference')
        selected=source[:5500]+'\nCONCLUSION EXCERPT:\n'+source[-1100:]
        prompt=('阅读论文摘录，输出紧凑 JSON：domain（中文学科，最多15字）、topic（中文主题，最多60字）、'
                'definitions（最多3项缩写含义）、terms（最多6项关键术语）。后两个数组各项为 '
                '{"source":"摘录中的英文词","target":"本语境中的中文含义","evidence":"摘录中的短引文，最多100字符"}。'
                'evidence 必须逐字引用，术语不确定就不列入，不要猜测。只输出 JSON。\n'+selected)
        try:
            context=parse_json(self.model_for(job).complete(prompt,max_tokens=1800,json_mode=True))
            if not isinstance(context,dict): raise ValueError('invalid context')
            context['terms']=[x for x in context.get('terms',[]) if isinstance(x,dict) and x.get('source') and x.get('target') and x.get('evidence') and x['evidence'] in selected and x['source'].lower() in selected.lower()]
            context['definitions']=[x for x in context.get('definitions',[]) if isinstance(x,dict) and x.get('evidence') and x['evidence'] in selected]
            context['confirmed']=self.store.confirmed(context.get('domain',''))
            return context
        except Paused:raise
        except Exception as exc:
            job['review_flags'].append({'stage':'文章语境','reason':str(exc)})
            return {'domain':'待核对','topic':job['title'],'terms':[],'definitions':[],'confirmed':[]}

    def segment_context(self,job,index):
        segments=job['segments']; s=segments[index]
        heading=next((p['source'] for p in reversed(segments[:index]) if p['type']=='heading'),job['title'])
        terms=[t for t in job['context'].get('terms',[])+job['context'].get('confirmed',[]) if t['source'].lower() in s['source'].lower()]
        return {'domain':job['context'].get('domain'),'topic':job['context'].get('topic'),
                'section':heading,'definitions':job['context'].get('definitions',[])[:8],'terms':terms,
                'previous':segments[index-1]['source'][-600:] if index else '',
                'next':segments[index+1]['source'][:600] if index+1<len(segments) else ''}

    def process(self,job):
        job['status']='running'; folder=self.paths.jobs/job['id']
        if not job['segments']:
            self.update(job,'等待 PDF 解析',.01)
            parsed=self.pdf_work(job,lambda:self.extractor.extract(folder/'source.pdf',folder,lambda stage,p:self.update(job,stage,p*.12)))
            job.update(parsed); job['title']=job['metadata']['title']; self.checkpoint(job)
        local=self.model_for(job)
        if 'context' not in job:
            self.update(job,'建立全文语境',.13); job['context']=self.build_context(job); self.checkpoint(job)
        free=FreeTranslator(self.bridge,self.translation_gate,lambda:self.cancel_check(job)) if self.bridge else None
        eligible=[s for s in job['segments'] if s['status'] not in ['reference','preserved']]
        done=0
        for index,segment in enumerate(job['segments']):
            if segment['status'] in ['reference','preserved']: continue
            if segment['status']=='done': done+=1; continue
            self.update(job,f'翻译 {done+1}/{len(eligible)} · 原文 p.{segment["page"]}',.15+.75*done/max(1,len(eligible)))
            context=self.segment_context(job,index)
            target='English' if segment['language']=='zh' else 'Simplified Chinese'
            key=self.store.cache_key(segment['source'],target,context,'v4-protected-citations:'+job['settings']['mode']+':'+job['settings']['service'])
            cached=self.store.cached(key)
            try:
                if cached:
                    segment.update(cached)
                elif not segment.get('translation'):
                    translation=None;free_service=None
                    if job['settings']['mode']!='offline' and free:
                        errors=[]
                        primary=job['settings']['service']
                        for service in dict.fromkeys([primary,'cnki' if primary=='bing' else 'bing']):
                            try:
                                translation=free.translate(segment['source'],'zh-CN' if segment['language']=='zh' else 'en-US',
                                    'en-US' if segment['language']=='zh' else 'zh-CN',service,job.get('locator'),context.get('terms'))
                                segment['engine']='Zotero/'+service;free_service=service;break
                            except Paused:raise
                            except Exception as exc: errors.append(service+': '+str(exc)[:140])
                        if translation is None and job['settings']['mode']=='free' and not job['settings'].get('local_fallback'):
                            raise ConnectionError('免费服务暂未完成，进度已保存，请稍后继续。'+'；'.join(errors))
                    if translation is None:
                        if job['settings']['mode']=='free' and not job['settings'].get('local_fallback'):
                            raise ConnectionError('请打开 Zotero 并启用 PaperBridge 与 Translate for Zotero 插件，再点击继续。')
                        translated=[]
                        for part in split_text(segment['source'],1700): translated.append(local.translate(part,target,context))
                        translation=' '.join(translated); segment['engine']='local/'+local.backend
                    segment['translation']=translation
                    if free_service is not None:
                        job.setdefault('free_usage',[]).append({'service':free_service,
                            'chars':len(segment['source']),'segment_id':segment['id'],'at':time.time()})
                    self.store.put_cache(key,{'translation':translation,'engine':segment['engine']},segment['engine'])
                self.checkpoint(job)
                segment['issues']=list(dict.fromkeys(segment.get('issues',[])+check_translation(segment['source'],segment['translation'])))
                if job['settings']['local_review'] and not segment.get('local_checked'):
                    try:
                        segment['semantic_flags']=local.review(segment['source'],segment['translation'],context)
                    except Paused:raise
                    except Exception as exc: segment['semantic_flags']=['本地句义检查未完成：'+str(exc)[:100]]
                    segment['local_checked']=True
                    self.checkpoint(job)
                segment['issues']=list(dict.fromkeys(segment['issues']+segment.get('semantic_flags',[])+check_translation(segment['source'],segment['translation'])))
                segment['status']='done'; segment['completed_at']=time.time()
                done+=1; self.checkpoint(job)
            except Paused: raise
            except Exception as exc:
                segment['status']='failed'; segment.setdefault('issues',[]).append(str(exc))
                job['review_flags'].append({'id':segment['id'],'reason':str(exc)})
                self.checkpoint(job)
        from .review import review_job
        review_job(self,job)
        self.update(job,'等待排版（保留原页）',.93)
        job['review_flags']=[x for x in job.get('review_flags',[]) if not x.get('id')]+[
            {'id':s['id'],'page':s['page'],'issues':s.get('issues',[])} for s in job['segments'] if s.get('issues') or s['status']=='failed']
        job['outputs']=self.pdf_work(job,lambda:export_document(job,self.paths)); self.checkpoint(job)
        failures=sum(s['status']=='failed' for s in job['segments'])
        if failures and job['settings'].get('auto_import'):
            job['import_error']='尚有未翻译段落，已保留本地 PDF；重试完成后再自动附回 Zotero。'
        elif job['settings'].get('auto_import') and self.bridge and self.bridge.connected:
            try:
                self.update(job,'附回 Zotero',.98)
                result=self.bridge.request('import',{'path':job['outputs']['pdf'],'locator':job.get('locator'),
                    'metadata':job['metadata'],'title':job['title'],'fingerprint':job['fingerprint'],'job_id':job['id'],
                    'revision':job.get('revision',1),'source_path':str(folder/'source.pdf'),'source_name':job['source_name']},timeout=180)
                job['zotero_result']=result
                job.pop('import_error',None)
            except Exception as exc: job['import_error']=str(exc)
        elif job['settings'].get('auto_import'): job['import_error']='Zotero 未连接；可稍后点击“附回 Zotero”。'
        job['status']='partial' if failures else 'complete'
        job['stage']=f'完成 · {len(job["review_flags"])} 处提示' if not failures else f'已导出 · {failures} 段待重试'
        job['progress']=1; self.checkpoint(job)

    def export_again(self,ident):
        self.begin_maintenance()
        try:self._export_again(ident)
        finally:self.maintenance.release()

    def begin_maintenance(self):
        # Reserve before starting a background worker, atomically with enqueue.
        with self.lock:
            if self.active or any(j['status']=='queued' for j in self.jobs.values()):
                raise RuntimeError('请先暂停翻译任务，待当前段落保存后再导出。')
            if not self.maintenance.acquire(blocking=False):
                raise RuntimeError('已有 PDF 导出正在进行。')

    def _export_again(self,ident):
        job=self.jobs[ident];previous=job.get('revision',1)
        start_processing(job)
        try:
            job['revision']=previous+1
            with self.pdf_lock:job['outputs']=export_document(job,self.paths)
            # A successful retry also resolves a previous export failure in the UI.
            failures=sum(s.get('status')=='failed' for s in job.get('segments',[]))
            job['status']='partial' if failures else 'complete'
            job['stage']=f'已导出 · {failures} 段待重试' if failures else f'完成 · {len(job.get("review_flags",[]))} 处提示'
            job['progress']=1;job.pop('error',None)
        except Exception:
            job['revision']=previous
            raise
        finally:
            finish_processing(job)
            self.save_job(job)

    def close(self):
        with self.wake:
            self.closed=True
            self.pauses.update(self.active)
            for job in self.jobs.values():
                if job['status']=='queued':
                    job['status']='paused';job['stage']='已暂停，进度已保存';self.save_job(job)
            self.wake.notify_all()
        self.local.stop()
