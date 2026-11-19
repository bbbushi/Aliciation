"""Design-v2 business services. Wired through ChatService and thought tools.

Division of labour: the model proposes decisions and reasons; these services
own verification, versions, budgets, receipts and delivery. Nothing here calls
a model, and no method mutates permissions, quotas or receiver identity from
model arguments. The scheduler's numeric logic lives in ``alice/activity.py``
(per design §9); these classes expose it under the v2 contract.
"""
from __future__ import annotations

import time
import uuid
from .. import autonomy_policy
from dataclasses import dataclass, replace

from .models import (
    ActivityMode, ActivitySnapshot, ContactIntent, ContactReply, EvidenceKind, EvidenceRef,
    ExecutionContext, FinishDecision, ForegroundProjection, IdlePeriod,
    InvitationAttempt, InvitationState, MigrationPlan, ModeScore, OutcomeDraft,
    ProgressKind, ProgressReceipt, ProgressReport, Question, QuestionStatus,
    ReplyDecision, ScheduleDecision, Scope, Tuning, ViewRevision, ViewState, WaitKind,
)

_MAX_WAIT_KIND = {WaitKind.USER_FEEDBACK: QuestionStatus.WAITING_USER,
                  WaitKind.SOURCE: QuestionStatus.WAITING_SOURCE,
                  WaitKind.PERMISSION: QuestionStatus.WAITING_PERMISSION,
                  WaitKind.BUDGET: QuestionStatus.WAITING_BUDGET,
                  WaitKind.MATERIAL: QuestionStatus.WAITING_SOURCE}


