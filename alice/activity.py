"""Lifecycle and content-driven idle scheduling. Callers hold the Store lock.

No model or network calls: Alice supplies item assessments through thought tools.
Reading a schedule never draws a winner or changes the rotation ledger.

Design v2: idle is a voluntary no-call mode; waiting work stays ineligible no
matter how long it waits; self-authored text never refreshes the external
material version, and same-evidence inference is bounded per question family.
"""
from __future__ import annotations

import copy
import json
import math
import random
import time
from datetime import datetime

from . import autonomy_policy

WORK_MODES = ("interest", "problem", "iteration")
CONTACT_MODE = "contact"
ACTIVE_MODES = WORK_MODES + (CONTACT_MODE,)
MODES = ACTIVE_MODES + ("idle",)
DEFAULT_IDLE_SECONDS = 300
TOOL_GROUPS = ("memory", "history", "persona", "sources", "search")
DEFAULT_ACTIVITIES = {
    mode: {"seconds": seconds, "tools": list(TOOL_GROUPS if mode == "interest" else TOOL_GROUPS[:-1])}
    for mode, seconds in (("interest", 180), ("problem", 180), ("iteration", 120))
}
DEFAULT_ACTIVITIES[CONTACT_MODE] = {"seconds": 120, "tools": ["memory", "history"]}
SCHEMA = """
CREATE TABLE IF NOT EXISTS thought_agenda (
 question_id INTEGER PRIMARY KEY, activity TEXT NOT NULL,
 importance INTEGER NOT NULL DEFAULT 1, urgency INTEGER NOT NULL DEFAULT 0,
 due_at REAL NOT NULL DEFAULT 0, schedule_reason TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS autonomous_activity_runs (
 run_id TEXT PRIMARY KEY, activity TEXT NOT NULL, selection TEXT NOT NULL DEFAULT '{}',
 input_stamp TEXT NOT NULL DEFAULT '', started_at REAL NOT NULL DEFAULT 0,
 deadline REAL NOT NULL DEFAULT 0, charged INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS autonomous_activity_executions (
 id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, activity TEXT NOT NULL,
 input_stamp TEXT NOT NULL, started_at REAL NOT NULL, progress INTEGER NOT NULL DEFAULT 0
);
"""


def validate_activities(value, previous=None):
    if not isinstance(value, dict) or not set(WORK_MODES) <= value.keys() <= set(ACTIVE_MODES):
        raise ValueError("活动设置缺少既有模式或包含未知模式")
    result = copy.deepcopy({**DEFAULT_ACTIVITIES, **(previous or {})})
    for mode, item in value.items():
        if not isinstance(item, dict) or set(item) != {"seconds", "tools"}:
            raise ValueError("每种活动只能设置时长与工具")
        seconds, groups = item["seconds"], item["tools"]
        if type(seconds) is not int or not 0 <= seconds <= 86400:
            raise ValueError("活动时长须为0–86400秒")
        available = DEFAULT_ACTIVITIES[CONTACT_MODE]["tools"] if mode == CONTACT_MODE else TOOL_GROUPS
        if (not isinstance(groups, list) or any(not isinstance(g, str) or g not in available for g in groups)
                or len(set(groups)) != len(groups)):
            raise ValueError("活动工具组无效")
        result[mode] = {"seconds": seconds, "tools": list(groups)}
    return result


def in_window(start, end):
    from .discovery_memory import DAY_ZONE
    clock = datetime.now(DAY_ZONE).strftime("%H:%M")
    return start == end or (start <= clock < end if start < end else clock >= start or clock < end)


def lifecycle(config, *, testing_policy=True):
    requested = config["wake_state"]
    if testing_policy and autonomy_policy.TEST_CONTROLS_ONLY and not config["allow_paid"]:
        return {"requested": requested, "effective": "sleeping", "reason": "model_permission_required"}
    inside = (testing_policy and autonomy_policy.TEST_CONTROLS_ONLY) or in_window(config["window_start"], config["window_end"])
    return {"requested": requested, "effective": "awake" if requested == "awake" and inside else "sleeping",
            "reason": "requested_sleep" if requested != "awake" else "awake" if inside else "outside_wake_window"}


