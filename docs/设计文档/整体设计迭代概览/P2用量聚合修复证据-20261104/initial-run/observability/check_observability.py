"""Isolated dynamic observation diagnostics. No network, service, CLI or real provider."""
import sys
from pathlib import Path
RUN_ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(RUN_ROOT))
import check_support as support
import time
WORKSPACE=support.isolated_workspace('observability','workspace_'+str(time.time_ns()))
GUARD=support.protect(WORKSPACE)
import json
import sqlite3
import threading
import uuid
from types import SimpleNamespace as NS
from unittest.mock import patch
from alice import llm,loop,metrics,observation,channels,persona_context
from alice.config import Config
from alice.store import Store

ROOT=WORKSPACE.parent/('fixtures_'+str(time.time_ns()))
ROOT.mkdir()
RESULTS=support.Results('observability')
FACTS={}
SECRET='synthetic_SECRET_sentinel_abc123'
SENSITIVE='https://invalid.example/private?api_key='+SECRET+' private prompt sentinel'


def fact(name,value):
    FACTS[name]=value
    (ROOT/(name+'.json')).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    return value


class ProviderError(Exception):
    def __init__(self,status):
        super().__init__(SENSITIVE)
        self.status_code=status


class FakeProvider:
    def __init__(self,sequence):
        self.sequence=list(sequence);self.calls=[];self.chat=NS(completions=self)
    def create(self,**kwargs):
        self.calls.append(kwargs)
        if not self.sequence:raise AssertionError('Unexpected extra provider request')
        item=self.sequence.pop(0)
        if isinstance(item,BaseException):raise item
        return item


class FakeStream:
    def __init__(self,items):self.items=items;self.closed=False
    def __iter__(self):
        for item in self.items:
            if isinstance(item,BaseException):raise item
            yield item
    def close(self):self.closed=True


def receipt(prompt=20,completion=8,*,unknown=False,text='Synthetic final reply.'):
    return NS(model='fixture-receipt-model',usage=None if unknown else NS(prompt_tokens=prompt,completion_tokens=completion),
              choices=[NS(message=NS(content=text,tool_calls=None,role='assistant'),finish_reason='stop')])


def chunk(prompt=None,completion=None,*,has_usage=True,text='partial text'):
    return NS(model='fixture-stream-model',usage=NS(prompt_tokens=prompt,completion_tokens=completion) if has_usage else None,
              choices=[NS(delta=NS(content=text,tool_calls=None))])


def fixture(name):
    path=ROOT/(name+'.sqlite3');store=Store(path);store.conn.executescript(channels.SCHEMA)
    store.meta_set('session_epoch','fixture_epoch')
    cfg=Config();cfg.llm.chat_model='fixture-requested-model';cfg.llm.cheap_model='fixture-cheap-model'
    cfg.llm.base_url='https://invalid.example';cfg.context.transform_review_enabled=False
    return store,cfg,path


def observe(store,purpose='fixture',request='request_fixture'):
    return {'store':store,'purpose':purpose,'request_id':request,'context':{'fixture':'synthetic'}}


def events(store,request=None):
    # Windows timestamps can tie when backoff is patched; attempt is the
    # authoritative order inside one call, not the observer's newest-first SQL.
    return sorted(observation.llm_calls(store,request_id=request)['items'],key=lambda e:(e['ts'],e['attempt']))


def no_secrets(value):
    raw=json.dumps(value,ensure_ascii=False)
    assert SECRET not in raw and 'private prompt sentinel' not in raw and 'api_key=' not in raw


def retry_attempts_unknown_usage():
    store,cfg,_=fixture('retry_unknown');provider=FakeProvider([ProviderError(429),TimeoutError(SENSITIVE),receipt(unknown=True)])
    delays=[]
    with patch.object(llm,'time',NS(monotonic=time.monotonic,sleep=delays.append)):
        result=llm.chat(provider,cfg,[{'role':'user','content':'synthetic input'}],observation=observe(store,request='retry_request'))
    rows=events(store,'retry_request')
    assert len(provider.calls)==3 and len(rows)==3 and len({r['call_id'] for r in rows})==1
    assert [r['attempt'] for r in rows]==[1,2,3] and [r['status'] for r in rows]==['error','error','ok']
    assert all(r['usage']=='unknown' and r['usage_state']=='unknown' for r in rows)
    assert rows[0]['error_type']=='请求受限或账户额度不足' and rows[1]['error_type']=='连接超时，请检查网络后重试'
    assert rows[2]['model']=='fixture-receipt-model' and rows[2]['model_source']=='receipt'
    assert delays==[2,4] and result.usage is None
    no_secrets(rows)
    return fact('retry_attempts_unknown_usage',{'calls':len(provider.calls),'backoff_replaced_seconds':delays,'events':rows})


