"""GUI-only DSH authorization and records, with an independent execution slot."""
from __future__ import annotations

import json
import math
import time

from .channels import ChannelError
from .worker_tasks import ACTIVE, WorkerLedger, bounded_int
from .worker_transport import inspect_dsh_installation
from .worker_runtime import WorkerRuntime
from .worker_management import WorkerManagementMixin


class WorkerCoordinator(WorkerManagementMixin, WorkerRuntime):
    def __init__(self, service):
        self.service = service
        self.closing = False
        self.initialize_runtime()
        self.installation = inspect_dsh_installation()
        self.bind_store()

    def bind_store(self):
        self.ledger = WorkerLedger(self.service.store)
        self.ledger.interrupt()
        self.cancel_attempted.clear()
        # The grant and its budget are durable. Connection credentials/handles
        # stay in memory: startup never pairs, sends, or replays historical work.
        self.paused = False

    def _epoch(self, payload):
        if not isinstance(payload, dict):
            raise ChannelError(400, 'invalid_request', '协作请求必须是对象')
        self.service._channels.check_epoch(payload.get('session_epoch'))
        return self.service._channels.epoch

    def registration(self):
        connected = self.bridge is not None and self.connection_state == 'connected' and not self.paused and not self.closing
        enabled = bool(self.ledger.policy()['enabled'])
        result = {
            'name': 'dsh-agent', 'display_name': 'DSH', 'state': 'ready' if connected else self.connection_state,
            'enabled': enabled, 'available': connected,
            'reason': ('插件已连接；已保存授权继续有效，补充前会核对原会话状态。' if enabled else
                       '插件已连接；派工授权尚未开启，请在设置 → DSH 确认授权。') if connected else
                      ('尚未连接；已保存授权和任务记录，请到设置 → DSH 连接。' if enabled else
                       '尚未连接；授权设置和任务记录已保留，请到设置 → DSH 连接并确认授权。'),
            'permission_summary': '仅操作插件自建会话；原生权限不是操作系统沙箱。次数与时限不构成费用硬帽。',
            'manual_setup': {'title': '插件连接步骤', 'steps': [
                '在 DSH NEXT 安装并启用 0.3.0 或更新的 Alice 桥接插件；从旧版本更新时重新加载一次。',
                '点击“生成令牌并连接”；独立令牌在本机加密保存，插件自动读取，无需复制或设置环境变量。',
                '已保存授权会继续保留；首次使用或授权关闭时，确认目录、原生模式、执行次数和时限。'],
                'note': '自动令牌目前支持 Windows。断开保留令牌、授权、任务记录和已用次数；关闭授权才撤销任务资料。高级设置保留手动配对。'},
            'capabilities': self.bridge.hello.get('capabilities', {}) if self.bridge else {},
            'verification': 'authenticated_plugin' if connected else 'installation_metadata',
            'runtime_checked': connected,
        }
        if self.installation != 'reviewed_installation':
            result['state'] = 'unverified'
            result['reason'] = ('DSH 安装版本与已审阅版本不同，需要重新核验接口和权限。'
                                if self.installation == 'version_changed' else
                                '尚不能确认指定位置的 DSH 安装元数据；未读取个人配置，也未启动 DSH。')
            result['capabilities'] = {}
            # Exact labels are verified only for the pinned installation.
            result.pop('manual_setup', None)
        return result

    def status_locked(self):
        policy = self.ledger.policy()
        used = self.ledger.runs_used()
        return {'session_epoch': self.service._channels.epoch, 'revision': policy['revision'],
                'policy': {**{k: policy[k] for k in ('max_runs', 'workspace_root', 'permission_mode', 'max_seconds')},
                           'enabled': bool(policy['enabled']), 'native_permissions_ack': bool(policy['native_permissions_ack']),
                           'runs_used': used, 'remaining_runs': max(0, policy['max_runs'] - used)},
                'connection': {'connected': self.bridge is not None and self.connection_state == 'connected'
                                            and not self.paused and not self.closing,
                               'has_connection': self.bridge is not None,
                               'state': self.connection_state},
                'task_management': self.management_settings_locked(),
                'credential': dict(self.credential_status),
                'collaborators': [self.registration()]}

    def collaborators(self, payload=None):
        if payload is None:
            credential_status = self.credentials.metadata()
            with self.service._lock:
                self.credential_status = credential_status
                return self.status_locked()
        if not isinstance(payload, dict):
            raise ChannelError(400, 'invalid_request', '协作请求必须是对象')
        return self.connection_action(payload) if 'action' in payload else self.save_policy(payload)

    def _cancel_locked(self, task_id, epoch):
        task = self.ledger.task(task_id, epoch)
        if task['state'] in ACTIVE | {'unknown'}:
            self.cancel_attempted.discard(task_id)
            # Record intent only; the local execution owner reaps outside this lock.
            if task['bridge_instance'] == 'local:claude-agent':
                self._revoke_permission_files_locked(task)
                now = time.time()
                owned = task_id in self.claude_threads
                self.ledger.conn.execute(
                    'UPDATE worker_tasks SET cancel_requested=1,state=?,updated_at=?,version=version+1 WHERE task_id=?',
                    ('stopping' if owned else 'cancelled' if task['state'] == 'queued' else 'unknown', now, task_id))
                queued = not owned and task['state'] == 'queued'
                if queued:
                    attempts = self.ledger.conn.execute("SELECT attempt_id FROM worker_attempts WHERE task_id=? AND state='queued'", (task_id,)).fetchall()
                    for attempt in attempts:
                        self._finish_locked(task_id, attempt['attempt_id'], 'cancelled', idle=True, text='排队任务已取消。')
                    self.ledger.conn.execute("UPDATE worker_messages SET state='cancelled' WHERE task_id=? AND state='queued'", (task_id,))
                    self.ledger.conn.execute('UPDATE worker_tasks SET termination_confirmed=1 WHERE task_id=?', (task_id,))
                self.ledger.event(task_id, 'cancel_requested', payload={'text':
                    '已请求停止；等待本地执行器确认回收。' if owned else '排队任务已取消。' if queued else '执行器句柄未知，无法确认停止。'})
                self.wake.set()
                return {'task_id': task_id, 'cancel_requested': True, 'termination_confirmed': queued}
            queued = task['state'] == 'queued'
            self.ledger.conn.execute('UPDATE worker_tasks SET cancel_requested=1,state=?,agent_idle=? WHERE task_id=?',
                                     ('cancelled' if queued else 'stopping' if task['state'] != 'unknown' else 'unknown',
                                      int(queued), task_id))
            if queued:
                pending = self.ledger.conn.execute(
                    "SELECT attempt_id FROM worker_attempts WHERE task_id=? AND state='queued'", (task_id,)).fetchall()
                self.ledger.conn.execute("UPDATE worker_attempts SET state='cancelled' WHERE task_id=? AND state='queued'", (task_id,))
                self.ledger.conn.execute("UPDATE worker_messages SET state='cancelled' WHERE task_id=? AND state='queued'", (task_id,))
                for attempt in pending:
                    self.queue_worker_report_locked(task_id, attempt['attempt_id'], 'cancelled')
            self.ledger.event(task_id, 'cancel_requested', payload={
                'text': '未发送的消息已取消。' if queued else '已请求停止；等待 DSH 确认会话空闲，外部进程树与费用不作保证。'})
            self.wake.set()
        return {'task_id': task_id, 'cancel_requested': bool(task['cancel_requested']) or task['state'] in ACTIVE | {'unknown'},
                'termination_confirmed': bool(task['termination_confirmed'])}

    def tasks(self, action='list', payload=None):
        payload = payload or {}
        if action == 'recover_local':
            return self.recover_local(payload)
        if action in {'archive', 'restore', 'delete'}:
            return self.manage_task(action, payload)
        with self.service._lock:
            epoch = self.service._channels.epoch if action == 'list' else self._epoch(payload)
            if action == 'list':
                if set(payload) - {'limit', 'before', 'archived'}:
                    raise ChannelError(400, 'invalid_request', '列表参数无效')
                limit = bounded_int(payload.get('limit'), 40, 1, 100)
                before = bounded_int(payload.get('before'), 0, 0, 1000000)
                archived = bounded_int(payload.get('archived'), 0, 0, 1)
                rows = self.ledger.conn.execute(
                    "SELECT * FROM worker_tasks WHERE epoch=? AND scope='gui' AND deleted=0 AND archived=? ORDER BY "
                    "CASE WHEN state IN ('queued','starting','running','collecting','stopping','waiting') THEN 0 ELSE 1 END,"
                    "updated_at DESC,task_id DESC LIMIT ? OFFSET ?", (epoch, archived, limit + 1, before)).fetchall()
                cursor = self.ledger.conn.execute('SELECT COALESCE(MAX(cursor),0) FROM worker_events WHERE epoch=?', (epoch,)).fetchone()[0]
                return {'session_epoch': epoch, 'cursor': cursor,
                        'tasks': [self.ledger.summary(dict(r)) for r in rows[:limit]],
                        'next_before': before + limit if len(rows) > limit else None}
            allowed = {'detail': set(), 'events': {'after_cursor', 'limit'},
                       'cancel': {'request_id', 'revision'}, 'review': {'request_id', 'revision', 'review'},
                       'revoke': {'request_id', 'revision'}}
            if action not in allowed or set(payload) - ({'session_epoch', 'task_id'} | allowed[action]):
                raise ChannelError(400, 'invalid_request', '协作操作参数无效')
            task = self.ledger.task(payload.get('task_id'), epoch)
            if task['deleted']:
                raise ChannelError(404, 'not_found', '此协作任务已删除')
            if action == 'detail':
                messages = []
                if self.ledger.visible(task):
                    rows = self.ledger.conn.execute(
                        'SELECT message_id,attempt_id,actor,body,version,state,created_at,truncated FROM worker_messages '
                        'WHERE task_id=? ORDER BY created_at DESC,message_id DESC LIMIT 50', (task['task_id'],)).fetchall()
                    messages = [dict(row) for row in reversed(rows)]
                return {'session_epoch': epoch, 'task': self.ledger.summary(task),
                        'messages': messages, 'artifacts': [],
                        'tool_details_available': task['bridge_instance'] != 'local:claude-agent' and bool(self.bridge),
                        'usage': self.ledger.usage(task['task_id']), 'sources_verified': False}
            if action == 'events':
                limit = bounded_int(payload.get('limit'), 60, 1, 100)
                cursor = bounded_int(payload.get('after_cursor'), 0, 0, 2147483647)
                if cursor > task['last_sequence']:
                    raise ChannelError(409, 'resync_required', '事件游标失效，请重新读取任务')
                rows = self.ledger.conn.execute('SELECT * FROM worker_events WHERE task_id=? AND sequence>? '
                                                'ORDER BY sequence LIMIT ?', (task['task_id'], cursor, limit + 1)).fetchall()
                visible = self.ledger.visible(task)
                events = []
                for row in rows[:limit]:
                    event = dict(row)
                    event['payload'] = json.loads(event['payload']) if visible else {'text': '此事件正文已不可用'}
                    event.update({'visibility': 'gui', 'truncated': bool(event['truncated'])})
                    events.append(event)
                return {'session_epoch': epoch, 'task_id': task['task_id'], 'events': events,
                        'cursor': events[-1]['sequence'] if events else cursor, 'has_more': len(rows) > limit,
                        'content_available': visible,
                        'content_unavailable_reason': self.ledger.content_unavailable_reason(task),
                        'content_revoked': not visible}
            old = self.ledger.replay(epoch, payload.get('request_id'), {'action': action, **payload})
            if old is not None:
                return old
            if task['management_pending']:
                raise ChannelError(409, 'management_pending', '任务整理结果待确认，请先重试同一整理操作')
            if action in {'review', 'revoke'} and 'revision' not in payload:
                raise ChannelError(409, 'task_changed', '需要当前任务版本，请重新打开详情')
            if 'revision' in payload and (type(payload['revision']) is not int or payload['revision'] != task['version']):
                raise ChannelError(409, 'task_changed', '任务版本已变化，请重新打开详情')
            with self.ledger.conn:
                if action == 'cancel':
                    result = self._cancel_locked(task['task_id'], epoch)
                elif action == 'revoke':
                    self._cancel_locked(task['task_id'], epoch)
                    self.ledger.revoke(task['task_id'])
                    self.service.store.revoke_worker_context(task['task_id'])
                    result = {'task_id': task['task_id'], 'content_revoked': True}
                else:
                    if payload.get('review') not in {'accepted', 'rejected', 'unreviewed'}:
                        raise ChannelError(400, 'invalid_request', '人工核对结果无效')
                    if task['state'] != 'succeeded' or not self.ledger.visible(task):
                        raise ChannelError(409, 'not_reviewable', '只有已结束且资料有效的任务可以核对')
                    self.ledger.conn.execute('UPDATE worker_tasks SET review=?,version=version+1 WHERE task_id=?',
                                             (payload['review'], task['task_id']))
                    self.ledger.event(task['task_id'], 'human_reviewed', payload={'text': '人工核对：' + payload['review']})
                    result = {'task_id': task['task_id'], 'review': payload['review']}
                result['session_epoch'] = epoch
                self.ledger.receipt(epoch, payload['request_id'], {'action': action, **payload}, result)
            return result

    def recover_local(self, payload):
        """Explicit human recovery, never a kill by historical PID or a quota refund."""
        allowed = {'session_epoch', 'task_id', 'request_id', 'revision', 'confirmed_stopped', 'cost_usd', 'launched_at'}
        if not isinstance(payload, dict) or set(payload) - allowed or payload.get('confirmed_stopped') is not True:
            raise ChannelError(400, 'recovery_confirmation_required', '先人工核实本任务及子进程已经停止，再确认恢复')
        cost = payload.get('cost_usd')
        if cost is not None and (type(cost) not in (int, float) or not math.isfinite(cost) or cost < 0):
            raise ChannelError(400, 'invalid_cost', '核对费用须为非负数或保留未知')
        launched_at = payload.get('launched_at')
        if launched_at is not None and (type(launched_at) not in (int, float)
                or not math.isfinite(launched_at) or not 0 < launched_at <= time.time()):
            raise ChannelError(400, 'invalid_launch_time', '核对启动时间须为有效的过去时间戳')
        with self.control_lock:
            with self.service._lock:
                epoch = self._epoch(payload)
                old = self.ledger.replay(epoch, payload.get('request_id'), {'action': 'recover_local', **payload})
                if old is not None:
                    return old
                task = self.ledger.task(payload.get('task_id'), epoch)
                if (task['bridge_instance'] != 'local:claude-agent' or task['state'] not in
                        {'unknown', 'succeeded', 'failed', 'cancelled', 'interrupted'}
                        or task['task_id'] in self.claude_threads or task['version'] != payload.get('revision')):
                    raise ChannelError(409, 'recovery_unavailable', '任务仍有执行线程或版本已变化，请刷新')
                transport = self.claude_transports.get(task['task_id'])
            # Reap only the still-owned handle. No historical PID is consulted.
            confirmed = transport.reap() if transport else bool(task['termination_confirmed'])
            human_recovery = transport is not None and not confirmed and transport.can_accept_human_recovery()
            with self.service._lock, self.ledger.conn:
                self.service._channels.check_epoch(epoch)
                current = self.ledger.task(task['task_id'], epoch)
                if current['version'] != task['version']:
                    raise ChannelError(409, 'task_changed', '任务已变化，请刷新')
                if transport is not None and not confirmed and not human_recovery:
                    raise ChannelError(409, 'termination_unknown', '监督进程仍在运行或自有Job无法核实停止；保留未知与占槽')
                self.ledger.conn.execute(
                    "UPDATE worker_tasks SET agent_idle=1,termination_confirmed=?,recovery_basis=?,version=version+1 WHERE task_id=?",
                    (int(confirmed), 'owned_tree_empty' if confirmed else 'human_attested', task['task_id']))
                if cost is not None or launched_at is not None:
                    attempt = self.ledger.conn.execute('SELECT attempt_id,usage_json,launched_at FROM worker_attempts WHERE task_id=? '
                        'ORDER BY created_at DESC LIMIT 1', (task['task_id'],)).fetchone()
                    usage = json.loads(attempt['usage_json'])
                    if cost is not None and usage.get('cost_known') is True:
                        raise ChannelError(409, 'cost_already_reported', '已有费用报告，不覆盖原记录')
                    if cost is not None:
                        usage.update(cost_usd=cost, cost_known=True, source='human_reported', billing='reported_only')
                    if launched_at is not None:
                        if attempt['launched_at'] is not None:
                            raise ChannelError(409, 'launch_time_known', '已有宿主启动时间，不覆盖原记录')
                        self.ledger.conn.execute('UPDATE worker_attempts SET launched_at=? WHERE attempt_id=?',
                                                 (launched_at, attempt['attempt_id']))
                    self.ledger.conn.execute('UPDATE worker_attempts SET usage_json=? WHERE attempt_id=?',
                                             (json.dumps(usage), attempt['attempt_id']))
                self.ledger.event(task['task_id'], 'human_recovery', payload={'text':
                    '已核实停止并解除占槽；未重派、未退款。停止依据：' + ('宿主整树为空。' if confirmed else '人工确认，非宿主OS证据。')})
                result = {'session_epoch': epoch, 'task_id': task['task_id'], 'agent_idle': True,
                          'termination_confirmed': confirmed, 'recovery_basis': 'owned_tree_empty' if confirmed else 'human_attested'}
                self.ledger.receipt(epoch, payload['request_id'], {'action': 'recover_local', **payload}, result)
                self.claude_transports.pop(task['task_id'], None)
                self.wake.set()
                return result

    def prepare_chat_locked(self, request):
        if request.get('worker_target') is not None:
            self.validate_chat_target_locked(request)
        if request.get('channel_id') != 'gui' or self.closing or self.paused:
            return None
        policies = {name: self._worker_policy_locked(name) for name in ('dsh-agent', 'claude-agent')}
        enabled = {name: bool(policy['enabled']) for name, policy in policies.items()}
        if not any(enabled.values()):
            return None
        from .worker_tools import WorkerChatSession
        return WorkerChatSession(self, {**request, 'worker_policy_revisions': {
            name: policy['revision'] for name, policy in policies.items() if enabled[name]}},
            available_workers=enabled)

    def begin_shutdown(self):
        self.closing = True
        self.stop.set()
        self.wake.set()

    def before_reset(self):
        self.ledger.interrupt()
        self.service.store.revoke_worker_context()
        self.generation += 1
        self.bridge = None
        self.connection_state = 'disconnected'

    def shutdown(self):
        self.drain()
        with self.service._lock:
            self.ledger.interrupt()
            self.bridge = None

    def unavailable(self):
        raise ChannelError(409, 'worker_file_transfer_unavailable',
                           '本轮支持获准目录派工，文件导入及附件收取下载尚未开放。')