def awake(store, *, testing_policy=True):
    from .thought_workspace import settings
    return lifecycle(settings(store), testing_policy=testing_policy)["effective"] == "awake"


def presentation_state(store):
    if not awake(store):
        return "sleeping"
    if active_idle(store):
        return "resting"
    running = store.conn.execute("SELECT 1 FROM exploration_runs WHERE epoch=? AND status='running' LIMIT 1",
                                 (store.meta_get("session_epoch"),)).fetchone()
    return "running" if running else "awake"


def origin_mode(origin):
    return "iteration" if origin == "review" else "interest" if origin in {"interest", "exploration"} else "problem"


def assessment(store, row):
    saved = store.conn.execute("SELECT * FROM thought_agenda WHERE question_id=?", (row["id"],)).fetchone()
    value = dict(saved) if saved else {"activity": origin_mode(row["origin"]), "importance": 1, "urgency": 0,
                                       "due_at": 0, "schedule_reason": ""}
    from .autonomy_v2.planning import ActivityPlanning
    value['value_waiting'] = ActivityPlanning(store).parked(row)
    return value


def assess(store, row, args):
    keys = {"activity", "importance", "urgency", "due_at", "schedule_reason"}
    if not keys.intersection(args):
        return
    value = {**assessment(store, row), **{k: v for k, v in args.items() if k in keys}}
    if value["activity"] not in WORK_MODES:
        raise ValueError("活动模式无效")
    for key in ("importance", "urgency"):
        if type(value[key]) is not int or not 0 <= value[key] <= 3:
            raise ValueError("价值与紧迫度须为0–3")
    due = value["due_at"]
    if type(due) not in (int, float) or not math.isfinite(due) or not 0 <= due <= 253402300799:
        raise ValueError("截止时间须为有效Unix秒数，0表示无期限")
    if not isinstance(value["schedule_reason"], str) or len(value["schedule_reason"]) > 500:
        raise ValueError("调度理由超长或无效")
    store.conn.execute("INSERT INTO thought_agenda(question_id,activity,importance,urgency,due_at,schedule_reason) "
        "VALUES(?,?,?,?,?,?) ON CONFLICT(question_id) DO UPDATE SET activity=excluded.activity,"
        "importance=excluded.importance,urgency=excluded.urgency,due_at=excluded.due_at,schedule_reason=excluded.schedule_reason",
        (row["id"], *(value[k] for k in ("activity", "importance", "urgency", "due_at", "schedule_reason"))))


def candidates(store):
    """Only open questions are eligible; relevant waits reopen explicitly.

    A waiting_user question never requeues on any new user turn; the foreground
    model judges relevance and reopens it explicitly. Permission and budget
    waits likewise reopen through explicit updates, not timers.
    """
    from . import thought_workspace as thoughts
    from .autonomy_v2.planning import ActivityPlanning
    planning = ActivityPlanning(store)
    return [r for r in thoughts.question_rows(store) if r["question"] and r["status"] == "open"
            and thoughts.valid_basis(store, json.loads(r["basis"])) and not planning.parked(r)]


def external_version(store, row):
    """External basis identity: user turns, granted sources, user-derived memories.

    Self-authored text (question versions, stances, agenda) never changes this,
    so re-reading one's own notes cannot unlock another same-evidence window.
    """
    import hashlib
    from .autonomy_v2.adapters import material_version_of
    return material_version_of(store, row).external_material_version


def family_of(store, question_id):
    from .autonomy_v2.adapters import family_of as _family
    return _family(store, question_id)


def remaining_windows(store, row, maximum):
    from .autonomy_v2.adapters import question_from_row
    return None if autonomy_policy.TEST_CONTROLS_ONLY else _remaining_windows(store, question_from_row(store, row), maximum)


def _remaining_windows(store, question, maximum):
    row = store.conn.execute("SELECT windows,bonus FROM v2_family_windows WHERE family_id=? AND external_version=?",
        (question.family_id, question.material.external_material_version)).fetchone()
    return maximum if row is None else max(0, maximum + row["bonus"] - row["windows"])


