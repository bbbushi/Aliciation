"""Harmless fixed-protocol replacement. No Alice/Claude/model/network imports."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

if len(sys.argv) > 1 and sys.argv[1] == '--child':
    root = Path(sys.argv[2])
    (root/'child-ready.json').write_text(json.dumps({'pid': os.getpid()}), encoding='utf-8')
    # Independent finite lifetime even if the parent/host unexpectedly disappears.
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        time.sleep(.05)
    raise SystemExit(0)

raw = sys.stdin.readline()
if not raw:
    raise SystemExit(0)
request = json.loads(raw)
assert request['operation'] == 'start'
root = Path(request['workspace'])
root.mkdir(parents=True, exist_ok=True)
(root/'go-received.json').write_text(json.dumps({'pid': os.getpid()}), encoding='utf-8')
scenario = request['brief']
def emit(kind, **extra):
    print(json.dumps({'protocol': 'alice-worker-process-v1', 'kind': kind, **extra}), flush=True)
if scenario == 'invalid_protocol':
    print('{"protocol":"wrong"}', flush=True)
    time.sleep(5)
    raise SystemExit(0)
child = subprocess.Popen([sys.executable, '-I', '-S', '-B', __file__, '--child', str(root)],
                         creationflags=subprocess.CREATE_NO_WINDOW,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
deadline = time.monotonic() + 8
while not (root/'child-ready.json').exists() and time.monotonic() < deadline:
    time.sleep(.02)
emit('event', type='child_started', payload={'pid': child.pid})
if scenario == 'parent_exits':
    raise SystemExit(0)
if scenario == 'terminal_claim':
    emit('terminal', termination_confirmed=True, terminal={'frame':'result','state':'succeeded'})
    raise SystemExit(0)
try:
    command = sys.stdin.readline()
    if command:
        assert json.loads(command)['operation'] == 'cancel'
finally:
    child.terminate()
    child.wait(timeout=5)
    emit('terminal', termination_confirmed=True, terminal={'frame':'local','state':'cancelled'})
