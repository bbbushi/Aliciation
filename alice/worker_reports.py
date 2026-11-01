"""Once-only GUI completion notices; no model, transport or ordinary prose copy."""
from __future__ import annotations

import time

from .channels import ChannelError
from .worker_context import WORKER_SHADOW


OUTCOMES = frozenset({'succeeded', 'failed', 'cancelled', 'interrupted', 'unknown'})
REQUEST_FINISHED = frozenset({'completed', 'completed_with_warning', 'failed', 'interrupted'})
MAX_REPLY_CHARS = 6000
STATUS_TEXT = {
    'succeeded': 'DSH 已结束本轮执行；结果尚未人工验收。',
    'failed': '本轮协作未成功完成；未自动重派，请在协作会话查看状态。',
    'cancelled': '本轮协作已停止或在发送前取消；外部进程树终止仍未确认。',
    'interrupted': '本轮协作已中断；执行与费用状态仍可能未知，未自动重派。',
    'unknown': '本轮协作状态暂不可确认；执行与费用未知，未自动重派。',
}


class WorkerReportsMixin:
    def queue_worker_report_locked(self, task_id, attempt_id, outcome):
        """Called in the terminal-state transaction; never backfill old attempts."""
        if outcome not in OUTCOMES:
            return
        try:
            # A report failure must not roll back or relabel the actual execution.
            with self.service.store.source_transaction():
                self.ledger.conn.execute(
                    'INSERT OR IGNORE INTO worker_reports '
                    '(attempt_id,task_id,epoch,outcome,report_state,created_at) '
                    "SELECT a.attempt_id,t.task_id,t.epoch,?,'pending',? "
                    'FROM worker_attempts a JOIN worker_tasks t USING(task_id) '
                    "WHERE a.attempt_id=? AND t.task_id=? AND t.scope='gui' AND a.state=?",
                    (outcome, time.time(), attempt_id, task_id, outcome))
        except Exception:
            # No report exception, body or transport retry enters execution flow.
            return

    def publish_worker_report(self):
        """Try one pending notice without waiting on the foreground chat slot."""
        token = None
        try:
            with self.service._lock:
                if self.closing or self.paused or self.stop.is_set():
                    return
                pending = self.ledger.conn.execute(
                    "SELECT attempt_id,epoch FROM worker_reports WHERE report_state='pending' "
                    'ORDER BY created_at,attempt_id LIMIT 1').fetchone()
                if pending is None:
                    return
                attempt_id, epoch = pending['attempt_id'], pending['epoch']
            # Never wait here: reset owns this slot while joining the worker.
            token = self.service._channels.reserve('gui', 'worker-report-' + attempt_id,
                                                   wait=False, notify_foreground=False)
            with self.service._lock:
                if self.closing or self.paused or self.stop.is_set():
                    return
                self._publish_worker_report_locked(attempt_id, epoch)
        except ChannelError:
            # Busy foreground work leaves the durable pending row for a later tick.
            return
        except Exception:
            # Notification failures cannot change connection/execution state.
            return
        finally:
            if token is not None:
                self.service._channels.release(token)

    def _publish_worker_report_locked(self, attempt_id, epoch):
        store, conn = self.service.store, self.ledger.conn
        report = conn.execute(
            "SELECT * FROM worker_reports WHERE attempt_id=? AND report_state='pending'",
            (attempt_id,)).fetchone()
        if report is None:
            return
        task = conn.execute('SELECT * FROM worker_tasks WHERE task_id=?', (report['task_id'],)).fetchone()
        attempt = conn.execute('SELECT * FROM worker_attempts WHERE attempt_id=?', (attempt_id,)).fetchone()
        valid = (epoch == report['epoch'] == self.service._channels.epoch == store.meta_get('session_epoch')
                 and task is not None and task['epoch'] == epoch and task['scope'] == 'gui'
                 and self.ledger.visible(task) and report['outcome'] in OUTCOMES
                 and attempt is not None and attempt['task_id'] == task['task_id'])
        origin = (conn.execute('SELECT session_epoch,channel_id,status FROM channel_requests WHERE request_id=?',
                               (attempt['originating_request_id'],)).fetchone() if valid else None)
        if not valid or origin is None or origin['session_epoch'] != epoch or origin['channel_id'] != 'gui':
            with store.source_transaction():
                conn.execute("UPDATE worker_reports SET report_state='suppressed' WHERE attempt_id=? AND report_state='pending'",
                             (attempt_id,))
            return
        if origin['status'] not in REQUEST_FINISHED:
            return

        outcome = report['outcome']
        status = STATUS_TEXT[outcome]
        text = '【DSH 协作回报 · 宿主转录】\n任务 ' + task['task_id'] + '\n' + status
        reply = None
        if outcome == 'succeeded':
            reply = conn.execute(
                "SELECT * FROM worker_messages WHERE task_id=? AND attempt_id=? AND actor='dsh' "
                "AND state='completed' AND trim(body)!='' ORDER BY rowid DESC LIMIT 1",
                (task['task_id'], attempt_id)).fetchone()
            if reply is not None:
                excerpt = reply['body'][:MAX_REPLY_CHARS]
                text += '\n\nDSH 自报（未核验）：\n' + excerpt
                if reply['truncated'] or len(excerpt) != len(reply['body']):
                    text += '\n\n（公开回复有截断；请在协作会话核对完整性。）'
            else:
                text += '\n尚无可展示的完整公开回复，请在协作会话查看工作记录。'

        anchor_id = 'report-anchor-' + attempt_id
        now = time.time()
        with store.source_transaction():
            # The fixed host anchor also gives body-less notices the same expiry
            # and revocation rules. No task title/input enters ordinary turns.
            conn.execute(
                'INSERT INTO worker_messages '
                '(message_id,task_id,attempt_id,actor,body,version,state,created_at,truncated) '
                "VALUES(?,?,?,'host',?,1,'completed',?,0)",
                (anchor_id, task['task_id'], attempt_id, status, now))
            refs = [{'task_id': task['task_id'], 'message_id': anchor_id, 'version': 1,
                     'epoch': epoch, 'policy_revision': task['policy_revision']}]
            if reply is not None:
                refs.append({'task_id': task['task_id'], 'message_id': reply['message_id'],
                             'version': reply['version'], 'epoch': epoch,
                             'policy_revision': task['policy_revision']})
            if not store.worker_refs_valid(refs, scope='gui'):
                # Roll back the anchor too if expiry occurs during projection.
                raise ValueError('worker_report_expired')
            turn_id = conn.execute("INSERT INTO turns(ts,role,content) VALUES(?,'assistant',?)",
                                   (now, WORKER_SHADOW)).lastrowid
            store._worker_turn_write(turn_id, text, refs, 'gui')
            if not store.worker_refs_valid(refs, scope='gui'):
                raise ValueError('worker_report_expired')
            conn.execute("UPDATE worker_reports SET report_state='delivered',turn_id=? "
                         "WHERE attempt_id=? AND report_state='pending'", (turn_id, attempt_id))
