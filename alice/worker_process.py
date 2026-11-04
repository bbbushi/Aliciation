"""Fixed worker process protocol; no CLI-specific parser or addon import.

The adapter waits for the first stdin frame before creating any descendants.
On Windows the host assigns that waiting process to a non-breakaway Job before
sending GO. Only Job ActiveProcesses=0 proves the owned tree is stopped.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

MAX_FRAME = 256 * 1024


class _WindowsJob:
    def __init__(self):
        import ctypes as c
        from ctypes import wintypes as w
        self.c = c
        self.k = c.WinDLL('kernel32', use_last_error=True)
        class Basic(c.Structure):
            _fields_ = [('process_time', c.c_int64), ('job_time', c.c_int64),
                        ('flags', w.DWORD), ('min_ws', c.c_size_t), ('max_ws', c.c_size_t),
                        ('active_limit', w.DWORD), ('affinity', c.c_size_t),
                        ('priority', w.DWORD), ('scheduling', w.DWORD)]
        class Io(c.Structure):
            _fields_ = [(name, c.c_uint64) for name in ('read_ops', 'write_ops', 'other_ops',
                                                      'read_bytes', 'write_bytes', 'other_bytes')]
        class Extended(c.Structure):
            _fields_ = [('basic', Basic), ('io', Io), ('process_memory', c.c_size_t),
                        ('job_memory', c.c_size_t), ('peak_process', c.c_size_t), ('peak_job', c.c_size_t)]
        class Accounting(c.Structure):
            _fields_ = [(name, c.c_int64) for name in ('user', 'kernel', 'period_user', 'period_kernel')] + [
                (name, w.DWORD) for name in ('faults', 'total', 'active', 'terminated')]
        self.Accounting = Accounting
        self.k.CreateJobObjectW.argtypes = (c.c_void_p, w.LPCWSTR)
        self.k.CreateJobObjectW.restype = w.HANDLE
        for name, args in {
            'SetInformationJobObject': (w.HANDLE, c.c_int, c.c_void_p, w.DWORD),
            'AssignProcessToJobObject': (w.HANDLE, w.HANDLE),
            'TerminateJobObject': (w.HANDLE, w.UINT),
            'QueryInformationJobObject': (w.HANDLE, c.c_int, c.c_void_p, w.DWORD, c.c_void_p),
            'CloseHandle': (w.HANDLE,),
        }.items():
            getattr(self.k, name).argtypes = args
            getattr(self.k, name).restype = w.BOOL
        self.handle = self.k.CreateJobObjectW(None, None)
        if not self.handle:
            raise OSError(c.get_last_error(), 'job_create_failed')
        info = Extended()
        info.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE; no breakaway.
        if not self.k.SetInformationJobObject(self.handle, 9, c.byref(info), c.sizeof(info)):
            self.close()
            raise OSError(c.get_last_error(), 'job_limits_failed')

    def assign(self, proc):
        if not self.k.AssignProcessToJobObject(self.handle, int(proc._handle)):
            raise OSError(self.c.get_last_error(), 'job_assign_failed')

    def empty(self):
        info = self.Accounting()
        if not self.k.QueryInformationJobObject(self.handle, 1, self.c.byref(info), self.c.sizeof(info), None):
            return False
        return info.active == 0

    def terminate(self):
        self.k.TerminateJobObject(self.handle, 1)

    def close(self):
        if self.handle:
            self.k.CloseHandle(self.handle)
            self.handle = None


class WorkerProcessTransport:
    def __init__(self, *, brief, sandbox, control_dir, identity, max_turns, on_event, hard_deadline=900, authorize_start=None):
        self.request = {'protocol': 'alice-worker-process-v1', 'operation': 'start',
                        'brief': brief, 'workspace': str(sandbox), 'control_dir': str(control_dir), 'identity': identity,
                        'max_turns': max_turns, 'deadline': hard_deadline}
        self.on_event = on_event
        self.authorize_start = authorize_start
        self.proc = None
        self.job = None
        self.assigned = False
        self.go_sent = False
        self.done = threading.Event()
        self.cancelled = threading.Event()
        self.lock = threading.Lock()
        self.reap_lock = threading.Lock()
        self.result = None
        self.adapter_stopped = False
        self.protocol_lost = False
        self.cancel_sent = False
        self.termination_confirmed = False
        self.deadline = hard_deadline

    def start(self):
        entry = Path(__file__).resolve().parents[1] / 'addons' / 'claude_agent' / 'adapter.py'
        if self.cancelled.is_set():
            self._finish({'frame': 'local', 'state': 'cancelled', 'code': 'cancelled_before_go'})
            return
        # No task or descendant exists before GO. Assign failure closes stdin
        # and terminates only this owned waiting Popen handle, never an old PID.
        if os.name == 'nt':
            self.job = _WindowsJob()
        self.proc = subprocess.Popen([sys.executable, '-I', str(entry)], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        try:
            if self.job is not None:
                self.job.assign(self.proc)
                self.assigned = True
            if self.cancelled.is_set():
                self._finish({'frame': 'local', 'state': 'cancelled', 'code': 'cancelled_before_go'})
                return
            if self.authorize_start:
                self.authorize_start()
            self.go_sent = True  # partial write/flush may already let the adapter launch
            self.proc.stdin.write((json.dumps(self.request, ensure_ascii=False) + '\n').encode())
            self.proc.stdin.flush()
            threading.Thread(target=self._read, daemon=True).start()
            threading.Thread(target=self._watch, daemon=True).start()
        except Exception:
            self.proc.terminate()
            raise

    def _finish(self, result):
        with self.lock:
            if self.result is None:
                self.result = result
        self.done.set()

    def _read(self):
        try:
            while True:
                line = self.proc.stdout.readline(MAX_FRAME + 1)
                if not line:
                    raise ValueError('eof')
                if len(line) > MAX_FRAME or not line.endswith(b'\n'):
                    raise ValueError('oversize')
                frame = json.loads(line)
                if not isinstance(frame, dict) or frame.get('protocol') != 'alice-worker-process-v1':
                    raise ValueError('protocol')
                if frame.get('kind') == 'terminal':
                    terminal = frame.get('terminal')
                    if not isinstance(terminal, dict):
                        raise ValueError('terminal')
                    self.adapter_stopped = frame.get('termination_confirmed') is True
                    self._finish(terminal)
                    return
                if frame.get('kind') == 'event' and isinstance(frame.get('payload'), dict):
                    self.on_event(frame.get('type'), frame['payload'])
        except Exception:
            self.protocol_lost = True
            self._finish({'frame': 'local', 'state': 'unknown', 'code': 'adapter_protocol_lost'})

    def _watch(self):
        if not self.done.wait(self.deadline + 15):
            self.cancel('adapter_deadline')

    def terminal(self):
        with self.lock:
            return self.result

    def cancel(self, reason='host_cancel'):
        self.cancelled.set()
        self._request_cancel()
        # Allow the adapter to reap its POSIX process group before host teardown.
        self.done.wait(12)
        self._finish({'frame': 'local', 'state': 'cancelled', 'code': reason})

    def _request_cancel(self):
        # One bounded frame, sent at most once; EOF also asks the fixed adapter
        # to reap its owned CLI group. No historic PID is ever signalled here.
        with self.lock:
            if self.cancel_sent:
                return
            self.cancel_sent = True
        try:
            if self.proc and self.proc.stdin:
                self.proc.stdin.write(b'{"operation":"cancel"}\n')
                self.proc.stdin.flush()
                self.proc.stdin.close()
        except (OSError, ValueError):
            pass

    def can_accept_human_recovery(self):
        """An exited POSIX adapter leaves no live supervisory handle to recover.

        This permits explicit human attestation only; it supplies no proof that
        descendants stopped. A live adapter or Windows Job stays under reap.
        """
        return (os.name == 'posix' and self.protocol_lost and self.proc is not None
                and self.proc.poll() is not None)

    def reap(self):
        with self.reap_lock:
            if self.proc is None:
                self.termination_confirmed = True
                if self.job:
                    self.job.close()
            elif self.job is not None and self.assigned:
                self.job.terminate()
                deadline = time.monotonic() + 8
                while not self.job.empty() and time.monotonic() < deadline:
                    time.sleep(.05)
                self.termination_confirmed = self.job.empty()
            elif not self.go_sent:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=5)
                    self.termination_confirmed = True  # trusted waiting adapter never received GO
                except subprocess.TimeoutExpired:
                    pass
            elif os.name == 'posix':
                if self.protocol_lost and self.proc.poll() is None:
                    self._request_cancel()
                    try:
                        self.proc.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        pass
                self.termination_confirmed = self.adapter_stopped
            # Root exit or a terminal frame alone is never Windows tree evidence.
            if self.termination_confirmed:
                try:
                    if self.proc:
                        self.proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.termination_confirmed = False
                if self.job:
                    self.job.close()
            return self.termination_confirmed
