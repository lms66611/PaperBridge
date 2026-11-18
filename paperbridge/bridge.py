from __future__ import annotations
import http.server, json, os, pathlib, queue, secrets, sys, threading, time
from .storage import atomic_json

class ZoteroBridge:
    """Authenticated loopback queue. The Zotero plugin polls; no Zotero DB access."""
    def __init__(self, paths, submit, get_jobs):
        self.paths, self.submit, self.get_jobs = paths, submit, get_jobs
        self.token = secrets.token_urlsafe(32)
        self.commands = queue.Queue()
        self.pending = {}
        self.lock = threading.Lock()
        self.last_seen = 0
        self.details = {}
        bridge = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self,*args): pass
            def respond(self,status,value):
                raw=json.dumps(value,ensure_ascii=False).encode('utf-8')
                self.send_response(status)
                self.send_header('Content-Type','application/json; charset=utf-8')
                self.send_header('Content-Length',str(len(raw)))
                self.send_header('Cache-Control','no-store')
                self.end_headers()
                try:self.wfile.write(raw)
                except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):pass
            def authorized(self):
                if self.headers.get('Origin'):
                    return False
                return secrets.compare_digest(self.headers.get('Authorization',''), 'Bearer '+bridge.token)
            def do_GET(self):
                if not self.authorized(): return self.respond(403,{'error':'unauthorized'})
                if self.path == '/bridge/poll':
                    bridge.last_seen=time.time()
                    command=None
                    while True:
                        try: candidate=bridge.commands.get(timeout=2)
                        except queue.Empty: break
                        if candidate.get('expires',0)>=time.time():
                            command=candidate;break
                    return self.respond(200,{'command':command})
                if self.path == '/jobs':
                    jobs=bridge.get_jobs()
                    return self.respond(200,{'jobs':[{k:j.get(k) for k in ['id','title','status','stage','progress','error','outputs']} for j in jobs]})
                return self.respond(200,{'name':'PaperBridge','version':'1.4.7'})
            def do_POST(self):
                if not self.authorized(): return self.respond(403,{'error':'unauthorized'})
                try:
                    length=int(self.headers.get('Content-Length','0'))
                    if length > 4*1024*1024: raise ValueError('request too large')
                    data=json.loads(self.rfile.read(length))
                    if self.path == '/bridge/hello':
                        bridge.last_seen=time.time(); bridge.details=data
                        return self.respond(200,{'ok':True})
                    if self.path == '/bridge/result':
                        with bridge.lock:
                            pending=bridge.pending.get(data['id'])
                            if pending:
                                pending['result']=data
                                pending['event'].set()
                        return self.respond(200,{'ok':True})
                    if self.path == '/jobs':
                        ids=[bridge.submit(item['path'],item.get('locator'),item.get('metadata')) for item in data['files']]
                        return self.respond(200,{'ids':ids})
                    raise ValueError('unknown endpoint')
                except Exception as exc:
                    return self.respond(400,{'error':str(exc)})

        self.server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.server.daemon_threads=True
        self.url=f'http://127.0.0.1:{self.server.server_address[1]}'
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        executable=[sys.executable] if getattr(sys,'frozen',False) else [sys.executable,str(paths.root/'main.py')]
        atomic_json(paths.discovery,{'url':self.url,'token':self.token,'pid':os.getpid(),'command':executable,'version':'1.4.7'})

    @property
    def connected(self):
        return time.time()-self.last_seen < 75

    def request(self,kind,payload,timeout=90):
        if not self.connected:
            raise ConnectionError('Zotero 桥接未连接；请启动 Zotero 并安装随包插件。')
        ident=secrets.token_hex(12)
        pending={'event':threading.Event(),'result':None}
        with self.lock: self.pending[ident]=pending
        self.commands.put({'id':ident,'type':kind,'expires':time.time()+timeout,**payload})
        try:
            if not pending['event'].wait(timeout):
                raise TimeoutError('Zotero 服务超时，任务进度已保存。')
            response=pending['result']
            if response.get('error'): raise RuntimeError(response['error'])
            return response.get('result')
        finally:
            with self.lock: self.pending.pop(ident,None)

    def close(self):
        self.server.shutdown()
        try:
            current=json.loads(self.paths.discovery.read_text('utf-8'))
            if current.get('token')==self.token:
                current['url']=''; current['token']=''
                atomic_json(self.paths.discovery,current)
        except OSError: pass