def terminal_error_and_cancel():
    store,cfg,_=fixture('terminal_error');provider=FakeProvider([ProviderError(401) for _ in range(3)]);caught=None
    with patch.object(llm,'time',NS(monotonic=time.monotonic,sleep=lambda seconds:None)):
        try:llm.chat(provider,cfg,[],observation=observe(store,request='failure_request'))
        except RuntimeError as exc:caught=str(exc)
    rows=events(store,'failure_request');assert caught and len(rows)==3 and all(r['status']=='error' for r in rows)
    no_secrets({'message':caught,'events':rows})
    cancelled=FakeProvider([KeyboardInterrupt(SENSITIVE)])
    try:llm.chat(cancelled,cfg,[],observation=observe(store,request='cancel_request'))
    except KeyboardInterrupt:pass
    else:raise AssertionError('Cancellation was swallowed')
    cancel_rows=events(store,'cancel_request')
    assert len(cancelled.calls)==1 and len(cancel_rows)==1 and cancel_rows[0]['status']=='cancelled'
    no_secrets(cancel_rows)
    return fact('terminal_error_and_cancel',{'terminal_error':caught,'failed_attempts':rows,'cancelled':cancel_rows})


def successful_partial_stream_receipt():
    store,cfg,_=fixture('partial_field');stream=FakeStream([chunk(9,None)]);provider=FakeProvider([stream]);usage={};deltas=[]
    reply=loop._stream_round(provider,cfg,[],deltas.append,usage_out=usage,tools=[],observation=observe(store,'main_stream'))
    rows=events(store)
    assert reply.content=='partial text' and stream.closed and rows[0]['status']=='ok'
    assert rows[0]['usage']=={'prompt':9} and rows[0]['usage_state']=='partial_receipt'
    assert usage=={'prompt':9,'incomplete':True}
    return fact('successful_partial_stream_receipt',{'aggregate':usage,'events':rows,'closed':stream.closed})


def streaming_unknown_and_failure():
    store,cfg,_=fixture('stream_unknown');stream=FakeStream([chunk(has_usage=False),ConnectionError(SENSITIVE)])
    provider=FakeProvider([stream]);usage={}
    try:loop._stream_round(provider,cfg,[],lambda text:None,usage_out=usage,tools=[],observation=observe(store,'main_stream'))
    except ConnectionError:pass
    else:raise AssertionError('Stream failure was swallowed')
    rows=events(store);assert stream.closed and rows[0]['status']=='error'
    assert rows[0]['usage']=='unknown' and usage=={'incomplete':True}
    no_secrets(rows)
    return fact('streaming_unknown_and_failure',{'aggregate':usage,'events':rows,'closed':stream.closed})


def queue_flush_does_not_commit_business():
    store,cfg,path=fixture('transaction');store.conn.execute('BEGIN')
    store.conn.execute("INSERT INTO meta(key,value) VALUES('uncommitted_business','sentinel')")
    metrics.record_llm_call(store,'fixture',time.monotonic(),receipt(),request_id='outer_tx',call_id='fixture_call')
    changes=store.conn.total_changes;metrics.flush_calls(store)
    assert store.conn.in_transaction and store.conn.total_changes==changes and len(store._llm_events)==1
    observer=sqlite3.connect(path)
    assert observer.execute("SELECT value FROM meta WHERE key='uncommitted_business'").fetchone() is None
    assert observer.execute("SELECT COUNT(*) FROM turn_metrics WHERE module='llm_call:fixture'").fetchone()[0]==0
    store.conn.rollback();assert len(store._llm_events)==1
    metrics.flush_calls(store)
    assert not store.conn.in_transaction and not store._llm_events
    assert observer.execute("SELECT value FROM meta WHERE key='uncommitted_business'").fetchone() is None
    assert observer.execute("SELECT COUNT(*) FROM turn_metrics WHERE module='llm_call:fixture'").fetchone()[0]==1
    observer.close()
    return fact('queue_flush_does_not_commit_business',{'outer_business_rolled_back':True,'event_survived_in_queue':True,
                'event_later_flushed':True,'real_sqlite_independent_reader':True})


