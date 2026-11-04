"""Isolated real-SQLite/file recovery checks; no model, network or live state.

Run: python -I -S -B run_recovery.py SUFFIX
All fault injection replaces I/O/transaction boundaries, never product algorithms.
"""
from pathlib import Path
import sys
import json
import sqlite3
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from check_support import isolated_workspace, protect, Results
SUFFIX = sys.argv[1]
WS = isolated_workspace('recovery', 'workspace_' + SUFFIX)
blocked = protect(WS)
from alice import config as paths, persona_transaction as tx, persona_reset as reset, persona
from alice.store import Store

R = Results('recovery')
OLD = '# Alice\n\n## 底色 · 气质与风格\n\n- 旧风格。\n'
NEW = '# Alice\n\n## 底色 · 气质与风格\n\n- 新风格，条件完整。\n'
MANUAL = '# Alice\n\n## 底色 · 气质与风格\n\n- 后来人工编辑。\n'
EPOCH = 'a' * 32
PRIOR = {'title': '首个完整先验', 'content': '主张：只在条件成立时适用。\n状态：先验', 'kind': 'stance', 'layer': 'longterm'}
SECOND = {**PRIOR, 'title': '第二个先验'}

def bind(name):
    data = WS / 'fixtures' / name
    data.mkdir(parents=True)
    paths.DATA_DIR = data
    paths.DB_PATH = data / 'alice.db'
    paths.PERSONA_DIR = data / 'persona'
    paths.PERSONA_FILE = paths.PERSONA_DIR / 'persona.md'
    paths.PERSONA_HISTORY_DIR = paths.PERSONA_DIR / 'history'
    paths.PERSONA_CHANGELOG = paths.PERSONA_DIR / 'CHANGELOG.md'
    persona.PERSONA_IMPORT_DIR = paths.PERSONA_DIR / 'imports'
    return data

def setup(name, old=OLD):
    data = bind(name)
    store = Store(paths.DB_PATH)
    store.meta_set('session_epoch', EPOCH)
    store.meta_set('persona_version', 4)
    store.meta_set('persona_seeded_at', 100)
    store.meta_set('last_evolve_at', 200)
    if old is not None:
        tx.atomic_text(paths.PERSONA_FILE, old)
    return store, 'imp_' + uuid.uuid4().hex

def state(store, op):
    journal = paths.PERSONA_DIR / 'imports' / op / 'op.json'
    return {'file_sha256': tx.digest(tx.current_text()),
            'phase': json.loads(journal.read_text('utf-8')).get('phase') if journal.exists() else None,
            'marker': store.meta_get('persona_import:' + op),
            'rows': [dict(row) for row in store.conn.execute('SELECT id,title,content FROM memories ORDER BY id')],
            'meta': {key: store.meta_get(key) for key in ('session_epoch','persona_version','persona_seeded_at','last_evolve_at')}}

def save_evidence(name, evidence):
    target = paths.DATA_DIR / 'evidence.json'
    target.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), 'utf-8')
    return {'evidence': str(target), **evidence}

def count(store):
    return store.conn.execute('SELECT count(*) FROM memories').fetchone()[0]

def expect_raises(kind, call):
    try:
        call()
    except kind as exc:
        return str(exc)
    raise AssertionError('Expected ' + kind.__name__)

def normal(name, old):
    store, op = setup(name, old)
    try:
        tx.install(store, op, NEW, old, [PRIOR, SECOND], ())
        first = state(store, op)
        assert tx.current_text() == NEW and count(store) == 2
        assert first['phase'] == 'completed' and first['marker']
        tx.recover(store); tx.recover(store)
        assert state(store, op) == first
        replay = expect_raises(ValueError, lambda: tx.install(store, op, NEW, NEW, [PRIOR], ()))
        return save_evidence(name, {'state': first, 'replay_rejected': replay})
    finally:
        store.close()