@dataclass(slots=True, kw_only=True)
class ProgressService:
    repository: object
    evidence: object
    host: object
    tuning: Tuning

    def link_family(self, context: ExecutionContext, question_id: int,
                    *, target_family_id: str, reason: str) -> Question:
        """Explicit related-question binding; ancestry and evidence stay put."""
        question = self.repository.get_question(context.scope, question_id)
        if not isinstance(target_family_id, str) or not target_family_id.startswith("q") or len(target_family_id) > 40:
            raise ValueError("问题族引用无效")
        with self.repository.transaction():
            self.repository.link_family_override(question_id, target_family_id, (reason or "")[:500])
        return self.repository.get_question(context.scope, question_id)

    def classify(self, context: ExecutionContext, question: Question | None,
                 report: ProgressReport) -> ProgressReceipt:
        """Validate a claim; a rewrite or successful write alone is not progress."""
        maximum = self.tuning.same_material_windows
        run_id = context.run_id or context.request_id
        external = question.material.external_material_version if question is not None else ""
        working = question.material.working_revision if question is not None else ""
        if not isinstance(report.summary, str) or not report.summary.strip():
            raise ValueError("结束回执缺少简短结果说明")
        substantive = False
        reason = "reported"
        if report.progress_kind == ProgressKind.EVIDENCE:
            self.evidence.validate_refs(context.scope, report.evidence_refs)
            external_refs = tuple(r for r in report.evidence_refs if r.kind not in {
                EvidenceKind.SELF_VIEW, EvidenceKind.HISTORICAL_AI_SPEECH})
            if question is None:
                substantive, reason = bool(external_refs), "free_window_evidence"
            elif external_refs and self.evidence.relevant_change(question, external_refs):
                substantive, reason = True, "new_external_evidence"
            else:
                reason = "evidence_already_known"
        elif report.progress_kind == ProgressKind.ACTION:
            self.evidence.validate_actions(context, report.action_refs)
            substantive, reason = bool(report.action_refs), "actual_action_receipt"
        elif report.progress_kind == ProgressKind.INFERENCE:
            substantive, reason = True, "bounded_inference"
        elif report.progress_kind == ProgressKind.REWRITE:
            reason = "wording_revision_only"
        else:
            reason = "no_substantive_change"
        if report.decision == FinishDecision.WAIT and report.wait_condition is None:
            raise ValueError("选择等待时须给出等待条件")
        remaining = (self.repository.remaining_family_windows(question, maximum=maximum)
                     if question is not None else maximum)
        return ProgressReceipt(run_id=run_id, actual_execution=True, substantive_progress=substantive,
                               resulting_status=None, external_material_version=external,
                               working_revision=working, remaining_family_windows=remaining, reason=reason,
                               question_id=question.id if question is not None else None)

    def finish(self, context: ExecutionContext, question: Question | None,
               report: ProgressReport, *, actual_execution: bool | None = None) -> ProgressReceipt:
        """Background-only, versioned, transactional and idempotent."""
        self.host.validate_background_context(context)
        existing = self.repository.get_progress(context.run_id or context.request_id)
        if existing is not None:
            return existing
        receipt = self.classify(context, question, report)
        if actual_execution is not None:
            receipt = replace(receipt, actual_execution=actual_execution)
        existing = self.repository.get_progress(receipt.run_id)
        if existing is not None:
            return existing
        maximum = self.tuning.same_material_windows
        if not autonomy_policy.TEST_CONTROLS_ONLY and question is not None and receipt.substantive_progress and report.progress_kind == ProgressKind.INFERENCE:
            receipt = replace(receipt, remaining_family_windows=self.repository.grant_family_bonus(question, maximum=maximum))
        resulting = None
        if question is not None:
            resulting = question.status.value
            if report.decision == FinishDecision.WAIT and report.wait_condition is not None:
                status = _MAX_WAIT_KIND.get(report.wait_condition.kind)
                if status is None:
                    raise ValueError("等待条件类型无法映射为疑问状态")
                with self.repository.transaction():
                    self.repository.apply_host_status(question.id, status, report.wait_condition)
                    if report.next_step:
                        self.repository.store.conn.execute("UPDATE thought_questions SET next_step=? WHERE id=?",
                                                           (report.next_step[:1000], question.id))
                resulting = status.value
        if report.decision == FinishDecision.REST:
            self._enter_rest(context, report.requested_idle_seconds)
        if resulting is not None:
            receipt = replace(receipt, resulting_status=QuestionStatus(resulting))
        return self.repository.save_progress(context, report, receipt)

    def _enter_rest(self, context: ExecutionContext, requested: int | None) -> None:
        idle_seconds = autonomy_policy.TEST_IDLE_SECONDS if autonomy_policy.TEST_CONTROLS_ONLY else 0
        for policy in (() if autonomy_policy.TEST_CONTROLS_ONLY else self.tuning.modes):
            if policy.mode.value == "idle":
                idle_seconds = policy.seconds
        if idle_seconds <= 0:
            return  # Voluntary idle disabled by setting; hard waiting still applies.
        seconds = idle_seconds if requested is None else max(1, int(requested)) if autonomy_policy.TEST_CONTROLS_ONLY else max(1, min(int(requested), idle_seconds))
        now = time.time()
        self.repository.save_idle(IdlePeriod(scope=context.scope, started_at=now, until_at=now + seconds,
                                             reason="activity_finish_rest",
                                             related_run_id=context.run_id), scope=context.scope)


