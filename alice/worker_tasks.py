"""Worker ledger primitives. Call only while holding ChatService's Store lock.

No network, subprocesses, model calls or raw adapter logs belong in this module.
Task bodies have their own lifetime and never become ordinary conversation rows.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import uuid

from .channels import ChannelError

SCHEMA = """
CREATE TABLE IF NOT EXISTS worker_policy (
 id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL,
 enabled INTEGER NOT NULL DEFAULT 0, max_runs INTEGER NOT NULL DEFAULT 0
);
INSERT OR IGNORE INTO worker_policy(id,revision) VALUES(1,1);
CREATE TABLE IF NOT EXISTS worker_tasks (
 task_id TEXT PRIMARY KEY, epoch TEXT NOT NULL, scope TEXT NOT NULL DEFAULT 'gui',
 policy_revision INTEGER NOT NULL, state TEXT NOT NULL, title TEXT NOT NULL,
 created_at REAL NOT NULL, updated_at REAL NOT NULL, expires_at REAL NOT NULL,
 revoked INTEGER NOT NULL DEFAULT 0, version INTEGER NOT NULL DEFAULT 1,
 last_sequence INTEGER NOT NULL DEFAULT 0, session_id TEXT,
 review TEXT NOT NULL DEFAULT 'unreviewed', cancel_requested INTEGER NOT NULL DEFAULT 0,
 termination_confirmed INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS worker_messages (
 message_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, attempt_id TEXT,
 actor TEXT NOT NULL, body TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1,
 state TEXT NOT NULL, created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS worker_attempts (
 attempt_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, message_id TEXT NOT NULL,
 state TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL,
 usage_json TEXT NOT NULL DEFAULT '{"known":false}', sent INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS worker_events (
 cursor INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL UNIQUE,
 task_id TEXT NOT NULL, attempt_id TEXT, message_id TEXT, epoch TEXT NOT NULL,
 scope TEXT NOT NULL, policy_revision INTEGER NOT NULL, sequence INTEGER NOT NULL,
 type TEXT NOT NULL, actor TEXT NOT NULL, observed_at REAL NOT NULL,
 payload TEXT NOT NULL, validation_state TEXT NOT NULL,
 UNIQUE(task_id,sequence)
);
CREATE INDEX IF NOT EXISTS worker_events_task ON worker_events(task_id,sequence);
CREATE TABLE IF NOT EXISTS worker_requests (
 epoch TEXT NOT NULL, request_id TEXT NOT NULL, digest TEXT NOT NULL,
 result TEXT NOT NULL, PRIMARY KEY(epoch,request_id)
);
CREATE TABLE IF NOT EXISTS worker_reports (
 attempt_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, epoch TEXT NOT NULL,
 outcome TEXT NOT NULL, report_state TEXT NOT NULL DEFAULT 'pending',
 kind TEXT NOT NULL DEFAULT 'legacy_transcript',
 turn_id INTEGER UNIQUE, created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS worker_reports_pending ON worker_reports(report_state,created_at);
CREATE TABLE IF NOT EXISTS worker_management_operations (
 epoch TEXT NOT NULL, request_id TEXT NOT NULL, task_id TEXT NOT NULL,
 action TEXT NOT NULL, digest TEXT NOT NULL, bridge_instance TEXT, session_id TEXT,
 remote_payload TEXT NOT NULL, settings_revision INTEGER NOT NULL,
 state TEXT NOT NULL DEFAULT 'pending', result TEXT,
 created_at REAL NOT NULL, updated_at REAL NOT NULL,
 PRIMARY KEY(epoch,request_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS worker_management_pending
 ON worker_management_operations(task_id) WHERE state='pending';
CREATE TABLE IF NOT EXISTS worker_management_settings (
 id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL DEFAULT 1,
 sync_archive INTEGER NOT NULL DEFAULT 0, sync_delete INTEGER NOT NULL DEFAULT 0
);
INSERT OR IGNORE INTO worker_management_settings(id) VALUES(1);
"""
ACTIVE = frozenset({'queued', 'starting', 'running', 'waiting_permission', 'collecting', 'stopping'})
TERMINAL = frozenset({'succeeded', 'failed', 'cancelled', 'interrupted', 'unknown'})


def identity(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,128}', value):
        raise ChannelError(400, 'invalid_request', '协作记录标识无效')
    return value


def bounded_int(value, default, minimum, maximum):
    if value is None:
        return default
    if type(value) is not int or not minimum <= value <= maximum:
        raise ChannelError(400, 'invalid_request', '协作记录分页参数无效')
    return value


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def safe_text(value, maximum=12000):
    """Defense in depth; adapter must select public fields before calling this."""
    if not isinstance(value, str):
        raise ValueError('公开消息必须是文本')
    value = value[:maximum]
    value = re.sub(r'(?i)\b(?:bearer\s+|sk-)[A-Za-z0-9_./+\-=]{8,}', '[凭据已隐藏]', value)
    value = re.sub(r'(?i)\b(api[_-]?key|access[_-]?token|authorization|cookie|password)\s*[:=]\s*[^\s,;]+',
                   r'\1=[已隐藏]', value)
    value = re.sub(r'(?i)\b[A-Z]:[\\/][^\s\n<>"\u3002]+', '[本机路径]', value)
    return value


class WorkerLedger:
    def __init__(self, store):
        self.store = store
        self.conn = store.conn
        self.conn.executescript(SCHEMA)
        migrations = {
            'worker_policy': {'workspace_root': "TEXT NOT NULL DEFAULT ''",
                              'permission_mode': "TEXT NOT NULL DEFAULT 'read-only'",
                              'native_permissions_ack': 'INTEGER NOT NULL DEFAULT 0',
                              'max_seconds': 'INTEGER NOT NULL DEFAULT 600'},
            'worker_tasks': {'bridge_instance': 'TEXT', 'remote_cursor': 'INTEGER NOT NULL DEFAULT 0',
                             'agent_idle': 'INTEGER NOT NULL DEFAULT 0',
                             'archived': 'INTEGER NOT NULL DEFAULT 0',
                             'deleted': 'INTEGER NOT NULL DEFAULT 0',
                             'management_pending': 'TEXT',
                             'management_sync_dsh': 'INTEGER NOT NULL DEFAULT 0',
                             'workspace_root': "TEXT NOT NULL DEFAULT ''",
                             'session_created_at': 'INTEGER',
                             'session_closed': 'INTEGER NOT NULL DEFAULT 0'},
            'worker_attempts': {'request_id': 'TEXT', 'originating_request_id': 'TEXT',
                               'tool_call_id': 'TEXT', 'deadline': 'REAL'},
            'worker_messages': {'truncated': 'INTEGER NOT NULL DEFAULT 0'},
            'worker_events': {'truncated': 'INTEGER NOT NULL DEFAULT 0'},
            'worker_reports': {'kind': "TEXT NOT NULL DEFAULT 'legacy_transcript'"},
        }
        with self.conn:
            for table, columns in migrations.items():
                existing = {r[1] for r in self.conn.execute('PRAGMA table_info(' + table + ')')}
                for name, declaration in columns.items():
                    if name not in existing:
                        self.conn.execute('ALTER TABLE ' + table + ' ADD COLUMN ' + name + ' ' + declaration)
            # Reconnecting after a restart is not permission to generate an old
            # summary. Preserve delivery as a status notice, with no model call.
            self.conn.execute("UPDATE worker_reports SET report_state='pending_notice',kind='status' "
                              "WHERE report_state IN ('pending','summarizing')")

    def policy(self):
        return dict(self.conn.execute('SELECT * FROM worker_policy WHERE id=1').fetchone())

    def runs_used(self):
        return self.conn.execute('SELECT COUNT(*) FROM worker_attempts a JOIN worker_tasks t USING(task_id) '
                                 'WHERE t.policy_revision=?', (self.policy()['revision'],)).fetchone()[0]

    def task(self, task_id, epoch):
        row = self.conn.execute("SELECT * FROM worker_tasks WHERE task_id=? AND epoch=? AND scope='gui'",
                                (identity(task_id), epoch)).fetchone()
        if row is None:
            raise ChannelError(404, 'not_found', '当前会话没有此协作任务')
        return dict(row)

    def content_unavailable_reason(self, task):
        """Keep connectivity, disabled access and actual revocation distinct."""
        if task['deleted']:
            return 'deleted'
        if task['revoked']:
            return 'revoked'
        if task['expires_at'] <= time.time():
            return 'expired'
        if task['bridge_instance'] == 'local:claude-agent':  # sqlite3.Row 无 .get()，统一下标
            # 本地轨（claude）任务的可见性按 claude_policy 判，不随 DSH 政策起落。
            try:
                cp = self.conn.execute(
                    'SELECT revision,enabled FROM claude_policy WHERE id=1').fetchone()
            except Exception:
                return 'authorization_disabled'
            if cp is None or not cp['enabled']:
                return 'authorization_disabled'
            if task['policy_revision'] != cp['revision']:
                return 'policy_changed'
            return None
        policy = self.policy()
        if task['policy_revision'] != policy['revision']:
            return 'policy_changed'
        if not policy['enabled']:
            return 'authorization_disabled'
        return None

    def visible(self, task):
        return self.content_unavailable_reason(task) is None

    def summary(self, task):
        reason = self.content_unavailable_reason(task)
        unavailable_titles = {'deleted': '任务已删除', 'revoked': '任务正文已撤销',
                              'expired': '任务资料已过期', 'policy_changed': '任务授权已变更',
                              'authorization_disabled': '任务资料待恢复授权'}
        return {**{k: task[k] for k in ('task_id', 'state', 'updated_at', 'created_at', 'last_sequence',
                                       'review', 'version', 'cancel_requested', 'termination_confirmed')},
                'name': 'claude-agent' if task['bridge_instance'] == 'local:claude-agent' else 'dsh-agent', 'agent_idle': bool(task['agent_idle']),
                'session_closed': bool(task['session_closed']),
                'archived': bool(task['archived']), 'deleted': bool(task['deleted']),
                'management_pending': task['management_pending'],
                'management_sync_dsh': bool(task['management_sync_dsh']),
                'title': task['title'] if reason is None else unavailable_titles[reason],
                'content_available': reason is None, 'content_unavailable_reason': reason,
                # Existing clients use this legacy field as an availability
                # flag. New clients display the explicit reason above.
                'content_revoked': reason is not None, 'usage': self.usage(task['task_id'])}

    def record_usage(self, attempt_id, report):
        value = {'known': False, 'cost_known': False, 'source': 'dsh_reported', 'coverage': 'owned_agent_only'}
        if isinstance(report, dict):
            for key in ('model_requests', 'reported_requests', 'input_tokens', 'output_tokens'):
                number = report.get(key)
                if type(number) is int and 0 <= number <= 10**12:
                    value[key] = number
        self.conn.execute('UPDATE worker_attempts SET usage_json=? WHERE attempt_id=?',
                          (json.dumps(value), attempt_id))

    def usage(self, task_id):
        value = {'known': False, 'cost_known': False, 'source': 'dsh_reported', 'coverage': 'owned_agent_only'}
        for row in self.conn.execute('SELECT usage_json FROM worker_attempts WHERE task_id=?', (task_id,)):
            report = json.loads(row[0])
            for key in ('input_tokens', 'output_tokens'):
                if type(report.get(key)) is int and report[key] >= 0:
                    value['reported_' + key] = value.get('reported_' + key, 0) + report[key]
        return value

    def event(self, task_id, kind, *, actor='host', payload=None, attempt_id=None, message_id=None, truncated=False):
        """Caller owns transaction; state and final event commit together."""
        task = self.conn.execute('SELECT * FROM worker_tasks WHERE task_id=?', (task_id,)).fetchone()
        sequence = task['last_sequence'] + 1
        now = time.time()
        event_id = uuid.uuid4().hex
        # Public projection only. Never persist arbitrary terminal/SDK objects,
        # private reasoning, environment maps or authentication fields.
        payload = payload or {}
        public = {key: safe_text(payload[key]) for key in ('text', 'summary', 'tool_name')
                  if isinstance(payload.get(key), str)}
        truncated = truncated or any(len(payload[key]) > 12000 for key in public)
        encoded = json.dumps(public, ensure_ascii=False, allow_nan=False)
        while len(encoded.encode()) > 24000:
            largest = max(public, key=lambda key: len(public[key]))
            public[largest] = public[largest][:len(public[largest]) // 2]
            truncated = True
            encoded = json.dumps(public, ensure_ascii=False, allow_nan=False)
        self.conn.execute('INSERT INTO worker_events(event_id,task_id,attempt_id,message_id,epoch,scope,'
                          'policy_revision,sequence,type,actor,observed_at,payload,validation_state,truncated) '
                          'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                          (event_id, task_id, attempt_id, message_id, task['epoch'], 'gui',
                           task['policy_revision'], sequence, kind, actor, now, encoded,
                           'host_observed' if actor == 'host' else 'agent_report_unverified', int(bool(truncated))))
        self.conn.execute('UPDATE worker_tasks SET last_sequence=?,updated_at=? WHERE task_id=?',
                          (sequence, now, task_id))

    def replay(self, epoch, request_id, payload):
        row = self.conn.execute('SELECT digest,result FROM worker_requests WHERE epoch=? AND request_id=?',
                                (epoch, identity(request_id))).fetchone()
        if row:
            if row['digest'] != digest(payload):
                raise ChannelError(409, 'request_conflict', '同一请求标识不能更换参数')
            return json.loads(row['result'])
        # A management request reserves its identity before remote I/O. An
        # uncertain archive/restore must not free that ID for another mutation.
        pending = self.conn.execute(
            'SELECT digest FROM worker_management_operations WHERE epoch=? AND request_id=?',
            (epoch, request_id)).fetchone()
        if pending and pending['digest'] != digest(payload):
            raise ChannelError(409, 'request_conflict', '同一请求标识不能更换参数')
        return None

    def receipt(self, epoch, request_id, payload, result):
        self.conn.execute('INSERT INTO worker_requests(epoch,request_id,digest,result) VALUES(?,?,?,?)',
                          (epoch, request_id, digest(payload), json.dumps(result, ensure_ascii=False)))

    def interrupt(self, *, report=None):
        """Startup/reset never replays a send or kills a historical PID."""
        with self.conn:
            rows = self.conn.execute("SELECT task_id FROM worker_tasks WHERE state IN "
                                     "('queued','starting','running','waiting_permission','collecting','stopping')").fetchall()
            for row in rows:
                attempts = (self.conn.execute(
                    "SELECT attempt_id FROM worker_attempts WHERE task_id=? AND state IN "
                    "('queued','starting','running','waiting_permission','collecting','stopping')", (row['task_id'],)).fetchall()
                    if report is not None else [])
                self.conn.execute("UPDATE worker_tasks SET state='unknown',cancel_requested=1 WHERE task_id=?",
                                  (row['task_id'],))
                self.conn.execute("UPDATE worker_attempts SET state='unknown',updated_at=? WHERE task_id=? "
                                  "AND state IN ('queued','starting','running','waiting_permission','collecting','stopping')",
                                  (time.time(), row['task_id']))
                self.event(row['task_id'], 'connection_lost', payload={
                    'text': '宿主连接中断；执行和费用未知。没有自动重发，也没有按历史 PID 终止进程。'})
                # Only live execution failures request reports. Startup, archive
                # and drain keep the default and never backfill historical work.
                for attempt in attempts:
                    report(row['task_id'], attempt['attempt_id'], 'unknown')

    def revoke(self, task_id):
        self.conn.execute("UPDATE worker_tasks SET revoked=1,version=version+1,title='' WHERE task_id=?", (task_id,))
        self.conn.execute("UPDATE worker_messages SET body='',version=version+1 WHERE task_id=?", (task_id,))
        self.conn.execute("UPDATE worker_events SET payload='{}' WHERE task_id=?", (task_id,))
        self.event(task_id, 'content_revoked', payload={'text': '任务资料已撤回；只保留宿主状态。'})
