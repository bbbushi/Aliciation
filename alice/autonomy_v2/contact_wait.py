"""Persistent reply semantics, independent of quota/cooldown test policy.

An empty intent_id is a channel-wide availability hold. Topic holds survive
ordinary greetings and a global 'available'; only a relevant reply or an
explicitly agreed time permits that topic to be reconsidered.
"""
from __future__ import annotations

import time


class ContactWait:
    def __init__(self, store):
        self.store = store

    def rows(self, scope, *, active_only=True):
        rows = self.store.conn.execute(
            "SELECT * FROM v2_contact_holds WHERE epoch=? AND channel_id=? "
            "ORDER BY updated_at DESC", (scope.session_epoch, scope.channel_id)).fetchall()
        now = time.time()
        return [dict(r) for r in rows if not active_only or r['resume_at'] is None or r['resume_at'] > now]

    def blockers(self, scope, intent_id=None):
        rows = self.rows(scope)
        result = []
        if any(not r['intent_id'] for r in rows):
            result.append('waiting_user_availability')
        if intent_id and any(r['intent_id'] == intent_id for r in rows):
            result.append('topic_waiting_user')
        return tuple(result)

    def hold(self, scope, *, reason, attempt_id=None, intent_id=None, resume_at=None):
        now = time.time()
        last = self.store.conn.execute("SELECT COALESCE(MAX(id),0) FROM turns WHERE role='user'").fetchone()[0]
        for identity in ('', intent_id) if intent_id else ('',):
            self.store.conn.execute(
                "INSERT INTO v2_contact_holds(epoch,channel_id,intent_id,reason,attempt_id,"
                "after_user_turn_id,resume_at,updated_at) VALUES(?,?,?,?,?,?,?,?) "
                "ON CONFLICT(epoch,channel_id,intent_id) DO UPDATE SET reason=excluded.reason,"
                "attempt_id=excluded.attempt_id,after_user_turn_id=excluded.after_user_turn_id,"
                "resume_at=excluded.resume_at,updated_at=excluded.updated_at",
                (scope.session_epoch, scope.channel_id, identity, reason, attempt_id, last, resume_at, now))
        # Prepared text must never wait in an automatic outbox until a user
        # returns. A later opportunity requires a new model decision.
        self.store.conn.execute(
            "UPDATE v2_invitation_attempts SET state='withdrawn',version=version+1,"
            "deferred_reason='user_unavailable',updated_at=? WHERE epoch=? AND state='prepared'",
            (now, scope.session_epoch))
        if intent_id:
            question = self.store.conn.execute(
                "SELECT q.id FROM v2_contact_intents i JOIN thought_questions q ON q.id=i.question_id "
                "AND q.epoch=i.epoch WHERE i.id=? AND i.epoch=? AND i.motive='request' AND q.status='open'",
                (intent_id, scope.session_epoch)).fetchone()
            if question:
                from .adapters import ActivityRepositoryAdapter
                from .models import QuestionStatus, WaitCondition, WaitKind
                ActivityRepositoryAdapter(self.store).apply_host_status(question[0], QuestionStatus.WAITING_USER,
                    WaitCondition(kind=WaitKind.USER_FEEDBACK, reason='等待用户补充邀请所需信息',
                                  context_hint='相关用户信息到达后再恢复；普通问候不解除等待',
                                  after_user_turn_id=last))

    def clear(self, scope, *, intent_id=None):
        self.store.conn.execute(
            "DELETE FROM v2_contact_holds WHERE epoch=? AND channel_id=? AND (intent_id='' OR intent_id=?)",
            (scope.session_epoch, scope.channel_id, intent_id or ''))

    def feedback_attempts(self, scope):
        """Late replies remain possible; no source body or expired text here."""
        return self.store.conn.execute(
            "SELECT a.* FROM v2_invitation_attempts a JOIN v2_contact_holds h "
            "ON h.attempt_id=a.id AND h.intent_id=a.intent_id AND h.epoch=a.epoch "
            "JOIN v2_contact_intents i ON i.id=a.intent_id AND i.epoch=a.epoch "
            "WHERE a.epoch=? AND h.channel_id=? AND a.state='deferred' AND i.state='active' "
            "ORDER BY h.updated_at DESC LIMIT 5", (scope.session_epoch, scope.channel_id)).fetchall()

    def is_late_reply(self, scope, attempt_id):
        return any(r['id'] == attempt_id for r in self.feedback_attempts(scope))

    def bootstrap(self, scope):
        """One-time conservative adoption of pre-upgrade unacknowledged invites.

        No replay, no model call, no change to original invitation history.
        Runs only under the coordinator's existing source transaction.
        """
        key = 'contact_wait_v1:' + scope.session_epoch + ':' + scope.channel_id
        if self.store.meta_get(key):
            return
        rows = self.store.conn.execute(
            "SELECT a.* FROM v2_invitation_attempts a JOIN v2_contact_intents i "
            "ON a.intent_id=i.id AND a.epoch=i.epoch WHERE a.epoch=? AND i.state='active' "
            "AND a.state='deferred' AND NOT EXISTS(SELECT 1 FROM v2_invitation_attempts b "
            "WHERE b.intent_id=a.intent_id AND b.epoch=a.epoch AND b.created_at>a.created_at) "
            "ORDER BY a.updated_at", (scope.session_epoch,)).fetchall()
        for row in rows:
            self.hold(scope, reason='prior_unanswered', attempt_id=row['id'], intent_id=row['intent_id'])
        self.store.meta_set(key, '1')