@dataclass(slots=True, kw_only=True)
class WaitingService:
    repository: object
    evidence: object

    def apply_outcome_wait(self, context: ExecutionContext, draft: OutcomeDraft) -> Question:
        """next_status and wait_condition are independent of remaining text.

        ``remaining`` stays the question's executable next step (v1 column
        semantics); an open question without one is a contract violation.
        """
        question = self.repository.get_question(context.scope, draft.question_id)
        if question.version != draft.expected_question_version:
            raise ValueError("疑问版本已变化，请重新读取后提交")
        status = draft.next_status
        if status == QuestionStatus.OPEN and not draft.remaining.strip():
            raise ValueError("保持 open 须在 remaining 里给出剩余可执行事项")
        wait = draft.wait_condition
        if status in {QuestionStatus.WAITING_USER, QuestionStatus.WAITING_SOURCE,
                      QuestionStatus.WAITING_PERMISSION, QuestionStatus.WAITING_BUDGET}:
            if wait is None:
                raise ValueError("等待状态须附等待条件")
            if _MAX_WAIT_KIND.get(wait.kind) != status:
                raise ValueError("等待条件类型与状态不一致")
            self.evidence.validate_refs(context.scope, wait.related_refs)
            if wait.after_user_turn_id is not None and wait.after_user_turn_id < 0:
                raise ValueError("等待引用无效")
        updated = replace(question, status=status,
                          next_step=draft.remaining or question.next_step,
                          wait_condition=wait if status.value.startswith("waiting") else None)
        return self.repository.save_question(updated, expected_version=question.version)

    def reconsider(self, context: ExecutionContext, question: Question,
                   relevant_refs: tuple[EvidenceRef, ...]) -> Question:
        """Only a mechanically verified relevant change reopens the wait."""
        if question.status not in _MAX_WAIT_KIND.values():
            return question
        if relevant_refs and self.evidence.relevant_change(question, relevant_refs):
            updated = replace(question, status=QuestionStatus.OPEN, wait_condition=None)
            return self.repository.save_question(updated, expected_version=question.version)
        return question


@dataclass(slots=True, kw_only=True)
class ActivityScheduler:
    repository: object
    evidence: object
    clock: object
    host: object
    tuning: Tuning

    def _config(self, store):
        from .. import thought_workspace as thoughts
        return thoughts.settings(store)

    def scores(self, context: ExecutionContext) -> tuple[ModeScore, ...]:
        """Read-only readiness then weights, including voluntary idle."""
        from ..activity import weights
        store = self.repository.store
        projected = weights(store, self._config(store))
        return tuple(ModeScore(mode=ActivityMode(mode), eligible=item["weight"] > 0, weight=item["weight"],
                               reason=item["reason"], question_id=item.get("question_id"),
                               content_score=item.get("content_score", 0),
                               rotation_score=item.get("rotation", 0))
                     for mode, item in projected["modes"].items())

    def choose(self, context: ExecutionContext) -> ScheduleDecision:
        """Distinguish mandatory condition waiting from choosing IDLE."""
        from ..activity import choose
        store = self.repository.store
        mode, projected = choose(store, self._config(store))
        scores = tuple(ModeScore(mode=ActivityMode(m), eligible=i["weight"] > 0, weight=i["weight"], reason=i["reason"],
                                 question_id=i.get("question_id"), content_score=i.get("content_score", 0),
                                 rotation_score=i.get("rotation", 0))
                       for m, i in projected["modes"].items())
        idle_until = None
        if mode == "idle":
            idle_until = time.time() + max(0, projected.get("idle_seconds", 0))
        return ScheduleDecision(scope=context.scope,
                                mode=(ActivityMode(mode) if mode else None),
                                reason=projected.get("reason", ""), scores=scores,
                                question_id=projected.get("question_id"), idle_until_at=idle_until)

    def enter_idle(self, context: ExecutionContext, *, reason: str,
                   requested_seconds: int | None = None) -> IdlePeriod:
        """Persist the timer; zero model, tool or memory steps."""
        idle_seconds = autonomy_policy.TEST_IDLE_SECONDS if autonomy_policy.TEST_CONTROLS_ONLY else 0
        for policy in (() if autonomy_policy.TEST_CONTROLS_ONLY else self.tuning.modes):
            if policy.mode.value == "idle":
                idle_seconds = policy.seconds
        if idle_seconds <= 0:
            raise ValueError("发呆时长设为0，主动抽选发呆已停用")
        seconds = idle_seconds if requested_seconds is None else max(1, int(requested_seconds)) if autonomy_policy.TEST_CONTROLS_ONLY else max(1, min(int(requested_seconds), idle_seconds))
        now = time.time()
        period = IdlePeriod(scope=context.scope, started_at=now, until_at=now + seconds,
                            reason=(reason or "voluntary_rest")[:200], related_run_id=context.run_id)
        with self.repository.transaction():
            self.repository.save_idle(period, scope=context.scope)
            from ..activity import record_idle_choice
            record_idle_choice(self.repository.store, period.started_at, seconds, period.reason)
        return period

    def reconsider_idle(self, context: ExecutionContext,
                        relevant_refs: tuple[EvidenceRef, ...] = ()) -> ScheduleDecision:
        """Expiry permits a new choice; it never forces work."""
        period = self.repository.get_idle(context.scope)
        if period is None:
            return self.choose(context)
        now = self.clock.snapshot().wall_at
        changed = bool(relevant_refs)
        if now < period.until_at and not changed:
            return ScheduleDecision(scope=context.scope, mode=None, reason="idle_active",
                                    scores=(), question_id=None, idle_until_at=period.until_at)
        with self.repository.transaction():
            self.repository.save_idle(None, scope=context.scope)
            from .. import thought_workspace as thoughts
            thoughts.event(self.repository.store, "idle_ended", "gui",
                           {"reason": "expired" if not changed else "relevant_change",
                            "was_until": period.until_at})
        return self.choose(context)


