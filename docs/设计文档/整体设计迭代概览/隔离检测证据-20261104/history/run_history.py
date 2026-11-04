"""C0/C3 semantic checks with copied production code, real temporary SQLite.
Run: python -I -S -B run_history.py UNIQUE_SUFFIX
No model calls; recap assertions cover structural provenance, not model judgment.
"""
from pathlib import Path
import sys,json,time
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from check_support import isolated_workspace,protect,Results
WS=isolated_workspace('history','workspace_'+sys.argv[1])
blocked=protect(WS)
from alice import config as paths,context,retrieval,channels,worker_tasks
from alice.store import Store
from alice.context_memory import MemoryContext
from alice.context_history import HistoryContext
from alice.context_recap import RecapContext

R=Results('history')
EPOCH='a'*32
PERSONA='# Alice\n\n## 底色 · 气质与风格\n\n- 简洁且保留条件。\n'
CURRENT='当前问题原话：必须保留这段完整条件，不得截断。'

def fixture(name):
    folder=WS/'fixtures'/name;folder.mkdir(parents=True)
    paths.DATA_DIR=folder
    paths.DB_PATH=folder/'alice.db'
    paths.PERSONA_DIR=folder/'persona'
    paths.PERSONA_FILE=paths.PERSONA_DIR/'persona.md'
    paths.PERSONA_HISTORY_DIR=paths.PERSONA_DIR/'history'
    paths.PERSONA_CHANGELOG=paths.PERSONA_DIR/'CHANGELOG.md'
    s=Store(paths.DB_PATH)
    s.conn.executescript(channels.SCHEMA)
    s.conn.executescript(worker_tasks.SCHEMA)
    s.meta_set('session_epoch',EPOCH)
    cfg=paths.Config()
    cfg.context.assembly_mode='indexed'
    cfg.context.history_enabled=True
    cfg.context.history_calls=4
    cfg.context.reply_reserve_tokens=100
    return s,cfg,folder

def pair(s,index,*,channel='gui',epoch=EPOCH,status='completed',user=None,assistant=None):
    rid=f'request_{index:08d}'
    now=1793750400.0+index*3
    with s.conn:
        s.conn.execute('INSERT INTO channel_requests(request_id,session_epoch,channel_id,source_key,source_json,payload_hash,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)',
            (rid,epoch,channel,rid,'{}','fixture_hash','accepted',now,now))
    uid=s.add_turn('user',user or f'主题{index:02d} 用户完整限制：仅限本地，不许传给其他渠道。',ts=now,request_id=rid)
    aid=s.add_turn('assistant',assistant or f'主题{index:02d} AI当时回应：保留条件且不把旧话当授权。',ts=now+1,request_id=rid)
    with s.conn:s.conn.execute('UPDATE channel_requests SET status=? WHERE request_id=?',(status,rid))
    return uid,aid

def assemble(s,cfg,*,scope='gui',reserved=0):
    session=MemoryContext(s,cfg,scope,CURRENT)
    manifests=[]
    messages=retrieval.build_messages(s,cfg,PERSONA,now=1793750500,current_user_text=CURRENT,
        source_scope=scope,memory_session=session,manifest_out=manifests,reserved_tokens=reserved)
    boundary=manifests[0].history
    history=HistoryContext(session,boundary=boundary)
    session.history=history
    recap=RecapContext(s,cfg,scope,boundary=boundary)
    return messages,boundary,history,recap

def record(folder,detail):
    (folder/'evidence.json').write_text(json.dumps(detail,ensure_ascii=False,indent=2),'utf-8')
    return {'evidence':str(folder/'evidence.json'),**detail}

def search_ref(h,query,uid):
    result=json.loads(h.execute('history_search',{'query':query}))
    candidates=[item['ref'] for item in result.get('items',[]) if h.issued[item['ref']][0]==uid]
    assert len(candidates)==1, result
    return candidates[0]