def consume_window(store, row, run_id, maximum):
    """Charge one real execution window to the family; idempotent by run_id."""
    from .autonomy_v2.adapters import question_from_row
    question = question_from_row(store, row)
    with store.source_transaction():
        existing = store.conn.execute("SELECT windows,bonus,last_run_id FROM v2_family_windows "
            "WHERE family_id=? AND external_version=?",
            (question.family_id, question.material.external_material_version)).fetchone()
        if existing is not None and existing["last_run_id"] == run_id:
            return max(0, maximum + existing["bonus"] - existing["windows"])
        windows = (existing["windows"] if existing else 0) + 1
        bonus = existing["bonus"] if existing else 0
        store.conn.execute("INSERT INTO v2_family_windows(family_id,external_version,windows,bonus,last_run_id,updated_at) "
            "VALUES(?,?,?,?,?,?) ON CONFLICT(family_id,external_version) DO UPDATE SET windows=excluded.windows,"
            "bonus=excluded.bonus,last_run_id=excluded.last_run_id,updated_at=excluded.updated_at",
            (question.family_id, question.material.external_material_version, windows, bonus, run_id, time.time()))
        return max(0, maximum + bonus - windows)


def input_stamp(store):
    """External-only aggregate stamp kept for audit and legacy rows."""
    import hashlib
    from .context_memory import version
    material = [[r["id"], version(r)] for r in store.memory_rows(purpose="thought", scope="gui")
                if r["kind"] != "stance"][:40]
    latest = store.conn.execute("SELECT COALESCE(MAX(id),0) FROM turns WHERE role='user'").fetchone()[0]
    has_interests = store.conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='discovery_interests'").fetchone()
    interests = [list(r) for r in store.conn.execute("SELECT id,version FROM discovery_interests WHERE enabled=1 AND scope_json LIKE '%gui%' ORDER BY id")] if has_interests else []
    sources = [list(r) for r in store.conn.execute("SELECT id,version FROM source_packets "
        "WHERE revoked=0 AND expires_at>? AND scope_json LIKE '%gui%' ORDER BY created_at DESC LIMIT 40", (time.time(),))]
    return hashlib.sha256(json.dumps([latest, material, interests, sources]).encode()).hexdigest()


def active_idle(store):
    row = store.conn.execute("SELECT * FROM v2_activity_idle WHERE scope='gui'").fetchone()
    if row is None or row["until_at"] <= time.time():
        return None
    return dict(row)


def fixed_idle_seconds(config):
    seconds = config.get("idle_rest_seconds", DEFAULT_IDLE_SECONDS)
    return seconds if type(seconds) is int and 0 <= seconds <= 86400 else DEFAULT_IDLE_SECONDS


def enter_idle(store, reason, *, related_run_id=None, scope="gui", selection=None):
    """Persist the rest timer; zero model, tool or memory steps happen here."""
    from .thought_workspace import settings
    seconds = fixed_idle_seconds(settings(store))
    if seconds <= 0:
        return None
    now = time.time()
    reason = reason[:200]
    with store.source_transaction():
        store.conn.execute("INSERT INTO v2_activity_idle(scope,started_at,until_at,reason,related_run_id) "
            "VALUES(?,?,?,?,?) ON CONFLICT(scope) DO UPDATE SET started_at=excluded.started_at,"
            "until_at=excluded.until_at,reason=excluded.reason,related_run_id=excluded.related_run_id",
            (scope, now, now + seconds, reason, related_run_id))
        record_idle_choice(store, now, seconds, reason, related_run_id=related_run_id, selection=selection)
    return {"started_at": now, "until_at": now + seconds, "reason": reason}


def normalize_legacy_idle(store):
    from .thought_workspace import event, settings
    key = "autonomy_fixed_idle_v1"
    with store.source_transaction():
        if store.meta_get(key):
            return
        seconds = fixed_idle_seconds(settings(store))
        changed = store.conn.execute(
            "UPDATE v2_activity_idle SET until_at=started_at+? WHERE scope='gui' "
            "AND reason='activity_finish_rest' AND until_at>? AND until_at>started_at+?",
            (seconds, time.time(), seconds)).rowcount
        if changed:
            event(store, "legacy_idle_shortened", "gui", {"fixed_seconds": seconds})
        store.meta_set(key, "1")


