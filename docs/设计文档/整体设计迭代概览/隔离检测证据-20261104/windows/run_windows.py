from pathlib import Path
import ctypes
from ctypes import wintypes as w
import json
import os
import subprocess
import sys
import time
from unittest.mock import patch

RUN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RUN))
from check_support import Results, isolated_workspace, protect
workspace = isolated_workspace('windows', 'snapshot-' + str(os.getpid()))
blocked = protect(workspace, allow_owned_processes=True)
from alice.worker_process import WorkerProcessTransport, _WindowsJob

helper = RUN/'windows'/'controlled_adapter.py'
results = Results('windows')
original_popen = subprocess.Popen
launched = []
kernel = ctypes.WinDLL('kernel32', use_last_error=True)
kernel.OpenProcess.argtypes = (w.DWORD, w.BOOL, w.DWORD)
kernel.OpenProcess.restype = w.HANDLE
kernel.WaitForSingleObject.argtypes = (w.HANDLE, w.DWORD)
kernel.WaitForSingleObject.restype = w.DWORD
kernel.CloseHandle.argtypes = (w.HANDLE,)
kernel.CloseHandle.restype = w.BOOL

def wait_for(predicate, seconds=8):
    until = time.monotonic()+seconds
    while time.monotonic()<until:
        if predicate(): return
        time.sleep(.02)
    raise AssertionError('Timed out waiting for owned helper evidence')

def replacement_popen(argv, **kwargs):
    assert str(argv[-1]).endswith('addons\\claude_agent\\adapter.py') or str(argv[-1]).endswith('addons/claude_agent/adapter.py')
    proc = original_popen([sys.executable, '-I', '-S', '-B', str(helper)], **kwargs)
    launched.append(proc)
    return proc

def child_handle(directory):
    file=directory/'child-ready.json'
    wait_for(file.exists)
    pid=json.loads(file.read_text(encoding='utf-8'))['pid']
    handle=kernel.OpenProcess(0x100000 | 0x1000, False, pid)
    assert handle, 'Could not retain the known owned-child process handle'
    return pid, handle

def stopped(handle):
    return kernel.WaitForSingleObject(handle, 0) == 0

def cleanup(transport, handle=None):
    try:
        if not transport.termination_confirmed:
            transport.reap()
    finally:
        if transport.job: transport.job.close()
        if transport.proc:
            try: transport.proc.wait(timeout=5)
            except subprocess.TimeoutExpired: transport.proc.terminate(); transport.proc.wait(timeout=5)
            for stream in (transport.proc.stdin, transport.proc.stdout):
                if stream:
                    try: stream.close()
                    except (OSError, ValueError): pass
        if handle: kernel.CloseHandle(handle)

def make_transport(name, callback=None):
    directory=RUN/'windows'/('case-'+name+'-'+str(time.time_ns()))
    directory.mkdir()
    events=[]
    transport=WorkerProcessTransport(brief=name, sandbox=directory, control_dir=directory/'control',
        identity={'task_id':'diagnostic-task'}, max_turns=1,
        on_event=lambda kind,payload:events.append((kind,payload)),hard_deadline=20,
        authorize_start=callback)
    return transport,directory,events

def parent_exit():
    transport, directory, events=make_transport('parent_exits')
    handle=None
    try:
        with patch('subprocess.Popen', replacement_popen): transport.start()
        pid,handle=child_handle(directory)
        wait_for(lambda:transport.proc.poll() is not None)
        wait_for(lambda:transport.terminal() is not None)
        assert transport.protocol_lost and transport.terminal()['state']=='unknown'
        assert not stopped(handle) and not transport.job.empty()
        assert not transport.termination_confirmed
        assert transport.reap() is True
        wait_for(lambda:stopped(handle))
        return {'parent_pid':transport.proc.pid,'child_pid':pid,'parent_exit_was_not_tree_exit':True,
                'reap_confirmed':transport.termination_confirmed,'child_signalled':True}
    finally: cleanup(transport,handle)

def terminal_not_proof():
    transport,directory,events=make_transport('terminal_claim')
    handle=None
    try:
        with patch('subprocess.Popen', replacement_popen): transport.start()
        pid,handle=child_handle(directory)
        wait_for(lambda:transport.terminal() is not None)
        assert transport.adapter_stopped is True
        assert not stopped(handle) and not transport.job.empty() and not transport.termination_confirmed
        assert transport.reap() is True
        wait_for(lambda:stopped(handle))
        return {'child_pid':pid,'terminal_claim_not_accepted_as_job_proof':True}
    finally: cleanup(transport,handle)

