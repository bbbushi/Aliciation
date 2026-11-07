"""Worker-specific public API routing; no agent execution on read surfaces."""
from __future__ import annotations

from .channels import ChannelError


WORKERS = {'dsh': 'dsh-agent', 'claude': 'claude-agent'}
TASK_ACTIONS = frozenset({'detail', 'events', 'cancel', 'review', 'revoke',
                          'archive', 'restore', 'delete', 'recover_local'})


def catalog():
    """Static declarations only: no Store, credentials, installation or handshake."""
    return {'workers': [
        {'id': 'dsh', 'worker_id': 'dsh-agent', 'name': 'DSH', 'api': '/api/workers/dsh',
         'avatar': {'large': '/assets/dsh-avatar-a-v1.png', 'small': '/assets/dsh-avatar-small-v1.png'}},
        {'id': 'claude', 'worker_id': 'claude-agent', 'name': 'Claude', 'api': '/api/workers/claude',
         'avatar': {'large': '/assets/claude-avatar-large-v1.png', 'small': '/assets/claude-avatar-small-v1.png'}},
    ]}


def read(service, operation, *args, **kwargs):
    """Bound lock acquisition for both scoped and compatibility read endpoints."""
    if not service._lock.acquire(timeout=0.5):
        raise ChannelError(503, 'host_busy', 'Alice 正忙，请稍后重试读取；未启动任务')
    try:
        return operation(*args, **kwargs)
    finally:
        service._lock.release()


def access(service, worker, action, payload=None, *, read_only=False):
    if not isinstance(worker, str) or worker not in WORKERS:
        raise ChannelError(404, 'unknown_worker', '工具入口不存在')
    if not isinstance(action, str):
        raise ChannelError(400, 'invalid_action', '工具操作无效')
    if payload is not None and not isinstance(payload, dict):
        raise ChannelError(400, 'invalid_request', '工具请求必须是对象')
    worker_id, coordinator = WORKERS[worker], service._workers
    if payload is not None:
        payload = dict(payload)
        if 'worker_id' in payload and payload['worker_id'] != worker_id:
            raise ChannelError(409, 'worker_mismatch', '请求工具与当前入口不一致')
        # Permission replies bind worker_id cryptographically/by exact identity;
        # their existing protocol retains it. Other payload schemas need no alias.
        if action != 'permissions/answer':
            payload.pop('worker_id', None)

    is_read = (action in {'status', 'tasks', 'tasks/list', 'tasks/detail', 'tasks/events', 'permissions/list'}
               or action in {'policy', 'task-settings'} and payload is None)
    if read_only and not is_read:
        raise ChannelError(405, 'read_only_action', '此读取接口不允许修改工具状态')
    if action == 'status':
        if payload:
            raise ChannelError(400, 'invalid_request', '工具状态读取不接受参数')
        operation = lambda: coordinator.worker_status_locked(worker_id)
    elif action == 'policy':
        if worker == 'claude':
            operation = lambda: coordinator.claude_policy(payload)
        elif payload is None:
            operation = lambda: coordinator.worker_status_locked(worker_id)
        else:
            operation = lambda: coordinator.save_policy(payload, worker=worker_id)
    elif action == 'connection' and worker == 'dsh':
        if payload is None:
            raise ChannelError(400, 'invalid_request', '连接操作缺少参数')
        operation = lambda: coordinator.connection_action(payload, worker=worker_id)
    elif action == 'task-settings' and worker == 'dsh':
        operation = (lambda: coordinator.worker_status_locked(worker_id)) if payload is None else (
            lambda: coordinator.management_settings(payload, worker=worker_id))
    elif action in {'tasks', 'tasks/list'}:
        operation = lambda: coordinator.tasks('list', payload, worker=worker_id)
    elif action.startswith('tasks/') and action[6:] in TASK_ACTIONS:
        task_action = action[6:]
        if task_action == 'recover_local' and worker != 'claude':
            raise ChannelError(409, 'worker_action_unavailable', '此工具没有本地进程恢复操作')
        if worker == 'claude' and task_action in {'archive', 'restore', 'delete'} and payload is not None:
            # Compatibility field only; Claude has no DSH management preference.
            payload.setdefault('settings_revision', 0)
        operation = lambda: coordinator.tasks(task_action, payload, worker=worker_id)
    elif action in {'permissions/list', 'permissions/answer'} and worker == 'claude':
        operation = lambda: coordinator.permissions(action.split('/')[1], payload)
    elif action in {'inputs', 'artifacts/download'}:
        operation = coordinator.unavailable
    else:
        raise ChannelError(404, 'worker_action_unavailable', '当前工具不提供此操作')
    # Only known read operations hold the bounded Store lock across delegation.
    # Connection/recovery/remote management retain their own short lock sections.
    return read(service, operation) if is_read else operation()