def basic_boundary():
    s,cfg,folder=fixture('n16_33_boundary')
    try:
        for i in range(1,17):pair(s,i)
        assert s.add_turn('user',CURRENT,ts=1793750500)==33
        messages,b,h,r=assemble(s,cfg)
        assert b['retained_ids']==list(range(19,34)) and b['first_retained_id']==19
        assert h.boundary is b and r.boundary is b and h.cutoff==r.floor==19
        assert messages[-1]['role']=='user' and messages[-1]['content'].endswith(CURRENT)
        ref=search_ref(h,'主题09',17)
        loaded=json.loads(h.execute('history_load',{'refs':[ref]}))['items'][0]
        assert loaded['status']=='loaded' and [t['id'] for t in loaded['turns']]==[17,18]
        assert any(row['user_id']==17 and row['assistant_id']==18 for row in r.rows())
        return record(folder,{'boundary':b,'loaded':loaded,'recap_floor':r.floor,'same_boundary_object':True})
    finally:s.close()

def suffix_properties():
    s,cfg,folder=fixture('suffix_property_matrix')
    try:
        rows=[{'id':i,'role':'user' if i%2 else 'assistant','content':f'消息{i}：'+('条件不能丢。'*(i%4+1))} for i in range(1,34)]
        costs=[context.messages_tokens([{'role':r['role'],'content':r['content']} for r in rows[-n:]]) for n in (1,3,5,15)]
        budgets=sorted({0,1,10,100,300,1000,*costs,*(x-1 for x in costs),*(x+1 for x in costs)})
        evidence=[]
        for limit in (1,2,3,4,15,16,17,32,33,40):
            previous=[]
            for budget in [*budgets,None]:
                metadata={}
                kept=context.complete_round_suffix(rows,limit,budget,selection_out=metadata)
                ids=[r['id'] for r in kept]
                assert kept and kept[-1]==rows[-1] and kept[0]['role']=='user'
                assert ids==list(range(ids[0],34)) and len(ids)<=limit
                assert all(r['role']!='assistant' or r['id']-1 in ids for r in kept)
                assert len(kept)>=len(previous)
                dropped=metadata['exclusions'];dids=[x['id'] for x in dropped]
                assert len(dids)==len(set(dids))
                assert set(dids).isdisjoint(ids) and set(dids)|set(ids)==set(range(1,34))
                assert all(x['reason'] in ('message_limit','orphan_prefix','token_budget') for x in dropped)
                if budget is not None:
                    token_cost=context.messages_tokens([{'role':r['role'],'content':r['content']} for r in kept])
                    assert token_cost<=budget or (len(kept)==1 and metadata['current_user_over_budget'])
                previous=kept
                evidence.append({'limit':limit,'budget':budget,'retained':ids,'metadata':metadata})
        for prefix in ([{'id':0,'role':'assistant','content':'孤立回复'}],[]):
            meta={};kept=context.complete_round_suffix(prefix+rows,100,None,selection_out=meta)
            assert kept==rows
        return record(folder,{'matrix_count':len(evidence),'matrix':evidence,'properties':'current user whole; suffix order; pair integrity; disjoint/exhaustive reason partition; budget monotonicity'})
    finally:s.close()

def tight_boundary():
    s,cfg,folder=fixture('tight_budget_shared_boundary')
    try:
        for i in range(1,17):pair(s,i)
        s.add_turn('user',CURRENT)
        _,baseline,_,_=assemble(s,cfg)
        reserved=baseline['history_token_budget']-context.messages_tokens([{'role':'user','content':CURRENT}])-10
        messages,b,h,r=assemble(s,cfg,reserved=reserved)
        assert b['retained_ids']==[33] and h.cutoff==r.floor==33
        assert b['exclusions'] and any(x['reason']=='token_budget' for x in b['exclusions'])
        eligible={row['assistant_id'] for row in h.rows()}
        assert eligible==set(range(2,33,2)) and {row['assistant_id'] for row in r.rows()}==eligible
        assert messages[-1]['content'].endswith(CURRENT)
        ref=search_ref(h,'主题16',31)
        loaded=json.loads(h.execute('history_load',{'refs':[ref]}))['items'][0]
        assert loaded['status']=='loaded' and [x['id'] for x in loaded['turns']]==[31,32]
        return record(folder,{'boundary':b,'reserved':reserved,'loaded':loaded})
    finally:s.close()

