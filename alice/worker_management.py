"""Explicit GUI task organization, separate from execution grants and budgets."""
from __future__ import annotations

import json
import time

from .channels import ChannelError
from .worker_tasks import digest, identity
from .worker_transport import BridgeError


MANAGEABLE = frozenset({'succeeded', 'failed', 'cancelled', 'interrupted'})


class WorkerManagementMixin:
    def management_settings_locked(self):
        row = self.ledger.conn.execute('SELECT * FROM worker_management_settings WHERE id=1').fetchone()
        capabilities = ((self.bridge.hello or {}).get('management_capabilities', {})
                        if self.bridge is not None else {})
        if not isinstance(capabilities, dict):
            capabilities = {}
        return {'revision': row['revision'], 'sync_archive': bool(row['sync_archive']),
                'sync_delete': bool(row['sync_delete']),
                'capabilities': {'archive': capabilities.get('archive') is True,
                                 'restore': capabilities.get('restore') is True,
                                 # NEXT has no reviewed public session-deletion API.
                                 'delete': False}}

    def management_settings(self, payload):
        keys = {'session_epoch', 'request_id', 'revision', 'sync_archive', 'sync_delete'}
        if (not isinstance(payload, dict) or set(payload) != keys
                or type(payload['sync_archive']) is not bool or type(payload['sync_delete']) is not bool):
            raise ChannelError(400, 'invalid_request', '任务整理设置参数无效')
        receipt_payload = {'action': 'worker_task_settings', **payload}
        with self.control_lock, self.service._lock:
            epoch = self._epoch(payload)
            old = self.ledger.replay(epoch, payload['request_id'], receipt_payload)
            if old is not None:
                return self.status_locked()
            if self.closing or self.paused:
                raise ChannelError(409, 'draining', '正在停止协作任务，请稍后再修改整理设置')
            current = self.management_settings_locked()
            if type(payload['revision']) is not int or payload['revision'] != current['revision']:
                raise ChannelError(409, 'management_settings_changed', '任务整理设置已变化，请刷新')
            if payload['sync_delete']:
                raise ChannelError(409, 'dsh_delete_unsupported', '当前 DSH 没有已核验的会话删除接口；可仅删除 Alice 记录')
            # This is a saved preference, not a remote operation. Allow it
            # while disconnected; manage_task checks live capabilities before
            # recording or sending any synchronized archive/restore request.
            changed = (current['sync_archive'] != payload['sync_archive']
                       or current['sync_delete'] != payload['sync_delete'])
            with self.ledger.conn:
                if changed:
                    self.ledger.conn.execute('UPDATE worker_management_settings SET revision=revision+1,'
                                             'sync_archive=?,sync_delete=? WHERE id=1',
                                             (int(payload['sync_archive']), int(payload['sync_delete'])))
                result = self.status_locked()
                self.ledger.receipt(epoch, payload['request_id'], receipt_payload, result)
            return result

    def _management_ready_locked(self, task):
        if task['state'] not in MANAGEABLE or not task['agent_idle']:
            raise ChannelError(409, 'task_busy', '请先停止任务并确认 DSH 会话已空闲；未知状态不能归档或删除')
        if self.ledger.conn.execute("SELECT 1 FROM worker_attempts WHERE task_id=? AND state IN "
                "('queued','starting','running','collecting','stopping') LIMIT 1", (task['task_id'],)).fetchone():
            raise ChannelError(409, 'task_busy', '此任务仍有未结束的执行记录')

    def _management_result_locked(self, task_id, epoch, *, completed, synced=False, error=None):
        task = self.ledger.task(task_id, epoch)
        result = {'session_epoch': epoch, 'task_id': task_id, 'version': task['version'],
                  'archived': bool(task['archived']), 'deleted': bool(task['deleted']),
                  'session_closed': bool(task['session_closed']),
                  'management_pending': task['management_pending'],
                  'management_sync_dsh': bool(task['management_sync_dsh']),
                  'completed': completed, 'synced_dsh': synced}
        if error:
            result['error'] = error
        return result

    def _management_workspace_locked(self, task):
        if task['workspace_root']:
            return task['workspace_root']
        # Older tasks did not duplicate their grant's cwd. Recover only a
        # matching immutable grant receipt, never the current authorization.
        candidates = set()
        for row in self.ledger.conn.execute('SELECT result FROM worker_requests WHERE epoch=?', (task['epoch'],)):
            try:
                result = json.loads(row['result'])
                policy = result.get('policy') if isinstance(result, dict) else None
                if (result.get('revision') == task['policy_revision'] and isinstance(policy, dict)
                        and isinstance(policy.get('workspace_root'), str) and policy['workspace_root']):
                    candidates.add(policy['workspace_root'])
            except (ValueError, TypeError, AttributeError, RecursionError):
                continue
        if len(candidates) != 1:
            raise ChannelError(409, 'management_identity_unavailable', '缺少此专属会话原始工作目录的可靠记录，未同步 DSH')
        return candidates.pop()

    def _management_remote_payload_locked(self, task, action, request_id):
        if not task['session_id'] or not task['bridge_instance']:
            raise ChannelError(409, 'management_identity_unavailable', '此任务没有可核验的 DSH 专属会话身份')
        first = self.ledger.conn.execute('SELECT updated_at FROM worker_attempts WHERE task_id=? '
                                         'ORDER BY created_at,rowid LIMIT 1', (task['task_id'],)).fetchone()
        if first is None or first['updated_at'] < task['created_at']:
            raise ChannelError(409, 'management_identity_unavailable', '缺少此专属会话的可靠创建时间范围')
        value = {'task_id': task['task_id'], 'request_id': request_id, 'operation': action,
                 'session_id': task['session_id'], 'origin_instance': task['bridge_instance'],
                 'cwd': self._management_workspace_locked(task),
                 'created_after': int(task['created_at'] * 1000),
                 'created_before': int(first['updated_at'] * 1000) + 1}
        if type(task['session_created_at']) is int and task['session_created_at'] > 0:
            value['session_created_at'] = task['session_created_at']
            value['created_after'] = task['session_created_at'] - 1
            value['created_before'] = task['session_created_at'] + 1
        return value

    def _apply_management_locked(self, task, action, *, sync):
        """Caller owns the transaction, including dependency erasure."""
        task_id = task['task_id']
        if action == 'delete':
            # Tombstones retain run usage, remote identity, attempt IDs and
            # receipts. Deleting rows would refund budget and allow replay.
            if not task['deleted']:
                self.ledger.revoke(task_id)
                self.service.store.revoke_worker_context(task_id)
            self.ledger.conn.execute("UPDATE worker_reports SET report_state='suppressed' "
                                     "WHERE task_id=? AND report_state IN ('pending','pending_notice','summarizing')", (task_id,))
            self.ledger.conn.execute('UPDATE worker_tasks SET deleted=1 WHERE task_id=?', (task_id,))
        else:
            # Archive is only list organization. It does not renew or revoke
            # body eligibility, and cannot resurrect previously revoked prose.
            self.ledger.conn.execute('UPDATE worker_tasks SET archived=? WHERE task_id=?',
                                     (int(action == 'archive'), task_id))
        self.ledger.conn.execute('UPDATE worker_tasks SET management_pending=NULL,management_sync_dsh=?,'
                                 'version=version+1 WHERE task_id=?', (int(sync), task_id))
        self.ledger.event(task_id, 'task_' + {'archive': 'archived', 'restore': 'restored', 'delete': 'deleted'}[action],
                          payload={'text': {'archive': '任务已归档；原有正文资格与执行次数不变。',
                                            'restore': '任务已恢复到当前列表；未续期或重新授权。',
                                            'delete': 'Alice 任务正文及派生内容已删除；执行与费用账本保留。'}[action]})

    def _finish_management_receipts_locked(self, operation, result, *, rejected=False):
        encoded = json.dumps(result, ensure_ascii=False)
        now = time.time()
        pending = self.ledger.conn.execute('SELECT epoch,request_id,digest FROM worker_management_operations '
            "WHERE epoch=? AND task_id=? AND action=? AND state IN ('pending','retry_pending')",
            (operation['epoch'], operation['task_id'], operation['action'])).fetchall()
        self.ledger.conn.execute('UPDATE worker_management_operations SET state=?,result=?,updated_at=? '
                                 "WHERE epoch=? AND task_id=? AND action=? AND state IN ('pending','retry_pending')",
                                 ('rejected' if rejected else 'completed', encoded, now,
                                  operation['epoch'], operation['task_id'], operation['action']))
        for row in pending:
            self.ledger.conn.execute('INSERT INTO worker_requests(epoch,request_id,digest,result) VALUES(?,?,?,?)',
                                     (row['epoch'], row['request_id'], row['digest'], encoded))

    def manage_task(self, action, payload):
        keys = {'session_epoch', 'task_id', 'request_id', 'revision', 'settings_revision'}
        if not isinstance(payload, dict) or set(payload) != keys or action not in {'archive', 'restore', 'delete'}:
            raise ChannelError(400, 'invalid_request', '任务整理参数无效')
        identity(payload['request_id'])
        receipt_payload = {'action': action, **payload}
        with self.control_lock:
            with self.service._lock:
                epoch = self._epoch(payload)
                old = self.ledger.replay(epoch, payload['request_id'], receipt_payload)
                if old is not None:
                    return old
                if self.closing or self.paused:
                    raise ChannelError(409, 'draining', '正在停止协作任务，请稍后再整理')
                task = self.ledger.task(payload['task_id'], epoch)
                operation = self.ledger.conn.execute("SELECT * FROM worker_management_operations "
                    "WHERE task_id=? AND state='pending'", (task['task_id'],)).fetchone()
                reserved = self.ledger.conn.execute('SELECT state FROM worker_management_operations '
                    'WHERE epoch=? AND request_id=?', (epoch, payload['request_id'])).fetchone()
                if reserved is not None:
                    # An HTTP replay is not consent to issue another remote
                    # request. Only a new explicit GUI action gets a new ID.
                    return self._management_result_locked(task['task_id'], epoch, completed=False,
                                                          error='management_pending')
                settings = self.management_settings_locked()
                if type(payload['revision']) is not int or payload['revision'] != task['version']:
                    raise ChannelError(409, 'task_changed', '任务版本已变化，请重新打开详情')
                if (type(payload['settings_revision']) is not int
                        or payload['settings_revision'] != settings['revision']):
                    raise ChannelError(409, 'management_settings_changed', '任务整理设置已变化，请刷新后重试')
                self._management_ready_locked(task)
                if task['deleted'] and action != 'delete':
                    raise ChannelError(404, 'not_found', '此协作任务已删除，不能恢复')
                if task['management_pending'] and operation is None:
                    raise ChannelError(409, 'management_pending', '任务整理记录不完整，不能执行冲突操作')
                if operation is not None and operation['action'] != action:
                    raise ChannelError(409, 'management_pending', '请先确认上一次整理操作，不能切换操作')
                sync = operation is not None or settings['sync_delete' if action == 'delete' else 'sync_archive']
                if not sync:
                    with self.service.store.source_transaction():
                        self._apply_management_locked(task, action, sync=False)
                        result = self._management_result_locked(task['task_id'], epoch, completed=True)
                        self.ledger.receipt(epoch, payload['request_id'], receipt_payload, result)
                    return result
                if action == 'delete':
                    raise ChannelError(409, 'dsh_delete_unsupported', '当前 DSH 没有已核验的会话删除接口；Alice 记录未删除')
                bridge, generation = self.bridge, self.generation
                if (bridge is None or self.connection_state != 'connected'
                        or not settings['capabilities'][action]):
                    raise ChannelError(409, 'management_unavailable', '请先连接支持会话整理的 DSH 插件；Alice 记录未移动')
                retrying = operation is not None
                if operation is None:
                    remote_payload = self._management_remote_payload_locked(task, action, payload['request_id'])
                    now = time.time()
                    with self.service.store.source_transaction():
                        self.ledger.conn.execute('INSERT INTO worker_management_operations '
                            '(epoch,request_id,task_id,action,digest,bridge_instance,session_id,remote_payload,'
                            'settings_revision,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                            (epoch, payload['request_id'], task['task_id'], action, digest(receipt_payload),
                             task['bridge_instance'], task['session_id'], json.dumps(remote_payload),
                             settings['revision'], now, now))
                        self.ledger.conn.execute('UPDATE worker_tasks SET management_pending=?,management_sync_dsh=1,'
                                                 'version=version+1 WHERE task_id=?', (action, task['task_id']))
                        self.ledger.event(task['task_id'], 'management_requested',
                                          payload={'text': '已登记 DSH 会话整理请求；等待远端确认后再移动 Alice 记录。'})
                    operation = self.ledger.conn.execute('SELECT * FROM worker_management_operations '
                        'WHERE epoch=? AND request_id=?', (epoch, payload['request_id'])).fetchone()
                else:
                    remote_payload = json.loads(operation['remote_payload'])
                    remote_payload['request_id'] = payload['request_id']
                    now = time.time()
                    with self.service.store.source_transaction():
                        self.ledger.conn.execute('INSERT INTO worker_management_operations '
                            '(epoch,request_id,task_id,action,digest,bridge_instance,session_id,remote_payload,'
                            'settings_revision,state,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                            (epoch, payload['request_id'], task['task_id'], action, digest(receipt_payload),
                             operation['bridge_instance'], operation['session_id'], json.dumps(remote_payload),
                             operation['settings_revision'], 'retry_pending', now, now))

            # One call per explicit GUI action. A retry repeats the original
            # immutable intent; the plugin first reads whether its target holds.
            error = None
            try:
                remote = bridge.call('maintain', remote_payload)
                if (remote.get('session_id') != operation['session_id'] or remote.get('operation') != action
                        or remote.get('status') != ('archived' if action == 'archive' else 'restored')
                        or remote.get('archived') is not (action == 'archive')
                        or remote.get('history_deleted') is not False or remote.get('model_invoked') is not False
                        or remote.get('agent_idle') is not True):
                    raise BridgeError('invalid_bridge_response')
            except BridgeError as exc:
                error = exc
            with self.service._lock:
                self.service._channels.check_epoch(epoch)
                if self.closing or self.paused or generation != self.generation or bridge is not self.bridge:
                    raise ChannelError(409, 'session_changed', '会话已变化；整理结果待重新确认')
                task = self.ledger.task(payload['task_id'], epoch)
                if task['management_pending'] != action:
                    raise ChannelError(409, 'management_pending', '整理记录已变化，未移动 Alice 任务')
                if error is not None and (error.uncertain or retrying):
                    return self._management_result_locked(task['task_id'], epoch, completed=False, error=error.code)
                with self.service.store.source_transaction():
                    if error is None:
                        if remote.get('dispose_confirmed') is True:
                            self.ledger.conn.execute('UPDATE worker_tasks SET session_closed=1 WHERE task_id=?',
                                                     (task['task_id'],))
                        self._apply_management_locked(task, action, sync=True)
                        result = self._management_result_locked(task['task_id'], epoch, completed=True, synced=True)
                    else:
                        self.ledger.conn.execute('UPDATE worker_tasks SET management_pending=NULL,version=version+1 '
                                                 'WHERE task_id=?', (task['task_id'],))
                        self.ledger.event(task['task_id'], 'management_rejected',
                                          payload={'text': 'DSH 拒绝会话整理；Alice 任务位置未改变。'})
                        result = self._management_result_locked(task['task_id'], epoch, completed=False, error=error.code)
                    self._finish_management_receipts_locked(operation, result, rejected=error is not None)
                return result
