"""Fixed v1 host process protocol. Never reads Alice modules, Store or SQL."""
from __future__ import annotations
import json
from pathlib import Path
import sys
import threading
import time

# -I intentionally omits the working directory; load only this fixed sibling.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from local_transport import ClaudeLocalTransport, normalize_result

PROTOCOL = 'alice-worker-process-v1'
MAX_FRAME = 256 * 1024
lock = threading.Lock()


def emit(frame):
    data = (json.dumps({'protocol': PROTOCOL, **frame}, ensure_ascii=False) + '\n').encode()
    if len(data) > MAX_FRAME:
        return
    with lock:
        sys.stdout.buffer.write(data)
        sys.stdout.buffer.flush()


def main():
    # Crucial Job startup barrier: no subprocess/CLI before the host sends GO.
    line = sys.stdin.buffer.readline(MAX_FRAME + 1)
    if not line or len(line) > MAX_FRAME:
        return 2
    req = json.loads(line)
    if req.get('protocol') != PROTOCOL or req.get('operation') != 'start':
        return 2
    control = Path(req['control_dir'])
    control.mkdir(parents=True, exist_ok=True)
    identity = req['identity']
    config = control / 'mcp.json'
    config.write_text(json.dumps({'mcpServers': {'alice_gate': {
        'command': sys.executable,
        'args': [str(Path(__file__).resolve().parent / 'gate_server.py'),
                 '--audit', str(control / 'permission_audit.jsonl'),
                 '--sandbox', req['workspace'], '--gate-dir', str(control),
                 '--task-id', identity['task_id'], '--attempt-id', identity['attempt_id'],
                 '--epoch', identity['epoch'], '--policy-revision', str(identity['policy_revision'])]
    }}}), encoding='utf-8')
    transport = ClaudeLocalTransport(brief=req['brief'], sandbox=Path(req['workspace']),
        gate_mcp_config=config, max_turns=req['max_turns'],
        hard_deadline=req['deadline'], host_supervised=True, on_event=lambda kind, payload: emit(
            {'kind': 'event', 'type': kind, 'payload': payload}) if kind in {'system', 'assistant'} else None)
    try:
        transport.start()
        def controls():
            while True:
                line = sys.stdin.buffer.readline(4097)
                if not line or len(line) > 4096:
                    transport.cancel('host_disconnected')
                    return
                try:
                    command = json.loads(line)
                    valid = isinstance(command, dict) and command.get('operation') == 'cancel'
                except ValueError:
                    valid = False
                transport.cancel('host_cancel' if valid else 'host_protocol_invalid')
                return
        threading.Thread(target=controls, daemon=True).start()
        while transport.terminal() is None:
            time.sleep(.1)
        terminal = transport.terminal()
        if terminal.get('frame') == 'result':
            terminal = {'frame': 'result', 'payload': normalize_result(terminal['payload'])}
    except Exception:
        terminal = {'frame': 'local', 'state': 'unknown', 'code': 'adapter_failed'}
    confirmed = transport.reap()
    emit({'kind': 'terminal', 'terminal': terminal, 'termination_confirmed': confirmed})
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