def clear_idle(store, *, reason="expired"):
    from .thought_workspace import event
    with store.source_transaction():
        row = store.conn.execute("SELECT until_at FROM v2_activity_idle WHERE scope='gui'").fetchone()
        store.conn.execute("DELETE FROM v2_activity_idle WHERE scope='gui'")
        if row:
            event(store, "idle_ended", "gui", {"reason": reason, "was_until": row[0]})


def record_idle_choice(store, started_at, seconds, reason, *, related_run_id=None, selection=None):
    """Independent selection receipt for voluntary rest (no model call)."""
    import uuid
    from .thought_workspace import dump
    identity = "idle_" + uuid.uuid4().hex[:16]
    now = time.time()
    selection = {**(selection or {}), "reason": reason, "idle_seconds": seconds}
    if related_run_id is not None:
        selection["related_run_id"] = related_run_id
    stamp = selection.get("input_stamp") or input_stamp(store)
    store.conn.execute("INSERT INTO exploration_runs(id,epoch,status,reason,question_id,basis,manual,created_at,updated_at) "
        "VALUES(?,?,'completed','idle_rest',NULL,?,0,?,?)",
        (identity, store.meta_get("session_epoch") or "",
         dump({"epoch": store.meta_get("session_epoch") or ""}), now, now))
    store.conn.execute("INSERT INTO autonomous_activity_runs(run_id,activity,selection,input_stamp,started_at,deadline,charged) "
        "VALUES(?,?,?,?,?,?,1)", (identity, "idle", dump(selection), stamp, started_at, started_at + seconds))
    store.conn.execute("INSERT INTO autonomous_activity_executions(run_id,activity,input_stamp,started_at,progress) "
        "VALUES(?,?,?,?,0)", (identity, "idle", stamp, started_at))


