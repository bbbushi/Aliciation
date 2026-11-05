"""Temporary architecture diagnostic; no real provider, adapter, service or CLI."""
import sys
from pathlib import Path
RUN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RUN_ROOT))
import check_support as support
import time
WORKSPACE = support.isolated_workspace('permissions', 'workspace_' + str(time.time_ns()))
GUARD = support.protect(WORKSPACE)

import hashlib
import io
import json
import os
import threading
import uuid
from types import SimpleNamespace as NS
from unittest.mock import patch
from alice.store import Store
from alice.worker_tasks import WorkerLedger
from alice.worker_permissions import WorkerPermissionsMixin, PermissionAnswerConflict, BINDING, action_digest
from alice.worker_reports import WorkerReportsMixin
from alice import worker_reports, metrics, channels
from addons.claude_agent import gate_server

RESULTS = support.Results('permissions')
FIXTURES = WORKSPACE.parent / ('fixtures_' + str(time.time_ns()))
FIXTURES.mkdir()


def expect_error(kind, callback):
    try:
        callback()
    except kind as exc:
        return type(exc).__name__
    raise AssertionError('Expected ' + kind.__name__)


class ChannelStub:
    def __init__(self, epoch):
        self.epoch, self.reserved, self.released = epoch, [], []
    def reserve(self, *args, **kwargs):
        token = str(uuid.uuid4())
        self.reserved.append(token)
        return token
    def release(self, token):
        self.released.append(token)


class ProviderStub:
    def __init__(self):
        self.calls = []
        self.chat = NS(completions=self)
    def create(self, **kwargs):
        self.calls.append(kwargs)
        return NS(model='diagnostic-receipt-model', usage=NS(prompt_tokens=17, completion_tokens=9),
                  choices=[NS(finish_reason='stop', message=NS(role='assistant',
                      content='协作者自报已整理资料，尚未复核。', tool_calls=None, function_call=None))])


