"""Host-owned permission decisions over the fixed v2 file protocol.

Every use returns through the host's current task/policy check; the CLI process
never carries a transferable allow-task cache. This is native supervision,
not an OS sandbox against a malicious same-user process.
"""
from __future__ import annotations
import hashlib
import json
import os
import re
from itertools import islice
import time
import uuid
from pathlib import Path
from .channels import ChannelError

MAX_ACTIVE_ATTEMPTS = 8
MAX_SCAN_ENTRIES = 512
MAX_PENDING = 128
MAX_SCAN_BYTES = 1024 * 1024
MAX_REQUEST_BYTES = 65536


class PermissionAnswerConflict(ValueError):
    pass


BINDING = ('id', 'task_id', 'attempt_id', 'epoch', 'worker_id', 'policy_revision', 'expires_at', 'action_digest', 'resource_digest')


def claude_gate_dir(policy):
    root = policy.get('workspace_root')
    return Path(root).resolve() / 'gate' if root else None


def action_digest(req):
    return hashlib.sha256(json.dumps({'tool': req['tool'], 'input': req['input']},
        sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


class WorkerPermissionsMixin:
    def _revoke_permission_files_locked(self, task):
        """Withdraw already-published answers before the gate consumes them."""
        root = Path(task['workspace_root']).resolve() / 'gate' / task['task_id']
        for row in self.ledger.conn.execute('SELECT attempt_id FROM worker_attempts WHERE task_id=?', (task['task_id'],)):
            directory = root / row['attempt_id']
            if directory.is_dir():
                try:
                    (directory / 'revoked').write_text('revoked', encoding='ascii')
                except OSError:
                    # Lifecycle cancellation still proceeds; process Job reaping
                    # is the outer boundary. Do not undo the revocation intent.
                    pass

    def _permission_identity_current_locked(self, req):
        if (req.get('worker_id') != 'claude-agent' or req.get('task_id') not in self.claude_threads
                or not re.fullmatch(r'pq_[0-9a-f]{32}', str(req.get('id', '')))):
            return False
        try:
            task = self.ledger.task(req['task_id'], self.service._channels.epoch)
            attempt = self.ledger.conn.execute('SELECT task_id,state FROM worker_attempts WHERE attempt_id=?',
                                              (req.get('attempt_id'),)).fetchone()
            policy = self._worker_policy_locked('claude-agent')
            return (attempt is not None and attempt['task_id'] == task['task_id']
                and attempt['state'] in {'starting', 'running', 'waiting_permission'}
                and task['bridge_instance'] == 'local:claude-agent' and not task['cancel_requested']
                and not self.closing and not self.stop.is_set()
                and req['epoch'] == task['epoch'] == self.service._channels.epoch
                and req['policy_revision'] == task['policy_revision'] == policy['revision']
                and self.ledger.visible(task) and policy.get('native_sandbox_ack')
                and time.time() < req['expires_at'] <= task['expires_at'])
        except (KeyError, TypeError, ValueError, ChannelError):
            return False

    def _permission_current_locked(self, req):
        try:
            return (self._permission_identity_current_locked(req) and isinstance(req.get('input'), dict)
                    and isinstance(req.get('tool'), str) and len(req['tool']) <= 200
                    and req['action_digest'] == action_digest(req))
        except (KeyError, TypeError, ValueError):
            return False

    def _pending_permissions_locked(self, gate_dir):
        # Never enumerate the historical task tree. Only live host-owned attempt
        # directories consume this bounded scan, before valid-result limits apply.
        status = {'active_attempts': 0, 'scanned_entries': 0, 'read_bytes': 0,
                  'valid_pending': 0, 'answered_pending': 0, 'ignored_entries': 0,
                  'exhausted': False, 'reason': None, 'read_errors': 0}
        self.permission_scan_status = status
        if gate_dir is None:
            return []
        active_ids = list(islice(self.claude_threads, MAX_ACTIVE_ATTEMPTS + 1))
        if len(active_ids) > MAX_ACTIVE_ATTEMPTS:
            status.update(exhausted=True, reason='active_attempt_limit')
        active_ids = active_ids[:MAX_ACTIVE_ATTEMPTS]
        if not active_ids:
            return []
        rows = self.ledger.conn.execute(
            "SELECT t.task_id,a.attempt_id FROM worker_tasks t JOIN worker_attempts a USING(task_id) "
            "WHERE t.epoch=? AND t.bridge_instance='local:claude-agent' AND t.task_id IN ("
            + ','.join('?' for _ in active_ids) + ") AND a.state IN ('starting','running','waiting_permission') LIMIT ?",
            (self.service._channels.epoch, *active_ids, MAX_ACTIVE_ATTEMPTS + 1)).fetchall()
        if len(rows) > MAX_ACTIVE_ATTEMPTS:
            status.update(exhausted=True, reason='active_attempt_limit')
        out = []
        deadline = time.monotonic() + .2
        for row in rows[:MAX_ACTIVE_ATTEMPTS]:
            directory = gate_dir / row['task_id'] / row['attempt_id']
            status['active_attempts'] += 1
            try:
                with os.scandir(directory) as entries:
                    for entry in entries:
                        if (status['scanned_entries'] >= MAX_SCAN_ENTRIES
                                or status['read_bytes'] >= MAX_SCAN_BYTES
                                or len(out) >= MAX_PENDING or time.monotonic() >= deadline):
                            status.update(exhausted=True, reason='scan_budget_exhausted')
                            return out
                        status['scanned_entries'] += 1
                        if not entry.name.endswith('.pending.json') or not entry.is_file(follow_symlinks=False):
                            continue
                        path = Path(entry.path)
                        try:
                            if entry.stat(follow_symlinks=False).st_size > MAX_REQUEST_BYTES:
                                status['ignored_entries'] += 1
                                continue
                            remaining = min(MAX_REQUEST_BYTES + 1, MAX_SCAN_BYTES - status['read_bytes'])
                            with path.open('rb') as stream:
                                raw = stream.read(remaining)
                            status['read_bytes'] += len(raw)
                            if len(raw) > MAX_REQUEST_BYTES:
                                status['ignored_entries'] += 1
                                continue
                            req = json.loads(raw)
                            if (not isinstance(req, dict) or not self._permission_current_locked(req)
                                    or req['task_id'] != row['task_id'] or req['attempt_id'] != row['attempt_id']
                                    or entry.name != req['id'] + '.pending.json'):
                                status['ignored_entries'] += 1
                                continue
                            key = self._permission_key(req)
                            req['resolved_resource'] = key[-1] if key is not None else None
                            req['resource_digest'] = hashlib.sha256(json.dumps(req['resolved_resource']).encode()).hexdigest()
                            req['age_seconds'] = max(0, int(time.time() - req['ts']))
                            # First answer is immutable. Neither GUI nor the
                            # allow-task cache may re-answer a decided ID.
                            if (directory / 'answers' / (req['id'] + '.json')).exists():
                                status['answered_pending'] += 1
                                continue
                            out.append(req)
                            status['valid_pending'] += 1
                        except (OSError, ValueError, TypeError, KeyError, RuntimeError):
                            status['read_errors'] += 1
            except FileNotFoundError:
                continue  # adapter may not have created its control directory yet
            except OSError:
                status['read_errors'] += 1
        return out

    def _permission_key(self, req):
        # Shell/unknown tools have no provable resource scope: allow_task becomes
        # one exact approval, never a wildcard. File grants are exact full actions.
        if req['tool'] not in {'Read', 'Write', 'Edit', 'NotebookEdit'}:
            return None
        path = req['input'].get('file_path') or req['input'].get('notebook_path')
        if not isinstance(path, str) or not path:
            return None
        task = self.ledger.task(req['task_id'], req['epoch'])
        base = Path(task['workspace_root']) / ('claude_task_' + task['task_id'][:8])
        target = Path(path)
        try:
            real = (target if target.is_absolute() else base / target).resolve()
        except (OSError, ValueError, RuntimeError):
            return None
        return (req['epoch'], req['task_id'], req['attempt_id'], req['worker_id'],
                req['policy_revision'], req['action_digest'], os.path.normcase(str(real)))

    def _write_permission_answer_locked(self, root, req, decision):
        target = root / req['task_id'] / req['attempt_id'] / 'answers'
        target.mkdir(parents=True, exist_ok=True)
        destination = target / (req['id'] + '.json')
        data = {key: req[key] for key in BINDING}
        data.update(decision=decision, answered_at=time.time())
        temporary = target / (uuid.uuid4().hex + '.tmp')
        try:
            temporary.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
            try:
                os.link(temporary, destination)
            except FileExistsError:
                with destination.open('rb') as stream:
                    raw = stream.read(8193)
                if len(raw) > 8192:
                    raise ValueError('质询答复记录超限')
                previous = json.loads(raw)
                if not isinstance(previous, dict):
                    raise ValueError('质询答复记录无效')
                if any(previous.get(key) != data[key] for key in (*BINDING, 'decision')):
                    raise PermissionAnswerConflict('此质询已有不同答复')
        finally:
            temporary.unlink(missing_ok=True)

    def _permission_diagnostic(self, code):
        # Public fixed codes and bounded counters only; no raw paths or input.
        previous = getattr(self, 'permission_answer_diagnostic', {})
        self.permission_answer_diagnostic = {'count': min(previous.get('count', 0) + 1, 1000000),
                                             'last_code': code}

    def _answer_cached_permissions_locked(self, gate_dir, task_id):
        root = gate_dir.parent.parent
        now = time.time()
        self.permission_grants = {k: expiry for k, expiry in self.permission_grants.items() if expiry > now}
        for req in self._pending_permissions_locked(root):
            if req['task_id'] != task_id:
                continue
            key = self._permission_key(req)
            if key is not None and key in self.permission_grants:
                try:
                    self._write_permission_answer_locked(root, req, 'allow_once')
                except PermissionAnswerConflict:
                    self._permission_diagnostic('first_answer_preserved')
                except (OSError, ValueError, TypeError):
                    self._permission_diagnostic('answer_write_unavailable')
                # A local answer conflict is not an execution/transport failure.

    def _permission_replay_locked(self, root, payload):
        # An answer can already have been consumed and its pending file removed.
        # Returning its receipt does not execute or renew that authorization.
        if root is None or not self._permission_identity_current_locked(payload):
            return None
        target = root / payload['task_id'] / payload['attempt_id'] / 'answers' / (payload['id'] + '.json')
        try:
            with target.open('rb') as stream:
                raw = stream.read(8193)
            if len(raw) > 8192:
                raise ValueError('质询答复记录超限')
            previous = json.loads(raw)
        except FileNotFoundError:
            return None
        if not isinstance(previous, dict):
            raise ValueError('质询答复记录无效')
        if any(previous.get(key) != payload.get(key) for key in (*BINDING, 'decision')):
            raise PermissionAnswerConflict('此质询已有不同答复，请刷新')
        return {'id': payload['id'], 'decision': payload['decision'], 'delivered': True, 'replayed': True}

    def permissions(self, action='list', payload=None):
        with self.service._lock:
            root = claude_gate_dir(self._worker_policy_locked('claude-agent'))
            if action == 'answer':
                if not isinstance(payload, dict) or payload.get('decision') not in {'allow_once', 'allow_task', 'deny'}:
                    raise ValueError('质询答复参数无效')
                replay = self._permission_replay_locked(root, payload)
                if replay is not None:
                    return replay
            pending = self._pending_permissions_locked(root)
            if action == 'list':
                return {'pending': pending, 'scan': dict(self.permission_scan_status),
                        'answer_diagnostic': dict(getattr(self, 'permission_answer_diagnostic', {}))}
            if action != 'answer' or not isinstance(payload, dict):
                raise ValueError('质询答复参数无效')
            req = next((r for r in pending if r['id'] == payload.get('id')), None)
            if req is None or any(req[key] != payload.get(key) for key in BINDING):
                raise ValueError('质询动作、身份或授权已变化，或活动扫描未完整，请刷新')
            decision = payload['decision']
            self._write_permission_answer_locked(root, req, decision)
            key = self._permission_key(req)
            if decision == 'allow_task' and key is not None:
                self.permission_grants[key] = req['expires_at']
            return {'id': req['id'], 'decision': decision, 'delivered': True,
                    'scope': 'exact_action_until_expiry' if decision == 'allow_task' and key else 'once'}
