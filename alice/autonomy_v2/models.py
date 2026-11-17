"""Standard-library-only data contracts for autonomous activity design v2.

Declarations only. These types do not validate input or authorize operations.
Times ending in ``_at`` are UTC Unix seconds; durations are seconds.
References are identities, not copies of source text or proof of its truth.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class WakeState(str, Enum):
    AWAKE = "awake"
    SLEEPING = "sleeping"


class ActivityMode(str, Enum):
    INTEREST = "interest"
    PROBLEM = "problem"
    ITERATION = "iteration"
    IDLE = "idle"


class QuestionStatus(str, Enum):
    OPEN = "open"
    WAITING_USER = "waiting_user"
    WAITING_SOURCE = "waiting_source"
    WAITING_PERMISSION = "waiting_permission"
    WAITING_BUDGET = "waiting_budget"
    PAUSED = "paused"
    CLOSED = "closed"
    RESOLVED = "resolved"


class ProgressKind(str, Enum):
    EVIDENCE = "evidence"
    ACTION = "action"
    INFERENCE = "inference"
    REWRITE = "rewrite"
    NONE = "none"


class FinishDecision(str, Enum):
    CONTINUE = "continue"
    WAIT = "wait"
    REST = "rest"
    DONE = "done"


class WaitKind(str, Enum):
    USER_FEEDBACK = "user_feedback"
    SOURCE = "source"
    PERMISSION = "permission"
    BUDGET = "budget"
    MATERIAL = "material"


class EvidenceKind(str, Enum):
    USER_MESSAGE = "user_message"
    USER_REPORT = "user_report"
    HISTORICAL_AI_SPEECH = "historical_ai_speech"
    SOURCE_CONTENT = "source_content"
    ACTION_RECEIPT = "action_receipt"
    SELF_VIEW = "self_view"


class ViewState(str, Enum):
    CURRENT = "current"
    CANDIDATE = "candidate"
    HISTORICAL = "historical"


class InvitationState(str, Enum):
    PREPARED = "prepared"
    AWAITING_REPLY = "awaiting_reply"
    ACCEPTED = "accepted"
    EXPANDED = "expanded"
    DEFERRED = "deferred"
    DECLINED = "declined"
    WITHDRAWN = "withdrawn"
    UNKNOWN = "unknown"


class ReplyDecision(str, Enum):
    ACCEPT = "accept"
    DEFER = "defer"
    DECLINE = "decline"


@dataclass(frozen=True, slots=True, kw_only=True)
class Scope:
    session_epoch: str
    channel_id: str


@dataclass(frozen=True, slots=True, kw_only=True)
class ClockSnapshot:
    wall_at: float
    monotonic_at: float
    clock_epoch: str  # Monotonic values cannot be reused across process epochs.


@dataclass(frozen=True, slots=True, kw_only=True)
class EvidenceRef:
    kind: EvidenceKind
    identity: str
    version: str
    content_fingerprint: str
    occurred_at: float | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class MaterialVersion:
    """External basis and self-authored work must have separate versions."""
    external_material_version: str
    working_revision: str
    evidence_refs: tuple[EvidenceRef, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class WaitCondition:
    kind: WaitKind
    reason: str
    related_refs: tuple[EvidenceRef, ...] = ()
    context_hint: str = ""  # Hint for the existing model, not a host regex trigger.
    after_user_turn_id: int | None = None
    not_before_at: float | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class Question:
    id: int
    version: int
    scope: Scope
    family_id: str
    activity: ActivityMode
    status: QuestionStatus
    question: str
    material: MaterialVersion
    importance: int
    urgency: int
    due_at: float | None = None
    parent_id: int | None = None
    next_step: str = ""
    wait_condition: WaitCondition | None = None
    current_view_id: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class ModePolicy:
    mode: ActivityMode
    seconds: int
    tool_groups: tuple[str, ...]  # IDLE requires no tool groups.


@dataclass(frozen=True, slots=True, kw_only=True)
class Tuning:
    """Suggested initial values, not user-approved constants or live defaults.

    No base preference or writable mode weight is defined.
    Existing global budgets/permissions remain owned by the current settings.
    """
    modes: tuple[ModePolicy, ...]
    same_material_windows: int = 2
    invitation_wait_seconds: int = 600
    same_topic_retry_seconds: int = 86400


@dataclass(frozen=True, slots=True, kw_only=True)
class BudgetSnapshot:
    model_calls_remaining: int
    tool_steps_remaining: int
    window_seconds_remaining: float
    allowed_tool_groups: tuple[str, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class IdlePeriod:
    scope: Scope
    started_at: float
    until_at: float
    reason: str
    related_run_id: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class ExecutionContext:
    """Host-bound context, never supplied directly as model tool arguments."""
    scope: Scope
    request_id: str
    run_id: str | None
    user_turn_id: int | None
    clock: ClockSnapshot
    budget: BudgetSnapshot
    manual_requested: bool
    family_id: str | None = None  # Host-bound even for a free-interest window.


@dataclass(frozen=True, slots=True, kw_only=True)
class ProgressReport:
    decision: FinishDecision
    progress_kind: ProgressKind
    summary: str
    evidence_refs: tuple[EvidenceRef, ...] = ()
    action_refs: tuple[str, ...] = ()
    next_step: str = ""
    wait_condition: WaitCondition | None = None
    requested_idle_seconds: int | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class ProgressReceipt:
    run_id: str
    actual_execution: bool
    substantive_progress: bool
    resulting_status: QuestionStatus | None
    external_material_version: str
    working_revision: str
    remaining_family_windows: int
    reason: str
    question_id: int | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class ModeScore:
    mode: ActivityMode
    eligible: bool
    weight: float
    reason: str
    question_id: int | None = None
    content_score: float = 0
    rotation_score: float = 0


@dataclass(frozen=True, slots=True, kw_only=True)
class ScheduleDecision:
    """mode=None denotes condition waiting, distinct from voluntary IDLE."""
    scope: Scope
    mode: ActivityMode | None
    reason: str
    scores: tuple[ModeScore, ...]
    question_id: int | None = None
    idle_until_at: float | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class ViewRevision:
    view_id: str
    revision: int
    scope: Scope
    question_id: int
    memory_id: int | None
    state: ViewState
    material: MaterialVersion
    conclusion: str
    uncertainty: str
    change_reason: str
    next_response: str
    previous_revision: int | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class OutcomeDraft:
    question_id: int
    expected_question_version: int
    kind: str  # Existing stance / knowledge / none contract.
    conclusion: str
    reason: str
    next_status: QuestionStatus
    remaining: str = ""
    wait_condition: WaitCondition | None = None
    uncertainty: str = ""
    next_response: str = ""
    view_id: str | None = None
    expected_view_revision: int | None = None
    create_independent_view: bool = False


@dataclass(frozen=True, slots=True, kw_only=True)
class ContactIntent:
    id: str
    version: int
    scope: Scope
    topic: str
    reason: str
    content_refs: tuple[str, ...]  # Full payload stays outside invitation indices.
    state: str  # active / deferred / closed
    motive: str = "share"  # Existing permission toggles: progress / request / share.
    question_id: int | None = None
    question_version: int | None = None
    view_id: str | None = None
    view_revision: int | None = None
    next_eligible_at: float | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class CommunicationPlan:
    decision: str  # invite / later / none; unknown is host-only.
    reason: str
    intent: ContactIntent | None = None
    invitation_text: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class InvitationAttempt:
    id: str
    version: int
    intent_id: str
    intent_version: int
    scope: Scope
    state: InvitationState
    invitation_text: str
    prepared_at: float
    delivered_at: float | None = None
    expires_at: float | None = None
    invitation_turn_id: int | None = None
    reply_user_turn_id: int | None = None
    expansion_request_id: str | None = None
    expansion_turn_id: int | None = None
    deferred_reason: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class ContactReply:
    """Business arguments; the real user message comes from ExecutionContext."""
    attempt_id: str
    expected_attempt_version: int
    decision: ReplyDecision
    reason: str
    requested_retry_at: float | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class InvitationIndex:
    attempt_id: str
    version: int
    topic: str
    state: InvitationState
    invitation_turn_id: int | None
    expires_at: float | None
    # Intentionally no content_refs, conclusion, or expansion body.


@dataclass(frozen=True, slots=True, kw_only=True)
class ExpansionMaterial:
    attempt_id: str
    intent_id: str
    scope: Scope
    bound_user_turn_id: int
    content_refs: tuple[str, ...]
    # Returned only for an accepted invitation in its bound foreground request.


@dataclass(frozen=True, slots=True, kw_only=True)
class ForegroundProjection:
    scope: Scope
    current_views: tuple[ViewRevision, ...]
    relevant_waits: tuple[Question, ...]
    pending_invitation: InvitationIndex | None
    expansion: ExpansionMaterial | None


@dataclass(frozen=True, slots=True, kw_only=True)
class ActivitySnapshot:
    scope: Scope
    settings_revision: int
    requested_state: WakeState
    effective_state: WakeState
    execution_status: str
    scores: tuple[ModeScore, ...]
    idle_period: IdlePeriod | None
    pending_invitation: InvitationIndex | None


@dataclass(frozen=True, slots=True, kw_only=True)
class MigrationPlan:
    identity: str
    scope: Scope
    from_contract: str
    to_contract: str
    data_format: int
    question_ids: tuple[int, ...]
    memory_ids: tuple[int, ...]
    legacy_contact_ids: tuple[int, ...]
    compatibility_notes: tuple[str, ...]
    requires_review: bool = True
