from __future__ import annotations
import copy, hashlib, json, os, pathlib, secrets, sqlite3, sys, threading, time

DEFAULTS = {
    'mode': 'free', 'service': 'bing', 'gpt_enabled': False,
    'gpt_model': 'gpt-5.6-luna', 'gpt_budget': 1.0,
    'gpt_input_price': 2.0, 'gpt_output_price': 10.0,
    'gpu': 'auto', 'auto_import': True, 'output_dir': 'results',
    'local_review': False, 'context_tokens': 8192,
    'pdf_layout': 'original-side-by-side', 'local_fallback': False,
    'workflow_version': 2,
    'parallel_documents': 2,
}

def atomic_json(path: pathlib.Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.' + secrets.token_hex(4) + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), 'utf-8')
    for attempt in range(9):
        try:
            os.replace(temp,path);return
        except OSError as exc:
            if getattr(exc,'winerror',None) in [5,32,33] and attempt<8:
                time.sleep(min(.05*(2**attempt),.4));continue
            # Some redirected Windows profile volumes reject ReplaceFile across reparse points.
            if getattr(exc,'winerror',None)!=17:raise
            import shutil
            shutil.copyfile(temp,path);temp.unlink();return

def read_json(path, default=None):
    try:
        return json.loads(pathlib.Path(path).read_text('utf-8'))
    except (OSError, ValueError):
        return copy.deepcopy(default)

def app_root():
    if getattr(sys, 'frozen', False):
        return pathlib.Path(sys.executable).resolve().parent
    return pathlib.Path(__file__).resolve().parents[1]

class InstanceGuard:
    def __init__(self):
        self.handle=None
        if os.name!='nt':return
        import ctypes
        from ctypes import wintypes
        self.kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        self.kernel.CreateMutexW.argtypes=[wintypes.LPVOID,wintypes.BOOL,wintypes.LPCWSTR]
        self.kernel.CreateMutexW.restype=wintypes.HANDLE
        self.kernel.CloseHandle.argtypes=[wintypes.HANDLE]
        user=os.environ.get('USERDOMAIN','')+'\\'+os.environ.get('USERNAME','')
        name='Local\\PaperBridge-'+hashlib.sha256(user.encode()).hexdigest()[:20]
        ctypes.set_last_error(0)
        self.handle=self.kernel.CreateMutexW(None,False,name)
        if not self.handle:raise OSError(ctypes.get_last_error(),'无法创建程序实例锁')
        if ctypes.get_last_error()==183:
            self.close();raise RuntimeError('PaperBridge 已在运行，请使用已打开的窗口。')
    def close(self):
        if self.handle:self.kernel.CloseHandle(self.handle);self.handle=None

class Paths:
    def __init__(self, root=None):
        self.root = pathlib.Path(root or app_root()).resolve()
        self.data = self.root / 'data'
        self.assets = self.root / 'assets'
        self.jobs = self.data / 'jobs'
        self.results = self.root / 'results'
        for path in [self.data, self.jobs, self.results]:
            path.mkdir(parents=True, exist_ok=True)
        probe = self.data / '.write-test'
        try:
            probe.write_text('ok'); probe.unlink()
        except OSError as exc:
            raise RuntimeError('请把整个程序文件夹复制到可写目录后启动。') from exc
        local = pathlib.Path(os.environ.get('LOCALAPPDATA', self.data)) / 'PaperBridge'
        local.mkdir(parents=True, exist_ok=True)
        self.discovery = local / 'bridge.json'

class Store:
    def __init__(self, paths: Paths):
        self.paths = paths
        self.lock = threading.RLock()
        saved=read_json(paths.data/'settings.json', {})
        # Upgrade existing installs to the explicitly requested free/PDF-only
        # workflow once. Never touch the machine's API credential store.
        if saved.get('workflow_version',0)<2:
            saved.update({k:DEFAULTS[k] for k in ('mode','gpt_enabled','local_review','local_fallback','auto_import','workflow_version','pdf_layout')})
            atomic_json(paths.data/'settings.json',{**DEFAULTS,**saved})
        self.settings = {**DEFAULTS, **saved}
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS translations (key TEXT PRIMARY KEY, value TEXT NOT NULL, engine TEXT, created REAL)')
            db.execute('CREATE TABLE IF NOT EXISTS confirmed_terms (domain TEXT, source TEXT, target TEXT, evidence TEXT, PRIMARY KEY(domain,source))')

    def connect(self):
        db = sqlite3.connect(self.paths.data/'memory.sqlite', timeout=30)
        db.execute('PRAGMA journal_mode=WAL')
        return db

    def save_settings(self, settings):
        with self.lock:
            self.settings.update(settings)
            atomic_json(self.paths.data/'settings.json', self.settings)

    def cache_key(self, source, language, context, engine):
        return hashlib.sha256(json.dumps([source,language,context,engine],ensure_ascii=False,sort_keys=True).encode()).hexdigest()

    def cached(self, key):
        with self.connect() as db:
            row = db.execute('SELECT value FROM translations WHERE key=?',(key,)).fetchone()
        return json.loads(row[0]) if row else None

    def put_cache(self, key, value, engine):
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO translations VALUES(?,?,?,?)',(key,json.dumps(value,ensure_ascii=False),engine,time.time()))

    def save_job(self, job):
        with self.lock:
            job['updated'] = time.time()
            job['root_at_save']=str(self.paths.root)
            atomic_json(self.paths.jobs/job['id']/'job.json', job)

    def get_job(self, job_id):
        if not job_id or any(c not in '0123456789abcdef' for c in job_id):
            return None
        return self.rebase(read_json(self.paths.jobs/job_id/'job.json'))

    def rebase(self,job):
        if not job:return job
        old=job.get('root_at_save','')
        if old and pathlib.Path(old)!=self.paths.root:
            for key,value in job.get('outputs',{}).items():
                try:job['outputs'][key]=str(self.paths.root/pathlib.Path(value).relative_to(old))
                except ValueError:pass
            job['root_at_save']=str(self.paths.root)
        return job

    def list_jobs(self):
        return sorted((self.rebase(j) for p in self.paths.jobs.glob('*/job.json') if (j:=read_json(p))),key=lambda j:j.get('created',0))

    def confirm_term(self, domain, source, target, evidence):
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO confirmed_terms VALUES(?,?,?,?)',(domain,source,target,evidence))

    def confirmed(self, domain):
        with self.connect() as db:
            return [dict(source=s,target=t,evidence=e) for s,t,e in db.execute('SELECT source,target,evidence FROM confirmed_terms WHERE domain=?',(domain,))]

def get_api_key():
    try:
        import keyring
        return keyring.get_password('PaperBridge','OpenAI') or ''
    except Exception:
        return ''

def set_api_key(value):
    import keyring
    if value.strip():
        keyring.set_password('PaperBridge','OpenAI', value.strip())
    elif get_api_key():
        keyring.delete_password('PaperBridge','OpenAI')
