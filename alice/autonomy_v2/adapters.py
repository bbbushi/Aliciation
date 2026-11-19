"""SQLite adapters over the shared Store. Callers hold the ChatService lock.

Every mutation runs inside ``store.source_transaction()`` so repositories, the
host port and existing thought tools share one unit of work; nothing here
commits on its own. Table names carry the ``v2_`` prefix (see schema.py).
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from .. import autonomy_policy
from dataclasses import replace

from .models import (
    ActivityMode, ActivitySnapshot, ClockSnapshot, ContactIntent, EvidenceKind,
    EvidenceRef, ExpansionMaterial, IdlePeriod, InvitationAttempt, InvitationState,
    MaterialVersion, MigrationPlan, ModeScore, ProgressReceipt, Question, QuestionStatus, Scope,
    Tuning, ViewRevision, ViewState, WaitCondition, WaitKind, WakeState,
)

_MAX_TURN = 253402300799


def dump(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _loads(value, default):
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _finite(value, low=0.0, high=_MAX_TURN) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and low <= value <= high


class SystemClock:
    """Wall + monotonic snapshot with a per-process epoch id."""

    def __init__(self):
        self.epoch = f"proc-{os.getpid()}-{time.time_ns()}"

    def snapshot(self) -> ClockSnapshot:
        return ClockSnapshot(wall_at=time.time(), monotonic_at=time.monotonic(), clock_epoch=self.epoch)


def scope_epoch(scope: Scope) -> str:
    return scope.session_epoch


# ---------------------------------------------------------------- questions

def family_of(store, question_id: int) -> str:
    """Parent-chain root wins unless an explicit override was linked."""
    row = store.conn.execute("SELECT family_id FROM v2_question_families WHERE question_id=?",
                             (question_id,)).fetchone()
    if row:
        return row[0]
    seen, current = set(), question_id
    while True:
        parent = store.conn.execute("SELECT parent_id FROM thought_questions WHERE id=?",
                                    (current,)).fetchone()
        if parent is None or parent[0] is None or parent[0] in seen or len(seen) > 50:
            return f"q{current}"
        seen.add(parent[0])
        current = parent[0]


def _basis_versions(store, basis: dict) -> tuple[dict, dict]:
    """Split a question basis into external identities and self-authored work."""
    packets = {}
    for pid in basis.get("packets") or []:
        row = store.conn.execute("SELECT version FROM source_packets WHERE id=?", (str(pid),)).fetchone()
        if row is not None:
            packets[str(pid)] = str(row[0])
    turns, own_turns = {}, {}
    for key, value in (basis.get("turns") or {}).items():
        row = store.conn.execute("SELECT role FROM turns WHERE id=?", (int(key),)).fetchone()
        if row is not None:
            (turns if row[0] == "user" else own_turns)[str(key)] = str(value)
    memories = {}
    stance_memories = {}
    for mid, ver in (basis.get("memories") or {}).items():
        row = store.conn.execute("SELECT kind FROM memories WHERE id=?", (int(mid),)).fetchone()
        if row is None:
            continue
        (stance_memories if row[0] == "stance" else memories)[str(mid)] = str(ver)
    return {"packets": packets, "turns": turns, "memories": memories}, {"stances": stance_memories, "own_turns": own_turns}


def material_version_of(store, row) -> MaterialVersion:
    basis = _loads(row["basis"], {})
    external, stances = _basis_versions(store, basis)
    head = store.conn.execute("SELECT revision FROM v2_view_heads WHERE question_id=?",
                              (row["id"],)).fetchone()
    working = dump({"question_version": row["version"], "stances": stances,
                    "view_revision": head[0] if head else None})
    return MaterialVersion(
        external_material_version=hashlib.sha256(dump(external).encode()).hexdigest()[:32],
        working_revision=hashlib.sha256(working.encode()).hexdigest()[:32],
        evidence_refs=tuple(
            EvidenceRef(kind=(EvidenceKind.SOURCE_CONTENT if key == "packets"
                              else EvidenceKind.USER_MESSAGE if key == "turns"
                              else EvidenceKind.USER_REPORT),
                        identity=identity, version=version, content_fingerprint="")
            for key, group in external.items() for identity, version in group.items()))


def wait_condition_of(store, question_id: int) -> WaitCondition | None:
    row = store.conn.execute("SELECT * FROM v2_question_waits WHERE question_id=?",
                             (question_id,)).fetchone()
    if row is None:
        return None
    return WaitCondition(kind=WaitKind(row["kind"]), reason=row["reason"],
                         context_hint=row["context_hint"],
                         related_refs=tuple(EvidenceRef(**{**ref, "kind": EvidenceKind(ref["kind"])}) for ref in _loads(row["related_refs"], [])
                                            if isinstance(ref, dict) and "kind" in ref and "identity" in ref
                                            and "version" in ref),
                         after_user_turn_id=row["after_user_turn_id"], not_before_at=row["not_before_at"])


def question_from_row(store, row) -> Question:
    agenda = store.conn.execute("SELECT activity,importance,urgency,due_at FROM thought_agenda "
                                "WHERE question_id=?", (row["id"],)).fetchone()
    from ..activity import origin_mode
    head = store.conn.execute("SELECT view_id FROM v2_view_heads WHERE question_id=?",
                              (row["id"],)).fetchone()
    return Question(
        id=row["id"], version=row["version"],
        scope=Scope(session_epoch=row["epoch"], channel_id="gui"),
        family_id=family_of(store, row["id"]),
        activity=ActivityMode(agenda[0] if agenda else origin_mode(row["origin"])),
        status=QuestionStatus(row["status"]),
        question=row["question"], material=material_version_of(store, row),
        importance=agenda[1] if agenda else 1, urgency=agenda[2] if agenda else 0,
        due_at=agenda[3] if agenda else 0, parent_id=row["parent_id"],
        next_step=row["next_step"], wait_condition=wait_condition_of(store, row["id"]),
        current_view_id=head[0] if head else None)


class ActivityRepositoryAdapter:
    def __init__(self, store):
        self.store = store

    # -- contract ---------------------------------------------------------
    def transaction(self):
        return self.store.source_transaction()

    def get_question(self, scope: Scope, question_id: int) -> Question:
        row = self.store.conn.execute("SELECT * FROM thought_questions WHERE id=? AND epoch=?",
                                      (question_id, scope_epoch(scope))).fetchone()
        if row is None:
            raise ValueError("疑问不存在或不在当前会话")
        return question_from_row(self.store, row)

    def list_questions(self, scope: Scope) -> tuple[Question, ...]:
        from .. import thought_workspace as thoughts
        rows = [r for r in thoughts.question_rows(self.store)
                if r["question"] and thoughts.valid_basis(self.store, _loads(r["basis"], {}))]
        return tuple(question_from_row(self.store, r) for r in rows)

    def list_candidates(self, scope: Scope) -> tuple[Question, ...]:
        """V2 requeue rules: open always; waiting_source only on newer packets.

        waiting_user never wakes mechanically (the foreground model judges
        relevance); permission/budget waits reopen through explicit updates.
        """
        return tuple(q for q in self.list_questions(scope) if q.status == QuestionStatus.OPEN)

    def list_family(self, scope: Scope, family_id: str) -> tuple[Question, ...]:
        return tuple(q for q in self.list_questions(scope) if q.family_id == family_id)

    def save_question(self, question: Question, *, expected_version: int) -> Question:
        with self.transaction():
            row = self.store.conn.execute(
                "SELECT * FROM thought_questions WHERE id=? AND epoch=? AND version=?",
                (question.id, scope_epoch(question.scope), expected_version)).fetchone()
            if row is None:
                raise ValueError("疑问版本已变化，请重新读取后提交")
            self.store.conn.execute(
                "UPDATE thought_questions SET status=?,progress=?,next_step=?,version=version+1,updated_at=? WHERE id=?",
                (question.status.value, row["progress"], question.next_step, time.time(), question.id))
            self._write_wait(question)
            from .. import thought_workspace as thoughts
            thoughts.event(self.store, "question_status_v2", question.id,
                           {"status": question.status.value, "expected_version": expected_version})
        return self.get_question(question.scope, question.id)

    def apply_host_status(self, question_id: int, status, wait_condition) -> None:
        """Host-authoritative status write; model-issued refs stay valid.

        Used by finish receipts: the host verified the report, so the usual
        expected-version gate does not apply, and mid-window refs must not be
        invalidated while the run is still finishing.
        """
        value = status.value if hasattr(status, "value") else status
        self.store.conn.execute("UPDATE thought_questions SET status=?,updated_at=? WHERE id=?",
                                (value, time.time(), question_id))
        if wait_condition is None:
            self.store.conn.execute("DELETE FROM v2_question_waits WHERE question_id=?", (question_id,))
            return
        refs = [{"kind": ref.kind.value, "identity": ref.identity, "version": ref.version,
                 "content_fingerprint": ref.content_fingerprint, "occurred_at": ref.occurred_at}
                for ref in wait_condition.related_refs]
        self.store.conn.execute(
            "INSERT INTO v2_question_waits(question_id,kind,reason,context_hint,related_refs,"
            "after_user_turn_id,not_before_at,set_at) VALUES(?,?,?,?,?,?,?,?) "
            "ON CONFLICT(question_id) DO UPDATE SET kind=excluded.kind,reason=excluded.reason,"
            "context_hint=excluded.context_hint,related_refs=excluded.related_refs,"
            "after_user_turn_id=excluded.after_user_turn_id,not_before_at=excluded.not_before_at,"
            "set_at=excluded.set_at",
            (question_id, wait_condition.kind.value, wait_condition.reason, wait_condition.context_hint,
             dump(refs), wait_condition.after_user_turn_id, wait_condition.not_before_at, time.time()))

    def _write_wait(self, question: Question) -> None:
        if question.wait_condition is None:
            self.store.conn.execute("DELETE FROM v2_question_waits WHERE question_id=?", (question.id,))
            return
        condition = question.wait_condition
        refs = [{"kind": ref.kind.value, "identity": ref.identity, "version": ref.version,
                 "content_fingerprint": ref.content_fingerprint, "occurred_at": ref.occurred_at}
                for ref in condition.related_refs]
        self.store.conn.execute(
            "INSERT INTO v2_question_waits(question_id,kind,reason,context_hint,related_refs,"
            "after_user_turn_id,not_before_at,set_at) VALUES(?,?,?,?,?,?,?,?) "
            "ON CONFLICT(question_id) DO UPDATE SET kind=excluded.kind,reason=excluded.reason,"
            "context_hint=excluded.context_hint,related_refs=excluded.related_refs,"
            "after_user_turn_id=excluded.after_user_turn_id,not_before_at=excluded.not_before_at,"
            "set_at=excluded.set_at",
            (question.id, condition.kind.value, condition.reason, condition.context_hint,
             dump(refs), condition.after_user_turn_id, condition.not_before_at, time.time()))

    def link_family_override(self, question_id: int, family_id: str, reason: str) -> None:
        self.store.conn.execute(
            "INSERT INTO v2_question_families(question_id,family_id,reason,set_at) VALUES(?,?,?,?) "
            "ON CONFLICT(question_id) DO UPDATE SET family_id=excluded.family_id,"
            "reason=excluded.reason,set_at=excluded.set_at",
            (question_id, family_id, reason, time.time()))

    # -- idle ---------------------------------------------------------------
    def get_idle(self, scope: Scope) -> IdlePeriod | None:
        row = self.store.conn.execute("SELECT * FROM v2_activity_idle WHERE scope=?",
                                      (scope.channel_id,)).fetchone()
        if row is None or row["until_at"] <= 0:
            return None
        return IdlePeriod(scope=scope, started_at=row["started_at"], until_at=row["until_at"],
                          reason=row["reason"], related_run_id=row["related_run_id"])

    def save_idle(self, period: IdlePeriod | None, *, scope: Scope) -> None:
        if period is None:
            self.store.conn.execute("DELETE FROM v2_activity_idle WHERE scope=?", (scope.channel_id,))
            return
        self.store.conn.execute(
            "INSERT INTO v2_activity_idle(scope,started_at,until_at,reason,related_run_id) "
            "VALUES(?,?,?,?,?) ON CONFLICT(scope) DO UPDATE SET started_at=excluded.started_at,"
            "until_at=excluded.until_at,reason=excluded.reason,related_run_id=excluded.related_run_id",
            (scope.channel_id, period.started_at, period.until_at, period.reason, period.related_run_id))

    # -- family windows ------------------------------------------------------
    def _window_row(self, question: Question, external_version: str):
        return self.store.conn.execute(
            "SELECT * FROM v2_family_windows WHERE family_id=? AND external_version=?",
            (question.family_id, external_version)).fetchone()

    def remaining_family_windows(self, question: Question, *, maximum: int) -> int:
        row = self._window_row(question, question.material.external_material_version)
        if row is None:
            return maximum
        return max(0, maximum + row["bonus"] - row["windows"])

    def consume_family_window(self, question: Question, *, run_id: str, maximum: int) -> int:
        """Atomic and idempotent by real run; a new child id grants nothing."""
        with self.transaction():
            version = question.material.external_material_version
            row = self._window_row(question, version)
            if row is not None and row["last_run_id"] == run_id:
                return max(0, maximum + row["bonus"] - row["windows"])
            windows = (row["windows"] if row else 0) + 1
            bonus = row["bonus"] if row else 0
            self.store.conn.execute(
                "INSERT INTO v2_family_windows(family_id,external_version,windows,bonus,last_run_id,updated_at) "
                "VALUES(?,?,?,?,?,?) ON CONFLICT(family_id,external_version) DO UPDATE SET "
                "windows=excluded.windows,bonus=excluded.bonus,last_run_id=excluded.last_run_id,"
                "updated_at=excluded.updated_at",
                (question.family_id, version, windows, bonus, run_id, time.time()))
            return max(0, maximum + bonus - windows)

    def grant_family_bonus(self, question: Question, *, maximum: int) -> int:
        """A substantive method step re-earns at most one extra window."""
        with self.transaction():
            version = question.material.external_material_version
            row = self._window_row(question, version)
            if row is None:
                return maximum
            bonus = min(maximum, row["bonus"] + 1)
            self.store.conn.execute("UPDATE v2_family_windows SET bonus=?,updated_at=? "
                                    "WHERE family_id=? AND external_version=?",
                                    (bonus, time.time(), question.family_id, version))
            return max(0, maximum + bonus - row["windows"])

    # -- receipts ------------------------------------------------------------
    def get_progress(self, run_id: str) -> ProgressReceipt | None:
        row = self.store.conn.execute("SELECT * FROM v2_progress_receipts WHERE run_id=?", (run_id,)).fetchone()
        if row is None:
            return None
        return ProgressReceipt(run_id=row["run_id"], question_id=row["question_id"],
                               actual_execution=bool(row["actual_execution"]),
                               substantive_progress=bool(row["substantive_progress"]),
                               resulting_status=QuestionStatus(row["resulting_status"]) if row["resulting_status"] else None,
                               external_material_version=row["external_material_version"],
                               working_revision=row["working_revision"],
                               remaining_family_windows=row["remaining_family_windows"],
                               reason=row["reason"])

    def save_progress(self, context, report, receipt: ProgressReceipt) -> ProgressReceipt:
        existing = self.get_progress(receipt.run_id)
        if existing is not None:
            return existing  # One finish receipt per execution window.
        with self.transaction():
            self.store.conn.execute(
                "INSERT OR IGNORE INTO v2_progress_receipts(run_id,question_id,decision,progress_kind,summary,"
                "actual_execution,substantive_progress,resulting_status,external_material_version,"
                "working_revision,remaining_family_windows,reason,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (receipt.run_id, receipt.question_id, report.decision.value, report.progress_kind.value,
                 report.summary[:1000], int(receipt.actual_execution), int(receipt.substantive_progress),
                 receipt.resulting_status.value if receipt.resulting_status else None,
                 receipt.external_material_version, receipt.working_revision,
                 receipt.remaining_family_windows, receipt.reason[:500], time.time()))
            self.store.conn.execute(
                "UPDATE autonomous_activity_executions SET progress=? WHERE run_id=? "
                "AND id=(SELECT MAX(id) FROM autonomous_activity_executions WHERE run_id=?)",
                (int(receipt.substantive_progress), receipt.run_id, receipt.run_id))
            from .. import thought_workspace as thoughts
            thoughts.event(self.store, "activity_receipt", receipt.run_id, {
                "decision": report.decision.value, "progress_kind": report.progress_kind.value,
                "substantive": receipt.substantive_progress, "question_id": receipt.question_id})
        return receipt

    # -- settings / snapshot ---------------------------------------------------
    def save_tuning(self, scope: Scope, tuning: Tuning, *, expected_revision: int) -> ActivitySnapshot:
        from .. import thought_workspace as thoughts
        config = thoughts.settings(self.store)
        patch = {"idle_rest_seconds": tuning_seconds(tuning, "idle"),
                 "same_material_windows": tuning.same_material_windows,
                 "invitation_wait_seconds": tuning.invitation_wait_seconds,
                 "same_topic_retry_seconds": tuning.same_topic_retry_seconds}
        if scope.session_epoch != self.store.meta_get("session_epoch"):
            raise ValueError("会话已变化")
        patch["activities"] = {p.mode.value: {"seconds": p.seconds, "tools": list(p.tool_groups)}
                               for p in tuning.modes if p.mode != ActivityMode.IDLE}
        if any(p.mode == ActivityMode.IDLE and p.tool_groups for p in tuning.modes):
            raise ValueError("发呆模式不使用工具")
        with self.transaction():
            if int(self.store.meta_get("autonomy_revision") or 0) != expected_revision:
                raise ValueError("设置已变化")
            thoughts.save_settings(self.store, patch)
            self.store.meta_set("autonomy_revision", str(expected_revision + 1))
        return self.snapshot(scope)

    def snapshot(self, scope: Scope) -> ActivitySnapshot:
        from .. import activity as v1_activity
        from .. import thought_workspace as thoughts
        config = thoughts.settings(self.store)
        lifecycle = v1_activity.lifecycle(config)
        scheduler = v1_activity.weights(self.store, config)
        idle = self.get_idle(scope)
        invitation = InvitationRepositoryAdapter(self.store).pending_index(scope)
        return ActivitySnapshot(scope=scope, settings_revision=int(self.store.meta_get("autonomy_revision") or 0),
                                requested_state=WakeState(config["wake_state"]),
                                effective_state=WakeState(lifecycle["effective"]),
                                execution_status="idle_rest" if idle and idle.until_at > time.time() else lifecycle["reason"],
                                scores=tuple(ModeScore(mode=ActivityMode(m), eligible=i["weight"] > 0,
                                                      weight=i["weight"], reason=i["reason"],
                                                      question_id=i.get("question_id"),
                                                      content_score=i.get("content_score", 0),
                                                      rotation_score=i.get("rotation", 0))
                                             for m, i in scheduler["modes"].items()),
                                idle_period=idle, pending_invitation=invitation)


def tuning_seconds(tuning: Tuning, mode: str) -> int:
    for policy in tuning.modes:
        if policy.mode.value == mode:
            return policy.seconds
    return 0


# ---------------------------------------------------------------- evidence

class StoreEvidence:
    def __init__(self, store):
        self.store = store

    def validate_refs(self, scope: Scope, refs: tuple[EvidenceRef, ...]) -> None:
        from ..thought_workspace import turn_version
        from ..context_memory import version
        if scope.session_epoch != self.store.meta_get("session_epoch") or scope.channel_id != "gui":
            raise ValueError("证据不属于当前会话")
        for ref in refs:
            if ref.kind in (EvidenceKind.USER_MESSAGE, EvidenceKind.HISTORICAL_AI_SPEECH):
                row = self.store.conn.execute("SELECT * FROM turns WHERE id=?", (int(ref.identity),)).fetchone()
                expected_role = "user" if ref.kind == EvidenceKind.USER_MESSAGE else "assistant"
                if row is None or row["role"] != expected_role or turn_version(row) != ref.version:
                    raise ValueError("历史轮次或说话者版本已变化")
            elif ref.kind == EvidenceKind.SOURCE_CONTENT:
                packets = self.store.source_packets_for([ref.identity], scope=scope.channel_id)
                if not packets or packets[0]["version"] != ref.version:
                    raise ValueError("引用的来源资料不可用或版本已变化")
            elif ref.kind in (EvidenceKind.USER_REPORT, EvidenceKind.SELF_VIEW):
                row = next((r for r in self.store.memory_rows(purpose="chat", scope=scope.channel_id)
                            if r["id"] == int(ref.identity)), None)
                if row is None or version(row) != ref.version:
                    raise ValueError("记忆资料或版本已变化")
                if (row["kind"] == "stance") != (ref.kind == EvidenceKind.SELF_VIEW):
                    raise ValueError("记忆与观点的证据身份不一致")
            else:
                raise ValueError("证据类型不支持；动作请使用action_refs")

    def material_version(self, question: Question) -> MaterialVersion:
        row = self.store.conn.execute("SELECT * FROM thought_questions WHERE id=?", (question.id,)).fetchone()
        if row is None:
            return question.material
        return material_version_of(self.store, row)

    def validate_actions(self, context, action_refs: tuple[str, ...]) -> None:
        run_id = getattr(context, "run_id", None) if context is not None else None
        if not run_id:
            raise ValueError("动作回执必须来自实际自主运行")
        row = self.store.conn.execute("SELECT checkpoint FROM exploration_runs WHERE id=?", (run_id,)).fetchone()
        messages = _loads(row[0], []) if row else []
        results = {m.get("tool_call_id"): m.get("content", "") for m in messages if m.get("role") == "tool"}
        called = {call.get("id") for m in messages for call in (m.get("tool_calls") or [])}
        ok_calls = set()
        for call_id in called:
            raw = results.get(call_id, "")
            try:
                body = json.loads(raw[raw.index("{"):])
            except (ValueError, TypeError):
                body = None
            if isinstance(body, dict) and "error" not in body and body.get("status") not in {
                    "blocked", "denied", "unavailable", "unknown_ref"}:
                ok_calls.add(call_id)
        for ref in action_refs:
            if ref not in ok_calls:
                raise ValueError(f"动作回执未在本次运行中实际发生：{ref}")

    def relevant_change(self, question: Question, new_refs: tuple[EvidenceRef, ...]) -> bool:
        known = {(ref.kind, ref.identity, ref.version) for ref in question.material.evidence_refs}
        for ref in new_refs:
            if ref.kind in (EvidenceKind.HISTORICAL_AI_SPEECH, EvidenceKind.SELF_VIEW):
                continue  # Old AI speech never reopens a wait by itself.
            if (ref.kind, ref.identity, ref.version) not in known:
                return True
        return False


# ---------------------------------------------------------------- views

class ViewRepositoryAdapter:
    def __init__(self, store):
        self.store = store

    def transaction(self):
        return self.store.source_transaction()

    def _revision_row(self, view_id: str, revision: int):
        return self.store.conn.execute("SELECT * FROM v2_view_revisions WHERE view_id=? AND revision=?",
                                       (view_id, revision)).fetchone()

    def _to_revision(self, row, scope: Scope) -> ViewRevision:
        question = self.store.conn.execute("SELECT * FROM thought_questions WHERE id=?",
                                           (row["question_id"],)).fetchone()
        material = material_version_of(self.store, question) if question is not None else MaterialVersion(external_material_version="", working_revision="", evidence_refs=())
        conclusion = uncertainty = next_response = ""
        memory = self.store.get_memory(row["memory_id"]) if row["memory_id"] else None
        if memory is not None:
            from ..thought import _field_of
            conclusion = _field_of(memory["content"], "主张") or memory["title"]
            uncertainty = _field_of(memory["content"], "不确定") or ""
            next_response = _field_of(memory["content"], "下次回应") or ""
        return ViewRevision(view_id=row["view_id"], revision=row["revision"], scope=scope,
                            question_id=row["question_id"], memory_id=row["memory_id"],
                            state=ViewState.CURRENT if self.is_current(row) else ViewState.HISTORICAL,
                            material=material, conclusion=conclusion, uncertainty=uncertainty,
                            change_reason=row["change_reason"], next_response=next_response,
                            previous_revision=row["previous_revision"])

    def is_current(self, revision_row) -> bool:
        head = self.store.conn.execute("SELECT view_id,revision FROM v2_view_heads WHERE question_id=?",
                                       (revision_row["question_id"],)).fetchone()
        return head is not None and head["view_id"] == revision_row["view_id"] and head["revision"] == revision_row["revision"]

    def get_current(self, scope: Scope, view_id: str) -> ViewRevision | None:
        row = self.store.conn.execute(
            "SELECT r.* FROM v2_view_revisions r JOIN v2_view_heads h ON h.view_id=r.view_id "
            "AND h.revision=r.revision WHERE r.view_id=? LIMIT 1", (view_id,)).fetchone()
        return self._to_revision(row, scope) if row else None

    def get_for_question(self, scope: Scope, question_id: int) -> ViewRevision | None:
        row = self.store.conn.execute(
            "SELECT r.* FROM v2_view_revisions r JOIN v2_view_heads h ON h.view_id=r.view_id "
            "AND h.revision=r.revision WHERE r.question_id=? LIMIT 1", (question_id,)).fetchone()
        return self._to_revision(row, scope) if row else None

    def history(self, scope: Scope, view_id: str) -> tuple[ViewRevision, ...]:
        rows = self.store.conn.execute("SELECT * FROM v2_view_revisions WHERE view_id=? ORDER BY revision",
                                       (view_id,)).fetchall()
        return tuple(self._to_revision(row, scope) for row in rows)

    def commit_revision(self, revision: ViewRevision, *, expected_revision: int | None) -> ViewRevision:
        with self.transaction():
            head = self.store.conn.execute("SELECT view_id,revision FROM v2_view_heads WHERE question_id=?",
                                           (revision.question_id,)).fetchone()
            same_chain = head is not None and head["view_id"] == revision.view_id
            current_revision = head["revision"] if same_chain else 0
            if expected_revision is not None and head is not None and same_chain and expected_revision != head["revision"]:
                raise ValueError("观点当前版本已变化，请重新读取后提交修订")
            self.store.conn.execute(
                "INSERT INTO v2_view_revisions(view_id,revision,question_id,memory_id,change_reason,"
                "previous_revision,created_at) VALUES(?,?,?,?,?,?,?)",
                (revision.view_id, current_revision + 1, revision.question_id, revision.memory_id,
                 revision.change_reason[:500], current_revision, time.time()))
            self.store.conn.execute(
                "INSERT INTO v2_view_heads(question_id,view_id,revision,memory_id,updated_at) VALUES(?,?,?,?,?) "
                "ON CONFLICT(question_id) DO UPDATE SET view_id=excluded.view_id,revision=excluded.revision,"
                "memory_id=excluded.memory_id,updated_at=excluded.updated_at",
                (revision.question_id, revision.view_id, current_revision + 1, revision.memory_id, time.time()))
            from .. import thought_workspace as thoughts
            thoughts.event(self.store, "view_revision_committed", revision.view_id,
                           {"question_id": revision.question_id, "revision": current_revision + 1,
                            "memory_id": revision.memory_id})
        return self.get_current(revision.scope, revision.view_id)

    def current_for_context(self, scope: Scope, query: str, *, limit: int) -> tuple[ViewRevision, ...]:
        from ..store import text_score
        rows = self.store.conn.execute(
            "SELECT r.* FROM v2_view_revisions r JOIN v2_view_heads h ON h.view_id=r.view_id "
            "AND h.revision=r.revision").fetchall()
        allowed = {r["id"]: r for r in self.store.memory_rows(purpose="chat", scope=scope.channel_id, kind="stance")}
        scored = []
        for row in rows:
            question = self.store.conn.execute("SELECT epoch FROM thought_questions WHERE id=?", (row["question_id"],)).fetchone()
            if question is None or question[0] != scope.session_epoch:
                continue
            memory = allowed.get(row["memory_id"])
            if memory is None:
                continue
            score = text_score(query, memory["title"] + " " + memory["content"]) if query else 1.0
            if not query or score >= .12:
                scored.append((score, row))
        scored.sort(key=lambda pair: -pair[0])
        return tuple(self._to_revision(row, scope) for _, row in scored[:limit])


# ---------------------------------------------------------------- invitations

def _intent_from_row(row) -> ContactIntent:
    return ContactIntent(id=row["id"], version=row["version"],
                         scope=Scope(session_epoch=row["epoch"], channel_id="gui"),
                         motive=row["motive"], topic=row["topic"], reason=row["reason"],
                         content_refs=tuple(_loads(row["content_refs"], [])),
                         state=row["state"], question_id=row["question_id"],
                         question_version=row["question_version"], view_id=row["view_id"],
                         view_revision=row["view_revision"], next_eligible_at=row["next_eligible_at"])


def _attempt_from_row(row) -> InvitationAttempt:
    return InvitationAttempt(id=row["id"], version=row["version"], intent_id=row["intent_id"],
                             intent_version=row["intent_version"],
                             scope=Scope(session_epoch=row["epoch"], channel_id="gui"),
                             state=InvitationState(row["state"]), invitation_text=row["invitation_text"],
                             prepared_at=row["prepared_at"], delivered_at=row["delivered_at"],
                             expires_at=row["expires_at"], invitation_turn_id=row["invitation_turn_id"],
                             reply_user_turn_id=row["reply_user_turn_id"],
                             expansion_request_id=row["expansion_request_id"],
                             expansion_turn_id=row["expansion_turn_id"],
                             deferred_reason=row["deferred_reason"])


class InvitationRepositoryAdapter:
    def __init__(self, store):
        self.store = store

    def transaction(self):
        return self.store.source_transaction()

    def get_intent(self, scope: Scope, intent_id: str) -> ContactIntent:
        row = self.store.conn.execute("SELECT * FROM v2_contact_intents WHERE id=? AND epoch=?",
                                      (intent_id, scope_epoch(scope))).fetchone()
        if row is None:
            raise ValueError("联系意图不存在或已失效")
        return _intent_from_row(row)

    def save_intent(self, intent: ContactIntent, *, expected_version: int | None) -> ContactIntent:
        with self.transaction():
            if expected_version is None:
                self.store.conn.execute(
                    "INSERT INTO v2_contact_intents(id,epoch,version,motive,topic,reason,content_refs,state,"
                    "question_id,question_version,view_id,view_revision,next_eligible_at,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (intent.id, scope_epoch(intent.scope), 1, intent.motive, intent.topic, intent.reason,
                     dump(list(intent.content_refs)), intent.state, intent.question_id, intent.question_version,
                     intent.view_id, intent.view_revision, intent.next_eligible_at, time.time(), time.time()))
            else:
                changed = self.store.conn.execute(
                    "UPDATE v2_contact_intents SET version=version+1,motive=?,topic=?,reason=?,content_refs=?,"
                    "state=?,question_id=?,question_version=?,view_id=?,view_revision=?,next_eligible_at=?,"
                    "updated_at=? WHERE id=? AND epoch=? AND version=?",
                    (intent.motive, intent.topic, intent.reason, dump(list(intent.content_refs)), intent.state,
                     intent.question_id, intent.question_version, intent.view_id, intent.view_revision,
                     intent.next_eligible_at, time.time(), intent.id, scope_epoch(intent.scope), expected_version)).rowcount
                if not changed:
                    raise ValueError("联系意图版本已变化，请刷新核对")
        return self.get_intent(intent.scope, intent.id)

    def get_attempt(self, scope: Scope, attempt_id: str) -> InvitationAttempt:
        row = self.store.conn.execute("SELECT * FROM v2_invitation_attempts WHERE id=? AND epoch=?",
                                      (attempt_id, scope_epoch(scope))).fetchone()
        if row is None:
            raise ValueError("邀请不存在或已失效")
        return _attempt_from_row(row)

    def save_attempt(self, attempt: InvitationAttempt, *, expected_version: int | None) -> InvitationAttempt:
        with self.transaction():
            if expected_version is None:
                self.store.conn.execute(
                    "INSERT INTO v2_invitation_attempts(id,intent_id,intent_version,epoch,version,state,"
                    "invitation_text,prepared_at,delivered_at,expires_at,invitation_turn_id,reply_user_turn_id,"
                    "expansion_request_id,expansion_turn_id,deferred_reason,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (attempt.id, attempt.intent_id, attempt.intent_version, scope_epoch(attempt.scope), 1,
                     attempt.state.value, attempt.invitation_text, attempt.prepared_at, attempt.delivered_at,
                     attempt.expires_at, attempt.invitation_turn_id, attempt.reply_user_turn_id,
                     attempt.expansion_request_id, attempt.expansion_turn_id, attempt.deferred_reason,
                     time.time(), time.time()))
            else:
                changed = self.store.conn.execute(
                    "UPDATE v2_invitation_attempts SET version=version+1,intent_version=?,state=?,"
                    "invitation_text=?,delivered_at=?,expires_at=?,invitation_turn_id=?,reply_user_turn_id=?,"
                    "expansion_request_id=?,expansion_turn_id=?,deferred_reason=?,updated_at=? "
                    "WHERE id=? AND epoch=? AND version=?",
                    (attempt.intent_version, attempt.state.value, attempt.invitation_text, attempt.delivered_at,
                     attempt.expires_at, attempt.invitation_turn_id, attempt.reply_user_turn_id,
                     attempt.expansion_request_id, attempt.expansion_turn_id, attempt.deferred_reason,
                     time.time(), attempt.id, scope_epoch(attempt.scope), expected_version)).rowcount
                if not changed:
                    raise ValueError("邀请版本已变化，请刷新核对")
        return self.get_attempt(attempt.scope, attempt.id)

    def pending_reply(self, scope: Scope) -> InvitationAttempt | None:
        row = self.store.conn.execute(
            "SELECT * FROM v2_invitation_attempts WHERE epoch=? AND state='awaiting_reply' "
            "AND expires_at>? ORDER BY updated_at DESC LIMIT 1", (scope_epoch(scope), time.time())).fetchone()
        return _attempt_from_row(row) if row else None

    def due_timeouts(self, scope: Scope, *, now_at: float) -> tuple[InvitationAttempt, ...]:
        rows = self.store.conn.execute(
            "SELECT * FROM v2_invitation_attempts WHERE epoch=? AND state='awaiting_reply' AND expires_at<=?",
            (scope_epoch(scope), now_at)).fetchall()
        return tuple(_attempt_from_row(row) for row in rows)

    def eligible_intents(self, scope: Scope, *, now_at: float) -> tuple[ContactIntent, ...]:
        rows = self.store.conn.execute(
            "SELECT * FROM v2_contact_intents i WHERE epoch=? AND state='active' "
            "AND NOT EXISTS (SELECT 1 FROM v2_invitation_attempts a WHERE a.intent_id=i.id "
            "AND a.epoch=i.epoch AND a.state IN ('prepared','awaiting_reply','accepted')) "
            "AND (? OR next_eligible_at IS NULL OR next_eligible_at<=?) ORDER BY updated_at DESC LIMIT 50",
            (scope_epoch(scope), int(autonomy_policy.TEST_CONTROLS_ONLY), now_at)).fetchall()
        return tuple(_intent_from_row(row) for row in rows)

    def pending_index(self, scope: Scope):
        from .models import InvitationIndex
        attempt = self.pending_reply(scope)
        if attempt is None:
            return None
        intent = self.get_intent(scope, attempt.intent_id)
        return InvitationIndex(attempt_id=attempt.id, version=attempt.version, topic=intent.topic,
                               state=attempt.state, invitation_turn_id=attempt.invitation_turn_id,
                               expires_at=attempt.expires_at)


# ---------------------------------------------------------------- host

class HostAdapter:
    """Delivery and verification that only the host may perform.

    The instance is bound to one Store; it never exposes receiver identity,
    quota mutation or receipt fabrication to model arguments.
    """

    def __init__(self, store):
        self.store = store

    def validate_intent(self, scope: Scope, intent: ContactIntent) -> None:
        from .. import thought_workspace as thoughts
        from ..context_memory import version
        if (scope.session_epoch != self.store.meta_get("session_epoch") or scope.channel_id != "gui"
                or intent.scope != scope or intent.state != "active" or not intent.content_refs):
            raise ValueError("联系意图不在当前会话或已结束")
        for ref in intent.content_refs:
            kind, _, tail = ref.partition(":")
            identity, sep, expected = tail.partition("@")
            if not sep or not identity.isdigit():
                raise ValueError("展开引用无效")
            if kind == "question":
                row = self.store.conn.execute("SELECT * FROM thought_questions WHERE id=? AND epoch=?",
                                              (int(identity), scope.session_epoch)).fetchone()
                if (row is None or str(row["version"]) != expected or not row["question"]
                        or not thoughts.valid_basis(self.store, _loads(row["basis"], {}))):
                    raise ValueError("邀请关联的问题或来源已变化，请重新准备")
            elif kind == "stance":
                memory = next((r for r in self.store.memory_rows(purpose="chat", scope="gui", kind="stance")
                               if r["id"] == int(identity)), None)
                if memory is None or version(memory)[:16] != expected:
                    raise ValueError("邀请关联的观点或来源已变化，请重新准备")
            else:
                raise ValueError("不支持的展开引用")

    def validate_background_context(self, context) -> None:
        if context.user_turn_id is not None or not str(context.run_id or "").startswith("explore_"):
            raise ValueError("结束回执只能在自主运行窗口内提交")
        row = self.store.conn.execute("SELECT epoch FROM exploration_runs WHERE id=?",
                                      (context.run_id,)).fetchone()
        if row is None or row[0] != context.scope.session_epoch:
            raise ValueError("自主运行不存在或已切换会话")

    def validate_foreground_reply(self, context, attempt: InvitationAttempt) -> None:
        if attempt.state != InvitationState.AWAITING_REPLY:
            raise ValueError("该邀请不在等待回应状态")
        if attempt.expires_at is not None and attempt.expires_at <= time.time():
            raise ValueError("邀请已超过等待期限")
        turn_id = context.user_turn_id
        if turn_id is None:
            raise ValueError("缺少当前真实用户消息，不能绑定邀请回应")
        row = self.store.conn.execute("SELECT id,role FROM turns WHERE id=?", (turn_id,)).fetchone()
        if row is None or row["role"] != "user":
            raise ValueError("回应必须绑定真实用户消息")
        request = self.store.conn.execute(
            "SELECT 1 FROM channel_requests WHERE request_id=? AND user_turn_id=? AND session_epoch=? AND channel_id=?",
            (context.request_id, turn_id, context.scope.session_epoch, context.scope.channel_id)).fetchone()
        if request is None or attempt.scope != context.scope:
            raise ValueError("邀请回应不属于本次真实请求")
        if attempt.invitation_turn_id is not None and turn_id <= attempt.invitation_turn_id:
            raise ValueError("回应消息必须晚于邀请投递")

    def can_start_work(self, context) -> bool:
        from .. import thought_workspace as thoughts
        from ..background_runtime import day
        config = thoughts.settings(self.store)
        if not config["allow_paid"]:
            return False
        usage = self.store.conn.execute("SELECT models FROM background_usage WHERE day=?", (day(),)).fetchone()
        if autonomy_policy.TEST_CONTROLS_ONLY:
            return config["wake_state"] == "awake"
        return (bool(config["run_models"] and config["run_steps"] and config["run_seconds"])
                and (usage[0] if usage else 0) < config["daily_models"])

    def commit_invitation_delivery(self, context, attempt: InvitationAttempt, *, wait_seconds: int) -> InvitationAttempt:
        """One transaction: message row, quota ledger entry, attempt receipt."""
        from .. import thought_workspace as thoughts
        config = thoughts.settings(self.store)
        repo = InvitationRepositoryAdapter(self.store)
        current = repo.get_attempt(attempt.scope, attempt.id)
        if current.state not in (InvitationState.PREPARED, InvitationState.AWAITING_REPLY):
            raise ValueError("邀请已不在可投递状态")
        if current.state == InvitationState.AWAITING_REPLY:
            return current  # Retry with the stable id returns the original receipt.
        now = time.time()
        with self.store.source_transaction():
            cur = self.store.conn.execute("INSERT INTO turns(ts,role,content) VALUES(?,'assistant',?)",
                                          (now, current.invitation_text))
            turn_id = cur.lastrowid
            intent = repo.get_intent(context.scope, current.intent_id)
            self.validate_intent(context.scope, intent)
            packets = []
            for ref in intent.content_refs:
                identity = int(ref.split(":", 1)[1].split("@", 1)[0])
                if ref.startswith("question:"):
                    q = self.store.conn.execute("SELECT basis FROM thought_questions WHERE id=?", (identity,)).fetchone()
                    packets.extend(_loads(q[0], {}).get("packets", []))
                elif ref.startswith("stance:"):
                    packets.extend(p["id"] for p in self.store.observation_sources(identity, scope="gui"))
            if packets:
                self.store.protect_source_turn(turn_id, list(dict.fromkeys(packets)), scope="gui")
            updated = replace(current, state=InvitationState.AWAITING_REPLY,
                              delivered_at=now, expires_at=now + wait_seconds,
                              invitation_turn_id=turn_id)
            repo.save_attempt(updated, expected_version=current.version)
            thoughts.event(self.store, "invitation_delivered_gui", current.id,
                           {"turn_id": turn_id, "receipt": "history_committed",
                            "expires_at": now + wait_seconds, "quota_charged": True})
        return repo.get_attempt(attempt.scope, attempt.id)

    def load_expansion(self, context, attempt: InvitationAttempt, intent: ContactIntent) -> ExpansionMaterial:
        if (attempt.state != InvitationState.ACCEPTED or attempt.scope != context.scope
                or attempt.reply_user_turn_id != context.user_turn_id
                or attempt.expansion_request_id != context.request_id):
            raise ValueError("展开材料只属于接受时的那次前台请求")
        row = self.store.conn.execute("SELECT id,role FROM turns WHERE id=?", (context.user_turn_id,)).fetchone()
        if row is None or row["role"] != "user":
            raise ValueError("展开材料需要绑定真实用户消息")
        self.validate_intent(context.scope, intent)
        return ExpansionMaterial(attempt_id=attempt.id, intent_id=intent.id, scope=intent.scope,
                                 bound_user_turn_id=context.user_turn_id, content_refs=intent.content_refs)

    def commit_expansion(self, context, attempt: InvitationAttempt, *, assistant_turn_id: int) -> InvitationAttempt:
        repo = InvitationRepositoryAdapter(self.store)
        current = repo.get_attempt(attempt.scope, attempt.id)
        if current.state == InvitationState.EXPANDED:
            return current  # Idempotent: the saved reply is already bound.
        if current.state != InvitationState.ACCEPTED or current.expansion_request_id != context.request_id:
            raise ValueError("只有本次接受的邀请才能绑定展开回复")
        row = self.store.conn.execute(
            "SELECT t.role FROM turns t JOIN channel_requests r ON r.assistant_turn_id=t.id "
            "WHERE t.id=? AND r.request_id=? AND r.user_turn_id=? AND r.session_epoch=? AND r.channel_id=?",
            (assistant_turn_id, context.request_id, context.user_turn_id,
             context.scope.session_epoch, context.scope.channel_id)).fetchone()
        if row is None or row["role"] != "assistant":
            raise ValueError("展开回执必须属于本次实际保存的回复")
        with self.store.source_transaction():
            updated = replace(current, state=InvitationState.EXPANDED,
                              expansion_turn_id=assistant_turn_id)
            repo.save_attempt(updated, expected_version=current.version)
            from .. import thought_workspace as thoughts
            thoughts.event(self.store, "invitation_expanded", current.id,
                           {"assistant_turn_id": assistant_turn_id, "invitation_counted": False})
        return repo.get_attempt(attempt.scope, attempt.id)

    def expansion_body(self, scope: Scope, material: ExpansionMaterial) -> dict:
        """Resolve references for the accepted request only; never before."""
        intent = InvitationRepositoryAdapter(self.store).get_intent(scope, material.intent_id)
        self.validate_intent(scope, intent)
        bodies = []
        for ref in material.content_refs:
            if ref.startswith("question:"):
                match = ref[len("question:"):].split("@")
                row = self.store.conn.execute("SELECT * FROM thought_questions WHERE id=? AND epoch=?",
                                              (int(match[0]), scope_epoch(scope))).fetchone()
                if row is not None:
                    bodies.append({"ref": ref, "kind": "question", "question": row["question"],
                                   "status": row["status"], "progress": row["progress"],
                                   "next_step": row["next_step"]})
            elif ref.startswith("stance:"):
                match = ref[len("stance:"):].split("@")
                memory = self.store.get_memory(int(match[0]))
                if memory is not None:
                    bodies.append({"ref": ref, "kind": "stance", "title": memory["title"],
                                   "content": memory["content"]})
        return {"attempt_id": material.attempt_id, "intent_id": material.intent_id,
                "bound_user_turn_id": material.bound_user_turn_id, "materials": bodies,
                "notice": "这是本次接受邀请后按需载入的展开材料，按其来源与不确定条件使用。"}


# ---------------------------------------------------------------- migration

class MigrationAdapter:
    def __init__(self, store):
        self.store = store

    def prepare(self, scope: Scope) -> MigrationPlan:
        epoch = scope_epoch(scope)
        pending = self.store.conn.execute(
            "SELECT id FROM thought_contacts WHERE epoch=? AND status IN('queued','pending_next_chat','needs_review') "
            "ORDER BY id", (epoch,)).fetchall()
        stances = self.store.conn.execute(
            "SELECT m.id FROM memories m WHERE m.kind='stance' AND m.provenance_status='valid' "
            "AND NOT EXISTS(SELECT 1 FROM v2_view_heads h WHERE h.memory_id=m.id)").fetchall()
        questions = self.store.conn.execute(
            "SELECT id FROM thought_questions WHERE epoch=? AND question<>''", (epoch,)).fetchall()
        notes = ("旧待发全文联系转为待重新准备邀请，不自动投递全文",
                 "同题旧观点仅建立可审计的当前指针，不做语义合并",
                 "原始记忆ID与来源依赖保持不变，不做批量重写")
        snapshot = {
            "epoch": epoch,
            "contacts": [list(r) for r in self.store.conn.execute(
                "SELECT id,version,status,basis,message FROM thought_contacts WHERE epoch=? "
                "AND status IN('queued','pending_next_chat','needs_review') ORDER BY id", (epoch,))],
            "questions": [list(r) for r in self.store.conn.execute(
                "SELECT id,version,outcome_id FROM thought_questions WHERE epoch=? AND question<>'' ORDER BY id", (epoch,))],
            "memories": [r[0] for r in stances],
        }
        identity = "mig_" + hashlib.sha256(dump(snapshot).encode()).hexdigest()[:24]
        return MigrationPlan(identity=identity, scope=scope,
                             from_contract="autonomy-v1", to_contract="autonomy-v2",
                             data_format=1, question_ids=tuple(r[0] for r in questions),
                             memory_ids=tuple(r[0] for r in stances),
                             legacy_contact_ids=tuple(r[0] for r in pending),
                             compatibility_notes=notes, requires_review=True)

    def apply(self, plan: MigrationPlan) -> None:
        epoch = scope_epoch(plan.scope)
        if (epoch != self.store.meta_get("session_epoch") or plan.scope.channel_id != "gui"
                or plan.from_contract != "autonomy-v1" or plan.to_contract != "autonomy-v2"
                or plan.data_format != 1):
            raise ValueError("迁移计划不属于当前会话或合同")
        current = self.prepare(plan.scope)
        if (current.identity != plan.identity or current.question_ids != plan.question_ids
                or current.memory_ids != plan.memory_ids or current.legacy_contact_ids != plan.legacy_contact_ids):
            raise ValueError("迁移对象已变化，请重新准备并核对计划")
        repo_i = InvitationRepositoryAdapter(self.store)
        with self.store.source_transaction():
            for contact_id in plan.legacy_contact_ids:
                row = self.store.conn.execute("SELECT * FROM thought_contacts WHERE id=? AND epoch=?",
                                              (contact_id, epoch)).fetchone()
                if row is None or row["status"] not in ("queued", "pending_next_chat", "needs_review"):
                    continue
                intent = ContactIntent(
                    id="ci_" + uuid.uuid4().hex[:16], version=1,
                    scope=Scope(session_epoch=epoch, channel_id="gui"), motive=row["motive"],
                    topic=(row["message"] or "").strip()[:80] or "（迁移的旧联系意图）",
                    reason="由旧全文联系条目迁移；需重新准备简短邀请",
                    content_refs=([f"question:{row['question_id']}@{row['question_version']}"]
                                  if row["question_id"] else []),
                    state="active", question_id=row["question_id"], question_version=row["question_version"],
                    next_eligible_at=time.time())
                repo_i.save_intent(intent, expected_version=None)
                self.store.conn.execute(
                    "UPDATE v2_contact_intents SET legacy_contact_id=? WHERE id=?", (contact_id, intent.id))
                self.store.conn.execute(
                    "UPDATE thought_contacts SET status='migrated',version=version+1,updated_at=? "
                    "WHERE id=?", (time.time(), contact_id))
            for question_id in plan.question_ids:
                row = self.store.conn.execute("SELECT * FROM thought_questions WHERE id=?",
                                              (question_id,)).fetchone()
                if row is None or row["outcome_id"] is None:
                    continue
                head = self.store.conn.execute("SELECT 1 FROM v2_view_heads WHERE question_id=?",
                                               (question_id,)).fetchone()
                if head:
                    continue
                memory = self.store.get_memory(row["outcome_id"])
                if memory is None or memory["kind"] != "stance":
                    continue
                view_id = f"view_{question_id}"
                self.store.conn.execute(
                    "INSERT INTO v2_view_revisions(view_id,revision,question_id,memory_id,change_reason,"
                    "previous_revision,created_at) VALUES(?,?,?,?,'迁移时建立当前指针',NULL,?)",
                    (view_id, 1, question_id, row["outcome_id"], time.time()))
                self.store.conn.execute(
                    "INSERT INTO v2_view_heads(question_id,view_id,revision,memory_id,updated_at) "
                    "VALUES(?,?,?,?,?) ON CONFLICT(question_id) DO UPDATE SET view_id=excluded.view_id,"
                    "revision=excluded.revision,memory_id=excluded.memory_id,updated_at=excluded.updated_at",
                    (question_id, view_id, 1, row["outcome_id"], time.time()))
            from .. import thought_workspace as thoughts
            thoughts.event(self.store, "autonomy_migration_applied", plan.identity,
                           {"contacts": len(plan.legacy_contact_ids), "questions": len(plan.question_ids)})