@dataclass(slots=True, kw_only=True)
class ViewService:
    repository: object
    questions: object
    evidence: object

    def commit_outcome(self, context: ExecutionContext, draft: OutcomeDraft, *,
                       basis: dict, old_memory=None) -> ViewRevision | None:
        """Default to revising the current view; kind=none creates none.

        Revision, history and the question's current pointer commit in one unit
        of work; memory ids, provenance and dependencies are preserved.
        """
        store = self.repository.store
        question = self.questions.get_question(context.scope, draft.question_id)
        if question.version != draft.expected_question_version:
            raise ValueError("疑问版本已变化，请重新读取后提交")
        head = self.repository.get_for_question(context.scope, draft.question_id)
        if draft.kind == "stance" and head is not None and old_memory is None and not draft.create_independent_view:
            raise ValueError("该问题已有当前观点；请先读取当前版并作为修订提交，或说明另立独立观点的理由")
        if head is not None and old_memory is not None:
            if head.memory_id != old_memory["id"]:
                raise ValueError("修订对象不是该问题的当前观点，请先读取当前版")
            if draft.expected_view_revision is not None and draft.expected_view_revision != head.revision:
                raise ValueError("观点当前版本已变化，请重新读取后修订")
        if draft.kind == "none":
            return None
        view_id = head.view_id if head is not None else (
            f"view_{draft.question_id}" if not draft.create_independent_view
            else f"view_{draft.question_id}_{uuid.uuid4().hex[:6]}")
        now = time.time()
        content = ("当前采纳的思辨（不是独立事实验证）\n主张：" + draft.conclusion.replace("\n", "\n  ")
                   if draft.kind == "stance"
                   else "探索所得（按所引材料理解）：\n" + draft.conclusion)
        if draft.kind == "stance":
            content += "\n不确定：" + (draft.uncertainty or "当前采纳的解释，尚未独立验证")
            content += "\n下次回应：" + (draft.next_response or "待相关情境检验")
            content += "\n状态：先验"
            if old_memory is not None:
                content += f"\n曾以为：{old_memory['title']}\n修订自：question:{draft.question_id}@{draft.expected_question_version}"
        content += f"\n保留理由：{draft.reason}\n出处：question:{draft.question_id}@{draft.expected_question_version}"
        outcome_title = f"Q{draft.question_id}v{draft.expected_question_version} " + question.question
        outcome_title = outcome_title[:24]
        with store.source_transaction():
            if basis.get("packets"):
                mid = store.save_observation(basis["packets"], title=outcome_title, content=content, scope="gui")
                if mid is None:
                    raise ValueError("来源当前不允许保存成果，疑问保持不变")
                if draft.kind == "stance":
                    store.conn.execute("UPDATE memories SET kind='stance',layer='longterm' WHERE id=?", (mid,))
            else:
                mid = store.insert_memory(outcome_title, content,
                                          kind="stance" if draft.kind == "stance" else "event",
                                          layer="longterm" if draft.kind == "stance" else "active")
            for parent_id, digest in (basis.get("memories") or {}).items():
                from ..observation_memory import version as dependency_version
                store.conn.execute(
                    "INSERT OR IGNORE INTO memory_dependencies(memory_id,parent_memory_id,parent_version) VALUES(?,?,?)",
                    (mid, int(parent_id), dependency_version(store.get_memory(int(parent_id)))))
            if old_memory is not None:
                store.conn.execute("INSERT INTO view_revisions(old_id,new_id,reason,created_at) VALUES(?,?,?,?)",
                                   (old_memory["id"], mid, draft.reason, now))
                if head is not None:
                    store.conn.execute("UPDATE memories SET layer='warehouse' WHERE id=? AND layer='longterm'",
                                       (old_memory["id"],))
            revision = ViewRevision(view_id=view_id, revision=0, scope=context.scope,
                                    question_id=draft.question_id, memory_id=mid,
                                    state=ViewState.CANDIDATE, material=question.material, conclusion=draft.conclusion,
                                    uncertainty=draft.uncertainty, change_reason=draft.reason,
                                    next_response=draft.next_response,
                                    previous_revision=head.revision if head is not None else None)
            committed = self.repository.commit_revision(
                revision, expected_revision=(head.revision if head is not None and head.view_id == view_id else None))
            store.conn.execute("UPDATE thought_questions SET outcome_id=?,updated_at=? WHERE id=?",
                               (mid, now, draft.question_id))
        return committed

    def current_for_context(self, scope: Scope, *, query: str, limit: int) -> tuple[ViewRevision, ...]:
        return self.repository.current_for_context(scope, query, limit=limit)