def weights(store, config):
    """Readiness first, then bounded weights. No semantic text matching."""
    from .autonomy_v2.adapters import question_from_row
    now = time.time()
    epoch = store.meta_get("session_epoch")
    maximum = max(1, min(int(config.get("same_material_windows") or 2), 20))
    history = store.conn.execute("SELECT a.* FROM autonomous_activity_executions a JOIN exploration_runs r ON r.id=a.run_id "
        "WHERE r.epoch=? ORDER BY a.id DESC LIMIT 60", (epoch,)).fetchall()
    debt = {mode: 0 for mode in MODES}
    for record in reversed(history):
        for mode in MODES:
            debt[mode] = 0 if record["activity"] == mode else min(6, debt[mode] + 1)
    previous = history[0]["activity"] if history else None
    result = {mode: {"weight": 0, "content_score": 1, "rotation": debt[mode], "question_id": None,
                     "priority": False, "reason": "no_content"} for mode in MODES}
    pools = {mode: [] for mode in WORK_MODES}
    for row in candidates(store):
        a = assessment(store, row)
        due = a["due_at"]
        near = 6 if due and due - now <= 3600 else 3 if due and due - now <= 86400 else 0
        explicit = row["origin"] == "user_request"
        score = 1 + 2*a["importance"] + 2*a["urgency"] + near + 4*explicit
        pools[a["activity"]].append(((bool(explicit or near == 6), score, -row["last_considered"]), score, row))
    stamp = input_stamp(store)
    blocked_modes = 0
    for mode, pool in pools.items():
        item = result[mode]
        pool.sort(key=lambda entry: entry[0], reverse=True)
        for rank, score, row in pool[:5]:
            question = question_from_row(store, row)
            if not autonomy_policy.TEST_CONTROLS_ONLY and _remaining_windows(store, question, maximum) <= 0:
                item["reason"] = "same_material_window"
                continue
            item.update(content_score=score, question_id=row["id"], priority=rank[0],
                        reason="actionable_question", _rank=rank)
            break
        if not autonomy_policy.TEST_CONTROLS_ONLY and not config["activities"][mode]["seconds"]:
            item["reason"] = "mode_disabled"
            continue
        if item["reason"] == "same_material_window":
            blocked_modes += 1
            continue
        if not item["question_id"]:
            from .autonomy_v2.planning import ActivityPlanning
            if ActivityPlanning(store).free_waiting(epoch, mode, stamp):
                item['reason'] = 'value_waiting'
                blocked_modes += 1
                continue
            # Free exploration also has a finite same-material budget. A model
            # must not escape a waiting question by repeatedly choosing no qid.
            free_count = store.conn.execute(
                "SELECT COUNT(*) FROM autonomous_activity_executions a JOIN exploration_runs r ON r.id=a.run_id "
                "WHERE r.epoch=? AND a.activity=? AND a.input_stamp=? AND r.question_id IS NULL",
                (epoch, mode, stamp)).fetchone()[0]
            if not autonomy_policy.TEST_CONTROLS_ONLY and free_count >= maximum:
                item["reason"] = "same_material_window"
                blocked_modes += 1
                continue
            if mode == "problem":
                continue
            if mode == "iteration" and not store.conn.execute("SELECT 1 FROM turns LIMIT 1").fetchone():
                continue
            item["reason"] = "new_material_or_curiosity"
        item["weight"] = round((item["content_score"] + debt[mode]) * (.55 if previous == mode else 1), 3)
    contact = result[CONTACT_MODE]
    contact["reason"] = contact_readiness(store, config, epoch, stamp)
    if contact["reason"] == "contact_opportunity":
        contact["weight"] = round((1 + debt[CONTACT_MODE]) * (.55 if previous == CONTACT_MODE else 1), 3)
    # Waiting work with real content is blocked, not absent; it raises rest tendency.
    waiting = store.conn.execute(
        "SELECT COALESCE(a.activity, CASE q.origin WHEN 'review' THEN 'iteration' "
        "WHEN 'interest' THEN 'interest' WHEN 'exploration' THEN 'interest' ELSE 'problem' END) AS mode "
        "FROM thought_questions q LEFT JOIN thought_agenda a ON a.question_id=q.id "
        "WHERE q.epoch=? AND q.status LIKE 'waiting%'", (epoch,)).fetchall()
    blocked_modes += len({r[0] for r in waiting if r[0] in WORK_MODES})
    dry = 0
    for record in history:
        if record["activity"] == "idle":
            break
        if record["activity"] == CONTACT_MODE:
            continue  # A conversation decision is not a research attempt.
        if record["progress"]:
            break
        dry += 1
    idle_seconds = fixed_idle_seconds(config)
    idle = result["idle"]
    idle["reason"] = "idle_disabled" if idle_seconds <= 0 else "voluntary_rest"
    idle["content_score"] = 1 + 2*min(dry, 3) + 2*min(blocked_modes, 3)
    if idle_seconds > 0:
        idle["weight"] = round((idle["content_score"]) * (.55 if previous == "idle" else 1), 3)
    for item in result.values():
        item.pop("_rank", None)
    eligible = [mode for mode in ACTIVE_MODES if result[mode]["weight"] > 0]
    draw_weights = {}
    if eligible and not any(result[mode]["priority"] for mode in eligible):
        draw_weights = {mode: result[mode]["weight"] for mode in eligible}
        if idle["weight"] > 0:
            draw_weights["idle"] = idle["weight"]
    return {"modes": result, "input_stamp": stamp, "idle_seconds": idle_seconds,
            "blocked_modes": blocked_modes, "dry_windows": dry, "previous_activity": previous,
            "draw_weights": draw_weights}


def contact_readiness(store, config, epoch, stamp, *, exclude_run=None):
    from .autonomy_v2 import build
    from .autonomy_v2.models import Scope

    if not config["activities"][CONTACT_MODE]["seconds"]:
        return "mode_disabled"
    if config["delivery"] != "gui":
        return "contact_delivery_mode"
    scope = Scope(session_epoch=epoch or "", channel_id="gui")
    blockers = build(store=store).invitations.delivery_blockers(scope)
    if blockers:
        return blockers[0]
    # Do not pay repeatedly to find a topic in the same context. An explicit
    # appointment becoming due gives one more opportunity for a fresh decision.
    last = store.conn.execute(
        "SELECT a.started_at,d.decision FROM autonomous_activity_runs a JOIN exploration_runs r ON r.id=a.run_id "
        "LEFT JOIN v2_contact_decisions d ON d.run_id=r.id AND d.epoch=r.epoch "
        "WHERE r.epoch=? AND a.activity=? AND a.input_stamp=? AND r.model_calls>0 AND r.id<>? "
        "ORDER BY a.started_at DESC LIMIT 1", (epoch, CONTACT_MODE, stamp, exclude_run or "")).fetchone()
    if last is not None and not store.conn.execute(
        "SELECT 1 FROM v2_contact_holds WHERE epoch=? AND channel_id='gui' AND intent_id<>'' "
        "AND resume_at>? AND resume_at<=? LIMIT 1", (epoch, last["started_at"], time.time())).fetchone():
        return "contact_context_reviewed" if last["decision"] in {"none", "later", "invite"} else "contact_attempt_limit"
    return "contact_opportunity"