class Host(WorkerPermissionsMixin, WorkerReportsMixin):
    def __init__(self, name):
        self.root = FIXTURES / name
        self.root.mkdir()
        store = Store(self.root / 'fixture.sqlite3')
        self.service = NS(store=store, _lock=threading.RLock(), _channels=ChannelStub('epoch_diagnostic'),
                          client=ProviderStub(), cfg=NS(llm=NS(chat_model='diagnostic-requested-model',
                                                            base_url='https://invalid.example')))
        self.ledger = WorkerLedger(store)
        store.conn.executescript(channels.SCHEMA)
        store.meta_set('session_epoch', self.service._channels.epoch)
        store.conn.execute('UPDATE claude_policy SET enabled=1,native_sandbox_ack=1,workspace_root=?', (str(self.root),))
        store.conn.execute('UPDATE worker_policy SET enabled=1,max_runs=100,workspace_root=?', (str(self.root),))
        store.conn.commit()
        self.closing = self.paused = False
        self.stop = threading.Event()
        self.generation = 0
        self.report_thread = None
        self.claude_threads = {}
        self.permission_grants = {}
    def _worker_policy_locked(self, worker):
        assert worker == 'claude-agent'
        return dict(self.ledger.conn.execute('SELECT * FROM claude_policy').fetchone())
    def task(self, worker='claude', state='running', active=True, suffix=None):
        task_id = 'task_' + (suffix or uuid.uuid4().hex)
        attempt_id, message_id, origin = ['id_' + uuid.uuid4().hex for _ in range(3)]
        now = time.time()
        with self.ledger.conn:
            self.ledger.conn.execute('INSERT INTO worker_tasks(task_id,epoch,policy_revision,state,title,created_at,updated_at,expires_at,bridge_instance,workspace_root) VALUES(?,?,?,?,?,?,?,?,?,?)',
                (task_id,self.service._channels.epoch,1,state,'synthetic diagnostic task',now,now,now+3600,
                 'local:claude-agent' if worker=='claude' else 'diagnostic:dsh',str(self.root)))
            self.ledger.conn.execute('INSERT INTO worker_attempts(attempt_id,task_id,message_id,state,created_at,updated_at,originating_request_id) VALUES(?,?,?,?,?,?,?)',
                (attempt_id,task_id,message_id,state,now,now,origin))
            self.ledger.conn.execute('INSERT INTO worker_messages(message_id,task_id,attempt_id,actor,body,state,created_at) VALUES(?,?,?,?,?,?,?)',
                (message_id,task_id,attempt_id,'alice','Synthetic public task input','completed',now))
            self.ledger.conn.execute('INSERT INTO channel_requests(request_id,session_epoch,channel_id,source_key,source_json,payload_hash,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)',
                (origin,self.service._channels.epoch,'gui',origin,'{}','diagnostic','completed',now,now))
        if active and worker=='claude': self.claude_threads[task_id] = object()
        directory = self.root/'gate'/task_id/attempt_id
        directory.mkdir(parents=True)
        return NS(task_id=task_id, attempt_id=attempt_id, message_id=message_id, origin=origin,
                  directory=directory, sandbox=self.root/('claude_task_'+task_id[:8]), worker=worker)
    def pending(self, task, *, tool='Write', data=None, expires=None):
        data = {'file_path': str(task.sandbox/'result.txt'), 'content':'first'} if data is None else data
        req = dict(id='pq_'+uuid.uuid4().hex,ts=time.time(),task_id=task.task_id,attempt_id=task.attempt_id,
                   epoch=self.service._channels.epoch,policy_revision=1,worker_id='claude-agent',
                   expires_at=time.time()+600 if expires is None else expires,tool=tool,input=data,reason_hint='diagnostic')
        req['action_digest'] = action_digest(req)
        (task.directory/(req['id']+'.pending.json')).write_text(json.dumps(req),encoding='utf-8')
        return req
    def listed(self, req):
        return next(x for x in self.permissions()['pending'] if x['id']==req['id'])
    def answer(self, req, decision):
        return self.permissions('answer', {k:req[k] for k in BINDING}|{'decision':decision})
    def read_answer(self, task, req):
        return json.loads((task.directory/'answers'/(req['id']+'.json')).read_text(encoding='utf-8'))
    def poll_cache(self, task):
        with self.service._lock: self._answer_cached_permissions_locked(task.directory,task.task_id)
    def add_report_reply(self, task):
        with self.ledger.conn:
            self.ledger.conn.execute('INSERT INTO worker_messages(message_id,task_id,attempt_id,actor,body,state,created_at) VALUES(?,?,?,?,?,?,?)',
                ('reply_'+uuid.uuid4().hex,task.task_id,task.attempt_id,'agent' if task.worker=='claude' else 'dsh',
                 'Synthetic independent result text for reporting.','completed',time.time()))
        self.queue_worker_report_locked(task.task_id,task.attempt_id,'succeeded')


def first_answer_atomic():
    h=Host('atomic'); t=h.task(); req=h.listed(h.pending(t)); barrier=threading.Barrier(16)
    records=[]; lock=threading.Lock()
    def writer(index):
        decision='allow_task' if index%2 else 'deny'
        barrier.wait()
        try:
            h._write_permission_answer_locked(h.root/'gate',req,decision)
            item={'decision':decision,'result':'accepted'}
        except PermissionAnswerConflict:
            item={'decision':decision,'result':'conflict'}
        except Exception as exc: item={'decision':decision,'result':type(exc).__name__}
        with lock: records.append(item)
    threads=[threading.Thread(target=writer,args=(i,)) for i in range(16)]
    for thread in threads: thread.start()
    for thread in threads: thread.join(10); assert not thread.is_alive()
    final=h.read_answer(t,req)
    assert all(x['result']==('accepted' if x['decision']==final['decision'] else 'conflict') for x in records)
    assert len(list((t.directory/'answers').glob('*.json')))==1
    assert not list((t.directory/'answers').glob('*.tmp'))
    return {'os_link':'real Windows filesystem hard-link publication','writers':records,'winner':final['decision'],'single_immutable_answer':True}