@dataclass(slots=True, kw_only=True)
class InvitationService:
    repository: object
    host: object
    clock: object
    tuning: Tuning

    def _now(self) -> float:
        return self.clock.snapshot().wall_at

    def prepare(self, context: ExecutionContext, intent: ContactIntent,
                *, invitation_text: str) -> InvitationAttempt:
        """Short invitation only; preparing is not delivering."""
        if not isinstance(invitation_text, str) or not invitation_text.strip() or len(invitation_text) > 240:
            raise ValueError("邀请须为一句简短的话（240字符内），不携带展开正文")
        if intent.state != "active":
            raise ValueError("联系意图已结束，不能准备邀请")
        self.host.validate_intent(context.scope, intent)
        now = self._now()
        if not autonomy_policy.TEST_CONTROLS_ONLY and intent.next_eligible_at is not None and intent.next_eligible_at > now:
            raise ValueError("该主题尚在再邀间隔内")
        if self.repository.pending_reply(context.scope) is not None:
            raise ValueError("同一时间仅保留一个待回应邀请")
        prepared = next((a for a in self._attempts(context.scope, intent.id)
                         if a.state == InvitationState.PREPARED), None)
        if prepared is not None:
            raise ValueError("该意图已有待投递邀请")
        with self.repository.transaction():
            saved_intent = self.record_intent(context, intent)
            attempt = InvitationAttempt(id="inv_" + uuid.uuid4().hex[:20], version=1, intent_id=saved_intent.id,
                           intent_version=saved_intent.version, scope=context.scope,
                           state=InvitationState.PREPARED, invitation_text=invitation_text.strip(),
                           prepared_at=now)
            saved = self.repository.save_attempt(attempt, expected_version=None)
            from .. import thought_workspace as thoughts
            thoughts.event(self.repository.store, "invitation_prepared", saved.id,
                           {"intent_id": saved_intent.id, "chars": len(invitation_text)})
        return saved

    def record_intent(self, context: ExecutionContext, intent: ContactIntent) -> ContactIntent:
        """Persist the wish to talk independently of delivery permission/time."""
        self.host.validate_intent(context.scope, intent)
        duplicate = self.repository.store.conn.execute(
            "SELECT id FROM v2_contact_intents WHERE epoch=? AND id<>? "
            "AND (topic=? OR (question_id IS NOT NULL AND question_id=?)) LIMIT 1",
            (context.scope.session_epoch, intent.id, intent.topic, intent.question_id)).fetchone()
        if duplicate is not None:
            raise ValueError("该主题已有联系意图，请沿用意图索引并等待合适机会；已拒绝的话题不自动重邀")
        active = self.repository.store.conn.execute(
            "SELECT COUNT(*) FROM v2_contact_intents WHERE epoch=? AND state='active'",
            (context.scope.session_epoch,)).fetchone()[0]
        exists = self.repository.store.conn.execute(
            "SELECT 1 FROM v2_contact_intents WHERE id=? AND epoch=?",
            (intent.id, context.scope.session_epoch)).fetchone()
        if active >= 20 and not exists:
            raise ValueError("活跃联系意图已达容量，请先结束或合并旧意图")
        if exists:
            current = self.repository.get_intent(context.scope, intent.id)
            if current.version != intent.version or current.state != "active":
                raise ValueError("联系意图版本已变化或已结束")
            if current == intent:
                return current
            if any(a.state in {InvitationState.PREPARED, InvitationState.AWAITING_REPLY,
                               InvitationState.ACCEPTED} for a in self._attempts(context.scope, intent.id)):
                raise ValueError("该意图正在邀请或展开，暂不修改内容")
        return self.repository.save_intent(intent, expected_version=intent.version if exists else None)

    def delivery_blockers(self, scope: Scope, intent: ContactIntent | None = None,
                          *, attempt_id: str | None = None) -> tuple[str, ...]:
        """Live projection, shared by decision, delivery and the UI."""
        from .. import activity, thought_workspace as thoughts
        store = self.repository.store
        config = thoughts.settings(store)
        blocked = []
        if not config["allow_paid"]:
            blocked.append("master_disabled")
        if not activity.awake(store):
            blocked.append("not_awake")
        if not config["proactive_enabled"]:
            blocked.append("contact_permission_disabled")
        if activity.active_idle(store):
            blocked.append("resting")
        pending = self.repository.pending_reply(scope)
        if pending is not None and pending.id != attempt_id:
            blocked.append("awaiting_reply")
        if intent is not None:
            try:
                self.host.validate_intent(scope, intent)
            except ValueError:
                blocked.append("source_changed")
        if not autonomy_policy.TEST_CONTROLS_ONLY:
            if not activity.in_window(config["contact_start"], config["contact_end"]):
                blocked.append("outside_contact_window")
            if intent is not None and not config.get(intent.motive):
                blocked.append("motive_disabled")
            if intent is not None and intent.next_eligible_at is not None and intent.next_eligible_at > self._now():
                blocked.append("retry_not_due")
            if not thoughts.contact_capacity(store, config):
                blocked.append("contact_capacity")
        return tuple(blocked)

    def _attempts(self, scope: Scope, intent_id: str) -> tuple[InvitationAttempt, ...]:
        rows = self.repository.store.conn.execute(
            "SELECT id FROM v2_invitation_attempts WHERE intent_id=? AND epoch=? ORDER BY id",
            (intent_id, scope.session_epoch)).fetchall()
        return tuple(self.repository.get_attempt(scope, r[0]) for r in rows)

    def deliver(self, context: ExecutionContext, attempt: InvitationAttempt) -> InvitationAttempt:
        """Awake/non-idle grant + channel + quota; one atomic actual receipt."""
        if attempt.state == InvitationState.AWAITING_REPLY:
            return attempt
        if attempt.state != InvitationState.PREPARED:
            raise ValueError("邀请已不在可投递状态")
        intent = self.repository.get_intent(context.scope, attempt.intent_id)
        try:
            self.host.validate_intent(context.scope, intent)
        except ValueError as exc:
            return self.repository.save_attempt(replace(attempt, state=InvitationState.WITHDRAWN,
                deferred_reason=str(exc)[:500]), expected_version=attempt.version)
        if intent.version != attempt.intent_version:
            return self.repository.save_attempt(replace(attempt, state=InvitationState.WITHDRAWN,
                deferred_reason="intent_version_changed"), expected_version=attempt.version)
        if self.delivery_blockers(context.scope, intent, attempt_id=attempt.id):
            return attempt
        return self.host.commit_invitation_delivery(context, attempt,
                                                    wait_seconds=self.tuning.invitation_wait_seconds)

    def reply(self, context: ExecutionContext, reply: ContactReply) -> InvitationAttempt:
        """Bind the current real user message; the background model cannot accept.

        ACCEPT always requires a real foreground user turn. DEFER and DECLINE
        may also arrive as direct user actions (panel buttons), which carry no
        expansion and therefore no turn binding.
        """
        attempt = self.repository.get_attempt(context.scope, reply.attempt_id)
        if attempt.version != reply.expected_attempt_version:
            raise ValueError("邀请版本已变化，请刷新核对")
        if attempt.state != InvitationState.AWAITING_REPLY:
            raise ValueError("该邀请不在等待回应状态")
        if attempt.expires_at is not None and attempt.expires_at <= self._now():
            raise ValueError("邀请已超过等待期限")
        if reply.decision == ReplyDecision.ACCEPT:
            self.host.validate_foreground_reply(context, attempt)
        now = self._now()
        intent = self.repository.get_intent(context.scope, attempt.intent_id)
        if reply.decision == ReplyDecision.ACCEPT:
            self.host.validate_intent(context.scope, intent)
            updated = replace(attempt, state=InvitationState.ACCEPTED,
                                      reply_user_turn_id=context.user_turn_id,
                                      expansion_request_id=context.request_id)
            saved = self.repository.save_attempt(updated, expected_version=attempt.version)
        elif reply.decision == ReplyDecision.DEFER:
            retry = reply.requested_retry_at
            if retry is not None and not (now <= retry <= now + 30 * 86400):
                raise ValueError("约定的再次邀请时间超出合理范围")
            updated = replace(attempt, state=InvitationState.DEFERRED,
                                      deferred_reason=(reply.reason or "user_deferred")[:500])
            saved = self.repository.save_attempt(updated, expected_version=attempt.version)
            intent = replace(intent, state="active",
                                     next_eligible_at=retry if retry is not None
                                     else now if autonomy_policy.TEST_CONTROLS_ONLY else now + self.tuning.same_topic_retry_seconds)
            self.repository.save_intent(intent, expected_version=intent.version)
        else:
            updated = replace(attempt, state=InvitationState.DECLINED,
                                      deferred_reason=(reply.reason or "user_declined")[:500])
            saved = self.repository.save_attempt(updated, expected_version=attempt.version)
            intent = replace(intent, state="closed", next_eligible_at=None)
            self.repository.save_intent(intent, expected_version=intent.version)
        from .. import thought_workspace as thoughts
        thoughts.event(self.repository.store, "invitation_reply", saved.id,
                       {"decision": reply.decision.value, "user_turn_id": context.user_turn_id})
        return saved

    def expire(self, scope: Scope) -> tuple[InvitationAttempt, ...]:
        """Silent persisted deadline handling; no model call or follow-up."""
        now = self._now()
        expired = []
        for attempt in self.repository.due_timeouts(scope, now_at=now):
            intent = self.repository.get_intent(scope, attempt.intent_id)
            with self.repository.transaction():
                updated = replace(attempt, state=InvitationState.DEFERRED,
                                          deferred_reason="await_timeout")
                saved = self.repository.save_attempt(updated, expected_version=attempt.version)
                intent = replace(intent, next_eligible_at=now if autonomy_policy.TEST_CONTROLS_ONLY else now + self.tuning.same_topic_retry_seconds)
                self.repository.save_intent(intent, expected_version=intent.version)
            expired.append(saved)
        return tuple(expired)

    def reconsider(self, context: ExecutionContext, intent: ContactIntent) -> ContactIntent:
        """A later opportunity permits reevaluation, never automatic resend."""
        now = self._now()
        if intent.state != "active":
            return intent
        if not autonomy_policy.TEST_CONTROLS_ONLY and intent.next_eligible_at is not None and intent.next_eligible_at > now:
            return intent
        return intent  # Whether it is still worth raising stays a model decision at prepare.

    def commit_expansion(self, context: ExecutionContext, *, attempt_id: str,
                         assistant_turn_id: int) -> InvitationAttempt:
        attempt = self.repository.get_attempt(context.scope, attempt_id)
        return self.host.commit_expansion(context, attempt, assistant_turn_id=assistant_turn_id)