def transaction_visibility():
    name = 'atomic_marker_visibility'
    store, op = setup(name)
    original = tx._set_meta
    views = []
    def traced(conn, key, value):
        original(conn, key, value)
        if key == 'persona_import:' + op:
            with sqlite3.connect(paths.DB_PATH) as observer:
                views.append({'outside_rows': observer.execute('SELECT count(*) FROM memories').fetchone()[0],
                              'outside_marker': observer.execute('SELECT value FROM meta WHERE key=?',(key,)).fetchone(),
                              'inside_rows': count(store), 'in_transaction': conn.in_transaction})
    tx._set_meta = traced
    try:
        tx.install(store, op, NEW, OLD, [PRIOR, SECOND], ())
        assert views == [{'outside_rows':0,'outside_marker':None,'inside_rows':2,'in_transaction':True}]
        assert count(store) == 2 and store.meta_get('persona_import:' + op)
        return save_evidence(name, {'boundary_observation': views, 'after':state(store, op)})
    finally:
        tx._set_meta = original
        store.close()

def before_commit_failure(point):
    name = 'rollback_' + point
    store, op = setup(name)
    original_insert, original_meta = store.insert_memory, tx._set_meta
    calls = 0
    def insert(**item):
        nonlocal calls
        calls += 1
        mid = original_insert(**item)
        if point == 'after_first_prior' and calls == 1:
            raise OSError('injected after real first INSERT')
        return mid
    def meta(conn, key, value):
        if point == 'before_marker' and key == 'persona_import:' + op:
            raise OSError('injected before marker INSERT')
        return original_meta(conn, key, value)
    store.insert_memory, tx._set_meta = insert, meta
    try:
        error = expect_raises(OSError, lambda: tx.install(store, op, NEW, OLD, [PRIOR, SECOND], ()))
        observed = state(store, op)
        assert tx.current_text() == OLD and count(store) == 0 and not observed['marker']
        assert observed['phase'] == 'rolled_back' and observed['meta']['persona_version'] == '4'
        tx.recover(store)
        assert state(store, op) == observed
        return save_evidence(name, {'injection': point, 'error': error, 'after':observed})
    finally:
        store.insert_memory, tx._set_meta = original_insert, original_meta
        store.close()

def file_failure(point):
    name = 'file_' + point
    store, op = setup(name)
    original_atomic, original_record = tx.atomic_text, tx.write_record
    fired = False
    def atomic(path, text):
        nonlocal fired
        if path == paths.PERSONA_FILE and point == 'before_replace' and not fired:
            fired = True
            raise OSError('injected before persona file replacement')
        return original_atomic(path,text)
    def record(path, rec):
        nonlocal fired
        if rec['phase'] == point and not fired:
            fired = True
            raise OSError('injected journal boundary: ' + point)
        return original_record(path,rec)
    tx.atomic_text, tx.write_record = atomic, record
    try:
        error = None
        try:
            tx.install(store,op,NEW,OLD,[PRIOR,SECOND],())
        except OSError as exc:
            error = str(exc)
        assert fired
        observed = state(store,op)
        committed = point in ('file_replaced','completed')
        assert tx.current_text() == (NEW if committed else OLD)
        assert count(store) == (2 if committed else 0)
        assert observed['phase'] == ('completed' if committed else 'rolled_back')
        assert (error is None) == committed
        tx.recover(store); assert state(store,op) == observed
        return save_evidence(name, {'injection':point,'returned_error':error,'after':observed})
    finally:
        tx.atomic_text, tx.write_record = original_atomic, original_record
        store.close()

