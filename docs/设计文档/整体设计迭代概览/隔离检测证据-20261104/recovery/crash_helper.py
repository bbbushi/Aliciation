"""One owned Python process: crash at a real boundary, or reopen and recover."""
from pathlib import Path
import sys, os, json, uuid
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from check_support import isolated_workspace,protect
mode,stage,suffix=sys.argv[1:]
workspace_name='hard_'+suffix+'_'+stage
WS=isolated_workspace('recovery',workspace_name) if mode=='crash' else ROOT/'recovery'/workspace_name
blocked=protect(WS,allow_owned_processes=True)
from alice import config as paths, persona_transaction as tx, persona_reset as reset, persona
from alice.store import Store
OLD='# Alice\n\n## 底色 · 气质与风格\n\n- 旧版简单表达。\n'
NEW='# Alice\n\n## 底色 · 气质与风格\n\n- 新版条件完整。\n'
EPOCH='a'*32
OP='imp_'+'b'*32
PRIORS=[{'title':'先验一','content':'主张：保留完整条件一。\n状态：先验','layer':'longterm','kind':'stance'},
        {'title':'先验二','content':'主张：保留完整条件二。\n状态：先验','layer':'longterm','kind':'stance'}]

def snapshot(s):
    journal=paths.PERSONA_DIR/'imports'/OP/'op.json'
    return {'file_sha256':tx.digest(tx.current_text()),
            'memories':[dict(r) for r in s.conn.execute('SELECT id,title FROM memories ORDER BY id')],
            'marker':s.meta_get('persona_import:'+OP),
            'epoch':s.meta_get('session_epoch'),
            'journal':json.loads(journal.read_text('utf-8')) if journal.exists() else None,
            'reset':reset.read()}

def die(s):
    (WS/'crash_evidence.json').write_text(json.dumps({'stage':stage,'in_transaction':s.conn.in_transaction,
        'state':snapshot(s),'blocked_attempts':blocked},ensure_ascii=False,indent=2),'utf-8')
    os._exit(86)

if mode=='crash':
    s=Store(paths.DB_PATH)
    s.meta_set('session_epoch',EPOCH)
    tx.atomic_text(paths.PERSONA_FILE,OLD)
    if stage.startswith('reset_'):
        s.insert_memory(**PRIORS[0])
        plan=persona.prepare_direct_install(s,paths.Config(),NEW,'hard-exit probe')
        (WS/'expected.json').write_text(json.dumps({'candidate':plan['candidate_md'],'op':plan['op_id']},ensure_ascii=False),'utf-8')
        s.close()
        dest=paths.DATA_DIR/'archive'/'20261104-120000'
        rec=reset.begin(dest,EPOCH,plan);dest.mkdir(parents=True)
        moved=0
        for name in reset.NAMES:
            src=paths.DATA_DIR/name
            if src.exists():
                os.replace(src,dest/name);moved+=1
                if stage=='reset_partial_move' and moved==1:
                    (WS/'crash_evidence.json').write_text(json.dumps({'stage':stage,'reset':reset.read(),'blocked_attempts':blocked},ensure_ascii=False,indent=2),'utf-8')
                    os._exit(86)
        rec['phase']='archived';reset.write(rec)
        if stage=='reset_after_archive':
            (WS/'crash_evidence.json').write_text(json.dumps({'stage':stage,'reset':reset.read(),'blocked_attempts':blocked},ensure_ascii=False,indent=2),'utf-8')
            os._exit(86)
        s=Store(paths.DB_PATH)
        if stage=='reset_fresh_db_without_epoch':die(s)
        s.meta_set('session_epoch',rec['new_epoch'])
        rec['phase']='installing';reset.write(rec)
        persona.commit_import_plan(s,paths.Config(),plan,archived_from=dest,archived=True)
        if stage=='reset_after_install':die(s)
        raise AssertionError('unknown reset stage')
    original_record,original_meta,original_atomic,original_insert=tx.write_record,tx._set_meta,tx.atomic_text,s.insert_memory
    def record(path,rec):
        if stage=='after_db_commit' and rec['phase']=='db_committed':die(s)
        original_record(path,rec)
        if stage=='after_prepared' and rec['phase']=='prepared':die(s)
        if stage=='after_file_journal' and rec['phase']=='file_replaced':die(s)
    def meta(conn,key,value):
        original_meta(conn,key,value)
        if stage=='after_marker_before_commit' and key=='persona_import:'+OP:die(s)
    def atomic(path,text):
        original_atomic(path,text)
        if stage=='after_file_replace' and path==paths.PERSONA_FILE:die(s)
    def insert(**item):
        mid=original_insert(**item)
        if stage=='after_first_insert':die(s)
        return mid
    tx.write_record,tx._set_meta,tx.atomic_text,s.insert_memory=record,meta,atomic,insert
    tx.install(s,OP,NEW,OLD,PRIORS,())
    raise AssertionError('crash hook not reached')

try:
    if stage.startswith('reset_'):
        record=reset.before_open()
        s=Store(paths.DB_PATH)
        before=snapshot(s)
        reset.after_open(s,paths.Config(),record)
        after=snapshot(s)
        if stage=='reset_partial_move':
            assert reset.read()['phase']=='rolled_back' and tx.current_text()==OLD
            assert s.meta_get('session_epoch')==EPOCH and len(after['memories'])==1
        else:
            expected=json.loads((WS/'expected.json').read_text('utf-8'))
            assert reset.read()['phase']=='completed' and tx.current_text()==expected['candidate']
            assert s.meta_get('session_epoch')==reset.read()['new_epoch'] and len(after['memories'])==2
            assert s.meta_get('persona_import:'+expected['op'])
        assert reset.before_open() is None
        assert (paths.DATA_DIR/'archive'/'20261104-120000'/'persona'/'persona.md').read_text('utf-8')==OLD if stage!='reset_partial_move' else True
    else:
        s=Store(paths.DB_PATH)
        before=snapshot(s)
        tx.recover(s)
        after=snapshot(s)
        committed=stage in ('after_file_replace','after_file_journal')
        assert tx.current_text()==(NEW if committed else OLD)
        assert len(after['memories'])==(2 if committed else 0)
        assert after['journal']['phase']==('completed' if committed else 'rolled_back')
        assert bool(after['marker'])==committed
        tx.recover(s);assert snapshot(s)==after
    s.close()
    result={'name':'hard_exit_'+stage,'status':'passed','before_recovery':before,'after_recovery':after,
            'workspace':str(WS),'blocked_attempts':blocked,'limitation':'os._exit crash is not power-loss proof'}
except Exception as exc:
    import traceback
    result={'name':'hard_exit_'+stage,'status':'failed','exception':type(exc).__name__,'message':str(exc),'traceback':traceback.format_exc(),'workspace':str(WS),'blocked_attempts':blocked}
(WS/'verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),'utf-8')
print(json.dumps({'name':result['name'],'status':result['status'],'result':str(WS/'verification.json')},ensure_ascii=False),flush=True)
sys.exit(0 if result['status']=='passed' else 1)