@dataclass(slots=True, kw_only=True)
class ForegroundAssembler:
    views: ViewService
    questions: object
    invitations: object
    host: object

    def project(self, context: ExecutionContext, *, user_text: str) -> ForegroundProjection:
        """Relevant current views + bounded waits + at most one invitation."""
        from ..store import text_score
        scope = context.scope
        current_views = self.views.current_for_context(scope, query=user_text, limit=2)
        waits = tuple(q for q in self.questions.list_questions(scope)
                      if q.status.value.startswith("waiting")
                      and (not user_text or text_score(user_text, q.question + " " + (q.next_step or "")) >= .12))[:3]
        pending = self.invitations.pending_index(scope)
        expansion = None
        row = self.invitations.store.conn.execute(
            "SELECT id FROM v2_invitation_attempts WHERE epoch=? AND state='accepted' "
            "AND expansion_request_id=? LIMIT 1", (scope.session_epoch, context.request_id)).fetchone()
        if row is not None:
            attempt = self.invitations.get_attempt(scope, row[0])
            intent = self.invitations.get_intent(scope, attempt.intent_id)
            expansion = self.host.load_expansion(context, attempt, intent)
        return ForegroundProjection(scope=scope, current_views=current_views, relevant_waits=waits,
                                    pending_invitation=pending, expansion=expansion)