def observer_readonly_no_flush():
    store,cfg,_=fixture('readonly');metrics.record_llm_call(store,'fixture',time.monotonic(),receipt(),request_id='committed',call_id='c1')
    metrics.flush_calls(store)
    metrics.record_llm_call(store,'fixture',time.monotonic(),None,request_id='pending',call_id='c2')
    store.conn.execute('BEGIN');store.conn.execute("INSERT INTO meta(key,value) VALUES('business_pending','1')")
    changes=store.conn.total_changes;queued=list(store._llm_events);attempted=[]
    blocked={sqlite3.SQLITE_INSERT,sqlite3.SQLITE_UPDATE,sqlite3.SQLITE_DELETE,sqlite3.SQLITE_TRANSACTION}
    def authorizer(action,arg1,arg2,database,trigger):
        if action in blocked:attempted.append([action,arg1,arg2]);return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK
    store.conn.set_authorizer(authorizer)
    try:
        with patch.object(metrics,'flush_calls',side_effect=AssertionError('Observer must not flush')):
            all_rows=observation.llm_calls(store)
            selected=observation.llm_calls(store,request_id='pending')
    finally:store.conn.set_authorizer(None)
    assert len(all_rows['items'])==2 and len(selected['items'])==1 and selected['items'][0]['storage']=='pending'
    assert not attempted and changes==store.conn.total_changes and queued==list(store._llm_events) and store.conn.in_transaction
    store.conn.rollback()
    return fact('observer_readonly_no_flush',{'events':all_rows,'write_authorizer_attempts':attempted,
                'queue_unchanged':True,'outer_transaction_still_open':True})


def observation_write_failure_no_model_repeat():
    store,cfg,_=fixture('write_failure');provider=FakeProvider([receipt()])
    store.conn.executescript("CREATE TRIGGER diagnostic_block_receipts BEFORE INSERT ON turn_metrics WHEN NEW.module LIKE 'llm_call:%' BEGIN SELECT RAISE(FAIL,'injected_observation_write_failure'); END;")
    result=llm.chat(provider,cfg,[],observation=observe(store,request='write_failure_request'))
    for _ in range(3):metrics.flush_calls(store)
    assert result.choices[0].message.content and len(provider.calls)==1 and len(store._llm_events)==1
    assert store.conn.execute("SELECT COUNT(*) FROM turn_metrics WHERE module LIKE 'llm_call:%'").fetchone()[0]==0
    pending=events(store)
    store.conn.execute('DROP TRIGGER diagnostic_block_receipts');store.conn.commit();metrics.flush_calls(store)
    assert len(provider.calls)==1 and len(events(store))==1 and not store._llm_events
    return fact('observation_write_failure_no_model_repeat',{'sqlite_fault':'trigger RAISE(FAIL) on receipt INSERT',
                'failed_flushes':3,'provider_calls':len(provider.calls),'pending_before_recovery':pending,'after_recovery':events(store)})


def full_loop(name,sequence,stream=False):
    store,cfg,_=fixture(name);provider=FakeProvider(sequence);request='request_'+uuid.uuid4().hex;now=time.time()
    store.conn.execute('INSERT INTO channel_requests(request_id,session_epoch,channel_id,source_key,source_json,payload_hash,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)',
        (request,'fixture_epoch','gui',request,'{}','fixture','accepted',now,now));store.conn.commit()
    snapshot=persona_context.parse_snapshot('## 底色 · 气质与风格\n- 用简明直接的话回应。\n')
    usage={};deltas=[];delays=[]
    with patch.object(persona_context,'load_persona_snapshot',return_value=snapshot),patch.object(llm,'time',NS(monotonic=time.monotonic,sleep=delays.append)):
        reply=loop.chat_turn(store,provider,cfg,'请回应这条隔离诊断消息。',request_id=request,usage_out=usage,
                             on_delta=deltas.append if stream else None,lock=threading.RLock())
    records=events(store,request)
    basis=observation.context_basis(store)['items'][0]['basis']
    turns=[dict(r) for r in store.conn.execute('SELECT role,content FROM turns ORDER BY id')]
    assert reply=='Synthetic final reply.' and len(turns)==2 and turns[-1]['content']==reply
    assert basis['request_id']==request and basis['usage']==usage
    return {'reply':reply,'aggregate':usage,'events':records,'context_basis_usage':basis['usage'],
            'provider_calls':len(provider.calls),'backoff_replaced_seconds':delays,'turns':turns,'streamed_deltas':deltas,
            'stubs':['synthetic parsed persona snapshot loader','fake provider','backoff sleep only'],
            'real_path':'loop.chat_turn -> steps.drive -> assembly -> provider methods -> SQLite turns/context_basis/receipts'}