def interrupted(point, edit=None):
    """Retain an authentic DB+file interruption by suppressing only inline recovery.
    Separate hard-exit helpers additionally verify OS process termination windows.
    """
    name = 'interruption_' + point + ('_' + edit if edit else '')
    store, op = setup(name)
    original_record, original_recover = tx.write_record, tx.recover
    fired = False
    def record(path, rec):
        nonlocal fired
        if rec['phase'] == point and not fired:
            fired = True
            raise OSError('injected journal interruption')
        return original_record(path, rec)
    def no_inline_recovery(s):
        if fired:
            raise OSError('simulated unavailable inline recovery')
        return original_recover(s)
    tx.write_record, tx.recover = record, no_inline_recovery
    try:
        expect_raises(OSError, lambda: tx.install(store,op,NEW,OLD,[PRIOR,SECOND],()))
    finally:
        tx.write_record, tx.recover = original_record, original_recover
    before = state(store,op)
    if edit == 'manual':
        tx.atomic_text(paths.PERSONA_FILE, MANUAL)
    elif edit == 'epoch':
        store.meta_set('session_epoch', 'b' * 32)
    elif edit == 'changed_prior':
        with store.conn:
            store.conn.execute("UPDATE memories SET content='later independent change' WHERE id=(SELECT min(id) FROM memories)")
    store.close()
    store = Store(paths.DB_PATH)
    try:
        error = None
        if edit in ('epoch','changed_prior'):
            unchanged = state(store,op)
            error = expect_raises(ValueError, lambda:tx.recover(store))
            assert state(store,op) == unchanged
        else:
            tx.recover(store)
            committed = point == 'file_replaced' and edit is None
            assert tx.current_text() == (MANUAL if edit == 'manual' else NEW if committed else OLD)
            assert count(store) == (2 if committed else 0)
            assert state(store,op)['phase'] == ('superseded' if edit == 'manual' else 'completed' if committed else 'rolled_back')
            once = state(store,op)
            tx.recover(store); assert state(store,op) == once
        return save_evidence(name, {'interrupted':before,'edit':edit,'error':error,'recovered':state(store,op)})
    finally:
        store.close()

def outer_transaction():
    store,op = setup('outer_transaction_rejected')
    try:
        store.conn.execute('BEGIN')
        store.conn.execute("INSERT INTO meta(key,value) VALUES('caller','keep')")
        error = expect_raises(ValueError,lambda:tx.install(store,op,NEW,OLD,[PRIOR],()))
        assert store.conn.in_transaction and store.meta_get('caller') == 'keep'
        store.conn.rollback()
        assert store.meta_get('caller') is None and tx.current_text() == OLD and count(store) == 0
        tx.recover(store)
        return save_evidence('outer_transaction_rejected',{'error':error,'after':state(store,op)})
    finally:
        store.close()