@dataclass(slots=True, kw_only=True)
class AutonomyV2:
    """Application facade: sole routing entry for the v2 capabilities."""
    scheduler: ActivityScheduler
    progress: ProgressService
    waiting: WaitingService
    views: ViewService
    invitations: InvitationService
    foreground: ForegroundAssembler
    repository: object
    migration: object
    communication: object

    def configure(self, scope: Scope, *, expected_revision: int, tuning: Tuning) -> ActivitySnapshot:
        from ..channels import ChannelError
        store = self.repository.store
        current = int(store.meta_get("autonomy_revision") or 0)
        if expected_revision != current:
            raise ChannelError(409, "settings_changed", "设置已改变，请刷新后再保存")
        return self.repository.save_tuning(scope, tuning, expected_revision=expected_revision)

    def snapshot(self, scope: Scope) -> ActivitySnapshot:
        return self.repository.snapshot(scope)

    def activity_finish(self, context: ExecutionContext, question_id: int | None,
                        report: ProgressReport, *, communication=None, basis=None) -> ProgressReceipt:
        question = self.repository.get_question(context.scope, question_id) if question_id is not None else None
        with self.repository.transaction():
            previous = self.communication.get(context)
            if previous is not None and previous["decision"] == "unknown" and communication is not None:
                # A resumed run can replace the host's placeholder exactly once
                # with a real model decision. Explicit decisions stay immutable.
                self.repository.store.conn.execute(
                    "DELETE FROM v2_contact_decisions WHERE run_id=? AND epoch=?",
                    (context.run_id, context.scope.session_epoch))
                self.repository.store.conn.execute(
                    "DELETE FROM v2_progress_receipts WHERE run_id=?", (context.run_id,))
            receipt = self.progress.finish(context, question, report)
            self.communication.finish(context, communication, basis=basis or {})
            return receipt

    def contact_reply(self, context: ExecutionContext, reply: ContactReply) -> InvitationAttempt:
        with self.repository.transaction():
            return self.invitations.reply(context, reply)

    def prepare_migration(self, scope: Scope) -> MigrationPlan:
        return self.migration.prepare(scope)

    def apply_migration(self, plan: MigrationPlan) -> None:
        self.migration.apply(plan)