def qualify_pairs():
    s,cfg,folder=fixture('scope_epoch_source_worker_eligibility')
    try:
        ids={}
        ids['gui_completed']=pair(s,1)
        ids['gui_warning']=pair(s,2,status='completed_with_warning')
        ids['qq_completed']=pair(s,3,channel='qq-owner')
        ids['different_epoch']=pair(s,4,epoch='b'*32)
        ids['failed']=pair(s,5,status='failed')
        ids['generated']=pair(s,6,status='generated')
        for index,key in enumerate(('source_user','source_assistant','worker_user','worker_assistant','report_assistant'),7):
            ids[key]=pair(s,index)
        with s.conn:
            for key,side in [('source_user',0),('source_assistant',1)]:
                s.conn.execute("INSERT INTO source_turns VALUES(?,'gui','',0,'[]')",(ids[key][side],))
            for key,side in [('worker_user',0),('worker_assistant',1)]:
                s.conn.execute("INSERT INTO worker_turns VALUES(?,'gui','','[]')",(ids[key][side],))
            s.conn.execute("INSERT INTO worker_reports(attempt_id,task_id,epoch,outcome,report_state,turn_id,created_at) VALUES('fixture_attempt','fixture_task',?,'succeeded','delivered',?,1)",(EPOCH,ids['report_assistant'][1]))
        s.add_turn('user','没有channel_requests绑定的旧CLI问题');s.add_turn('assistant','未标渠道答复')
        s.add_turn('user',CURRENT)
        cfg.context.recent_turns=1
        _,b,h,r=assemble(s,cfg)
        gui={row['user_id'] for row in h.rows()}
        assert gui=={ids['gui_completed'][0],ids['gui_warning'][0]}
        assert {row['user_id'] for row in r.rows()}==gui
        _,_,qh,qr=assemble(s,cfg,scope='qq-owner')
        assert {row['user_id'] for row in qh.rows()}=={ids['qq_completed'][0]}
        assert {row['user_id'] for row in qr.rows()}=={ids['qq_completed'][0]}
        return record(folder,{'synthetic_pairs':ids,'boundary':b,'gui_eligible':sorted(gui),'qq_eligible':[ids['qq_completed'][0]],'excluded_note':'source/worker marker remains excluding even with withdrawn empty body and expiry=0'})
    finally:s.close()

def ref_lifecycle(kind):
    s,cfg,folder=fixture('ref_'+kind)
    try:
        uid,aid=pair(s,1)
        s.add_turn('user',CURRENT);cfg.context.recent_turns=1
        _,b,h,r=assemble(s,cfg)
        ref=search_ref(h,'主题01',uid)
        if kind=='cross_request':
            _,_,other,_=assemble(s,cfg)
            result=json.loads(other.execute('history_load',{'refs':[ref]}))
            assert result['items'][0]['status']=='unavailable' and h.valid()
        elif kind=='version_changed':
            with s.conn:s.conn.execute("UPDATE turns SET content='经过后续更正的完整回答' WHERE id=?",(aid,))
            assert not h.valid()
            result=json.loads(h.execute('history_load',{'refs':[ref]}))
            assert result['items'][0]['status']=='stale_ref'
        elif kind=='epoch_changed':
            s.meta_set('session_epoch','b'*32)
            assert not h.valid() and not r.valid() and h.rows()==[] and r.rows()==[]
            result=json.loads(h.execute('history_load',{'refs':[ref]}))
            assert result['items'][0]['status']=='unavailable'
        elif kind in ('source_bound_later','worker_bound_later'):
            with s.conn:
                if kind=='source_bound_later':s.conn.execute("INSERT INTO source_turns VALUES(?,'gui','',0,'[]')",(aid,))
                else:s.conn.execute("INSERT INTO worker_turns VALUES(?,'gui','','[]')",(uid,))
            assert not h.valid()
            result=json.loads(h.execute('history_load',{'refs':[ref]}))
            assert result['items'][0]['status']=='unavailable' and r.rows()==[]
        return record(folder,{'kind':kind,'result':result,'boundary':b})
    finally:s.close()