def cancellation():
    transport,directory,events=make_transport('cooperative_cancel')
    handle=None
    try:
        with patch('subprocess.Popen', replacement_popen): transport.start()
        pid,handle=child_handle(directory)
        assert not stopped(handle)
        transport.cancel('diagnostic_cancel')
        transport._request_cancel()  # idempotent second request must not reopen/replay.
        assert transport.cancel_sent and transport.terminal()['state']=='cancelled'
        assert transport.reap() is True
        wait_for(lambda:stopped(handle))
        return {'child_pid':pid,'cancel_sent':True,'confirmed':True}
    finally: cleanup(transport,handle)

def authorization_denied():
    invoked=[]
    def deny():
        invoked.append(True)
        raise PermissionError('synthetic host denial before GO')
    transport,directory,events=make_transport('authorization_denied',deny)
    try:
        try:
            with patch('subprocess.Popen', replacement_popen): transport.start()
        except PermissionError as exc: assert str(exc)=='synthetic host denial before GO'
        else: raise AssertionError('Expected host authorization denial')
        assert invoked == [True] and transport.proc is not None and transport.assigned
        assert not transport.go_sent
        assert transport.reap() is True
        assert not (directory/'go-received.json').exists()
        assert not (directory/'child-ready.json').exists()
        return {'go_sent':False,'no_child_created':True,'assigned_job_reaped':True}
    finally: cleanup(transport)

def assignment_failed():
    transport,directory,events=make_transport('assignment_failed')
    try:
        try:
            with patch('subprocess.Popen', replacement_popen), patch.object(_WindowsJob,'assign',side_effect=OSError('injected assign failure')):
                transport.start()
        except OSError as exc: assert str(exc)=='injected assign failure'
        else: raise AssertionError('Expected injected assign failure')
        assert transport.proc is not None
        assert not transport.go_sent and not transport.assigned
        assert transport.reap() is True
        assert not (directory/'go-received.json').exists()
        return {'go_sent':False,'waiting_adapter_stopped':True}
    finally: cleanup(transport)

def cancelled_before_start():
    transport,directory,events=make_transport('cancelled_before_start')
    count=len(launched)
    try:
        transport.cancelled.set()
        with patch('subprocess.Popen', replacement_popen): transport.start()
        assert len(launched)==count and transport.proc is None
        assert transport.terminal()['code']=='cancelled_before_go'
        assert transport.reap()
        return {'spawned':0,'go_sent':False}
    finally: cleanup(transport)

def malformed_protocol():
    transport,directory,events=make_transport('invalid_protocol')
    try:
        with patch('subprocess.Popen', replacement_popen): transport.start()
        wait_for(lambda:transport.terminal() is not None)
        assert transport.protocol_lost and transport.terminal()['state']=='unknown'
        assert transport.reap() is True
        return {'protocol_lost':True,'owned_process_reaped':True}
    finally: cleanup(transport)

def kill_on_job_close():
    transport,directory,events=make_transport('cooperative_cancel')
    handle=None
    try:
        with patch('subprocess.Popen', replacement_popen): transport.start()
        pid,handle=child_handle(directory)
        assert not stopped(handle)
        transport.job.close()
        wait_for(lambda:stopped(handle))
        transport.proc.wait(timeout=5)
        # Resource cleanup only: no claim that protocol/normal result succeeded.
        return {'child_pid':pid,'host_handle_close_killed_child':True,'parent_exited':True}
    finally:
        if transport.proc:
            for stream in (transport.proc.stdin,transport.proc.stdout):
                if stream:
                    try: stream.close()
                    except (OSError,ValueError): pass
        if handle: kernel.CloseHandle(handle)

for name,callback in [('parent_exits_descendant_alive_then_job_reap',parent_exit),
        ('terminal_claim_is_not_tree_proof',terminal_not_proof),('cancel_and_duplicate_cancel',cancellation),
        ('authorization_denied_before_go',authorization_denied),('job_assign_failure_before_go',assignment_failed),
        ('cancelled_before_start_no_spawn',cancelled_before_start),('invalid_protocol_unknown_then_reap',malformed_protocol),
        ('kill_on_job_handle_close',kill_on_job_close)]:
    results.check(name,callback)
data=results.save(protection_events=blocked, all_owned_parent_processes_exited=all(p.poll() is not None for p in launched),
    scope='Real Windows Job and process handles; harmless protocol replacement; no real Claude/DSH/model')
print(json.dumps({'summary':{'passed':data['passed'],'failed':data['failed'],'all_parents_exited':data['all_owned_parent_processes_exited']}}))
