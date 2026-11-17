"""Dependency interfaces. Concrete SQLite/Store adapters are in adapters.py.

    Protocol bodies declare signatures only; they are not no-op adapters.
    All mutations require host locking, version checks and source transactions.
"""
from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Protocol

from .models import (
    ActivitySnapshot, ClockSnapshot, ContactIntent, EvidenceRef, ExecutionContext,
    ExpansionMaterial, IdlePeriod, InvitationAttempt, MaterialVersion,
    MigrationPlan, ProgressReceipt, ProgressReport, Question, Scope, Tuning, ViewRevision,
)


class Clock(Protocol):
    def snapshot(self) -> ClockSnapshot: ...


class EvidencePort(Protocol):
    def validate_refs(self, scope: Scope, refs: tuple[EvidenceRef, ...]) -> None:
        """Verify current source identity/scope/eligibility, not semantic truth."""
        ...

    def material_version(self, question: Question) -> MaterialVersion:
        """Normalize external content identities separately from self revisions."""
        ...

    def validate_actions(self, context: ExecutionContext, action_refs: tuple[str, ...]) -> None:
        """Only actual, attributable receipts qualify; never model assertions."""
        ...

    def relevant_change(self, question: Question, new_refs: tuple[EvidenceRef, ...]) -> bool:
        """Mechanical identity/version check after semantic relevance selection."""
        ...


class ActivityRepository(Protocol):
    def transaction(self) -> AbstractContextManager[None]: ...
    def get_question(self, scope: Scope, question_id: int) -> Question: ...
    def list_candidates(self, scope: Scope) -> tuple[Question, ...]: ...
    def list_family(self, scope: Scope, family_id: str) -> tuple[Question, ...]: ...
    def save_question(self, question: Question, *, expected_version: int) -> Question: ...
    def get_idle(self, scope: Scope) -> IdlePeriod | None: ...
    def save_idle(self, period: IdlePeriod | None, *, scope: Scope) -> None: ...

    def remaining_family_windows(self, question: Question, *, maximum: int) -> int: ...

    def consume_family_window(self, question: Question, *, run_id: str, maximum: int) -> int:
        """Atomic, idempotent by real execution window; not by a new child ID."""
        ...

    def save_progress(self, context: ExecutionContext, report: ProgressReport,
                      receipt: ProgressReceipt) -> ProgressReceipt:
        """Idempotent finish receipt; execution and substantive progress differ."""
        ...

    def save_tuning(self, scope: Scope, tuning: Tuning, *, expected_revision: int) -> ActivitySnapshot:
        """Preserve existing budgets/grants; reject stale settings revisions."""
        ...

    def snapshot(self, scope: Scope) -> ActivitySnapshot:
        """Read-only: no selection draw, time reset, model call or resume."""
        ...


class ViewRepository(Protocol):
    def get_current(self, scope: Scope, view_id: str) -> ViewRevision | None: ...
    def get_for_question(self, scope: Scope, question_id: int) -> ViewRevision | None: ...
    def history(self, scope: Scope, view_id: str) -> tuple[ViewRevision, ...]: ...

    def commit_revision(self, revision: ViewRevision, *, expected_revision: int | None) -> ViewRevision:
        """Atomically save history/current pointer; retain IDs and dependencies."""
        ...

    def current_for_context(self, scope: Scope, query: str, *, limit: int) -> tuple[ViewRevision, ...]: ...


class InvitationRepository(Protocol):
    def get_intent(self, scope: Scope, intent_id: str) -> ContactIntent: ...
    def save_intent(self, intent: ContactIntent, *, expected_version: int | None) -> ContactIntent: ...
    def get_attempt(self, scope: Scope, attempt_id: str) -> InvitationAttempt: ...
    def save_attempt(self, attempt: InvitationAttempt, *, expected_version: int | None) -> InvitationAttempt: ...

    def pending_reply(self, scope: Scope) -> InvitationAttempt | None:
        """At most one real, delivered invitation awaiting reply per session."""
        ...

    def due_timeouts(self, scope: Scope, *, now_at: float) -> tuple[InvitationAttempt, ...]: ...
    def eligible_intents(self, scope: Scope, *, now_at: float) -> tuple[ContactIntent, ...]: ...


class HostPort(Protocol):
    def validate_intent(self, scope: Scope, intent: ContactIntent) -> None: ...
    def validate_background_context(self, context: ExecutionContext) -> None: ...
    def validate_foreground_reply(self, context: ExecutionContext, attempt: InvitationAttempt) -> None: ...
    def can_start_work(self, context: ExecutionContext) -> bool: ...

    def commit_invitation_delivery(self, context: ExecutionContext, attempt: InvitationAttempt,
                                   *, wait_seconds: int) -> InvitationAttempt:
        """One transaction: source/permission checks, message, contact quota,
        attempt ID/version and expiry. Retry must return the original receipt.
        No hidden body is posted. Repository and host share a unit of work.
        """
        ...

    def load_expansion(self, context: ExecutionContext, attempt: InvitationAttempt,
                       intent: ContactIntent) -> ExpansionMaterial:
        """Only the accepted, bound foreground request; recheck live sources."""
        ...

    def commit_expansion(self, context: ExecutionContext, attempt: InvitationAttempt,
                         *, assistant_turn_id: int) -> InvitationAttempt:
        """Bind actual saved reply, not a predicted result; never count a new invitation."""
        ...


class MigrationPort(Protocol):
    def prepare(self, scope: Scope) -> MigrationPlan:
        """Read-only, no model use: inspect true outcome links and compatibility."""
        ...

    def apply(self, plan: MigrationPlan) -> None:
        """Explicit migration transaction; no destructive consolidation or permission expansion."""
        ...