def complete_load_budget():
    s,cfg,folder=fixture('complete_pair_budget')
    try:
        uid,aid=pair(s,1,user='主题01 '+('完整条件。'*260),assistant='完整答复。'*100)
        s.add_turn('user',CURRENT);cfg.context.recent_turns=1;cfg.context.history_max_chars=1200
        _,b,h,_=assemble(s,cfg)
        ref=search_ref(h,'主题01',uid)
        too_large=json.loads(h.execute('history_load',{'refs':[ref]}))
        assert too_large['items'][0]['status']=='too_large' and 'turns' not in too_large['items'][0]
        cfg.context.history_max_chars=8000
        h.remaining_tokens=1
        limited=json.loads(h.execute('history_load',{'refs':[ref]}))
        assert limited.get('error')=='budget_exceeded' and ref not in h.loaded
        h.remaining_tokens=None
        loaded=json.loads(h.execute('history_load',{'refs':[ref]}))
        assert loaded['items'][0]['status']=='loaded'
        body=loaded['items'][0]['turns']
        assert [x['id'] for x in body]==[uid,aid]
        assert all(x['content']==s.conn.execute('SELECT content FROM turns WHERE id=?',(x['id'],)).fetchone()[0] for x in body)
        return record(folder,{'too_large':too_large,'token_budget':limited,'loaded_characters':[len(x['content']) for x in body],'whole_pair_ids':[uid,aid]})
    finally:s.close()

def recap_bindings():
    s,cfg,folder=fixture('recap_literal_version_binding')
    try:
        for i in range(1,5):pair(s,i)
        s.add_turn('user',CURRENT);cfg.context.recent_turns=1
        _,b,h,r=assemble(s,cfg)
        rows=r.pending()
        assert rows and {x['user_id'] for x in rows}=={1,3,5,7}
        assert r.claim(rows) and not r.claim(rows)
        text=r.parse(json.dumps({'items':[{'turn_id':rows[0]['user_id'],'quote':rows[0]['user_content']}]},ensure_ascii=False),rows)
        assert text and r.parse(json.dumps({'items':[{'turn_id':rows[0]['user_id'],'quote':'未在原文出现的捏造内容'}]},ensure_ascii=False),rows) is None
        r.save(rows,text,{'valid':True,'reason':'synthetic structural fixture only, not model validation'})
        injected=r.inject()
        assert injected and text in injected and r.valid()
        with s.conn:s.conn.execute("UPDATE turns SET content='后续修订' WHERE id=?",(rows[0]['assistant_id'],))
        assert not r.valid()
        fresh=RecapContext(s,cfg,'gui',boundary=b)
        assert fresh.inject() is None
        assert RecapContext(s,cfg,'qq-owner',boundary=b).inject() is None
        return record(folder,{'boundary':b,'cards':r.card_ids,'literal_quote':text,'after_source_change_valid':r.valid(),'fresh_injection':fresh.inject(),'claim_is_once_only':True})
    finally:s.close()

def cursor_stale():
    s,cfg,folder=fixture('cursor_stale_after_history_change')
    try:
        for i in range(1,13):pair(s,i,user=f'共同主题 用户{i}完整原文',assistant=f'共同主题 答复{i}完整原文')
        s.add_turn('user',CURRENT);cfg.context.recent_turns=1
        _,_,h,_=assemble(s,cfg)
        first=json.loads(h.execute('history_search',{'query':'共同主题'}))
        assert first['cursor'] and len(first['items'])==8
        with s.conn:s.conn.execute("UPDATE turns SET content='共同主题 后来编辑' WHERE id=1")
        second=json.loads(h.execute('history_search',{'query':'共同主题','cursor':first['cursor']}))
        assert second['error']=='stale_cursor'
        return record(folder,{'initial_items':len(first['items']),'second':second})
    finally:s.close()

R.check('N16_33_real_assembly_and_17_18_load',basic_boundary)
R.check('suffix_property_matrix',suffix_properties)
R.check('tight_budget_shared_boundary',tight_boundary)
R.check('scope_epoch_source_worker_eligibility',qualify_pairs)
for kind in ('cross_request','version_changed','epoch_changed','source_bound_later','worker_bound_later'):
    R.check('ref_'+kind,lambda kind=kind:ref_lifecycle(kind))
R.check('whole_pair_budget_no_truncation',complete_load_budget)
R.check('recap_literal_version_binding',recap_bindings)
R.check('cursor_stale_after_history_change',cursor_stale)
data=R.save(workspace=str(WS),blocked_attempts=blocked,limitations='synthetic ordinary turns in real SQLite; no models/servers; recap verdict fixture checks provenance mechanics only')
print(json.dumps({'passed':data['passed'],'failed':data['failed'],'results':str(ROOT/'history'/'results.json')}),flush=True)
sys.exit(1 if data['failed'] else 0)
