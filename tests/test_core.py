import json,pathlib,threading,urllib.error,urllib.request
import pytest
from paperbridge.storage import Paths,Store
from paperbridge.bridge import ZoteroBridge
from paperbridge.engines import split_text,GPTReviewer,BudgetExceeded,parse_json
from paperbridge.pipeline import check_translation
from paperbridge.extract import reading_order

def test_scientific_facts_and_negation_source():
    assert not check_translation('Fuel A did not react at 300 K and 1.25 MPa.','燃料 A 在 300 K 和 1.25 MPa 下未反应。')
    assert check_translation('The yield rose from 0.5 to 2.5%.','产率由 0.5 升至 25%。')
    assert not check_translation('Fig. 1 shows 300 K and 1.25 MPa.','图1显示300 K和1.25 MPa。')


def test_local_review_never_rewrites_and_requires_source_evidence(monkeypatch):
    from paperbridge.engines import LocalModel
    model=LocalModel(None,{})
    monkeypatch.setattr(model,'complete',lambda *a,**kw:json.dumps({'translation':'Wrong rewrite',
        'issues':[{'quote':'absent quote','reason':'invented'}, {'quote':'did not react','reason':'请核对否定'}]}))
    source='The fuel did not react.';translation='燃料未发生反应。'
    assert model.review(source,translation,{})==['本地待核实：did not react — 请核对否定']
    assert translation=='燃料未发生反应。'

def test_long_split_does_not_drop_text():
    source='First result 1.25 MPa. The second result is unchanged. '*90
    pieces=split_text(source,650)
    assert ' '.join(' '.join(pieces).split())==' '.join(source.split())
    assert all(len(p)<=650 for p in pieces)

def test_asymmetric_abstract_precedes_intro():
    def block(name,bbox):return {'name':name,'bbox':bbox}
    items=[block('intro',[35,450,285,630]),block('continuation',[310,450,560,630]),
           block('abstract',[200,285,560,420]),block('keywords',[35,285,125,340]),block('title',[35,160,450,195])]
    names=[x['name'] for x in reading_order(items,595)]
    assert names.index('abstract')<names.index('intro')<names.index('continuation')

def test_context_specific_cache_and_confirmed_terms(tmp_path):
    store=Store(Paths(tmp_path))
    a=store.cache_key('cracking','zh',{'domain':'fuel'},'local')
    b=store.cache_key('cracking','zh',{'domain':'materials'},'local')
    assert a!=b
    store.confirm_term('fuel','cracking','裂解','fuel cracking')
    assert store.confirmed('materials')==[]
    assert store.confirmed('fuel')[0]['target']=='裂解'

def test_portable_job_rebase(tmp_path):
    store=Store(Paths(tmp_path/'new'))
    job={'id':'abcd','root_at_save':str(tmp_path/'old'),'outputs':{'pdf':str(tmp_path/'old/results/a.pdf')}}
    assert store.rebase(job)['outputs']['pdf']==str(tmp_path/'new/results/a.pdf')

def test_bridge_auth_queue_and_reply(tmp_path):
    paths=Paths(tmp_path);paths.discovery=tmp_path/'bridge.json'
    bridge=ZoteroBridge(paths,lambda *x:'job',lambda:[])
    try:
        with pytest.raises(urllib.error.HTTPError) as e:urllib.request.urlopen(bridge.url+'/jobs')
        assert e.value.code==403
        headers={'Authorization':'Bearer '+bridge.token,'Content-Type':'application/json'}
        def call(path,data=None):
            req=urllib.request.Request(bridge.url+path,data=json.dumps(data).encode() if data is not None else None,headers=headers)
            return json.load(urllib.request.urlopen(req))
        call('/bridge/hello',{'test':True})
        result=[]
        thread=threading.Thread(target=lambda:result.append(bridge.request('translate',{'text':'hello'},timeout=4)))
        thread.start();command=call('/bridge/poll')['command']
        call('/bridge/result',{'id':command['id'],'result':{'text':'你好'}})
        thread.join(4)
        assert result==[{'text':'你好'}]
    finally:bridge.close()

def test_budget_prevents_paid_request(monkeypatch):
    from paperbridge.storage import DEFAULTS
    import paperbridge.engines as engines
    monkeypatch.setattr(engines,'get_api_key',lambda:'test-only')
    monkeypatch.setattr(engines,'json_request',lambda *x,**kw:pytest.fail('Must not dispatch a paid request'))
    reviewer=GPTReviewer({**DEFAULTS,'gpt_budget':0},{'gpt_cost':0})
    with pytest.raises(BudgetExceeded):reviewer.review({'id':'s1','source':'test','translation':'测试'}, {})

def test_truncated_json_rejected():
    with pytest.raises(ValueError):parse_json('{"translation":"unfinished')

def test_export_pair_orientation():
    from paperbridge.export import bilingual
    assert bilingual({'language':'zh','source':'中文','translation':'English'})==('English','中文')
    assert bilingual({'language':'en','source':'English','translation':'中文'})==('English','中文')
    from paperbridge.extract import language
    assert language('1 引言')=='zh'
    assert language('5 结论')=='zh'
    assert language('Fig. 5. Ignition delay time.')=='en'


def test_article_terms_preserve_codes_and_reject_lost_markers():
    from paperbridge.engines import TerminologyGuard
    guard=TerminologyGuard('Fuel cracking of RP-3 produced C2H4.',[
        {'source':'cracking','target':'裂解'},{'source':'RP-3','target':'错误代号'}])
    assert 'PBSYM' in guard.text
    assert guard.restore(guard.text)=='Fuel 裂解 of RP-3 produced C2H4.'
    with pytest.raises(ValueError):guard.restore('遗漏标记的译文')


def test_checkpoint_recovers_from_brief_windows_file_lock(tmp_path,monkeypatch):
    import paperbridge.storage as storage
    original=storage.os.replace;calls=[]
    def transient(source,target):
        calls.append(1)
        if len(calls)<3:
            error=PermissionError('temporary sharing violation');error.winerror=32;raise error
        return original(source,target)
    monkeypatch.setattr(storage.os,'replace',transient)
    monkeypatch.setattr(storage.time,'sleep',lambda _:None)
    storage.atomic_json(tmp_path/'job.json',{'progress':.5})
    assert json.loads((tmp_path/'job.json').read_text('utf-8'))=={'progress':.5}
    assert len(calls)==3
