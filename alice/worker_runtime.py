"""One independent DSH execution slot; all network waits occur outside Store locks."""
from __future__ import annotations

import hashlib
import json
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


class WorkerRuntime(WorkerReportsMixin):
    def initialize_runtime(self):
        self.bridge = None
        self.connection_state = 'disconnected'
        self.generation = 0
        self.paused = False
        self.control_lock = threading.RLock()
        self.wake = threading.Event()
        self.stop = threading.Event()
        self.thread = None
        self.report_thread = None
        self.cancel_attempted = set()
        self.credentials = ManagedCredential()
        self.credential_status = self.credentials.metadata()

    def _start_thread_locked(self):
        if self.thread is None or not self.thread.is_alive():
            self.stop.clear()
            self.thread = threading.Thread(target=self._supervise, name='alice-dsh', daemon=True)
            self.thread.start()
        self.wake.set()

    def connection_action(self, payload):
        action = payload.get('action')
        if action in {'connect_managed', 'rotate_token'}:
            return self.managed_connection_action(payload)
        allowed = {'session_epoch', 'request_id', 'action'} | ({'token'} if action == 'connect' else set())
        if action not in {'connect', 'disconnect'} or set(payload) != allowed:
            raise ChannelError(400, 'invalid_request', '连接参数无效')
        # Serialize human connection changes; never hold this lock in the worker.
        with self.control_lock:
            with self.service._lock:
                epoch = self._epoch(payload)
                identity(payload.get('request_id'))
                if self.ledger.replay(epoch, payload['request_id'], payload) is not None:
                    return self.status_locked()
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
                    self.connection_state = 'connected'
                    self.paused = False
                    self._start_thread_locked()
                    result = self.status_locked()
                    with self.ledger.conn:
                        # Worker receipts store only a digest of input, not the token.
                        self.ledger.receipt(epoch, payload['request_id'], payload, result)
                    return result
            self.drain()
            with self.service._lock:
                self.service._channels.check_epoch(epoch)
                self.bridge = None
                self.connection_state = 'disconnected'
                self.generation += 1
                self.paused = False
                result = self.status_locked()
                with self.ledger.conn:
                    self.ledger.receipt(epoch, payload['request_id'], payload, result)
                return result

    def managed_connection_action(self, payload):
        if set(payload) != {'session_epoch', 'request_id', 'action'}:
            raise ChannelError(400, 'invalid_request', '托管令牌操作参数无效')
        action = payload['action']
        with self.control_lock:
            with self.service._lock:
                epoch = self._epoch(payload)
                identity(payload.get('request_id'))
                old = self.ledger.replay(epoch, payload['request_id'], payload)
                if old is not None:
                    return {**self.status_locked(), 'setup_message': '这次令牌设置已处理，未重复操作。请查看当前连接状态。'}
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
                    self.connection_state = 'connected'
                    self._start_thread_locked()
                    setup_message += ('原授权继续有效；补充前会核对原会话状态。' if self.ledger.policy()['enabled'] else
                                      '派工授权尚未开启；使用原参数恢复不会清除未撤销记录或重置次数。')
                result = {**self.status_locked(), 'setup_message': setup_message}
                with self.ledger.conn:
                    # Input and receipt contain no secret, ciphertext or secret ID.
                    self.ledger.receipt(epoch, payload['request_id'], payload, result)
                return result

    def _disable_locked(self):
        epoch = self.service._channels.epoch
        self.ledger.conn.execute('UPDATE worker_policy SET enabled=0,revision=revision+1 WHERE id=1')
        for row in self.ledger.conn.execute('SELECT task_id FROM worker_tasks WHERE epoch=?', (epoch,)).fetchall():
            self._cancel_locked(row['task_id'], epoch)
            self.ledger.revoke(row['task_id'])
        self.service.store.revoke_worker_context()

    def save_policy(self, payload):
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
                return self.status_locked()
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
                result = {**self.status_locked(), 'policy_unchanged': True}
                with self.ledger.conn:
                    self.ledger.receipt(epoch, payload['request_id'], payload, result)
                return result
            if payload['enabled']:
                if self.bridge is None or self.connection_state != 'connected':
                    raise ChannelError(409, 'not_connected', '请先完成 DSH 配对')
                if self.ledger.conn.execute("SELECT 1 FROM worker_tasks WHERE epoch=? AND "
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
                result = self.status_locked()
                if resumed:
                    result['policy_resumed'] = True
                self.ledger.receipt(epoch, payload['request_id'], payload, result)
            self.wake.set()
            return result

    def _admit_locked(self, request, *, execution=False):
        self.service._channels.check_epoch(request.get('session_epoch'))
        policy = self.ledger.policy()
        if (request.get('channel_id') != 'gui' or not policy['enabled'] or not policy['native_permissions_ack']
                or policy['permission_mode'] not in {'read-only', 'workspace-write'} or not policy['workspace_root']
                or request.get('worker_policy_revision') != policy['revision']):
            raise ChannelError(403, 'worker_not_authorized', '协作授权已失效')
        if execution and (self.closing or self.paused or self.bridge is None or self.connection_state != 'connected'):
            raise ChannelError(409, 'not_connected', 'DSH 连接不可用，未提交任务')
        return policy

    def validate_chat_target_locked(self, request):
        """Validate routing metadata without importing any task/message body."""
        target = normalize_worker_target(request.get('worker_target'))
        if target is None:
            return None
        policy = self.ledger.policy()
        self._admit_locked({**request, 'worker_policy_revision': policy['revision']})
        if self.closing or self.paused or self.stop.is_set() or self.bridge is None:
            raise ChannelError(409, 'worker_target_unavailable', '协作引用目标当前不可用，请重新选择')
        task = self.ledger.task(target['task_id'], request['session_epoch'])
        if (not self.ledger.visible(task) or task['archived'] or task['management_pending']
                or task['session_closed'] or task['bridge_instance'] != self.bridge.instance_id):
            raise ChannelError(409, 'worker_target_unavailable', '协作引用已失效或会话已关闭，请重新选择')
        # Running/unknown targets may still be inspected/stopped. Only the send
        # branch decides whether this same task is currently resumable.
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
            policy = self._admit_locked(request, execution=True)
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
            policy = self._admit_locked(request, execution=True)
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
            policy = self._admit_locked(request, execution=name in {'dispatch_worker', 'send_worker_message'})
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
                return {'result': {'tasks': [{key: bool(row[key]) if key in {'cancel_requested', 'termination_confirmed'}
                                             else row[key] for key in fields} for row in rows]}}
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
                                        'policy_revision': policy['revision']} for r in rows],
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
                    if self.ledger.runs_used() >= policy['max_runs']:
                        raise ChannelError(409, 'run_budget_exhausted', '本次授权执行次数已用完')
                    if self.ledger.conn.execute("SELECT COUNT(*) FROM worker_attempts WHERE state='queued'").fetchone()[0] >= 4:
                        raise ChannelError(409, 'queue_full', '待办已满，请等待现有任务')
                    if name == 'dispatch_worker':
                        if args.get('name') != 'dsh-agent':
                            raise ChannelError(400, 'unknown_worker', '仅支持 dsh-agent')
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

    def _next_job_locked(self):
        if self.closing or self.paused or self.bridge is None:
            return None
        epoch = self.service._channels.epoch
        # An uncertain run keeps the slot occupied until remote idle is observed.
        uncertain = self.ledger.conn.execute("SELECT task_id FROM worker_tasks WHERE epoch=? AND state='unknown' "
                                             'AND agent_idle=0 AND bridge_instance=? LIMIT 1',
                                             (epoch, self.bridge.instance_id)).fetchone()
        if uncertain:
            task_id = uncertain['task_id']
            return ('cancel', task_id) if (task_id not in self.cancel_attempted
                                           and self.ledger.task(task_id, epoch)['cancel_requested']) else None
        if self.connection_state != 'connected':
            return None
        row = self.ledger.conn.execute("SELECT a.* FROM worker_attempts a JOIN worker_tasks t USING(task_id) "
                                        "WHERE a.state='queued' AND t.epoch=? AND t.deleted=0 AND t.archived=0 "
                                        "AND t.management_pending IS NULL ORDER BY a.created_at,a.rowid LIMIT 1", (epoch,)).fetchone()
        return ('run', dict(row)) if row else None

    def _supervise(self):
        while not self.stop.is_set():
            self.publish_worker_report()
            with self.service._lock:
                job = self._next_job_locked()
            if job:
                try:
                    if job[0] == 'run':
                        self._run(job[1])
                    else:
                        self._resolve_cancel(job[1])
                except Exception:
                    # No raw exception can enter GUI, logs or model context.
                    with self.service._lock:
                        self.connection_state = 'uncertain'
                        self.ledger.interrupt(report=self.queue_worker_report_locked)
            self.publish_worker_report()
            self.wake.wait(0.75 if job else 2)
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
                    if uncertain:
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
                    self.connection_state = 'connected'
        except BridgeError:
            # No repeated failed cancellation network loop. User may explicitly retry.
            with self.service._lock:
                self.connection_state = 'uncertain'

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
        # The report owns the GUI slot, not DSH execution. Marking stop discards
        # its eventual result; never delay DSH cancellation by joining model I/O.
        # Service close/reset drain the GUI slot before closing Store/client.
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=25)
            if thread.is_alive():
                raise ChannelError(409, 'worker_draining', '协作请求仍在退出，请稍后重试')
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
            self.thread = None
            self.connection_state = 'paused'