def same_answer_replay():
    h=Host('replay'); t=h.task(); raw=h.pending(t); req=h.listed(raw)
    first=h.answer(req,'allow_task'); before=h.read_answer(t,req); grants=dict(h.permission_grants)
    (t.directory/(req['id']+'.pending.json')).unlink()
    replay=h.answer(req,'allow_task'); after=h.read_answer(t,req)
    assert replay['replayed'] and before==after and grants==h.permission_grants
    conflict=expect_error(PermissionAnswerConflict,lambda:h.answer(req,'deny'))
    return {'first':first,'replay':replay,'different_answer':conflict,'original_expiry_preserved':True}


def gate_race(revoke=False):
    h=Host('gate_revoke' if revoke else 'gate_allow'); t=h.task(); t.sandbox.mkdir()
    output,errors=io.StringIO(),io.StringIO(); waiting=threading.Event(); release=threading.Event(); done=[]
    def controlled_sleep(seconds):
        waiting.set()
        if not release.wait(10): raise TimeoutError('harness gate barrier')
    gate_clock=NS(time=time.time,strftime=time.strftime,sleep=controlled_sleep)
    message={'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'arguments':{'tool_name':'Write',
             'input':{'file_path':str(t.sandbox/'result.txt'),'content':'first'}}}}
    gate_io=NS(stdin=io.StringIO(json.dumps(message)+'\n'),stdout=output,stderr=errors)
    argv=['gate-diagnostic','--audit',str(t.directory/'audit.jsonl'),'--sandbox',str(t.sandbox),
          '--gate-dir',str(t.directory),'--task-id',t.task_id,'--attempt-id',t.attempt_id,
          '--epoch',h.service._channels.epoch,'--policy-revision','1']
    def run_gate():
        try: done.append({'exit':gate_server.main()})
        except BaseException as exc: done.append({'error':repr(exc)})
    poll_records=[]; stop_poll=threading.Event()
    def poll():
        while not stop_poll.wait(.2):
            try:
                h.poll_cache(t)
                poll_records.append(dict(h.permission_scan_status))
            except Exception as exc: poll_records.append({'error':repr(exc)})
    with patch.object(gate_server,'sys',gate_io),patch.object(gate_server,'time',gate_clock),patch.object(sys,'argv',argv):
        thread=threading.Thread(target=run_gate); thread.start()
        try:
            assert waiting.wait(5),'gate did not produce pending request'
            req=h.permissions()['pending'][0]
            h.answer(req,'allow_task'); first=h.read_answer(t,req)
            cache_thread=threading.Thread(target=poll); cache_thread.start()
            try:
                time.sleep(.46)
                assert len(poll_records)>=2 and all(not r.get('error') for r in poll_records)
                assert all(r['answered_pending']==1 for r in poll_records)
                if revoke:
                    with h.service._lock,h.ledger.conn:
                        h.ledger.conn.execute('UPDATE worker_tasks SET cancel_requested=1,revoked=1 WHERE task_id=?',(t.task_id,))
                        h._revoke_permission_files_locked(h.ledger.task(t.task_id,h.service._channels.epoch))
                    expect_error(ValueError,lambda:h.answer(req,'allow_task'))
                release.set(); thread.join(5)
                assert not thread.is_alive() and done==[{'exit':0}],done
            finally:
                stop_poll.set(); cache_thread.join(3)
        finally:
            release.set(); thread.join(5)
    response=json.loads(json.loads(output.getvalue())['result']['content'][0]['text'])
    assert response['behavior']==('deny' if revoke else 'allow'),response
    assert h.read_answer(t,req)==first
    assert not (t.directory/(req['id']+'.pending.json')).exists()
    return {'gate_response':response,'cache_poll_seconds':.2,'cache_polls':poll_records,
            'timing_injection':'gate sleep held until two real 0.2s cache polls; production gate main executed in process',
            'first_answer_preserved':True,'revoked_after_answer_before_consume':revoke}


