from __future__ import annotations
import argparse, json, multiprocessing, pathlib, subprocess, sys, time


def run_pending_update():
    from paperbridge.storage import atomic_json
    from paperbridge.updater import apply_bundle,wait_for_exit
    root=pathlib.Path(sys.executable).resolve().parent
    package,version,pid=sys.argv[2:5]
    try:
        wait_for_exit(int(pid))
        apply_bundle(root,package,version)
    except Exception as exc:
        atomic_json(root/'updates/last-update.json',{'status':'failed','version':version,'error':str(exc)})
    subprocess.Popen([sys.executable],cwd=root,creationflags=getattr(subprocess,'DETACHED_PROCESS',0))

def main():
    from paperbridge.storage import Paths,Store
    parser=argparse.ArgumentParser(description='PaperBridge portable scientific translation')
    parser.add_argument('--root',type=pathlib.Path)
    parser.add_argument('--headless',action='store_true')
    parser.add_argument('--extract',type=pathlib.Path)
    parser.add_argument('--translate',nargs='+',type=pathlib.Path)
    parser.add_argument('--offline',action='store_true')
    parser.add_argument('--no-review',action='store_true')
    parser.add_argument('--self-test',action='store_true')
    parser.add_argument('--export-existing',action='store_true',help='Re-export saved jobs using the original-page PDF layout')
    parser.add_argument('--output-dir',type=pathlib.Path)
    parser.add_argument('--ui-smoke-test',action='store_true',help=argparse.SUPPRESS)
    args=parser.parse_args()
    def smoke(stage):
        if args.ui_smoke_test:print('UI smoke: '+stage,flush=True)
    paths=Paths(args.root);store=Store(paths)
    if args.offline:store.save_settings({'mode':'offline'})
    if args.no_review:store.save_settings({'local_review':False})
    if args.export_existing:
        from paperbridge.export import export_document
        for job in store.list_jobs():
            if not job.get('segments'):continue
            if args.output_dir:job['settings']['output_dir']=str(args.output_dir.resolve())
            job['revision']=job.get('revision',1)+1
            job['outputs']=export_document(job,paths);store.save_job(job)
            print(json.dumps(job['outputs'],ensure_ascii=False),flush=True)
        return
    if args.extract:
        from paperbridge.extract import PDFExtractor
        folder=paths.data/'extract-test';folder.mkdir(exist_ok=True)
        result=PDFExtractor(paths).extract(args.extract,folder,lambda s,p:print(s,round(p,2),flush=True))
        print(json.dumps({'title':result['metadata']['title'],'segments':len(result['segments'])},ensure_ascii=False));return
    if args.self_test:
        from paperbridge.engines import LocalModel
        local=LocalModel(paths,store.settings)
        try:
            text=local.translate('The fuel surrogate did not crack at 300 K.','Simplified Chinese',{'domain':'航空燃料热解'})
            print(json.dumps({'backend':local.backend,'translation':text},ensure_ascii=False),flush=True)
            (paths.data/'self-test.json').write_text(json.dumps({'backend':local.backend,'translation':text},ensure_ascii=False),'utf-8')
        finally:local.stop()
        return
    import atexit
    from paperbridge.storage import InstanceGuard
    smoke('instance guard')
    guard=InstanceGuard();atexit.register(guard.close)
    from paperbridge.pipeline import Controller
    from paperbridge.bridge import ZoteroBridge
    smoke('controller')
    controller=Controller(paths,store)
    smoke('bridge')
    bridge=ZoteroBridge(paths,controller.submit,controller.list_jobs);controller.bridge=bridge
    if args.translate:
        ids=[controller.submit(path,sequence=i+1) for i,path in enumerate(args.translate)]
        for ident in ids:controller.enqueue(ident)
        last=''
        while any(controller.jobs[i]['status'] in ['queued','running'] for i in ids):
            status=' | '.join(controller.jobs[i]['stage'] for i in ids)
            if status!=last:print(status,flush=True);last=status
            time.sleep(2)
        print(json.dumps([{k:controller.jobs[i].get(k) for k in ['id','status','error','outputs']} for i in ids],ensure_ascii=False,indent=2),flush=True)
        controller.close();bridge.close();return
    if args.headless:
        print('PaperBridge ready',bridge.url,flush=True)
        try:
            while True:time.sleep(1)
        except KeyboardInterrupt:pass
        finally:controller.close();bridge.close()
        return
    from paperbridge.gui import Application
    smoke('application')
    app=Application(controller,bridge,hidden=args.ui_smoke_test)
    smoke('event loop')
    if args.ui_smoke_test:app.root.after(350,app.close)
    app.run()
    smoke('closed')

if __name__=='__main__':
    multiprocessing.freeze_support()
    try:
        if len(sys.argv)==5 and sys.argv[1]=='--apply-update':run_pending_update()
        else:main()
    except Exception:
        import traceback
        error=traceback.format_exc()
        if getattr(sys,'frozen',False):
            (pathlib.Path(sys.executable).parent/'startup-error.log').write_text(error,'utf-8')
            if not any(a in sys.argv for a in ['--headless','--self-test','--extract','--translate','--export-existing','--ui-smoke-test']):
                import tkinter.messagebox as mb
                mb.showerror('PaperBridge 启动失败',error[-1800:])
            sys.exit(1)
        else:raise