def reset_case(kind):
    name = 'reset_' + kind
    store,op = setup(name)
    store.insert_memory(**PRIOR)
    store.add_turn('user','old isolated transcript')
    plan = persona.prepare_direct_install(store, paths.Config(), NEW, 'isolated reset probe') if kind != 'blank_forward' else None
    store.close()
    dest = paths.DATA_DIR / 'archive' / '20261104-120000'
    record = reset.begin(dest,EPOCH,plan)
    dest.mkdir(parents=True)
    import os
    if kind in ('partial_move','partial_move_conflict'):
        os.replace(paths.DB_PATH,dest/'alice.db')
        if kind == 'partial_move_conflict':
            tx.atomic_text(paths.PERSONA_FILE,MANUAL)
            error = expect_raises(ValueError,reset.before_open)
            assert not paths.DB_PATH.exists() and (dest/'alice.db').exists()
            assert tx.current_text() == MANUAL
            return save_evidence(name,{'error':error,'journal':reset.read()})
        assert reset.before_open() is None
        assert paths.DB_PATH.exists() and not (dest/'alice.db').exists()
        assert tx.current_text() == OLD and reset.read()['phase'] == 'rolled_back'
        assert reset.before_open() is None
        return save_evidence(name,{'journal':reset.read()})
    for filename in reset.NAMES:
        src = paths.DATA_DIR / filename
        if src.exists(): os.replace(src,dest/filename)
    record['phase']='archived'; reset.write(record)
    if kind == 'archive_conflict':
        tx.atomic_text(dest/'persona'/'persona.md',MANUAL)
        error=expect_raises(ValueError,reset.before_open)
        assert not paths.DB_PATH.exists()
        return save_evidence(name,{'error':error,'journal':reset.read()})
    pending = reset.before_open()
    store=Store(paths.DB_PATH)
    try:
        if kind == 'wrong_epoch':
            store.meta_set('session_epoch','c'*32)
            error=expect_raises(ValueError,lambda:reset.after_open(store,paths.Config(),pending))
            assert store.meta_get('session_epoch')=='c'*32 and count(store)==0
            return save_evidence(name,{'error':error,'journal':reset.read()})
        if kind in ('attempted_rollback','installed_before_reset_finalize'):
            store.meta_set('session_epoch',pending['new_epoch'])
            pending['phase']='installing'; reset.write(pending)
            if kind == 'attempted_rollback':
                original=tx.atomic_text
                def fail(path,text):
                    if path == paths.PERSONA_FILE: raise OSError('reset candidate replacement failed')
                    return original(path,text)
                tx.atomic_text=fail
                try:
                    expect_raises(OSError,lambda:persona.commit_import_plan(store,paths.Config(),plan,archived_from=dest,archived=True))
                finally: tx.atomic_text=original
                error=expect_raises(ValueError,lambda:reset.after_open(store,paths.Config(),pending))
                assert count(store)==0 and not paths.PERSONA_FILE.exists()
                return save_evidence(name,{'error':error,'journal':reset.read(),'import':state(store,plan['op_id'])})
            persona.commit_import_plan(store,paths.Config(),plan,archived_from=dest,archived=True)
            assert reset.read()['phase']=='installing'
        reset.after_open(store,paths.Config(),pending)
        assert reset.read()['phase']=='completed' and store.meta_get('session_epoch')==pending['new_epoch']
        assert store.conn.execute('SELECT count(*) FROM turns').fetchone()[0]==0
        assert tx.current_text()==(None if kind=='blank_forward' else NEW.rstrip()+'\n\n## 生长 · 我的来历\n\n（用户提供的 Alice 格式人格）\n\n## 生长 · 自我叙事\n\n（尚未形成——将随共同经历自然生长）\n\n## 生长 · 我现在的想法\n\n（尚未形成——将在思辨中从经历里长出观点）\n\n## 生长 · 与用户的关系\n\n（尚未形成）\n')
        prior_count=count(store)
        assert prior_count==(0 if kind=='blank_forward' else 2)
        assert reset.before_open() is None
        assert (dest/'persona'/'persona.md').read_text('utf-8')==OLD
        return save_evidence(name,{'journal':reset.read(),'new_prior_count':prior_count,'new_epoch':store.meta_get('session_epoch')})
    finally: store.close()

R.check('first_install',lambda:normal('first_install',None))
R.check('replace_install',lambda:normal('replace_install',OLD))
R.check('atomic_marker_visibility',transaction_visibility)
for p in ('after_first_prior','before_marker'):
    R.check('rollback_'+p,lambda p=p:before_commit_failure(p))
for p in ('before_replace','db_committed','file_replaced','completed'):
    R.check('file_'+p,lambda p=p:file_failure(p))
for p,e in [('db_committed',None),('file_replaced',None),('db_committed','manual'),('file_replaced','manual'),('db_committed','epoch'),('db_committed','changed_prior')]:
    R.check('interruption_'+p+('_'+e if e else ''),lambda p=p,e=e:interrupted(p,e))
R.check('outer_transaction_rejected',outer_transaction)
for kind in ('partial_move','partial_move_conflict','archive_conflict','archived_forward','blank_forward','wrong_epoch','attempted_rollback','installed_before_reset_finalize'):
    R.check('reset_'+kind,lambda kind=kind:reset_case(kind))
data=R.save(workspace=str(WS),blocked_attempts=blocked,method='real SQLite and temporary files; injected boundary exceptions; not power-loss proof')
print(json.dumps({'passed':data['passed'],'failed':data['failed'],'results':str(ROOT/'recovery'/'results.json')}),flush=True)
sys.exit(1 if data['failed'] else 0)