def cache_exact_scope():
    h=Host('scope'); t=h.task(); r=h.listed(h.pending(t)); h.answer(r,'allow_task')
    identical=h.pending(t,data=dict(r['input'])); changed=h.pending(t,data={**r['input'],'content':'second'})
    sibling=h.pending(t,data={**r['input'],'file_path':str(t.sandbox/'other.txt')})
    other=h.task(); other_task=h.pending(other,data=dict(r['input']))
    h.poll_cache(t)
    h.poll_cache(other)
    assert h.read_answer(t,identical)['decision']=='allow_once'
    for task,req in ((t,changed),(t,sibling),(other,other_task)):
        assert not (task.directory/'answers'/(req['id']+'.json')).exists()
    shell=h.listed(h.pending(t,tool='Bash',data={'command':'echo diagnostic'})); result=h.answer(shell,'allow_task')
    shell_next=h.pending(t,tool='Bash',data={'command':'echo diagnostic'});h.poll_cache(t)
    assert result['scope']=='once' and not (t.directory/'answers'/(shell_next['id']+'.json')).exists()
    return {'same_full_action':'cached allow_once','changed_body':'pending','changed_file':'pending',
            'different_task':'pending','shell_allow_task':'once only'}


def authorization_boundaries():
    facts=[]
    for name in ['expired_request','expired_grant','policy_changed','task_revoked','cancel_requested','epoch_changed','attempt_ended','host_stopping']:
        h=Host('boundary_'+name); t=h.task();req=h.listed(h.pending(t));h.answer(req,'allow_task')
        fresh=h.pending(t,data=dict(req['input']))
        if name=='expired_request':
            p=t.directory/(fresh['id']+'.pending.json');fresh['expires_at']=time.time()-1;p.write_text(json.dumps(fresh),encoding='utf-8')
        elif name=='expired_grant': h.permission_grants={key:time.time()-1 for key in h.permission_grants}
        elif name=='policy_changed': h.ledger.conn.execute('UPDATE claude_policy SET revision=2')
        elif name=='task_revoked': h.ledger.conn.execute('UPDATE worker_tasks SET revoked=1 WHERE task_id=?',(t.task_id,))
        elif name=='cancel_requested': h.ledger.conn.execute('UPDATE worker_tasks SET cancel_requested=1 WHERE task_id=?',(t.task_id,))
        elif name=='epoch_changed': h.service._channels.epoch='different_epoch'
        elif name=='attempt_ended': h.ledger.conn.execute("UPDATE worker_attempts SET state='succeeded' WHERE attempt_id=?",(t.attempt_id,))
        elif name=='host_stopping': h.stop.set()
        h.ledger.conn.commit();h.poll_cache(t)
        assert not (t.directory/'answers'/(fresh['id']+'.json')).exists(),name
        if name not in {'expired_request','expired_grant'}:
            expect_error(ValueError,lambda:h.answer(req,'allow_task'))
        facts.append({'condition':name,'new_cached_authorization':False})
    return facts


def historical_pending_isolation():
    h=Host('history'); t=h.task(); req=h.pending(t)
    historical=0
    for index in range(900):
        directory=h.root/'gate'/('old_task_'+str(index))/('old_attempt_'+str(index));directory.mkdir(parents=True)
        (directory/'pq_old.pending.json').write_text('{}',encoding='utf-8'); historical+=1
    old_attempt=t.directory.parent/'ended_attempt';old_attempt.mkdir()
    for index in range(160): (old_attempt/('old_'+str(index)+'.pending.json')).write_text('{}',encoding='utf-8')
    out=h.permissions()
    assert [x['id'] for x in out['pending']]==[req['id']]
    assert out['scan']['scanned_entries']==1 and not out['scan']['exhausted']
    return {'old_tasks':historical,'old_same_task_attempt_pending':160,'active_scan':out['scan']}


