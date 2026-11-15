"""One independent DSH execution slot; all network waits occur outside Store locks."""
from __future__ import annotations

import hashlib
import json
import math
import queue
from pathlib import Path
import re
import threading
import time
import uuid

from .channels import ChannelError, normalize_worker_target
from .worker_tasks import ACTIVE, bounded_int, identity, safe_text
from .worker_transport import BridgeError, DshBridge
from .worker_credentials import CredentialError, ManagedCredential
from .worker_reports import WorkerReportsMixin


def day_start() -> float:
    """当日零点（本地时区），claude 日预算窗。"""
    import datetime
    now = datetime.datetime.now()
    return time.mktime(now.replace(hour=0, minute=0, second=0, microsecond=0).timetuple())


from .worker_permissions import WorkerPermissionsMixin


class WorkerRuntime(WorkerReportsMixin, WorkerPermissionsMixin):
    def initialize_runtime(self):
        self.bridge = None
        self.connection_state = 'disconnected'
        self.generation = 0
        self.paused = False
        self.control_lock = threading.RLock()
        self.claude_control_lock = threading.RLock()
        self.wake = threading.Event()
        self.stop = threading.Event()
        self.thread = None
        self.dsh_thread = None
        self.report_thread = None
        self.claude_threads = {}
        self.claude_transports = {}
        self.cancel_attempted = set()
        self.permission_grants = {}
        self.dsh_paused = False
        self.credentials = ManagedCredential()
        self.credential_status = self.credentials.metadata()

    def _start_thread_locked(self):
        if self.closing or self.paused:
            return
        if self.thread is None or not self.thread.is_alive():
            self.stop.clear()
            self.thread = threading.Thread(target=self._supervise, name='alice-workers', daemon=True)
            self.thread.start()
        self.wake.set()

    def connection_action(self, payload, *, worker=None):
        action = payload.get('action')
        if action in {'connect_managed', 'rotate_token'}:
            return self.managed_connection_action(payload, worker=worker)
        allowed = {'session_epoch', 'request_id', 'action'} | ({'token'} if action == 'connect' else set())
        if action not in {'connect', 'disconnect'} or set(payload) != allowed:
            raise ChannelError(400, 'invalid_request', '连接参数无效')
        # Serialize human connection changes; never hold this lock in the worker.
        with self.control_lock:
            with self.service._lock:
                epoch = self._epoch(payload)
                identity(payload.get('request_id'))
                if self.ledger.replay(epoch, payload['request_id'], payload) is not None:
                    return self._status_response_locked(worker)
                if self.closing:
                    raise ChannelError(503, 'draining', 'Alice 正在关闭')
                if action == 'connect' and self.bridge is not None:
                    raise ChannelError(409, 'already_connected', '请先断开旧连接，再重新配对')
                if action == 'connect' and self.installation != 'reviewed_installation':
                    raise ChannelError(409, 'unverified_installation', '请先核验当前 DSH 安装版本')
                generation = self.generation
            if action == 'connect':
                bridge = DshBridge(payload['token'])
                bridge.connect()  # No task/model/session is created by hello.
                with self.service._lock:
                    self.service._channels.check_epoch(epoch)
                    if self.closing or generation != self.generation:
                        raise ChannelError(409, 'session_changed', '会话已变化，请重新配对')
                    self.bridge = bridge
                    self.dsh_paused = False
                    self.connection_state = 'connected'
                    self.paused = False
                    self._start_thread_locked()
                    result = self._status_response_locked(worker)
                    with self.ledger.conn:
                        # Worker receipts store only a digest of input, not the token.
                        self.ledger.receipt(epoch, payload['request_id'], payload, result)
                    return result
            self._drain_dsh_connection()
            with self.service._lock:
                self.service._channels.check_epoch(epoch)
                self.bridge = None
                self.dsh_paused = False
                self.connection_state = 'disconnected'
                self.paused = False
                result = self._status_response_locked(worker)
                with self.ledger.conn:
                    self.ledger.receipt(epoch, payload['request_id'], payload, result)
                return result

    def managed_connection_action(self, payload, *, worker=None):
        if set(payload) != {'session_epoch', 'request_id', 'action'}:
            raise ChannelError(400, 'invalid_request', '托管令牌操作参数无效')
        action = payload['action']
        with self.control_lock:
            with self.service._lock:
                epoch = self._epoch(payload)
                identity(payload.get('request_id'))
                old = self.ledger.replay(epoch, payload['request_id'], payload)
                if old is not None:
                    return {**self._status_response_locked(worker), 'setup_message': '这次令牌设置已处理，未重复操作。请查看当前连接状态。'}
                if self.closing or self.paused:
                    raise ChannelError(409, 'draining', '正在停止协作任务，请稍后再设置令牌')
                if self.bridge is not None:
                    raise ChannelError(409, 'already_connected', '请先断开当前连接再设置令牌')
                if action == 'rotate_token' and self.ledger.policy()['enabled']:
                    raise ChannelError(409, 'worker_authorized', '请先关闭派工授权，再更换令牌')
                if action == 'connect_managed' and self.installation != 'reviewed_installation':
                    raise ChannelError(409, 'unverified_installation', '请先核验当前 DSH NEXT 安装版本')
                generation = self.generation
            operation_id = hashlib.sha256((epoch + '\0' + payload['request_id']).encode('ascii')).hexdigest()
            try:
                token = self.credentials.provision(operation_id, rotate=action == 'rotate_token')
            except CredentialError:
                metadata = self.credentials.metadata()
                with self.service._lock:
                    self.credential_status = metadata
                raise
            bridge = None
            setup_message = '独立令牌已更换并加密保存。请使用已保存令牌重新连接；旧任务的未知状态不会因此消失。'
            try:
                if action == 'connect_managed':
                    candidate = DshBridge(token)
                    # Only hello may be retried: no session/message/model operation.
                    # Allow the plugin's bounded credential watcher to observe creation.
                    ready_deadline = time.monotonic() + 9
                    while True:
                        with self.service._lock:
                            if self.closing or self.paused or generation != self.generation:
                                raise ChannelError(409, 'session_changed', '会话已变化；令牌已保存，未连接')
                        try:
                            candidate.connect(timeout=min(2, max(0.2, ready_deadline - time.monotonic())))
                            bridge = candidate
                            break
                        except BridgeError as exc:
                            if (exc.code not in {'bridge_unavailable', 'authentication_required', 'bridge_not_ready'}
                                    or time.monotonic() >= ready_deadline):
                                break
                            time.sleep(min(0.5, max(0, ready_deadline - time.monotonic())))
                    setup_message = ('独立令牌已保存并连接 DSH NEXT。已保存的授权、任务记录与已用次数保留。' if bridge else
                                     '独立令牌已加密保存，DSH NEXT 尚未就绪。若已启用新版插件，请稍后再次连接；旧版需加载 0.4.0 或更新版本。无需复制令牌或设置环境变量。')
            finally:
                token = None
            metadata = self.credentials.metadata()
            with self.service._lock:
                self.service._channels.check_epoch(epoch)
                if self.closing or self.paused or generation != self.generation:
                    raise ChannelError(409, 'session_changed', '会话已变化；令牌已保存，请重新连接')
                self.credential_status = metadata
                if bridge is not None:
                    self.bridge = bridge
                    self.dsh_paused = False
                    self.connection_state = 'connected'
                    self._start_thread_locked()
                    setup_message += ('原授权继续有效；补充前会核对原会话状态。' if self.ledger.policy()['enabled'] else
                                      '派工授权尚未开启；使用原参数恢复不会清除未撤销记录或重置次数。')
                result = {**self._status_response_locked(worker), 'setup_message': setup_message}
                with self.ledger.conn:
                    # Input and receipt contain no secret, ciphertext or secret ID.
                    self.ledger.receipt(epoch, payload['request_id'], payload, result)
                return result

    def _disable_locked(self):
        epoch = self.service._channels.epoch
        self.ledger.conn.execute('UPDATE worker_policy SET enabled=0,revision=revision+1 WHERE id=1')
        for row in self.ledger.conn.execute("SELECT task_id FROM worker_tasks WHERE epoch=? AND COALESCE(bridge_instance,'')!='local:claude-agent'", (epoch,)).fetchall():
            self._cancel_locked(row['task_id'], epoch)
            self.ledger.revoke(row['task_id'])
            self.service.store.revoke_worker_context(row['task_id'])

    def save_policy(self, payload, *, worker=None):
        keys = {'session_epoch', 'revision', 'request_id', 'enabled', 'max_runs', 'permission_mode',
                'native_permissions_ack', 'workspace_root', 'max_seconds'}
        if set(payload) != keys or type(payload.get('enabled')) is not bool:
            raise ChannelError(400, 'invalid_request', '授权参数无效')
        maximum = bounded_int(payload['max_runs'], 0, 0, 100)
        seconds = bounded_int(payload['max_seconds'], 600, 30, 1800)
        mode, root = payload['permission_mode'], payload['workspace_root']
        if mode not in {'read-only', 'workspace-write'} or not isinstance(root, str) or len(root) > 1000:
            raise ChannelError(400, 'invalid_request', '工作目录或模式无效')
        if type(payload['native_permissions_ack']) is not bool:
            raise ChannelError(400, 'invalid_request', '需要明确确认原生权限与费用限制')
        # Filesystem check is outside Store lock; browser supplies only the
        # explicitly authorized cwd, never a command, endpoint or credential.
        if payload['enabled']:
            path = Path(root)
            if not path.is_absolute() or not path.is_dir() or not payload['native_permissions_ack'] or maximum < 1:
                raise ChannelError(400, 'authorization_required', '请确认现有绝对工作目录、原生权限限制及执行次数')
            root = str(path.resolve(strict=True))
        with self.control_lock, self.service._lock:
            epoch = self._epoch(payload)
            old = self.ledger.replay(epoch, payload['request_id'], payload)
            if old is not None:
                return self._status_response_locked(worker)
            policy = self.ledger.policy()
            if type(payload['revision']) is not int or payload['revision'] != policy['revision']:
                raise ChannelError(409, 'policy_changed', '授权已变化，请刷新')
            if self.closing or self.paused:
                raise ChannelError(503, 'draining', '正在停止协作任务')
            same_parameters = (policy['max_runs'] == maximum
                               and policy['workspace_root'] == root
                               and policy['permission_mode'] == mode
                               and bool(policy['native_permissions_ack']) == payload['native_permissions_ack']
                               and policy['max_seconds'] == seconds)
            unchanged = bool(policy['enabled']) == payload['enabled'] and same_parameters
            if unchanged:
                # Saving the same grant is not revocation or a fresh budget.
                # Keep the revision so existing task bodies and run counts survive.
                result = {**self._status_response_locked(worker), 'policy_unchanged': True}
                with self.ledger.conn:
                    self.ledger.receipt(epoch, payload['request_id'], payload, result)
                return result
            if payload['enabled']:
                if self.bridge is None or self.connection_state != 'connected':
                    raise ChannelError(409, 'not_connected', '请先完成 DSH 配对')
                if self.ledger.conn.execute("SELECT 1 FROM worker_tasks WHERE epoch=? AND COALESCE(bridge_instance,'')!='local:claude-agent' AND "
                        "(state IN ('queued','starting','running','stopping') OR "
                        "(state='unknown' AND agent_idle=0 AND bridge_instance=?)) LIMIT 1",
                        (epoch, self.bridge.instance_id)).fetchone():
                    raise ChannelError(409, 'task_busy', '请先停止并确认现有任务，再修改授权')
            with self.ledger.conn:
                resumed = payload['enabled'] and not policy['enabled'] and same_parameters
                if not resumed:
                    self._disable_locked()
                self.ledger.conn.execute('UPDATE worker_policy SET enabled=?,max_runs=?,workspace_root=?,'
                                         'permission_mode=?,native_permissions_ack=?,max_seconds=? WHERE id=1',
                                         (int(payload['enabled']), maximum, root, mode,
                                          int(payload['native_permissions_ack']), seconds))
                result = self._status_response_locked(worker)
                if resumed:
                    result['policy_resumed'] = True
                self.ledger.receipt(epoch, payload['request_id'], payload, result)
            self.wake.set()
            return result

    def claude_policy(self, payload=None):
        """Public, independently versioned Claude grant; no adapter or SQL access by SDK."""
        if payload is not None:
            keys = {'session_epoch', 'request_id', 'revision', 'enabled', 'native_sandbox_ack',
                    'workspace_root', 'max_turns', 'budget_usd_per_task', 'budget_usd_per_day'}
            if (not isinstance(payload, dict) or set(payload) != keys
                    or type(payload['enabled']) is not bool or type(payload['native_sandbox_ack']) is not bool):
                raise ChannelError(400, 'invalid_request', 'Claude 授权参数无效')
            bounded_int(payload['max_turns'], 12, 1, 100)
            for key in ('budget_usd_per_task', 'budget_usd_per_day'):
                value = payload[key]
                if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 10000:
                    raise ChannelError(400, 'invalid_request', '费用软阈值须为0至10000的有限数值')
            root = payload['workspace_root']
            if not isinstance(root, str) or len(root) > 1000:
                raise ChannelError(400, 'invalid_request', '工作目录无效')
            if payload['enabled']:
                path = Path(root)
                if not path.is_absolute() or not path.is_dir() or not payload['native_sandbox_ack']:
                    raise ChannelError(400, 'authorization_required', '请确认现有绝对目录与原生权限监督限制')
                root = str(path.resolve(strict=True))
        with self.service._lock:
            def view():
                policy = self._worker_policy_locked('claude-agent')
                return {'session_epoch': self.service._channels.epoch, 'revision': policy['revision'],
                        'worker_id': 'claude-agent', 'policy': {**policy, 'enabled': bool(policy['enabled']),
                                                              'native_sandbox_ack': bool(policy.get('native_sandbox_ack'))},
                        'runs_used': self.ledger.runs_used('claude-agent', policy['revision']),
                        'metering': 'recorded_only', 'permission_profile': 'native_supervision_not_os_sandbox'}
            if payload is None:
                return view()
            epoch = self._epoch(payload)
            receipt_payload = {'operation': 'claude_policy', **payload}
            previous = self.ledger.replay(epoch, payload['request_id'], receipt_payload)
            if previous is not None:
                return view()
            policy = self._worker_policy_locked('claude-agent')
            if type(payload['revision']) is not int or policy['revision'] != payload['revision']:
                raise ChannelError(409, 'policy_changed', 'Claude 授权已变化，请刷新')
            if self.closing or self.paused:
                raise ChannelError(409, 'worker_draining', '协作正在关闭或重置')
            values = {key: payload[key] for key in ('enabled', 'native_sandbox_ack', 'workspace_root',
                      'max_turns', 'budget_usd_per_task', 'budget_usd_per_day')}
            values['workspace_root'] = root
            unchanged = all(policy[key] == value for key, value in values.items())
            with self.ledger.conn:
                if not unchanged:
                    self.ledger.conn.execute('UPDATE claude_policy SET revision=revision+1,enabled=?,'
                        'native_sandbox_ack=?,workspace_root=?,max_turns=?,budget_usd_per_task=?,'
                        'budget_usd_per_day=?,updated_at=? WHERE id=1',
                        (int(values['enabled']), int(values['native_sandbox_ack']), root, values['max_turns'],
                         values['budget_usd_per_task'], values['budget_usd_per_day'], int(time.time())))
                    # Publish the new policy before cancelling/revoking exactly its own tasks.
                    for row in self.ledger.conn.execute("SELECT task_id FROM worker_tasks WHERE epoch=? "
                            "AND bridge_instance='local:claude-agent'", (epoch,)).fetchall():
                        self._cancel_locked(row['task_id'], epoch)
                        self.ledger.revoke(row['task_id'])
                        self.service.store.revoke_worker_context(row['task_id'])
                    self.permission_grants.clear()
                result = {**view(), 'policy_unchanged': unchanged}
                self.ledger.receipt(epoch, payload['request_id'], receipt_payload, result)
            self.wake.set()
            return result

    def _claude_policy_admitted(self):
        """claude-agent 政策三核（enabled/ack/目录）。未迁移或未启用 → False。"""
        from .runtime_process import addon_available
        if not addon_available('claude-agent'):
            return False
        try:
            row = self.ledger.store.conn.execute(
                'SELECT enabled, native_sandbox_ack, workspace_root FROM claude_policy WHERE id=1').fetchone()
        except Exception:
            return False
        return bool(row and row['enabled'] and row['native_sandbox_ack'] and row['workspace_root'])

    @staticmethod
    def _task_worker(task):
        return 'claude-agent' if task['bridge_instance'] == 'local:claude-agent' else 'dsh-agent'

    def _worker_queue_locked(self, worker):
        """Each worker owns one execution slot and four queued attempts."""
        row = self.ledger.conn.execute(
            "SELECT COUNT(*) FROM worker_attempts a JOIN worker_tasks t USING(task_id) "
            "WHERE t.epoch=? AND a.state='queued' AND "
            "CASE WHEN t.bridge_instance='local:claude-agent' THEN 'claude-agent' ELSE 'dsh-agent' END=?",
            (self.service._channels.epoch, worker)).fetchone()
        return row[0]

    def _worker_activity_locked(self, worker):
        rows = self.ledger.conn.execute(
            "SELECT state FROM worker_tasks WHERE epoch=? AND agent_idle=0 AND "
            "state IN ('starting','running','waiting_permission','collecting','stopping','unknown') AND "
            "CASE WHEN bridge_instance='local:claude-agent' THEN 'claude-agent' ELSE 'dsh-agent' END=?",
            (self.service._channels.epoch, worker)).fetchall()
        return [row['state'] for row in rows]

    def _worker_policy_locked(self, worker):
        if worker == 'dsh-agent':
            return self.ledger.policy()
        if worker != 'claude-agent':
            raise ChannelError(400, 'unknown_worker', '未知协作者')
        try:
            row = self.ledger.conn.execute('SELECT * FROM claude_policy WHERE id=1').fetchone()
        except Exception:
            row = None
        return dict(row) if row else {'revision': 0, 'enabled': False}

    def _admit_locked(self, request, *, execution=False, worker=None):
        self.service._channels.check_epoch(request.get('session_epoch'))
        if request.get('channel_id') != 'gui':
            raise ChannelError(403, 'gui_only', '协作仅供本机 GUI 使用')
        if worker is None:
            target = normalize_worker_target(request.get('worker_target'))
            if target:
                worker = self._task_worker(self.ledger.task(target['task_id'], request['session_epoch']))
            else:
                snapshots = request.get('worker_policy_revisions', {})
                worker = next((name for name in ('dsh-agent', 'claude-agent')
                               if name in snapshots and self._worker_policy_locked(name)['enabled']), None)
                if worker is None:
                    worker = 'claude-agent' if self._claude_policy_admitted() else 'dsh-agent'
        policy = self._worker_policy_locked(worker)
        revisions = request.get('worker_policy_revisions')
        revision = (revisions.get(worker) if isinstance(revisions, dict)
                    else request.get('worker_policy_revision', policy['revision']))
        if not policy['enabled'] or revision != policy['revision']:
            raise ChannelError(403, 'worker_not_authorized', '协作授权已失效')
        if worker == 'claude-agent':
            ready = policy.get('native_sandbox_ack') and policy.get('workspace_root')
        else:
            ready = (policy['native_permissions_ack'] and policy['workspace_root']
                     and policy['permission_mode'] in {'read-only', 'workspace-write'})
        if not ready:
            raise ChannelError(403, 'worker_not_authorized', '协作授权尚未就绪')
        if execution and (self.closing or self.paused):
            raise ChannelError(409, 'worker_draining', '协作正在退出')
        if execution and worker == 'dsh-agent' and self.dsh_paused:
            raise ChannelError(409, 'worker_draining', 'DSH 正在断开，请完成断开后重新连接')
        if execution and worker == 'dsh-agent' and (self.bridge is None or self.connection_state != 'connected'):
            raise ChannelError(409, 'not_connected', 'DSH 连接不可用，未提交任务')
        return {**policy, 'worker_id': worker, 'claude_only': worker == 'claude-agent'}

    def validate_chat_target_locked(self, request):
        target = normalize_worker_target(request.get('worker_target'))
        if target is None:
            return None
        task = self.ledger.task(target['task_id'], request['session_epoch'])
        worker = self._task_worker(task)
        if target['collaborator'] != worker:
            raise ChannelError(409, 'worker_target_mismatch', '协作引用与任务所属工具不一致')
        self._admit_locked(request, worker=worker)
        if (self.closing or self.paused or not self.ledger.visible(task) or task['archived']
                or task['management_pending'] or task['session_closed']):
            raise ChannelError(409, 'worker_target_unavailable', '协作引用已失效或会话已关闭')
        if worker == 'dsh-agent' and (self.bridge is None or task['bridge_instance'] != self.bridge.instance_id):
            raise ChannelError(409, 'worker_target_unavailable', '原 DSH 会话当前不可用')
        return target

    def worker_target_view_locked(self, target, epoch):
        target = normalize_worker_target(target)
        if target is None:
            return None
        try:
            # This projection grants only a link to retained GUI history.
            # Action admission still uses validate_chat_target_locked above.
            self.service._channels.check_epoch(epoch)
            task = self.ledger.task(target['task_id'], epoch)
            target = {**target, 'collaborator': self._task_worker(task)}
            available = bool(epoch == self.service.store.meta_get('session_epoch')
                             and not task['deleted'] and self.ledger.visible(task))
        except ChannelError:
            available = False
        return {**target, 'available': available}

    def _target_tool_args_locked(self, request, name, args):
        target = normalize_worker_target(request.get('worker_target'))
        if target is None:
            return args
        if name == 'dispatch_worker':
            raise ChannelError(403, 'worker_target_bound', '本轮已引用既有任务，不能另建任务')
        task_id = target['task_id']
        if name == 'worker_task_status':
            task_ids = args.get('task_ids', [])
            if not isinstance(task_ids, list) or any(value != task_id for value in task_ids):
                raise ChannelError(403, 'worker_target_mismatch', '本轮只允许操作引用的协作任务')
            args = {**args, 'task_ids': [task_id]}
        elif name not in {'worker_task_read', 'send_worker_message', 'cancel_worker_task'} or args.get('task_id') != task_id:
            raise ChannelError(403, 'worker_target_mismatch', '本轮只允许操作引用的协作任务')
        self.validate_chat_target_locked(request)
        return args

    def _check_forwarding_locked(self, request, name, args):
        if name not in {'dispatch_worker', 'send_worker_message'}:
            return
        if request.get('worker_context_source_ids'):
            raise ChannelError(409, 'worker_source_forwarding_unavailable', '尚未开放来源资料转发，未发送')
        refs = request.get('worker_context_refs', [])
        if refs:
            if not self.service.store.worker_refs_valid(refs, scope='gui'):
                raise ChannelError(409, 'worker_context_unavailable', '任务上下文已失效，未发送')
            if name == 'dispatch_worker' or any(ref['task_id'] != args.get('task_id') for ref in refs):
                raise ChannelError(409, 'worker_cross_task_context', '尚未开放跨任务资料转发，请先撤回旧任务资料')

    def _followup_task_locked(self, task_id, epoch):
        task = self.ledger.task(task_id, epoch)
        if (not self.ledger.visible(task) or task['archived'] or task['management_pending']
                or task['session_closed'] or task['cancel_requested']
                or task['state'] not in {'succeeded', 'waiting'} or not task['agent_idle']
                or not task['session_id'] or self.bridge is None
                or task['bridge_instance'] != self.bridge.instance_id):
            raise ChannelError(409, 'task_not_resumable', '此任务暂不能继续；请检查状态或原 DSH 专属会话是否仍可用')
        return task

    @staticmethod
    def _tool_receipt(request, name, args, policy):
        request_id = hashlib.sha256((request['request_id'] + '\0' + request['tool_call_id']).encode()).hexdigest()
        payload = {'tool': name, 'args': args, 'policy_revision': policy['revision']}
        if request.get('worker_target') is not None:
            payload['worker_target'] = normalize_worker_target(request['worker_target'])
        return request_id, payload

    def _preflight_followup(self, request, args, tool_call_id):
        """Read one existing session before charging/queuing; never resume or send."""
        with self.service._lock:
            target_task = self.ledger.task(args.get('task_id'), request['session_epoch'])
            if self._task_worker(target_task) == 'claude-agent':
                raise ChannelError(409, 'worker_resume_unavailable', 'Claude 本地任务暂不支持续聊，可读取结果或请求停止')
            policy = self._admit_locked(request, execution=True, worker='dsh-agent')
            args = self._target_tool_args_locked(request, 'send_worker_message', args)
            self._check_forwarding_locked(request, 'send_worker_message', args)
            request_id, payload = self._tool_receipt({**request, 'tool_call_id': tool_call_id},
                                                     'send_worker_message', args, policy)
            old = self.ledger.replay(request['session_epoch'], request_id, payload)
            if old is not None:
                return {'replayed': old}
            task = self._followup_task_locked(args.get('task_id'), request['session_epoch'])
            if args.get('input_ids'):
                raise ChannelError(409, 'input_import_unavailable', '本轮派工使用获准工作目录，文件 ID 导入尚未开放')
            if self.ledger.runs_used() >= policy['max_runs']:
                raise ChannelError(409, 'run_budget_exhausted', '本次授权执行次数已用完')
            check = {'bridge': self.bridge, 'generation': self.generation, 'epoch': request['session_epoch'],
                     'revision': policy['revision'], 'task_id': task['task_id'],
                     'instance_id': self.bridge.instance_id, 'session_id': task['session_id']}
        remote, error = None, None
        try:
            remote = check['bridge'].call('status', {'task_id': check['task_id']})
        except BridgeError as exc:
            error = exc
        with self.service._lock:
            policy = self._admit_locked(request, execution=True, worker='dsh-agent')
            self._validate_followup_check_locked(check, policy)
            args = self._target_tool_args_locked(request, 'send_worker_message', args)
            self._check_forwarding_locked(request, 'send_worker_message', args)
            old = self.ledger.replay(check['epoch'], request_id, payload)
            if old is not None:
                return {'replayed': old}
            task = self._followup_task_locked(check['task_id'], check['epoch'])
            if task['session_id'] != check['session_id']:
                raise ChannelError(409, 'worker_session_changed', '原协作会话身份已变化，未发送补充')
            closed = (error is not None and not error.uncertain
                      and error.code in {'task_closed', 'task_expired', 'task_unknown', 'task_not_found'})
            if remote is not None:
                if remote.get('session_id') != check['session_id']:
                    raise ChannelError(409, 'worker_session_changed', 'DSH 返回的会话身份不符，未发送补充')
                closed = (remote.get('state') == 'closed' or remote.get('dispose_confirmed') is True
                          or remote.get('close_requested') is True)
            if closed:
                with self.ledger.conn:
                    self.ledger.conn.execute('UPDATE worker_tasks SET session_closed=1,version=version+1 WHERE task_id=?',
                                             (check['task_id'],))
                    self.ledger.event(check['task_id'], 'session_closed', payload={
                        'text': '原 DSH 专属会话已关闭或不再可用；历史资料与已用次数保留，未发送补充。'})
                raise ChannelError(409, 'task_not_resumable', '原 DSH 会话已关闭或过期，未发送补充；历史资料仍保留')
            if error is not None:
                raise error
            if (remote.get('agent_idle') is not True or remote.get('active_attempt_id') is not None
                    or remote.get('close_requested') is not False or remote.get('dispose_confirmed') is not False
                    or remote.get('state') not in {'succeeded', 'idle', 'waiting'}):
                raise ChannelError(409, 'task_not_resumable', '尚不能确认原 DSH 会话空闲，未发送补充')
            if (remote.get('permission_profile') != 'dsh_native_v1'
                    or remote.get('permission_mode') != policy['permission_mode']
                    or remote.get('approval_policy') != 'never'):
                raise ChannelError(409, 'permission_unavailable', '原 DSH 会话权限与当前授权不符，未发送补充')
            return check

    def _validate_followup_check_locked(self, check, policy):
        if (check['epoch'] != self.service._channels.epoch or check['generation'] != self.generation
                or check['bridge'] is not self.bridge or self.bridge is None
                or check['instance_id'] != self.bridge.instance_id or check['revision'] != policy['revision']):
            raise ChannelError(409, 'worker_session_changed', '连接或授权已变化，未发送补充')

    def tool_execute(self, request, name, args, tool_call_id):
        if not isinstance(args, dict) or not isinstance(tool_call_id, str) or not 1 <= len(tool_call_id) <= 200:
            raise ChannelError(400, 'invalid_request', '工具参数或身份无效')
        checked = self._preflight_followup(request, args, tool_call_id) if name == 'send_worker_message' else None
        if checked is not None and 'replayed' in checked:
            return {'result': checked['replayed']}
        with self.service._lock:
            worker = args.get('name') if name == 'dispatch_worker' else None
            if args.get('task_id'):
                worker = self._task_worker(self.ledger.task(args['task_id'], request['session_epoch']))
            policy = self._admit_locked(request, execution=name in {'dispatch_worker', 'send_worker_message'}, worker=worker)
            if checked is not None:
                self._validate_followup_check_locked(checked, policy)
            args = self._target_tool_args_locked(request, name, args)
            epoch = request['session_epoch']
            self._check_forwarding_locked(request, name, args)
            if name == 'worker_task_status':
                ids = args.get('task_ids')
                if ids:
                    rows = [self.ledger.task(task_id, epoch) for task_id in ids[:20]]
                    rows = [row for row in rows if not row['deleted']]
                else:
                    rows = self.ledger.conn.execute('SELECT * FROM worker_tasks WHERE epoch=? AND deleted=0 AND archived=0 '
                                                    'ORDER BY updated_at DESC LIMIT 20', (epoch,)).fetchall()
                fields = ('task_id', 'state', 'last_sequence', 'cancel_requested', 'termination_confirmed')
                return {'result': {'tasks': [{**{key: bool(row[key]) if key in {'cancel_requested', 'termination_confirmed'}
                                             else row[key] for key in fields},
                                             'name': self._task_worker(row), 'worker_id': self._task_worker(row)} for row in rows]}}
            if name == 'worker_task_read':
                task = self.ledger.task(args.get('task_id'), epoch)
                if not self.ledger.visible(task):
                    raise ChannelError(409, 'content_unavailable', '任务正文已过期或撤回')
                limit = bounded_int(args.get('limit'), 8, 1, 20)
                after = args.get('after_message_id')
                rowid = 0
                if after:
                    row = self.ledger.conn.execute('SELECT rowid FROM worker_messages WHERE task_id=? AND message_id=?',
                                                    (task['task_id'], identity(after))).fetchone()
                    if row is None:
                        raise ChannelError(409, 'resync_required', '消息游标不属于此任务')
                    rowid = row[0]
                rows = self.ledger.conn.execute('SELECT message_id,version FROM worker_messages WHERE task_id=? '
                                                "AND rowid>? AND state='completed' ORDER BY rowid LIMIT ?",
                                                (task['task_id'], rowid, limit)).fetchall()
                return {'worker_refs': [{'task_id': task['task_id'], 'message_id': r['message_id'],
                                        'version': r['version'], 'epoch': epoch,
                                        'policy_revision': task['policy_revision']} for r in rows],
                        'result': {'task_id': task['task_id'], 'state': task['state']}}
            request_id, receipt_payload = self._tool_receipt({**request, 'tool_call_id': tool_call_id}, name, args, policy)
            old = self.ledger.replay(epoch, request_id, receipt_payload)
            if old is not None:
                return {'result': old}
            with self.ledger.conn:
                if name == 'cancel_worker_task':
                    task = self.ledger.task(args.get('task_id'), epoch)
                    if task['deleted'] or task['management_pending']:
                        raise ChannelError(409, 'task_unavailable', '任务已删除或整理结果待确认')
                    result = self._cancel_locked(args.get('task_id'), epoch)
                elif name in {'dispatch_worker', 'send_worker_message'}:
                    if args.get('input_ids'):
                        raise ChannelError(409, 'input_import_unavailable', '本轮派工使用获准工作目录，文件 ID 导入尚未开放')
                    claude_only = bool(policy.get('claude_only')) and args.get('name') == 'claude-agent'
                    if not claude_only and self.ledger.runs_used() >= policy['max_runs']:
                        raise ChannelError(409, 'run_budget_exhausted', '本次授权执行次数已用完')
                    if self._worker_queue_locked(policy['worker_id']) >= 4:
                        raise ChannelError(409, 'queue_full', '此协作者的四个待办位置已满，请等待现有任务')
                    if name == 'dispatch_worker':
                        if args.get('name') == 'claude-agent':
                            # 本地 CLI 轨（分支限定 ytq）：独立政策表核验，与 DSH 桥互不影响。
                            cp = self._worker_policy_locked('claude-agent')
                            if cp is None:
                                raise ChannelError(409, 'worker_unverified', 'claude-agent 未完成数据迁移')
                            if not cp['enabled'] or not cp['native_sandbox_ack'] or not cp['workspace_root']:
                                raise ChannelError(409, 'worker_disabled',
                                                   'claude-agent 未启用：请在设置确认工作目录、'
                                                   '接受「权限质询不是操作系统沙箱」并开启派工授权')
                            brief = args.get('brief')
                            if not isinstance(brief, str) or not brief.strip() or len(brief) > 10000:
                                raise ChannelError(400, 'invalid_request', '任务说明须为 1–10000 字符')
                            outputs = args.get('requested_outputs', [])
                            if (not isinstance(outputs, list) or len(outputs) > 8
                                    or any(not isinstance(s, str) or not 1 <= len(s) <= 200 for s in outputs)):
                                raise ChannelError(400, 'invalid_request', '预期产物说明无效')
                            if outputs:
                                brief += '\n用户预期产物（保存到工作目录）：\n' + '\n'.join(outputs)
                            result = self._dispatch_claude_locked(brief, args, cp, request_id, tool_call_id, request['request_id'])
                            # claude 轨在此收口：登记/事件/线程已自洽，不得流入下方
                            # DSH 公共落账段（那里的路径改写与 INSERT 以 DSH 政策为准）。
                            self.ledger.receipt(epoch, request_id, receipt_payload, result)
                            self._start_thread_locked()
                            return {'result': result}
                        elif args.get('name') != 'dsh-agent':
                            raise ChannelError(400, 'unknown_worker', '仅支持 dsh-agent 或 claude-agent')
                        else:
                            brief = args.get('brief')
                        if not isinstance(brief, str) or not brief.strip() or len(brief) > 10000:
                            raise ChannelError(400, 'invalid_request', '任务说明须为 1–10000 字符')
                        outputs = args.get('requested_outputs', [])
                        if (not isinstance(outputs, list) or len(outputs) > 8
                                or any(not isinstance(s, str) or not 1 <= len(s) <= 200 for s in outputs)):
                            raise ChannelError(400, 'invalid_request', '预期产物说明无效')
                        if outputs:
                            brief += '\n用户预期产物（保存到获准工作目录，尚无自动下载）：\n' + '\n'.join(outputs)
                        task_id, now = uuid.uuid4().hex, time.time()
                        title = args.get('title') or brief.splitlines()[0][:80]
                        if not isinstance(title, str):
                            raise ChannelError(400, 'invalid_request', '任务标题无效')
                        self.ledger.conn.execute('INSERT INTO worker_tasks(task_id,epoch,policy_revision,state,title,'
                                                 'created_at,updated_at,expires_at,bridge_instance,workspace_root) VALUES(?,?,?,?,?,?,?,?,?,?)',
                                                 (task_id, epoch, policy['revision'], 'queued', safe_text(title, 80),
                                                  now, now, now + 7 * 86400, self.bridge.instance_id, policy['workspace_root']))
                        self.ledger.event(task_id, 'task_accepted', payload={'text': '已登记任务及本轮执行次数；尚未发送 DSH。'})
                    else:
                        task = self._followup_task_locked(args.get('task_id'), epoch)
                        if task['session_id'] != checked['session_id']:
                            raise ChannelError(409, 'worker_session_changed', '原协作会话身份已变化，未发送补充')
                        task_id, brief = task['task_id'], args.get('message')
                        if not isinstance(brief, str) or not brief.strip() or len(brief) > 10000:
                            raise ChannelError(400, 'invalid_request', '补充说明须为 1–10000 字符')
                    message_id, attempt_id, now = uuid.uuid4().hex, uuid.uuid4().hex, time.time()
                    # Use relative names inside the explicitly authorized cwd.
                    # Never silently redact a file target or secret and run a
                    # different instruction than the one recorded publicly.
                    for root in {policy['workspace_root'].replace('\\', '/'),
                                 policy['workspace_root'].replace('/', '\\')}:
                        brief = re.sub(re.escape(root.rstrip('\\/')) + r'(?=$|[\\/\s"\x27.,;])',
                                       '.', brief, flags=re.IGNORECASE)
                    if safe_text(brief, 12000) != brief:
                        raise ChannelError(400, 'sensitive_task_text', '请使用获准目录内的相对文件名，并移除凭据及其他本机路径')
                    self.ledger.conn.execute('INSERT INTO worker_messages(message_id,task_id,attempt_id,actor,body,state,created_at) '
                                             "VALUES(?,?,?,'alice',?,'queued',?)", (message_id, task_id, attempt_id, brief, now))
                    self.ledger.conn.execute('INSERT INTO worker_attempts(attempt_id,task_id,message_id,state,created_at,updated_at,'
                                             'request_id,originating_request_id,tool_call_id) VALUES(?,?,?,?,?,?,?,?,?)',
                                             (attempt_id, task_id, message_id, 'queued', now, now, request_id,
                                              request['request_id'], tool_call_id))
                    self.ledger.conn.execute("UPDATE worker_tasks SET state='queued',agent_idle=0,version=version+1 WHERE task_id=?", (task_id,))
                    self.ledger.event(task_id, 'alice_message_queued', actor='alice', payload={'text': brief},
                                      attempt_id=attempt_id, message_id=message_id)
                    result = {'task_id': task_id, 'message_id': message_id, 'attempt_id': attempt_id, 'state': 'queued'}
                else:
                    raise ChannelError(400, 'unknown_tool', '协作工具不存在')
                self.ledger.receipt(epoch, request_id, receipt_payload, result)
            self._start_thread_locked()
            return {'result': result}

    def _check_claude_budget_locked(self, policy):
        if policy['budget_usd_per_task'] <= 0 or policy['budget_usd_per_day'] <= 0:
            raise ChannelError(409, 'quota_exhausted', '费用软阈值为零，未启动任务')
        spent = 0.0
        unknown = False
        for row in self.ledger.conn.execute(
                "SELECT a.usage_json,a.sent,a.created_at,a.launched_at,a.state FROM worker_attempts a "
                "JOIN worker_tasks t USING(task_id) WHERE t.bridge_instance='local:claude-agent'"):
            report = json.loads(row['usage_json'])
            cost = report.get('cost_usd')
            if row['sent'] and row['launched_at'] is None:
                unknown = True  # legacy launch date requires explicit human reconciliation
            elif row['sent'] and (type(cost) not in (int, float) or not math.isfinite(cost) or cost < 0):
                unknown = True
            elif row['launched_at'] is not None and row['launched_at'] >= day_start() and type(cost) in (int, float):
                spent += cost
        if unknown:
            raise ChannelError(409, 'cost_unknown', '存在已发送任务的未知尾账；请先人工核对费用，未将未知计为零。')
        if spent >= policy['budget_usd_per_day']:
            raise ChannelError(409, 'quota_exhausted', '当日已报告费用达到软预算，未启动新任务。')

    def _dispatch_claude_locked(self, brief, args, cp, request_id, tool_call_id, originating_request_id):
        """claude-agent 本地轨登记（分支限定 ytq）：独立账本行 + 排队，由本地线程执行。

        与 DSH 分支共用 worker_tasks/messages/attempts/events 表与 GUI 任务卡；
        不触碰 bridge/session 语义（bridge_instance 记 'local:claude-agent'）。
        按已报告费用拒绝后续超额派工；此记录不构成最终账单硬上限。
        """
        self._check_claude_budget_locked(cp)
        task_id, now = uuid.uuid4().hex, time.time()
        title = args.get('title') or brief.splitlines()[0][:80]
        message_id, attempt_id = uuid.uuid4().hex, uuid.uuid4().hex
        self.ledger.conn.execute(
            'INSERT INTO worker_tasks(task_id,epoch,policy_revision,state,title,created_at,updated_at,'
            "expires_at,bridge_instance,workspace_root) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (task_id, self.service._channels.epoch, cp['revision'], 'queued', safe_text(title, 80),
             now, now, now + 7 * 86400, 'local:claude-agent', cp['workspace_root']))
        self.ledger.conn.execute(
            'INSERT INTO worker_messages(message_id,task_id,attempt_id,actor,body,state,created_at) '
            "VALUES(?,?,?,'alice',?,'queued',?)", (message_id, task_id, attempt_id, brief, now))
        self.ledger.conn.execute(
            'INSERT INTO worker_attempts(attempt_id,task_id,message_id,state,created_at,updated_at,'
            'request_id,originating_request_id,tool_call_id) VALUES(?,?,?,?,?,?,?,?,?)',
            (attempt_id, task_id, message_id, 'queued', now, now, request_id,
             originating_request_id, tool_call_id))
        self.ledger.event(task_id, 'task_accepted', attempt_id=attempt_id, message_id=message_id,
                          payload={'text': '已登记 Claude 本地任务，等待执行。'})
        self.ledger.event(task_id, 'alice_message_queued', actor='alice', attempt_id=attempt_id,
                          message_id=message_id, payload={'text': brief})
        return {'task_id': task_id, 'message_id': message_id, 'attempt_id': attempt_id,
                'state': 'queued', 'worker': 'claude-agent',
                'message': '已排队；执行中如需权限会向 GUI 质询。'}

    def _run_claude(self, task_id, brief, cp, attempt_id, message_id, tool_call_id):
        from .worker_process import WorkerProcessTransport
        from .worker_permissions import claude_gate_dir
        transport = None
        events = queue.Queue(maxsize=128)
        epoch, ledger = self.service._channels.epoch, self.ledger

        def on_event(kind, payload):
            # Never persist private thinking, terminal objects, auth or config.
            if kind == 'system' and payload.get('subtype') == 'init':
                event = ('claude_ready', 'CLI 已初始化。')
            elif kind == 'assistant':
                message = payload.get('message')
                content = message.get('content') if isinstance(message, dict) else None
                text = '\n'.join(block['text'] for block in content or []
                    if isinstance(block, dict) and block.get('type') == 'text' and isinstance(block.get('text'), str))
                if not text:
                    return
                event = ('claude_progress', safe_text(text, 2000))
            else:
                return
            try:
                events.put_nowait(event)
            except queue.Full:
                pass  # bounded progress only; final result has a separate slot

        term, confirmed = {}, False
        try:
            sandbox = Path(cp['workspace_root']).resolve() / f'claude_task_{task_id[:8]}'
            gate_dir = claude_gate_dir(cp) / task_id / attempt_id
            with self.service._lock, ledger.conn:
                task = ledger.task(task_id, epoch)
                if (task['cancel_requested'] or self.stop.is_set() or not ledger.visible(task)
                        or cp['revision'] != self._worker_policy_locked('claude-agent')['revision']):
                    self._finish_locked(task_id, attempt_id, 'cancelled', idle=True, text='本地任务在启动前已取消。')
                    ledger.conn.execute('UPDATE worker_tasks SET termination_confirmed=1 WHERE task_id=?', (task_id,))
                    return
                self._check_claude_budget_locked(cp)
                ledger.conn.execute("UPDATE worker_attempts SET state='starting',updated_at=? WHERE attempt_id=?", (time.time(), attempt_id))
                ledger.conn.execute("UPDATE worker_tasks SET state='starting' WHERE task_id=?", (task_id,))
                ledger.event(task_id, 'run_started', attempt_id=attempt_id, message_id=message_id,
                             payload={'text': '正在启动 Claude 本地任务。'})
            def authorize_start():
                with self.service._lock, ledger.conn:
                    current = ledger.task(task_id, epoch)
                    if (self.ledger is not ledger or self.stop.is_set() or self.closing
                            or current['cancel_requested'] or not ledger.visible(current)):
                        raise ChannelError(409, 'worker_cancelled', '启动前授权已失效')
                    self._check_claude_budget_locked(self._worker_policy_locked('claude-agent'))
                    # Conservative launch intent: a failed/partial GO is possible consumption.
                    ledger.conn.execute('UPDATE worker_attempts SET sent=1,launched_at=? WHERE attempt_id=?',
                                        (time.time(), attempt_id))
            transport = WorkerProcessTransport(brief=brief, sandbox=sandbox, control_dir=gate_dir,
                identity={'task_id': task_id, 'attempt_id': attempt_id, 'epoch': epoch,
                          'worker_id': 'claude-agent', 'policy_revision': cp['revision']},
                max_turns=int(cp['max_turns']), on_event=on_event, hard_deadline=900,
                authorize_start=authorize_start)
            with self.service._lock:
                self.claude_transports[task_id] = transport
            transport.start()
            if not transport.go_sent:
                term = transport.terminal() or {'frame': 'local', 'state': 'cancelled', 'code': 'no_go'}
                return
            with self.service._lock, ledger.conn:
                ledger.conn.execute("UPDATE worker_messages SET state='completed' WHERE message_id=?", (message_id,))
                ledger.conn.execute('UPDATE worker_attempts SET sent=1 WHERE attempt_id=?', (attempt_id,))
                ledger.event(task_id, 'alice_message_sent', attempt_id=attempt_id, message_id=message_id,
                             payload={'text': '任务书已发送至本地 CLI。'})
            while transport.terminal() is None:
                pending = any(gate_dir.glob('*.pending.json'))
                with self.service._lock, ledger.conn:
                    task = ledger.task(task_id, epoch)
                    if not ledger.visible(task):
                        ledger.revoke(task_id)
                        self.service.store.revoke_worker_context(task_id)
                    cancel = (self.stop.is_set() or self.closing or self.paused or task['cancel_requested']
                              or not ledger.visible(task) or not self._claude_policy_admitted())
                    self._answer_cached_permissions_locked(gate_dir, task_id)
                    while not events.empty():
                        kind, text = events.get_nowait()
                        ledger.event(task_id, kind, attempt_id=attempt_id, payload={'text': text})
                    state = 'waiting_permission' if pending else 'running'
                    if not cancel and task['state'] != state:
                        ledger.conn.execute('UPDATE worker_tasks SET state=?,updated_at=? WHERE task_id=?', (state, time.time(), task_id))
                        ledger.conn.execute('UPDATE worker_attempts SET state=?,updated_at=? WHERE attempt_id=?', (state, time.time(), attempt_id))
                        ledger.event(task_id, 'permission_waiting' if pending else 'run_active', attempt_id=attempt_id,
                                     payload={'text': '等待用户答复权限质询。' if pending else 'CLI 正在执行。'})
                if cancel:
                    with self.service._lock:
                        self._revoke_permission_files_locked(task)
                    transport.cancel('host_cancel')  # all process waits outside Store lock
                    break
                time.sleep(0.2)
            term = transport.terminal() or {}
        except Exception as exc:
            term = {'frame': 'local', 'state': 'unknown' if transport else 'failed',
                    'code': 'local_execution_error', 'message': type(exc).__name__}
        finally:
            if transport is not None:
                try:
                    confirmed = transport.reap()
                except Exception:
                    confirmed = False
            else:
                confirmed = True
            try:
                def persist_terminal():
                    with self.service._lock, ledger.conn:
                        if self.ledger is not ledger or self.service._channels.epoch != epoch:
                            return
                        task = ledger.task(task_id, epoch)
                        if task['state'] == 'cancelled':
                            return
                        result = term.get('payload') if term.get('frame') == 'result' else None
                        state = result['state'] if result else term.get('state', 'unknown')
                        if state not in {'succeeded', 'failed', 'cancelled', 'interrupted', 'unknown'}:
                            state = 'unknown'
                        if task['cancel_requested']:
                            state = 'cancelled' if confirmed else 'unknown'
                        if not confirmed:
                            state = 'unknown'
                        if result:
                            cost = result['cost_usd']
                            ledger.conn.execute('UPDATE worker_attempts SET usage_json=? WHERE attempt_id=?',
                                (json.dumps({'known': bool(result['usage']), **result['usage'], 'cost_usd': cost,
                                             'cost_known': cost is not None, 'source': 'claude_reported',
                                             'billing': 'reported_only', 'coverage': 'owned_agent_only'}), attempt_id))
                            if result['text'] and not task['cancel_requested'] and ledger.visible(task):
                                reply_mid = uuid.uuid4().hex
                                reply = safe_text(result['text'], 24000)
                                ledger.conn.execute("INSERT INTO worker_messages(message_id,task_id,attempt_id,actor,body,version,state,created_at,truncated) VALUES(?,?,?,'agent',?,1,'completed',?,?)",
                                    (reply_mid, task_id, attempt_id, reply, time.time(), int(len(result['text']) > len(reply))))
                                ledger.event(task_id, 'agent_message_completed', actor='agent', attempt_id=attempt_id,
                                             message_id=reply_mid, payload={'text': reply})
                            ledger.event(task_id, 'claude_completed', attempt_id=attempt_id,
                                payload={'summary': json.dumps({'cost_usd': cost, 'subtype': result['subtype'],
                                    'output_tokens': result['usage'].get('output_tokens')})})
                            if cost is not None and cost > cp['budget_usd_per_task']:
                                ledger.event(task_id, 'claude_budget_overrun', attempt_id=attempt_id,
                                             payload={'text': '报告费用超过单任务估算额度；消费不构成账单硬上限。'})
                        else:
                            ledger.event(task_id, 'claude_failed', attempt_id=attempt_id,
                                         payload={'text': safe_text(str(term.get('code', 'unknown')), 100)})
                        self._finish_locked(task_id, attempt_id, state, idle=confirmed,
                                            text='本地执行已结束，结果待复核。' if confirmed else '无法确认本地执行已停止；执行与费用可能未知。')
                        ledger.conn.execute('UPDATE worker_tasks SET termination_confirmed=? WHERE task_id=?', (int(confirmed), task_id))
                        ledger.conn.execute("UPDATE worker_messages SET state=? WHERE message_id=? AND state='queued'", (state, message_id))
                persist_terminal()
            finally:
                with self.service._lock:
                    if confirmed:
                        self.claude_transports.pop(task_id, None)
                    self.permission_grants = {key: value for key, value in self.permission_grants.items()
                                              if key[1] != task_id}
                    self.claude_threads.pop(task_id, None)
                    self.wake.set()

    def _start_local_job_locked(self):
        if self.closing or self.paused or self.stop.is_set() or self.claude_threads:
            return
        if self._worker_activity_locked('claude-agent'):
            return
        row = self.ledger.conn.execute("SELECT a.* FROM worker_attempts a JOIN worker_tasks t USING(task_id) "
            "WHERE a.state='queued' AND t.state='queued' AND t.bridge_instance='local:claude-agent' "
            "AND t.epoch=? ORDER BY a.created_at LIMIT 1", (self.service._channels.epoch,)).fetchone()
        if row is None:
            return
        cp = self._worker_policy_locked('claude-agent')
        task = self.ledger.task(row['task_id'], self.service._channels.epoch)
        if not self.ledger.visible(task) or not self._claude_policy_admitted() or task['cancel_requested']:
            with self.ledger.conn:
                self._finish_locked(task['task_id'], row['attempt_id'], 'cancelled', idle=True, text='排队任务在启动前已取消。')
                self.ledger.conn.execute('UPDATE worker_tasks SET termination_confirmed=1 WHERE task_id=?', (task['task_id'],))
            return
        brief = self.ledger.conn.execute('SELECT body FROM worker_messages WHERE message_id=?', (row['message_id'],)).fetchone()[0]
        thread = threading.Thread(target=self._run_claude,
            args=(row['task_id'], brief, cp, row['attempt_id'], row['message_id'], row['tool_call_id']),
            name='alice-claude', daemon=True)
        self.claude_threads[row['task_id']] = thread
        try:
            thread.start()
        except RuntimeError:
            self.claude_threads.pop(row['task_id'], None)
            with self.ledger.conn:
                self._finish_locked(row['task_id'], row['attempt_id'], 'failed', idle=True,
                                    text='本地执行线程无法启动，未发送任务。')

    def _next_job_locked(self):
        if self.closing or self.paused or self.dsh_paused or self.bridge is None or self.dsh_thread is not None:
            return None
        epoch = self.service._channels.epoch
        # An uncertain run keeps the slot occupied until remote idle is observed.
        uncertain = self.ledger.conn.execute("SELECT task_id,bridge_instance FROM worker_tasks WHERE epoch=? AND state='unknown' "
                                             "AND agent_idle=0 AND COALESCE(bridge_instance,'')!='local:claude-agent' LIMIT 1",
                                             (epoch,)).fetchone()
        if uncertain:
            if uncertain['bridge_instance'] != self.bridge.instance_id:
                return None  # a new bridge identity does not prove the old agent stopped
            task_id = uncertain['task_id']
            return ('cancel', task_id) if (task_id not in self.cancel_attempted
                                           and self.ledger.task(task_id, epoch)['cancel_requested']) else None
        if self.connection_state != 'connected':
            return None
        row = self.ledger.conn.execute("SELECT a.* FROM worker_attempts a JOIN worker_tasks t USING(task_id) "
                                        "WHERE a.state='queued' AND t.epoch=? AND t.deleted=0 AND t.archived=0 "
                                        "AND t.management_pending IS NULL AND t.bridge_instance=? ORDER BY a.created_at,a.rowid LIMIT 1", (epoch, self.bridge.instance_id)).fetchone()
        return ('run', dict(row)) if row else None

    def _run_dsh_job(self, job):
        try:
            if job[0] == 'run':
                self._run(job[1])
            else:
                self._resolve_cancel(job[1])
        except Exception:
            # A DSH failure must not interrupt Claude's execution or queue.
            with self.service._lock:
                if not self.dsh_paused:
                    self.connection_state = 'uncertain'
                self.ledger.interrupt(report=self.queue_worker_report_locked, worker_id='dsh-agent')
        finally:
            with self.service._lock:
                if self.dsh_thread is threading.current_thread():
                    self.dsh_thread = None
                self.wake.set()

    def _start_dsh_job_locked(self):
        job = self._next_job_locked()
        if job is None:
            return
        thread = threading.Thread(target=self._run_dsh_job, args=(job,), name='alice-dsh', daemon=True)
        # Reserve before start; a not-yet-alive thread still owns its slot.
        self.dsh_thread = thread
        try:
            thread.start()
        except RuntimeError:
            self.dsh_thread = None
            if job[0] == 'run':
                with self.ledger.conn:
                    self._finish_locked(job[1]['task_id'], job[1]['attempt_id'], 'failed', idle=True,
                                        text='DSH 执行线程无法启动，未发送任务。')

    def _supervise(self):
        while not self.stop.is_set():
            self.publish_worker_report()
            with self.service._lock:
                for worker, start in (('claude-agent', self._start_local_job_locked),
                                      ('dsh-agent', self._start_dsh_job_locked)):
                    try:
                        start()
                    except Exception:
                        if worker == 'dsh-agent' and not self.dsh_paused:
                            self.connection_state = 'uncertain'
                        self.ledger.interrupt(report=self.queue_worker_report_locked, worker_id=worker)
            self.wake.wait(0.75)
            self.wake.clear()

    def _finish_locked(self, task_id, attempt_id, state, *, idle=False, text=None):
        now = time.time()
        self.ledger.conn.execute('UPDATE worker_tasks SET state=?,agent_idle=?,updated_at=?,version=version+1 WHERE task_id=?',
                                 (state, int(idle), now, task_id))
        self.ledger.conn.execute('UPDATE worker_attempts SET state=?,updated_at=? WHERE attempt_id=?', (state, now, attempt_id))
        self.ledger.event(task_id, {'succeeded': 'run_completed', 'cancelled': 'run_stopped',
                                   'unknown': 'connection_lost'}.get(state, 'run_failed'),
                          payload={'text': text or ('DSH 会话已空闲；未确认外部进程树终止。' if idle else '执行或费用未知，未自动重发。')},
                          attempt_id=attempt_id)
        self.queue_worker_report_locked(task_id, attempt_id, state)

    def _run(self, attempt):
        task_id, attempt_id = attempt['task_id'], attempt['attempt_id']
        phase = 'create'
        with self.service._lock:
            epoch, generation, bridge = self.service._channels.epoch, self.generation, self.bridge
            task = self.ledger.task(task_id, epoch)
            policy = self.ledger.policy()
            if (not self.ledger.visible(task) or not policy['native_permissions_ack']
                    or task['cancel_requested'] or self.stop.is_set()):
                with self.ledger.conn:
                    self._finish_locked(task_id, attempt_id, 'cancelled', idle=True, text='尚未发送的任务已取消。')
                return
            body = self.ledger.conn.execute('SELECT body FROM worker_messages WHERE message_id=?', (attempt['message_id'],)).fetchone()[0]
            with self.ledger.conn:
                self.ledger.conn.execute("UPDATE worker_attempts SET state='starting',updated_at=? WHERE attempt_id=?", (time.time(), attempt_id))
                self.ledger.conn.execute("UPDATE worker_tasks SET state='starting' WHERE task_id=?", (task_id,))
        try:
            if not task['session_id']:
                created = bridge.call('create', {'task_id': task_id, 'request_id': task_id,
                    'cwd': policy['workspace_root'], 'permission_mode': policy['permission_mode'],
                    'permission_profile': 'dsh_native_v1', 'native_permission_ack': True,
                    'max_output_tokens': 4096, 'max_turns': 8, 'timeout_seconds': policy['max_seconds']})
                if (created.get('permission_mode') != policy['permission_mode'] or created.get('approval_policy') != 'never'
                        or not isinstance(created.get('session_id'), str)):
                    raise BridgeError('permission_unavailable')
                with self.service._lock, self.ledger.conn:
                    created_at = created.get('session_created_at')
                    if type(created_at) is not int or not 0 < created_at <= 10**15:
                        created_at = None
                    self.ledger.conn.execute('UPDATE worker_tasks SET session_id=?,session_created_at=? WHERE task_id=?',
                                             (created['session_id'], created_at, task_id))
            with self.service._lock:
                task = self.ledger.task(task_id, epoch)
                if generation != self.generation or self.stop.is_set() or not self.ledger.visible(task) or task['cancel_requested']:
                    raise BridgeError('cancel_before_send', uncertain=False)
                deadline = time.time() + policy['max_seconds']
                with self.ledger.conn:
                    # sent=1 means the request might reach DSH, not acceptance.
                    self.ledger.conn.execute('UPDATE worker_attempts SET sent=1,deadline=? WHERE attempt_id=?', (deadline, attempt_id))
            phase = 'send'
            sent = bridge.call('send', {'task_id': task_id, 'request_id': attempt['request_id'],
                'attempt_id': attempt_id, 'message_id': attempt['message_id'], 'text': body})
            if sent.get('accepted') is not True:
                raise BridgeError('send_unconfirmed')
            phase = 'poll'
            with self.service._lock, self.ledger.conn:
                self.ledger.conn.execute("UPDATE worker_attempts SET state='running' WHERE attempt_id=?", (attempt_id,))
                self.ledger.conn.execute("UPDATE worker_tasks SET state='running' WHERE task_id=?", (task_id,))
                self.ledger.conn.execute("UPDATE worker_messages SET state='completed' WHERE message_id=?", (attempt['message_id'],))
                self.ledger.event(task_id, 'alice_message_sent', payload={'text': 'DSH 已接受本次消息。'},
                                  attempt_id=attempt_id, message_id=attempt['message_id'])
            cancelling = False
            cancel_deadline = None
            while True:
                self.publish_worker_report()
                with self.service._lock:
                    task = self.ledger.task(task_id, epoch)
                    must_cancel = (self.stop.is_set() or task['cancel_requested'] or not self.ledger.visible(task)
                                   or time.time() >= deadline)
                if must_cancel and not cancelling:
                    cancelling, cancel_deadline = True, time.monotonic() + 8
                    with self.service._lock, self.ledger.conn:
                        self.cancel_attempted.add(task_id)
                        self.ledger.conn.execute("UPDATE worker_tasks SET cancel_requested=1,state='stopping' WHERE task_id=?", (task_id,))
                    bridge.call('cancel', {'task_id': task_id, 'request_id': uuid.uuid4().hex})
                self._read_events(bridge, task_id, epoch, attempt_id)
                remote = bridge.call('status', {'task_id': task_id})
                with self.service._lock, self.ledger.conn:
                    task = self.ledger.task(task_id, epoch)
                    self.ledger.record_usage(attempt_id, remote.get('usage'))
                    if (remote.get('agent_idle') is True and remote.get('state') in {'succeeded', 'failed', 'cancelled', 'idle', 'closed'}
                            and task['remote_cursor'] >= remote.get('event_cursor', 0)):
                        state = 'cancelled' if cancelling or remote['state'] in {'cancelled', 'closed'} else (
                            'succeeded' if remote['state'] == 'succeeded' else 'failed')
                        self._finish_locked(task_id, attempt_id, state, idle=True)
                        return
                if cancelling and time.monotonic() >= cancel_deadline:
                    raise BridgeError('cancel_unconfirmed')
                if self.stop.is_set():
                    time.sleep(0.1)
                else:
                    self.wake.wait(0.75)
                    self.wake.clear()
        except BridgeError as exc:
            with self.service._lock, self.ledger.conn:
                if generation == self.generation:
                    uncertain = exc.uncertain or phase == 'poll'
                    # Even a rejected send can leave an owned idle session.
                    self._finish_locked(task_id, attempt_id, 'unknown' if uncertain else 'failed',
                                        text='DSH 请求未确认，未自动重发。' if uncertain else 'DSH 拒绝本轮请求，未自动重发。')
                    if uncertain and not self.dsh_paused:
                        self.connection_state = 'uncertain'
                    self.ledger.conn.execute("UPDATE worker_messages SET state='unknown' WHERE message_id=? AND state='queued'", (attempt['message_id'],))

    def _read_events(self, bridge, task_id, epoch, attempt_id):
        for _ in range(4):
            with self.service._lock:
                task = self.ledger.task(task_id, epoch)
                cursor = task['remote_cursor']
            result = bridge.call('events', {'task_id': task_id, 'after_cursor': cursor, 'limit': 100})
            events, next_cursor = result.get('events'), result.get('next_cursor')
            if (not isinstance(events, list) or len(events) > 100 or type(next_cursor) is not int or next_cursor < cursor
                    or result.get('truncated') is True or result.get('floor_cursor', 0) > cursor):
                raise BridgeError('event_gap')
            with self.service._lock, self.ledger.conn:
                task = self.ledger.task(task_id, epoch)
                visible = self.ledger.visible(task)
                previous = cursor
                for item in events:
                    if not isinstance(item, dict) or type(item.get('sequence')) is not int or item['sequence'] <= previous:
                        raise BridgeError('invalid_events')
                    previous = item['sequence']
                    if item.get('attempt_id') not in {None, attempt_id}:
                        continue
                    kind = item.get('type')
                    if kind not in {'agent_message_completed', 'tool_started', 'tool_completed', 'tool_failed',
                                    'run_started', 'run_completed', 'run_failed', 'run_stopped', 'usage_updated', 'agent_question'}:
                        continue
                    message_id = None
                    payload = {}
                    truncated = item.get('text_truncated') is True or (
                        isinstance(item.get('text'), str) and len(item['text']) > 12000)
                    if visible:
                        payload = {key: safe_text(item[key]) for key in ('text', 'tool_name') if isinstance(item.get(key), str)}
                        if kind == 'agent_message_completed' and isinstance(item.get('message_id'), str) and payload.get('text'):
                            message_id = uuid.uuid5(uuid.NAMESPACE_URL, bridge.instance_id + '/' + task_id + '/' + item['message_id']).hex
                            self.ledger.conn.execute('INSERT OR IGNORE INTO worker_messages(message_id,task_id,attempt_id,actor,body,state,created_at,truncated) '
                                                     "VALUES(?,?,?,'dsh',?,'completed',?,?)", (message_id, task_id, attempt_id,
                                                      payload['text'], time.time(), int(truncated)))
                    self.ledger.event(task_id, kind, actor='dsh', payload=payload, attempt_id=attempt_id,
                                      message_id=message_id, truncated=truncated)
                if previous > next_cursor:
                    raise BridgeError('invalid_cursor')
                self.ledger.conn.execute('UPDATE worker_tasks SET remote_cursor=? WHERE task_id=?', (next_cursor, task_id))
            if not result.get('has_more', len(events) == 100):
                return

    def _resolve_cancel(self, task_id):
        with self.service._lock:
            bridge, epoch = self.bridge, self.service._channels.epoch
            self.cancel_attempted.add(task_id)
        if bridge is None:
            return
        try:
            bridge.call('cancel', {'task_id': task_id, 'request_id': uuid.uuid4().hex})
            remote = bridge.call('status', {'task_id': task_id})
            if remote.get('agent_idle') is True:
                with self.service._lock, self.ledger.conn:
                    self.ledger.conn.execute("UPDATE worker_tasks SET state='cancelled',agent_idle=1,version=version+1 WHERE task_id=?", (task_id,))
                    self.ledger.event(task_id, 'run_stopped', payload={'text': 'DSH 会话已空闲；外部进程树终止仍未确认。'})
                    if not self.dsh_paused:
                        self.connection_state = 'connected'
        except BridgeError:
            # No repeated failed cancellation network loop. User may explicitly retry.
            with self.service._lock:
                if not self.dsh_paused:
                    self.connection_state = 'uncertain'

    def _drain_dsh_connection(self):
        """Disconnect the DSH transport without touching Claude authorization/work."""
        with self.service._lock:
            self.dsh_paused = True
            self.connection_state = 'draining'
            epoch, bridge = self.service._channels.epoch, self.bridge
            rows = self.ledger.conn.execute("SELECT task_id FROM worker_tasks WHERE epoch=? "
                "AND COALESCE(bridge_instance,'')!='local:claude-agent'", (epoch,)).fetchall()
            with self.ledger.conn:
                for row in rows:
                    self._cancel_locked(row['task_id'], epoch)
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            with self.service._lock:
                active = self.ledger.conn.execute("SELECT 1 FROM worker_attempts a JOIN worker_tasks t USING(task_id) "
                    "WHERE t.epoch=? AND COALESCE(t.bridge_instance,'')!='local:claude-agent' "
                    "AND a.state IN ('starting','running','collecting','stopping') LIMIT 1", (epoch,)).fetchone()
                owned = self.dsh_thread is not None
            if not active and not owned:
                return
            time.sleep(.1)
        raise ChannelError(409, 'worker_draining', 'DSH 仍在退出，已暂停新派工，请稍后重试断开；Claude 不受影响')

    def drain(self):
        """Must be called without Store lock, before reset/close/disconnect."""
        with self.control_lock:
            self._drain_unlocked()

    def _drain_unlocked(self):
        with self.service._lock:
            self.paused = True
            self.connection_state = 'draining'
            self.stop.set()
            self.wake.set()
            thread, bridge, epoch = self.thread, self.bridge, self.service._channels.epoch
            local_threads = ([self.dsh_thread] if self.dsh_thread is not None else []) + list(self.claude_threads.values())
        # The report owns the GUI slot, not DSH execution. Marking stop discards
        # its eventual result; never delay DSH cancellation by joining model I/O.
        # Service close/reset drain the GUI slot before closing Store/client.
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=25)
            if thread.is_alive():
                raise ChannelError(409, 'worker_draining', '协作请求仍在退出，请稍后重试')
        for local in local_threads:
            local.join(timeout=25)
            if local.is_alive():
                raise ChannelError(409, 'worker_draining', '本地协作仍在回收，请稍后重试')
        with self.service._lock:
            with self.ledger.conn:
                # The execution thread can enqueue its final report while it
                # stops. Downgrade only after join; reconnect must not start a
                # historical summary request. Do not wait on report I/O here.
                self.ledger.conn.execute("UPDATE worker_reports SET report_state='pending_notice',kind='status' "
                                         "WHERE epoch=? AND report_state IN ('pending','summarizing')", (epoch,))
            tasks = self.ledger.conn.execute('SELECT task_id FROM worker_tasks WHERE epoch=? AND bridge_instance=? '
                                             "AND agent_idle=0 AND state IN ('starting','running','collecting','stopping','waiting','unknown') "
                                             'ORDER BY agent_idle,updated_at DESC',
                                             (epoch, bridge.instance_id if bridge else '')).fetchall()
        close_deadline = time.monotonic() + 10
        for row in tasks:
            if bridge is None or time.monotonic() >= close_deadline:
                break
            try:
                bridge.call('close', {'task_id': row['task_id'], 'request_id': uuid.uuid4().hex})
            except BridgeError:
                break  # Bounded drain; never kill the shared DSH host.
        with self.service._lock:
            self.ledger.interrupt()
            if self.ledger.conn.execute(
                    "SELECT 1 FROM worker_tasks WHERE epoch=? AND bridge_instance='local:claude-agent' "
                    "AND state='unknown' AND agent_idle=0 LIMIT 1", (epoch,)).fetchone():
                raise ChannelError(409, 'worker_recovery_required', '本地任务停止证据未知，请先人工核实并恢复任务状态。')
            self.thread = None
            self.connection_state = 'paused'
