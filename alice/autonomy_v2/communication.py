"""One communication decision per work window; no model calls or timers."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass

from .models import CommunicationPlan, ExecutionContext


@dataclass(slots=True, kw_only=True)
class CommunicationService:
    invitations: object

    @property
    def store(self):
        return self.invitations.repository.store

    def get(self, context: ExecutionContext):
        return self.get_for_run(context.scope.session_epoch, context.run_id or context.request_id)

    def get_for_run(self, epoch: str, run_id: str):
        row = self.store.conn.execute(
            "SELECT * FROM v2_contact_decisions WHERE run_id=? AND epoch=?", (run_id, epoch)).fetchone()
        if row is None:
            return None
        result = dict(row)
        basis = json.loads(result.pop("basis"))
        result["blockers"] = json.loads(result["blockers"])
        from ..thought_workspace import valid_basis
        if result["decision"] != "unknown" and not valid_basis(self.store, basis):
            result["reason"] = "资料来源已失效，理由不再展示"
            result["detail_state"] = "unavailable"
        return result

    def finish(self, context: ExecutionContext, plan: CommunicationPlan | None, *, basis: dict):
        """Caller wraps this and progress.finish in the same Store transaction."""
        self.invitations.host.validate_background_context(context)
        existing = self.get(context)
        if existing is not None:
            return existing
        if plan is None:
            decision, reason, outcome = "unknown", "窗口未提交交流判断", "not_evaluated"
        else:
            decision, reason = plan.decision, plan.reason
            if decision not in {"invite", "later", "none"} or not reason.strip():
                raise ValueError("交流决定须为 invite/later/none，并附简短理由")
            outcome = "no_contact" if decision == "none" else "intent_recorded"
            if decision == "none" and (plan.intent is not None or plan.invitation_text):
                raise ValueError("无需联系时不提交意图或邀请文本")
            if decision != "none" and plan.intent is None:
                raise ValueError("现在邀请或留待以后须有可用的交流意图")
        intent_id = attempt_id = None
        blockers = ()
        if plan is not None and plan.intent is not None:
            intent = self.invitations.record_intent(context, plan.intent)
            intent_id = intent.id
            # Even reconsidering only an index retains the qualifications of
            # its content, so the journal cannot outlive revoked source rights.
            from ..thought_workspace import merge_basis
            from ..context_memory import version
            basis = merge_basis(basis, intent.basis)
            for ref in intent.content_refs:
                kind, tail = ref.split(":", 1)
                identity = int(tail.split("@", 1)[0])
                if kind == "question":
                    row = self.store.conn.execute("SELECT basis FROM thought_questions WHERE id=?", (identity,)).fetchone()
                    basis = merge_basis(basis, json.loads(row[0]))
                elif kind == "stance":
                    basis = merge_basis(basis, {"epoch": context.scope.session_epoch,
                        "memories": {str(identity): version(self.store.get_memory(identity))},
                        "packets": [p["id"] for p in self.store.observation_sources(identity, scope="gui")]})
            blockers = self.invitations.delivery_blockers(context.scope, intent)
            if decision == "invite":
                if not plan.invitation_text.strip() or len(plan.invitation_text) > 240:
                    raise ValueError("现在邀请须提供240字符内的简短邀请，不带成果正文")
                if not blockers:
                    attempt = self.invitations.prepare(context, intent, invitation_text=plan.invitation_text)
                    attempt_id = attempt.id
                    outcome = "invitation_prepared"
                else:
                    outcome = "delivery_blocked"
        from ..thought_workspace import dump, event
        run_id = context.run_id or context.request_id
        self.store.conn.execute(
            "INSERT INTO v2_contact_decisions(run_id,epoch,decision,reason,outcome,intent_id,attempt_id,"
            "blockers,basis,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (run_id, context.scope.session_epoch, decision, reason, outcome, intent_id, attempt_id,
             dump(list(blockers)), dump(basis), time.time()))
        event(self.store, "communication_decided", run_id,
              {"decision": decision, "outcome": outcome, "intent_id": intent_id,
               "attempt_id": attempt_id, "blockers": list(blockers)})
        return self.get(context)