def scan_exhaustion():
    facts=[]
    h=Host('scan_pending');t=h.task()
    for _ in range(140):h.pending(t)
    out=h.permissions(); assert out['scan']['exhausted'] and len(out['pending'])<=128
    facts.append({'cause':'valid pending volume','returned':len(out['pending']),'scan':out['scan']})
    h=Host('scan_entries');t=h.task()
    for index in range(520):(t.directory/('junk_'+str(index)+'.txt')).write_text('',encoding='utf-8')
    out=h.permissions();assert out['scan']['exhausted'] and out['scan']['scanned_entries']<=512
    facts.append({'cause':'directory entries','scan':out['scan']})
    h=Host('scan_active')
    for _ in range(9):h.task()
    out=h.permissions();assert out['scan']['exhausted'] and out['scan']['reason']=='active_attempt_limit'
    facts.append({'cause':'active attempt count','scan':out['scan']})
    h=Host('scan_malformed');t=h.task();(t.directory/'malformed.pending.json').write_text('{',encoding='utf-8')
    out=h.permissions();assert out['scan']['read_errors']==1
    facts.append({'cause':'malformed request','scan':out['scan']})
    return facts


def answer_fault_locality():
    h=Host('answer_fault');t=h.task();req=h.listed(h.pending(t));h.answer(req,'allow_task');h.pending(t,data=dict(req['input']))
    for error,code in ((PermissionAnswerConflict('injected competing first answer'),'first_answer_preserved'),
                       (OSError('injected file write failure'),'answer_write_unavailable')):
        with patch.object(h,'_write_permission_answer_locked',side_effect=error): h.poll_cache(t)
        assert h.permission_answer_diagnostic['last_code']==code
    return {'fault_injection':['PermissionAnswerConflict','OSError'],'diagnostic':h.permission_answer_diagnostic,
            'exception_escaped_cache_poll':False}


def style_failure_once_and_later_report():
    h=Host('reports');bad=h.task(state='succeeded',active=False);h.add_report_reply(bad)
    with patch.object(worker_reports,'_style_excerpt',side_effect=ValueError('injected malformed expression')):
        h._run_worker_report(bad.attempt_id,h.service._channels.epoch,h.generation)
        h._run_worker_report(bad.attempt_id,h.service._channels.epoch,h.generation)
    row=dict(h.ledger.conn.execute('SELECT * FROM worker_reports WHERE attempt_id=?',(bad.attempt_id,)).fetchone())
    assert row['report_state']=='delivered' and row['kind']=='status' and not h.service.client.calls
    assert h.ledger.conn.execute('SELECT count(*) FROM turns').fetchone()[0]==1
    assert h.ledger.conn.execute("SELECT count(*) FROM worker_events WHERE type='report_style_unavailable'").fetchone()[0]==1
    identities=[]
    for worker in ('claude','dsh'):
        good=h.task(worker=worker,state='succeeded',active=False);h.add_report_reply(good)
        with patch.object(worker_reports,'_style_excerpt',return_value='Synthetic valid style'):
            h._run_worker_report(good.attempt_id,h.service._channels.epoch,h.generation)
            h._run_worker_report(good.attempt_id,h.service._channels.epoch,h.generation)
        report=dict(h.ledger.conn.execute('SELECT * FROM worker_reports WHERE attempt_id=?',(good.attempt_id,)).fetchone())
        assert report['report_state']=='delivered' and report['kind']=='alice_summary'
        identities.append((worker,good))
    assert len(h.service.client.calls)==2
    records=[json.loads(r[0]) for r in h.ledger.conn.execute("SELECT detail FROM turn_metrics WHERE module='llm_call:worker_summary' ORDER BY id")]
    assert len(records)==2 and len({r['call_id'] for r in records})==2
    for record,(worker,task) in zip(records,identities):
        assert record['request_id']==task.origin and record['context']['task_id']==task.task_id
        assert record['context']['worker_attempt_id']==task.attempt_id
        assert record['context']['worker_id']==worker+'-agent' and record['model']=='diagnostic-receipt-model'
        assert record['usage']=={'prompt':17,'completion':9}
    assert len(h.service._channels.reserved)==len(h.service._channels.released)==6
    assert h.ledger.conn.execute('SELECT count(*) FROM memories').fetchone()[0]==0
    assert all(r[0]=='（这一轮使用了协作任务资料；正文仅在 GUI 内、任务资料仍获准时可见。）' for r in h.ledger.conn.execute('SELECT content FROM turns'))
    return {'style_failure':'ValueError injected at expression boundary','fallback_deliveries':1,'style_error_events':1,
            'later_reports':2,'synthetic_provider_calls':2,'metrics':records,'ordinary_memory_writes':0,
            'channel_slot':'stub reserve/release; six matched','real_model_calls':0}