def choose(store, config, *, question=None):
    projected = weights(store, config)
    modes = projected["modes"]
    if question is not None:
        mode = assessment(store, question)["activity"]
        if not autonomy_policy.TEST_CONTROLS_ONLY and not config["activities"][mode]["seconds"]:
            raise ValueError("该问题所属活动时长为0，请先开放")
        return mode, {**projected, "selected": mode, "question_id": question["id"],
                      "reason": "explicit_task", "draw_weights": {}}
    eligible = [m for m in ACTIVE_MODES if modes[m]["weight"] > 0]
    priority = [m for m in eligible if modes[m]["priority"]]
    if priority:
        mode = max(priority, key=lambda m: modes[m]["weight"])
        return mode, {**projected, "selected": mode, "question_id": modes[mode]["question_id"], "reason": "priority_content"}
    if draw := projected["draw_weights"]:
        mode = random.choices(list(draw), weights=list(draw.values()), k=1)[0]
        return mode, {**projected, "selected": mode, "question_id": modes[mode].get("question_id"),
                      "reason": "weighted_rotation"}
    # No work mode can proceed: voluntary rest when enabled, condition waiting otherwise.
    if projected["idle_seconds"] > 0:
        return "idle", {**projected, "selected": "idle", "question_id": None, "reason": "no_actionable_work"}
    return None, {**projected, "selected": None, "question_id": None, "reason": "no_actionable_work"}


def register(store, run_id, mode, selection):
    from .thought_workspace import dump
    store.conn.execute("INSERT INTO autonomous_activity_runs(run_id,activity,selection,input_stamp) VALUES(?,?,?,?)",
        (run_id, mode, dump(selection), selection["input_stamp"]))


def run_info(store, run_id):
    row = store.conn.execute("SELECT * FROM autonomous_activity_runs WHERE run_id=?", (run_id,)).fetchone()
    if not row:
        return None
    result = dict(row)
    result["selection"] = json.loads(result["selection"])
    return result


def charge(store, run_id, *, progress=False):
    """Exactly once per execution window: actual activity, not semantic progress.

    The ledger feeds rotation only; substantive progress is decided solely by
    finish receipts written to v2_progress_receipts.
    """
    from .thought_workspace import event
    if store.conn.execute("UPDATE autonomous_activity_runs SET charged=1 WHERE run_id=? AND charged=0", (run_id,)).rowcount:
        info = run_info(store, run_id)
        store.conn.execute("INSERT INTO autonomous_activity_executions(run_id,activity,input_stamp,started_at,progress) VALUES(?,?,?,?,?)",
            (run_id, info["activity"], info["input_stamp"], info["started_at"],
             int(bool((store.conn.execute("SELECT substantive_progress FROM v2_progress_receipts WHERE run_id=?", (run_id,)).fetchone() or [0])[0]))))
        event(store, "activity_executed", run_id)


GUIDANCE = {
    "interest": "兴趣探索：围绕真实兴趣或自己的好奇补充知识，按需读取并保留出处，可形成新疑问。",
    "problem": "问题思考：优先推进目标疑问的具体缺口；条件不足时保存等待条件，不靠反复改写制造进展。",
    "iteration": "自我迭代：对照实际行动与反馈，区分观察、可能解释、拟尝试的调整。表达策略可直接形成；用户心理仍依证据。用真实记录描述试错，不补写成长经历。",
}