def main_loop_unknown_stream_fallback():
    data=full_loop('main_stream_unknown',[FakeStream([ConnectionError(SENSITIVE)]),receipt()],True)
    fact('main_loop_unknown_stream_fallback',data)
    assert data['provider_calls']==2 and data['aggregate']=={'incomplete':True,'prompt':20,'completion':8}
    assert [e['purpose'] for e in data['events']]==['main_stream','main_fallback']
    assert data['events'][0]['status']=='error' and data['events'][0]['usage']=='unknown'
    assert data['events'][1]['fallback'] and len({e['call_id'] for e in data['events']})==2
    no_secrets(data['events']);return data


def main_loop_partial_stream_aggregate():
    data=full_loop('main_stream_partial',[FakeStream([chunk(10,4),ConnectionError(SENSITIVE)]),receipt()],True)
    fact('main_loop_partial_stream_aggregate',data)
    assert data['provider_calls']==2 and data['events'][0]['usage_state']=='partial_receipt'
    assert data['aggregate'].get('incomplete') is True, 'Partial stream receipt must keep aggregate incomplete; observed '+json.dumps(data['aggregate'])
    return data


def main_loop_retry_unknown_aggregate():
    data=full_loop('main_retry',[ProviderError(429),TimeoutError(SENSITIVE),receipt()])
    fact('main_loop_retry_unknown_aggregate',data)
    assert data['provider_calls']==3 and [r['attempt'] for r in data['events']]==[1,2,3]
    assert [r['usage'] for r in data['events'][:2]]==['unknown','unknown']
    assert data['aggregate'].get('incomplete') is True, 'Unknown usage on failed attempts must keep aggregate incomplete; observed '+json.dumps(data['aggregate'])
    return data


for name,callback in [
    ('retry_attempts_unknown_usage',retry_attempts_unknown_usage),
    ('terminal_error_and_cancel',terminal_error_and_cancel),
    ('successful_partial_stream_receipt',successful_partial_stream_receipt),
    ('streaming_unknown_and_failure',streaming_unknown_and_failure),
    ('queue_flush_does_not_commit_business',queue_flush_does_not_commit_business),
    ('observer_readonly_no_flush',observer_readonly_no_flush),
    ('observation_write_failure_no_model_repeat',observation_write_failure_no_model_repeat),
    ('main_loop_unknown_stream_fallback',main_loop_unknown_stream_fallback),
    ('main_loop_partial_stream_aggregate',main_loop_partial_stream_aggregate),
    ('main_loop_retry_unknown_aggregate',main_loop_retry_unknown_aggregate),
]:RESULTS.check(name,callback)

data=RESULTS.save(workspace=str(WORKSPACE),fixtures=str(ROOT),facts=FACTS,python=sys.version,
    guard_attempts=GUARD,production_source_mutated=False,real_provider_calls=0,real_subprocesses=0,
    evidence='Real copied llm/loop/metrics/observation with SQLite; provider responses/exceptions and persona loader are synthetic; retry sleep replaced.',
    limitations=['No real provider/network/GUI/service','No live persona/config/user data',
                 'Synthetic receipts verify accounting/control flow, not provider charging or model behavior'])
print(json.dumps({'passed':data['passed'],'failed':data['failed'],'guard_attempts':GUARD,
                  'results':str(RUN_ROOT/'observability'/'results.json')},ensure_ascii=False))
raise SystemExit(bool(data['failed']))