def worker_usage_and_visibility_identity():
    h=Host('usage_identity');c=h.task();d=h.task(worker='dsh');cn=h.task()
    h.ledger.record_usage(d.attempt_id,{'input_tokens':100,'output_tokens':50})
    h.ledger.conn.execute('UPDATE worker_attempts SET usage_json=?,sent=1,launched_at=? WHERE attempt_id=?',
        (json.dumps({'source':'claude_reported','input_tokens':3,'output_tokens':4,'cost_usd':0.0,'cost_known':True}),time.time(),c.attempt_id))
    h.ledger.conn.execute('UPDATE worker_attempts SET sent=1 WHERE attempt_id=?',(cn.attempt_id,))
    h.ledger.conn.commit()
    assert h.ledger.runs_used('claude-agent')==2 and h.ledger.runs_used('dsh-agent')==1
    cu,du,unknown=[h.ledger.usage(t.task_id) for t in (c,d,cn)]
    assert cu['source']=='claude_reported' and cu['reported_input_tokens']==3 and cu['cost_known'] and cu['reported_cost_usd']==0
    assert du['source']=='dsh_reported' and du['reported_input_tokens']==100 and not du['cost_known']
    assert not unknown['cost_known'] and unknown['unknown_launch_attempts']==1
    h.ledger.conn.execute('UPDATE worker_policy SET enabled=0,revision=2');h.ledger.conn.commit()
    assert h.ledger.visible(h.ledger.task(c.task_id,h.service._channels.epoch))
    assert not h.ledger.visible(h.ledger.task(d.task_id,h.service._channels.epoch))
    return {'runs_used':{'claude':2,'dsh':1},'claude_usage':cu,'dsh_usage':du,'unknown_usage':unknown,
            'DSH_policy_revocation':'Claude remains visible; DSH hidden'}


for name,callback in [
    ('atomic_first_answer_16_concurrent_writers',first_answer_atomic),
    ('same_answer_replay_after_pending_consumed',same_answer_replay),
    ('actual_gate_vs_200ms_cache_poll',lambda:gate_race(False)),
    ('revocation_after_answer_before_gate_consumes',lambda:gate_race(True)),
    ('exact_action_cache_scope',cache_exact_scope),
    ('expiry_policy_epoch_cancel_attempt_boundaries',authorization_boundaries),
    ('historical_pending_cannot_hide_active_request',historical_pending_isolation),
    ('bounded_scan_exhaustion_and_read_diagnostics',scan_exhaustion),
    ('answer_fault_stays_local_to_permission',answer_fault_locality),
    ('bad_expression_once_then_later_reports_and_usage_identity',style_failure_once_and_later_report),
    ('worker_usage_runs_and_revocation_identity',worker_usage_and_visibility_identity),
]: RESULTS.check(name,callback)

data=RESULTS.save(workspace=str(WORKSPACE),fixtures=str(FIXTURES),python=sys.version,os=os.name,
    guard_attempts=GUARD,production_source_mutated=False,real_provider_calls=0,real_subprocesses=0,
    evidence='Real Windows filesystem/os.link, threads and SQLite; synthetic records; explicit gate timing and provider/channel/style/file-error stubs.',
    limitations=['No real Claude/DSH/QQ/service or network','No GUI rendering','No Windows Job process-tree or POSIX verification',
                 'Gate main is in-process; JSON-RPC pipe/CLI version compatibility not covered','Synthetic SQLite data is not live user data'])
print(json.dumps({'passed':data['passed'],'failed':data['failed'],'guard_attempts':GUARD,'results':str(RUN_ROOT/'permissions'/'results.json')},ensure_ascii=False))
raise SystemExit(bool(data['failed']))
