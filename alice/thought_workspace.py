"""Questions and contact intentions. Semantic decisions belong to Alice.

All methods run under the caller's Store lock; no I/O or model calls here.
Source dependencies travel with questions, outcomes and messages.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import uuid

from .capability_registry import sdk_group
from . import activity, autonomy_policy

SCHEMA = """
CREATE TABLE IF NOT EXISTS thought_questions (
 id INTEGER PRIMARY KEY AUTOINCREMENT, epoch TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1,
 question TEXT NOT NULL, reason TEXT NOT NULL, origin TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'open', progress TEXT NOT NULL DEFAULT '',
 next_step TEXT NOT NULL DEFAULT '', parent_id INTEGER, outcome_id INTEGER,
 basis TEXT NOT NULL DEFAULT '{}', created_at REAL NOT NULL, updated_at REAL NOT NULL,
 last_considered REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS thought_question_epoch ON thought_questions(epoch,status,updated_at);
CREATE TABLE IF NOT EXISTS exploration_runs (
 id TEXT PRIMARY KEY, epoch TEXT NOT NULL, status TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '',
 question_id INTEGER, checkpoint TEXT NOT NULL DEFAULT '[]', basis TEXT NOT NULL DEFAULT '{}',
 model_calls INTEGER NOT NULL DEFAULT 0, steps INTEGER NOT NULL DEFAULT 0,
 manual INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL, updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS thought_contacts (
 id INTEGER PRIMARY KEY AUTOINCREMENT, epoch TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1,
 question_id INTEGER, question_version INTEGER, motive TEXT NOT NULL, message TEXT NOT NULL,
 mode TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued', basis TEXT NOT NULL DEFAULT '{}',
 context_turn_id INTEGER NOT NULL, turn_id INTEGER,
 adoption_request_id TEXT,
 created_at REAL NOT NULL, expires_at REAL NOT NULL, updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS thought_events (
 id INTEGER PRIMARY KEY AUTOINCREMENT, epoch TEXT NOT NULL, kind TEXT NOT NULL,
 object_id TEXT NOT NULL, detail TEXT NOT NULL, created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS proactive_turns (
 turn_id INTEGER PRIMARY KEY, contact_id INTEGER UNIQUE NOT NULL, epoch TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS background_usage (
 day TEXT PRIMARY KEY, models INTEGER NOT NULL DEFAULT 0
);
""" + activity.SCHEMA

DEFAULTS = {"enabled": False, "allow_paid": False, "allow_external": False,
            "wake_state": "sleeping", "activities": activity.DEFAULT_ACTIVITIES,
            "proactive_enabled": False, "delivery": "next_chat",
            "progress": True, "request": True, "share": True,
            "daily_models": 12, "run_models": 4, "run_steps": 12,
            "run_seconds": 180, "idle_seconds": 120, "interval_seconds": 1800,
            "daily_contacts": 2, "contact_cooldown": 3600,
            "idle_rest_seconds": activity.DEFAULT_IDLE_SECONDS, "same_material_windows": 2,
            "invitation_wait_seconds": 600, "same_topic_retry_seconds": 86400,
            "window_start": "09:00", "window_end": "22:00",
            "contact_start": "09:00", "contact_end": "22:00"}

def dump(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def text(value, maximum=2000, *, empty=False):
    if not isinstance(value, str) or len(value) > maximum or (not empty and not value.strip()):
        raise ValueError("文字为空或超出长度")
    return value.strip()


def turn_version(row):
    return hashlib.sha256(dump([row["id"], row["role"], row["content"]]).encode()).hexdigest()


def settings(store, *, effective=True):
    try:
        saved = json.loads(store.meta_get("autonomy_settings") or "{}")
    except ValueError:
        saved = {}
    if not isinstance(saved, dict):
        saved = {}
    result = {**DEFAULTS, **{k: v for k, v in saved.items() if k in DEFAULTS}}
    result["wake_state"] = saved.get("wake_state", "awake" if saved.get("enabled") else "sleeping")
    result["enabled"] = result["wake_state"] == "awake"
    result["activities"] = activity.validate_activities(result["activities"])
    if effective:
        result["test_controls_only"] = autonomy_policy.TEST_CONTROLS_ONLY
        if autonomy_policy.TEST_CONTROLS_ONLY and not result["allow_external"]:
            for item in result["activities"].values():
                item["tools"] = [tool for tool in item["tools"] if tool != "search"]
    return result


def save_settings(store, patch):
    if not isinstance(patch, dict) or set(patch) - set(DEFAULTS):
        raise ValueError("设置字段无效")
    result = settings(store, effective=False)
    previous_delivery = result["delivery"]
    if "wake_state" in patch and "enabled" in patch and patch["enabled"] != (patch["wake_state"] == "awake"):
        raise ValueError("清醒状态与旧探索开关冲突")
    for key, value in patch.items():
        default = DEFAULTS[key]
        if key == "activities":
            value = activity.validate_activities(value, previous=result["activities"])
        elif key == "wake_state":
            if not isinstance(value, str) or value not in {"awake", "sleeping"}:
                raise ValueError("清醒状态无效")
        elif type(default) is bool:
            if type(value) is not bool:
                raise ValueError("开关必须为布尔值")
        elif type(default) is int:
            maximum = 86400 if key.endswith("seconds") or key == "contact_cooldown" else 1000
            if type(value) is not int or not 0 <= value <= maximum:
                raise ValueError("额度超出允许范围")
        elif key == "delivery":
            if value not in {"next_chat", "gui"}:
                raise ValueError("当前仅支持下次交流或站内主动消息")
        elif not isinstance(value, str) or not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", value):
            raise ValueError("时段格式应为HH:MM")
        result[key] = value
    if "wake_state" in patch:
        result["enabled"] = result["wake_state"] == "awake"
    elif "enabled" in patch:
        result["wake_state"] = "awake" if result["enabled"] else "sleeping"
    if result["run_models"] > 20 or result["run_steps"] > 100:
        raise ValueError("单次运行额度过大")
    if result["delivery"] != previous_delivery:
        store.conn.execute("UPDATE thought_contacts SET mode=?,status=CASE WHEN status='needs_review' THEN status ELSE ? END,version=version+1,updated_at=? "
            "WHERE epoch=? AND status IN('queued','pending_next_chat','needs_review')",
            (result["delivery"], "pending_next_chat" if result["delivery"] == "next_chat" else "queued",
             time.time(), store.meta_get("session_epoch")))
    store.meta_set("autonomy_settings", dump(result))
    event(store, "settings_changed", "settings", {"fields": sorted(patch)})
    return result


def event(store, kind, identity, detail=None):
    with store.source_transaction():
        store.conn.execute("INSERT INTO thought_events(epoch,kind,object_id,detail,created_at) VALUES(?,?,?,?,?)",
            (store.meta_get("session_epoch") or "", kind, str(identity), dump(detail or {}), time.time()))


def source_space_after_write(store, previous):
    limit = store.source_limits()["source_pool_bytes"]
    if limit is not None and store.source_usage_bytes() > max(previous, limit):
        raise ValueError("来源资料容量已满，本次写入未提交")


def contact_capacity(store, config):
    """One invitation counts as one proactive contact; acceptance does not."""
    if autonomy_policy.TEST_CONTROLS_ONLY:
        return True
    from datetime import datetime
    from .discovery_memory import DAY_ZONE
    now = time.time()
    start = datetime.now(DAY_ZONE).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    epoch = store.meta_get("session_epoch")
    stamps = [r[0] for r in store.conn.execute("SELECT updated_at FROM thought_contacts WHERE epoch=? "
        "AND status IN('delivered','incorporated','incorporating')", (epoch,)).fetchall()]
    stamps += [r[0] for r in store.conn.execute(
        "SELECT delivered_at FROM v2_invitation_attempts WHERE epoch=? AND delivered_at IS NOT NULL "
        "", (epoch,)).fetchall()]
    return (sum(stamp >= start for stamp in stamps) < config["daily_contacts"]
            and (not stamps or now - max(stamps) >= config["contact_cooldown"]))


_QUESTION_REF = re.compile(r"出处：question:(\d+)@")


def current_stance_rows(store, rows):
    """v2 current-version projection shared by registry and legacy assembly.

    A stance renders when it is its question's current head; a superseded
    revision (explicit chain or a later outcome on the same question) does
    not. Rows without any question link keep the previous behaviour.
    """
    heads = {r["memory_id"]: r["question_id"] for r in store.conn.execute(
        "SELECT memory_id,question_id FROM v2_view_heads WHERE memory_id IS NOT NULL")}
    outcomes = {r["outcome_id"] for r in store.conn.execute(
        "SELECT outcome_id FROM thought_questions WHERE outcome_id IS NOT NULL")}
    visible = {row["id"] for row in rows}
    replaced = {r["old_id"] for r in store.conn.execute("SELECT old_id,new_id FROM view_revisions")
                if r["new_id"] in visible or r["new_id"] in heads}
    keep = []
    for row in rows:
        if row["id"] in heads:
            keep.append(row)
            continue
        if row["id"] in replaced:
            continue
        match = _QUESTION_REF.search(row["content"])
        if match:
            question_id = int(match[1])
            if question_id in heads.values():
                continue  # The question moved to a newer current view.
            outcome = store.conn.execute("SELECT outcome_id FROM thought_questions WHERE id=?",
                                         (question_id,)).fetchone()
            if outcome is not None and outcome[0] is not None and row["id"] != outcome[0]:
                continue  # A later outcome superseded this legacy revision.
        keep.append(row)
    return keep


def execution_context(store, context):
    """Host-bound ExecutionContext; never assembled from model arguments."""
    from .autonomy_v2.adapters import SystemClock
    from .autonomy_v2.models import BudgetSnapshot, ExecutionContext, Scope
    request_id = str(context.get("request_id") or "")
    return ExecutionContext(
        scope=Scope(session_epoch=store.meta_get("session_epoch") or "", channel_id="gui"),
        request_id=request_id,
        run_id=request_id if request_id.startswith("explore_") else None,
        user_turn_id=context.get("user_turn_id"),
        clock=SystemClock().snapshot(),
        budget=BudgetSnapshot(model_calls_remaining=0, tool_steps_remaining=0,
                              window_seconds_remaining=0.0, allowed_tool_groups=()),
        manual_requested=bool(context.get("manual_requested")))


def valid_basis(store, basis, *, scope="gui"):
    from .context_memory import version
    if basis.get("epoch") != store.meta_get("session_epoch") or scope != "gui":
        return False
    if basis.get("workers"):
        return False  # Worker-derived thoughts need their own publication contract.
    packets = basis.get("packets", [])
    if packets and not store.source_packets_for(packets, scope=scope):
        return False
    for mid, expected in basis.get("memories", {}).items():
        row = store.get_memory(int(mid))
        if row is None or row["provenance_status"] != "valid" or version(row) != expected:
            return False
        if row["origin"] == "conversation":
            if not store._parents_available(row["id"], scope):
                return False
        elif not store.observation_sources(row["id"], scope=scope):
            return False
    for tid, expected in basis.get("turns", {}).items():
        row = store.conn.execute("SELECT * FROM turns WHERE id=?", (int(tid),)).fetchone()
        if row is None or turn_version({**dict(row), "content": store.turn_display(row["id"], scope=scope)}) != expected:
            return False
    for identity, expected in basis.get("interests", {}).items():
        row = store.conn.execute("SELECT version,enabled,scope_json FROM discovery_interests WHERE id=?",
                                 (identity,)).fetchone()
        if row is None or row["version"] != expected or not row["enabled"] or scope not in json.loads(row["scope_json"]):
            return False
    return True


def merge_basis(*values):
    result = {"epoch": "", "packets": [], "memories": {}, "turns": {}, "workers": []}
    for value in values:
        if not value:
            continue
        if result["epoch"] and result["epoch"] != value.get("epoch"):
            raise ValueError("来源属于其他会话")
        result["epoch"] = value.get("epoch", "")
        result["packets"] = list(dict.fromkeys(result["packets"] + value.get("packets", [])))
        for key in ("memories", "turns", "interests"):
            for identity, digest in value.get(key, {}).items():
                versions = result.setdefault(key, {})
                if identity in versions and versions[identity] != digest:
                    raise ValueError("来源版本已改变，请重新读取")
                versions[identity] = digest
        result["workers"] += value.get("workers", [])
    return result


def current_basis(store, context):
    basis = {"epoch": store.meta_get("session_epoch"), "packets": list(context.get("packet_ids", [])),
             "memories": {}, "turns": {}, "workers": list(context.get("worker_refs", []))}
    session = context.get("memory_session")
    if session is not None:
        basis["memories"] = {str(mid): digest for mid, digest in session.loaded.values()}
    from .context_memory import version
    for mid in context.get("recalled_memory_ids", []):
        row = store.get_memory(mid)
        if row is not None:
            basis["memories"][str(mid)] = version(row)
    tid = context.get("user_turn_id")
    if tid:
        row = store.conn.execute("SELECT * FROM turns WHERE id=?", (tid,)).fetchone()
        if row is not None:
            basis["turns"][str(tid)] = turn_version({**dict(row), "content": store.turn_display(tid, scope="gui")})
    if session is not None and session.history is not None:
        for ref in session.history.loaded:
            uid = session.history.issued[ref][0]
            rows = session.history.rows(user_id=uid)
            for pair in rows:
                for key in ("user_id", "assistant_id"):
                    row = store.conn.execute("SELECT * FROM turns WHERE id=?", (pair[key],)).fetchone()
                    if row:
                        basis["turns"][str(row["id"])] = turn_version(row)
    return merge_basis(basis, context.get("thought_basis"))


def question_rows(store, *, include_closed=False):
    sql = "SELECT * FROM thought_questions WHERE epoch=?"
    if not include_closed:
        sql += " AND status NOT IN('resolved','closed')"
    return store.conn.execute(sql + " ORDER BY updated_at DESC,id DESC LIMIT 500",
                              (store.meta_get("session_epoch") or "",)).fetchall()


def public_question(row, *, full=False):
    result = {"ref": f"question:{row['id']}@{row['version']}", "id": row["id"], "version": row["version"],
              "question": row["question"], "status": row["status"], "origin": row["origin"],
              "updated_at": row["updated_at"], "detail_state": "loaded" if full else "not_loaded"}
    if full:
        result.update(reason=row["reason"], progress=row["progress"], next_step=row["next_step"],
                      parent_id=row["parent_id"], outcome_id=row["outcome_id"], basis=json.loads(row["basis"]))
    return result


def expire_dependencies(store):
    """No prose copies survive a revoked/deleted dependency in these new stores."""
    epoch = store.meta_get("session_epoch") or ""
    with store.source_transaction():
        for intent in store.conn.execute("SELECT id,basis FROM v2_contact_intents WHERE epoch=? "
                                        "AND motive='chat' AND topic<>''", (epoch,)).fetchall():
            if not valid_basis(store, json.loads(intent["basis"]) or {}):
                store.conn.execute("UPDATE v2_contact_intents SET topic='',reason='',state='closed',"
                                   "version=version+1,updated_at=? WHERE id=?", (time.time(), intent["id"]))
        for plan in store.conn.execute("SELECT run_id,basis FROM v2_activity_plans WHERE epoch=? AND object<>''",
                                       (epoch,)).fetchall():
            if not valid_basis(store, json.loads(plan['basis'])):
                store.conn.execute("UPDATE v2_activity_plans SET object='',value='',why_now='',stop_condition='',"
                    "next_action='',result=NULL WHERE run_id=?", (plan['run_id'],))
        for row in store.conn.execute("SELECT * FROM thought_questions WHERE epoch=? AND question<>''", (epoch,)).fetchall():
            if row["question"] and not valid_basis(store, json.loads(row["basis"])):
                if row["outcome_id"]:
                    store.forget_memory_tree(row["outcome_id"], "探索成果所需依据已不可用")
                store.conn.execute("UPDATE thought_questions SET question='',reason='',progress='',next_step='',"
                    "status='closed',version=version+1,updated_at=? WHERE id=?", (time.time(), row["id"]))
                store.conn.execute("DELETE FROM thought_agenda WHERE question_id=?", (row["id"],))
                event(store, "question_unavailable", row["id"])
        for table, fields in (("thought_contacts", "message=''"), ("exploration_runs", "checkpoint='[]'")):
            active_body = "message<>''" if table == "thought_contacts" else "checkpoint<>'[]'"
            for row in store.conn.execute(f"SELECT * FROM {table} WHERE epoch=? AND {active_body}", (epoch,)).fetchall():
                if not valid_basis(store, json.loads(row["basis"])):
                    store.conn.execute(f"UPDATE {table} SET {fields},status='stale',updated_at=? WHERE id=?",
                                       (time.time(), row["id"]))
                    if table == "thought_contacts" and row["turn_id"]:
                        from .discovery_memory import SOURCE_SHADOW
                        if store.conn.execute("SELECT 1 FROM proactive_turns WHERE turn_id=?", (row["turn_id"],)).fetchone():
                            store.conn.execute("UPDATE turns SET content=? WHERE id=?", (SOURCE_SHADOW, row["turn_id"]))
                            store.conn.execute("UPDATE source_turns SET content='' WHERE turn_id=?", (row["turn_id"],))


class ThoughtSession:
    """Request-bound issued question references; updates require the shown version."""

    def __init__(self, store, context):
        self.store, self.context = store, context
        self.issued = set()
        self.contacts = set()
        self.invitations = set()
        self.intents = {}
        self.views = {}
        self.question_versions = {}
        self.epoch = store.meta_get("session_epoch")
        self.calls = 0

    def expose(self, row, *, full=False):
        item = public_question(row, full=full)
        from .autonomy_v2.planning import ActivityPlanning
        item['value_waiting'] = ActivityPlanning(self.store).parked(row)
        self.issued.add(item["ref"])
        self.question_versions[row["id"]] = row["version"]
        if full:
            self.context["thought_basis"] = merge_basis(self.context.get("thought_basis"), json.loads(row["basis"]))
            self.bind_sources()
        return item

    def valid(self):
        if self.epoch != self.store.meta_get("session_epoch"):
            return False
        for identity, expected in self.question_versions.items():
            row = self.store.conn.execute("SELECT version,basis FROM thought_questions WHERE id=? AND epoch=?", (identity, self.epoch)).fetchone()
            if row is None or row[0] != expected or not valid_basis(self.store, json.loads(row["basis"])):
                return False
        from .context_memory import version
        for mid, digest in self.views.values():
            row = self.store.get_memory(mid)
            if row is None or version(row) != digest or not valid_basis(self.store, {
                    "epoch": self.epoch, "memories": {str(mid): digest}}):
                return False
        return True

    def view_index(self, query, limit=4):
        from .context_memory import version
        from .store import text_score
        rows = current_stance_rows(self.store, self.store.memory_rows(purpose="chat", scope="gui", kind="stance"))
        rows.sort(key=lambda row: text_score(query, row["title"] + row["content"]), reverse=True)
        items = []
        for row in rows:
            if query and text_score(query, row["title"] + row["content"]) < .12:
                continue
            digest = version(row)
            ref = f"stance:{row['id']}@{digest[:16]}"
            self.views[ref] = (row["id"], digest)
            items.append({"ref": ref, "title": row["title"], "detail_state": "not_loaded"})
            if len(items) >= limit:
                break
        return items

    def get_view(self, ref):
        from .context_memory import version
        target = self.views.get(ref)
        if target is None:
            raise ValueError("请先检索取得真实观点引用")
        row = next((r for r in self.store.memory_rows(purpose="chat", scope="gui", kind="stance") if r["id"] == target[0]), None)
        if row is None or version(row) != target[1]:
            raise ValueError("观点来源或版本已改变")
        return row

    def bind_sources(self):
        packets = list(dict.fromkeys(self.context.get("packet_ids", []) + self.context.get("thought_basis", {}).get("packets", [])))
        cap = self.context.get("limits", self.store.source_limits())["chat_dependency_limit"] if autonomy_policy.active(self.context) else self.store.source_limits()["chat_dependency_limit"]
        if cap is not None and len(packets) > cap:
            raise ValueError("思想资料来源超过本轮依赖额度")
        self.context["packet_ids"] = packets
        if packets and self.context.get("user_turn_id"):
            self.store.protect_source_turn(self.context["user_turn_id"], packets, scope="gui")

    def get(self, ref):
        if ref not in self.issued:
            raise ValueError("请先检索取得真实疑问引用")
        match = re.fullmatch(r"question:(\d+)@(\d+)", ref)
        row = self.store.conn.execute("SELECT * FROM thought_questions WHERE id=? AND epoch=? AND version=?",
            (int(match[1]), self.epoch, int(match[2]))).fetchone()
        if row is None or not valid_basis(self.store, json.loads(row["basis"])):
            raise ValueError("疑问已改变或来源不可用，请重新检索")
        return row

    def initial(self, query):
        from .store import text_score
        from .autonomy_v2 import build
        from .autonomy_v2.models import Scope
        # Process a deadline also when the first returning user message wins
        # foreground priority before the next background scheduler tick.
        build(store=self.store).invitations.expire(Scope(session_epoch=self.epoch, channel_id='gui'))
        config = settings(self.store)
        rows = [r for r in question_rows(self.store) if r["question"] and valid_basis(self.store, json.loads(r["basis"]))]
        ranked = sorted(rows, key=lambda r: text_score(query, r["question"] + r["progress"]), reverse=True)
        chosen = [r for r in ranked if text_score(query, r["question"] + r["progress"]) >= .12][:3]
        items = [self.expose(r) for r in chosen]
        invitation = None
        if config["proactive_enabled"]:
            from .autonomy_v2.adapters import InvitationRepositoryAdapter
            from .autonomy_v2.models import Scope
            index = InvitationRepositoryAdapter(self.store).pending_index(
                Scope(session_epoch=self.epoch, channel_id="gui"))
            if index is not None:
                ref = f"invitation:{index.attempt_id}@{index.version}"
                self.invitations.add(ref)
                invitation = {"ref": ref, "topic": index.topic, "state": index.state.value,
                              "invitation_turn_id": index.invitation_turn_id,
                              "expires_at": index.expires_at, "detail_state": "not_loaded"}
        eligible = []
        if not self.context.get("user_turn_id"):
            from .autonomy_v2 import build
            from .autonomy_v2.models import Scope
            invitations = build(store=self.store).invitations
            repo = invitations.repository
            for intent in repo.eligible_intents(Scope(session_epoch=self.epoch, channel_id="gui"), now_at=time.time()):
                if intent.motive == "chat" and self.context.get("activity_mode") != activity.CONTACT_MODE:
                    continue
                current = True
                try:
                    invitations.host.validate_intent(intent.scope, intent)
                except ValueError:
                    if intent.motive == "chat":
                        continue
                    current = False
                ref = f"intent:{intent.id}@{intent.version}"
                self.intents[ref] = intent
                eligible.append({"ref": ref, "topic": intent.topic,
                                 "reason": intent.reason if current else "内容引用已变化；重新查阅当前问题或观点后沿用此意图更新引用",
                                 "content_refs": intent.content_refs,
                                 "detail_state": "not_loaded" if current else "stale"})
                if len(eligible) >= 5:
                    break
        views = self.view_index(query, limit=2) if query else []
        state = {key: config[key] for key in ("allow_paid", "allow_external", "proactive_enabled")}
        state["lifecycle"] = activity.lifecycle(config)
        if not self.context.get("user_turn_id"):
            from .autonomy_v2 import build
            from .autonomy_v2.models import Scope
            state["contact_blockers"] = build(store=self.store).invitations.delivery_blockers(
                Scope(session_epoch=self.epoch, channel_id="gui"))
        from .autonomy_v2.contact_wait import ContactWait
        from .autonomy_v2.models import Scope
        waits = ContactWait(self.store)
        scope = Scope(session_epoch=self.epoch, channel_id="gui")
        held = []
        from .autonomy_v2 import build
        stack = build(store=self.store)
        for attempt in waits.feedback_attempts(scope):
            intent = stack.invitations.repository.get_intent(scope, attempt['intent_id'])
            ref = f"invitation:{attempt['id']}@{attempt['version']}"
            self.invitations.add(ref)
            intent_ref = f"intent:{intent.id}@{intent.version}"
            self.intents[intent_ref] = intent
            try:
                stack.invitations.host.validate_intent(scope, intent)
                topic = intent.topic
            except ValueError:
                topic = '原内容引用已变化，需要相关回应后重新查阅'
            held.append({'ref': ref, 'intent_ref': intent_ref, 'topic': topic,
                         'last_outcome': attempt['state'], 'invitation_turn_id': attempt['invitation_turn_id'],
                         'detail_state': 'not_loaded'})
        state['contact_waits'] = waits.rows(scope)
        index = ("本轮思想运行状态：" + dump(state) + "\n思想索引（资料，正文按需读取）：\n"
                 + dump({"questions": items, "views": views, "pending_invitation": invitation,
                         "waiting_feedback": held, "eligible_contact_intents": eligible}))
        if self.context.get("user_turn_id"):
            index += ("\n如本轮用户消息是对待回应邀请的回答，先用 contact_reply 绑定真实决定；"
                      "迟来的相关回应仍可绑定waiting_feedback。用户表示忙用contact_availability(busy)，"
                      "明确恢复方便时用available；普通问候照常回应，不自动解除等待或接受旧邀请。"
                      "问号或不明回应先简短澄清原邀请，未接受前不展开成果正文。")
        return index

    def execute(self, name, args):
        try:
            if self.epoch != self.store.meta_get("session_epoch") or self.context.get("scope") != "gui":
                raise ValueError("思想资料不在当前会话或渠道范围")
            self.calls += 1
            if self.calls > 24 and not autonomy_policy.active(self.context):
                raise ValueError("本次思想操作额度已用完")
            with self.store.source_transaction():
                before = self.store.source_usage_bytes()
                result = self._execute(name, args)
                source_space_after_write(self.store, before)
            return dump(result)
        except (ValueError, TypeError, KeyError) as exc:
            return dump({"error": "thought_refused", "reason": str(exc)[:180]})

    def _browse_activity(self, args):
        from .autonomy_v2.adapters import HostAdapter
        from .autonomy_v2.planning import ActivityPlanning
        from .store import text_score

        store = self.store
        context = execution_context(store, self.context)
        HostAdapter(store).validate_background_context(context)
        planning = ActivityPlanning(store)
        browse = self.context.get('interest_browse')
        if browse is None or planning.get(context.run_id) is not None:
            raise ValueError('只在自由兴趣窗定题前浏览')
        source = args['source']
        if source in browse['sources']:
            raise ValueError('这一个库已经浏览过，请选另一库或定题')
        query = text(args.get('query', ''), 120, empty=True)
        if source == 'memory':
            if not browse['memory']:
                raise ValueError('本模式未开放记忆读取')
            result = self.context['memory_session'].browse(query, 6)
            if result.get('error'):
                return result
        elif source == 'questions':
            rows = [row for row in question_rows(store) if row['question'] and valid_basis(store, json.loads(row['basis']))]
            maximum = max(1, min(settings(store)["same_material_windows"], 20))
            ranked = []
            for row in rows:
                score = text_score(query, row['question']) if query else 0
                if query and score < .12:
                    continue
                ready = row['status'] == 'open' and not planning.parked(row) and (
                    autonomy_policy.active(self.context) or activity.remaining_windows(store, row, maximum) > 0)
                ranked.append(((score, ready), row))
            ranked.sort(key=lambda item: item[0], reverse=True)
            items = []
            for (_, ready), row in ranked[:6]:
                item = self.expose(row)
                item.update(question=item['question'][:180], actionable=ready)
                if row['status'].startswith('waiting'):
                    wait = store.conn.execute('SELECT reason FROM v2_question_waits WHERE question_id=?',
                                              (row['id'],)).fetchone()
                    if wait:
                        item['wait_reason'] = wait[0][:100]
                items.append(item)
            result = {'items': items}
        elif source == 'interests':
            rows = [row for row in store.conn.execute(
                "SELECT id,version,topic,summary,scope_json FROM discovery_interests WHERE enabled=1 "
                "ORDER BY created_at DESC,id") if 'gui' in json.loads(row['scope_json'])]
            if query:
                rows = sorted((row for row in rows if text_score(query, row['topic'] + ' ' + row['summary']) >= .12),
                              key=lambda row: text_score(query, row['topic'] + ' ' + row['summary']), reverse=True)
            rows = rows[:6]
            self.context['thought_basis'] = merge_basis(self.context.get('thought_basis'), {
                'epoch': self.epoch, 'interests': {row['id']: row['version'] for row in rows}})
            result = {'items': [{'topic': row['topic'][:100], 'summary': row['summary'][:120]} for row in rows]}
        else:
            raise ValueError('未知资料库')
        browse['sources'].add(source)
        return {'status': 'ok', **result, 'source': source, 'notice': '本机选题参考，不是当前用户的新任务。'}

    def _execute(self, name, args):
        store, now = self.store, time.time()
        if name == 'activity_browse':
            return self._browse_activity(args)
        if name == "contact_plan":
            return self._plan_contact(args)
        if name == 'activity_plan':
            from .autonomy_v2.planning import ActivityPlanning
            planning = ActivityPlanning(store)
            if planning.get(self.context.get('run_id')) is not None:
                return {'status': 'planned', 'plan': planning.get(self.context['run_id'])}
            question = self.get(args['question_ref']) if args.get('question_ref') else None
            plan_basis = current_basis(store, self.context)
            if question is not None:
                plan_basis = merge_basis(plan_basis, json.loads(question['basis']))
            if args.get('related_ref') and question is None:
                raise ValueError('关联问题族时须同时提供本题question_ref')
            if args.get('related_ref') and question is not None:
                related = self.get(args['related_ref'])
                plan_basis = merge_basis(plan_basis, json.loads(related['basis']))
                store.conn.execute('INSERT INTO v2_question_families(question_id,family_id,reason,set_at) '
                    'VALUES(?,?,?,?) ON CONFLICT(question_id) DO UPDATE SET family_id=excluded.family_id,'
                    'reason=excluded.reason,set_at=excluded.set_at',
                    (question['id'], activity.family_of(store, related['id']), '选题时显式关联', now))
            if self.context.get('interest_browse') is not None and question is not None and args['choice'] == 'explore':
                if question["status"] != "open" or planning.parked(question):
                    raise ValueError('这条疑问仍在等待或已暂停，请选择其他可推进的方向')
                maximum = max(1, min(settings(store)["same_material_windows"], 20))
                if not autonomy_policy.active(self.context) and activity.remaining_windows(store, question, maximum) <= 0:
                    raise ValueError('这条疑问在等新材料，请选择其他可推进的方向')
                activity.consume_window(store, question, self.context['run_id'], maximum)
            plan = planning.submit(execution_context(store, self.context), args,
                question_id=question['id'] if question else None, basis=plan_basis)
            return {'status': 'planned', 'plan': plan,
                    'notice': '目标已固定；先完成本次缺口，旁支另行评估。搁置或休息可以直接收尾。'}
        if name == 'contact_availability':
            from .autonomy_v2.adapters import HostAdapter
            from .autonomy_v2.contact_wait import ContactWait
            ctx = execution_context(store, self.context)
            HostAdapter(store).validate_user_context(ctx)
            availability = args.get('availability')
            if availability not in {'busy', 'available'}:
                raise ValueError('availability须为busy/available')
            reason = text(args.get('reason', ''), 240)
            waits = ContactWait(store)
            if availability == 'busy':
                retry = args.get('requested_retry_at')
                if retry is not None and (type(retry) not in (int, float) or not now <= retry <= now + 30*86400):
                    raise ValueError('用户约定时间须为未来30天内Unix秒')
                from .autonomy_v2 import build
                from .autonomy_v2.models import ContactReply, ReplyDecision
                stack = build(store=store)
                pending = stack.invitations.repository.pending_reply(ctx.scope)
                if pending is not None:
                    stack.contact_reply(ctx, ContactReply(attempt_id=pending.id,
                        expected_attempt_version=pending.version, decision=ReplyDecision.DEFER,
                        reason=reason, requested_retry_at=retry))
                else:
                    waits.hold(ctx.scope, reason='user_busy', resume_at=retry)
            else:
                if args.get('requested_retry_at') is not None:
                    raise ValueError('恢复交流不附延期时间')
                ref = args.get('intent_ref')
                intent = self.intents.get(ref) if ref else None
                if ref and intent is None:
                    raise ValueError('请使用waiting_feedback中的真实意图引用')
                rows = waits.rows(ctx.scope, active_only=False)
                after = max((r['after_user_turn_id'] for r in rows
                             if r['intent_id'] in ('', intent.id if intent else '')), default=0)
                if ctx.user_turn_id <= after:
                    raise ValueError('恢复须绑定等待建立后的真实用户消息')
                waits.clear(ctx.scope, intent_id=intent.id if intent else None)
            event(store, 'contact_availability', ctx.request_id,
                  {'availability': availability, 'user_turn_id': ctx.user_turn_id})
            return {'status': availability, 'waits': waits.rows(ctx.scope),
                    'notice': '只更新联系条件，不视为接受旧邀请，不投递积压正文。'}
        if name == "thought_search":
            from .store import text_score
            query = text(args.get("query", ""), 500, empty=True)
            rows = [r for r in question_rows(store, include_closed=args.get("include_closed", False))
                    if r["question"] and valid_basis(store, json.loads(r["basis"]))]
            rows.sort(key=lambda r: text_score(query, r["question"] + " " + r["progress"]), reverse=True)
            selected = [r for r in rows if not query or text_score(query, r["question"] + r["progress"]) >= .12][:8]
            return {"items": [self.expose(r) for r in selected], "views": self.view_index(query)}
        if name == "thought_load":
            detail = args.get("detail", "task")
            if detail not in {"task", "full"}:
                raise ValueError("detail须为task或full")
            if args["ref"].startswith("stance:"):
                row = self.get_view(args["ref"])
                from .context_memory import version
                self.context["thought_basis"] = merge_basis(self.context.get("thought_basis"), {
                    "epoch": self.epoch, "memories": {str(row["id"]): version(row)},
                    "packets": [p["id"] for p in store.observation_sources(row["id"], scope="gui")]})
                self.bind_sources()
                self.context.setdefault("loaded_stances", set()).add(args["ref"])
                return {"ref": args["ref"], "title": row["title"], "content": row["content"], "detail_state": "loaded",
                        "evidence_role": "current_view", "notice": "这是当前看法，可作推演参考，不是新外部证据或已验证效果。"}
            row = self.get(args["ref"])
            if detail == "task":
                return {**self.expose(row), "detail_state": "task_loaded", "scheduling": activity.assessment(store, row),
                        "task_goal": row["question"], "prior_detail_state": "not_loaded",
                        "notice": "这是收纳时的任务题目与状态，不是用户逐字原话；旧推演正文尚未读取。继续围绕此目标找材料，受限或未命中时保留目标与缺口。旧推演仅在需要时用detail=full展开。"}
            return {**self.expose(row, full=True), "scheduling": activity.assessment(store, row)}
        if name == "contact_reply":
            ref = args["ref"]
            if ref not in self.invitations:
                raise ValueError("请先在思想索引中取得真实邀请引用")
            match = re.fullmatch(r"invitation:(inv_[0-9a-f]+)@(\d+)", ref)
            if not match:
                raise ValueError("邀请引用无效")
            decision = args["decision"]
            if decision not in {"accept", "defer", "decline"}:
                raise ValueError("回应决定无效")
            if not self.context.get("user_turn_id"):
                raise ValueError("邀请回应必须绑定当前真实前台消息")
            from .autonomy_v2 import build
            from .autonomy_v2.adapters import HostAdapter
            from .autonomy_v2.models import ContactReply, ReplyDecision
            ctx = execution_context(store, self.context)
            stack = build(store=store)
            retry = args.get("requested_retry_at")
            if retry is not None and (not isinstance(retry, (int, float)) or isinstance(retry, bool) or not 0 <= retry <= 253402300799):
                raise ValueError("再次邀请时间无效")
            reply = ContactReply(attempt_id=match[1], expected_attempt_version=int(match[2]),
                                 decision=ReplyDecision(decision),
                                 reason=text(args.get("reason", ""), 500, empty=True),
                                 requested_retry_at=retry)
            attempt = stack.contact_reply(ctx, reply)
            if decision == "accept":
                host = HostAdapter(store)
                intent = stack.invitations.repository.get_intent(ctx.scope, attempt.intent_id)
                body = host.expansion_body(ctx.scope, host.load_expansion(ctx, attempt, intent))
                accepted_basis = {"epoch": self.epoch, "packets": [], "memories": {}, "turns": {}, "workers": []}
                from .context_memory import version
                for ref in intent.content_refs:
                    if ref.startswith("question:"):
                        qid = int(ref.split(":", 1)[1].split("@", 1)[0])
                        q = store.conn.execute("SELECT basis FROM thought_questions WHERE id=?", (qid,)).fetchone()
                        accepted_basis = merge_basis(accepted_basis, json.loads(q[0]))
                    elif ref.startswith("stance:"):
                        mid = int(ref.split(":", 1)[1].split("@", 1)[0])
                        accepted_basis["memories"][str(mid)] = version(store.get_memory(mid))
                        accepted_basis["packets"].extend(p["id"] for p in store.observation_sources(mid, scope="gui"))
                self.context["thought_basis"] = merge_basis(self.context.get("thought_basis"), accepted_basis, intent.basis)
                self.bind_sources()
                self.context.setdefault("invitation_expanded", []).append(attempt.id)
                return {"status": "accepted", "attempt": ref, "expansion": body,
                        "notice": "用户接受了本次邀请。围绕该主题展开这一条正常回复；接受不额外计一次主动联系。"}
            if decision == "defer":
                return {"status": "deferred", "attempt": ref,
                        "notice": "已持久等待用户或约定时间。简短收住，先做其他事，不换主题继续邀请。"}
            return {"status": "declined", "attempt": ref,
                    "notice": "用户明确拒绝该话题。本次收住；内容意图已结束，不自动排期重邀。"}
        if name == "activity_finish":
            if self.context.get("user_turn_id"):
                raise ValueError("结束回执只在自主探索窗口内使用，前台对话不需要")
            from .autonomy_v2 import build
            from .autonomy_v2.models import (
                CommunicationPlan, FinishDecision, ProgressKind, ProgressReport,
            )
            ctx = execution_context(store, self.context)
            stack = build(store=store)
            existing = stack.communication.get(ctx)
            if existing is not None and existing["decision"] != "unknown":
                return self._finish_result(stack.progress.repository.get_progress(ctx.run_id), existing)
            try:
                decision = FinishDecision(args["decision"])
                kind = ProgressKind(args["progress_kind"])
            except ValueError:
                raise ValueError("结束决定或进展类型无效")
            question = self.get(args["question_ref"]) if args.get("question_ref") else None
            report = ProgressReport(decision=decision, progress_kind=kind,
                                    summary=text(args["summary"], 1000),
                                    evidence_refs=tuple(item for ref in args.get("evidence_refs", [])
                                                        for item in self._resolve_evidence(ref)),
                                    action_refs=tuple(str(a) for a in args.get("action_refs", [])),
                                    next_step=text(args.get("next_step", ""), 1000, empty=True),
                                    wait_condition=self._wait_condition(args.get("wait_condition")))
            comm = args.get("communication")
            if not isinstance(comm, dict):
                raise ValueError("结束回执须附 communication：invite/later/none 和 reason")
            choice = comm.get("decision")
            if choice not in {"invite", "later", "none"}:
                raise ValueError("交流决定无效")
            reason = text(comm.get("reason", ""), 500)
            if choice == "none" and set(comm) - {"decision", "reason"}:
                raise ValueError("无需联系时只填写决定和理由")
            if choice == "later" and comm.get("invitation_text"):
                raise ValueError("留待以后只保留意图，不填写待发送的邀请文本")
            intent = self._contact_intent(comm, reason=reason) if choice != "none" else None
            plan = CommunicationPlan(decision=choice, reason=reason, intent=intent,
                invitation_text=text(comm.get("invitation_text", ""), 240, empty=True))
            from .autonomy_v2.planning import ActivityPlanning
            planning = ActivityPlanning(store)
            goal = planning.finish(ctx, args, question_id=question['id'] if question else None,
                                   basis=current_basis(store, self.context))
            receipt = stack.activity_finish(ctx, question["id"] if question is not None else None,
                report, communication=plan, basis=current_basis(store, self.context))
            return {**self._finish_result(receipt, stack.communication.get(ctx)), 'goal_result': goal}
        basis = current_basis(store, self.context)
        if not valid_basis(store, basis):
            raise ValueError("本次资料来源已变化或尚不支持此来源")
        if name == "thought_record":
            question, reason = text(args["question"], 300), text(args["reason"], 1000)
            origin = args["origin"]
            if origin not in {"user_request", "conversation", "memory", "review", "interest", "exploration"}:
                raise ValueError("问题来源类型无效")
            parent = self.get(args["parent_ref"]) if args.get("parent_ref") else None
            if parent:
                basis = merge_basis(basis, json.loads(parent["basis"]))
            for row in question_rows(store):
                if row["question"].strip() == question and valid_basis(store, json.loads(row["basis"])):
                    return {"status": "already_recorded", "question": self.expose(row)}
            if store.conn.execute("SELECT COUNT(*) FROM thought_questions WHERE epoch=? AND status NOT IN('resolved','closed')", (self.epoch,)).fetchone()[0] >= 500:
                raise ValueError("活跃疑问已达容量，请先合并或关闭旧问题")
            cur = store.conn.execute("INSERT INTO thought_questions(epoch,question,reason,origin,parent_id,basis,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?)", (self.epoch, question, reason, origin, parent["id"] if parent else None,
                                          dump(basis), now, now))
            if self.context.get('run_id'):
                # A research side-note is not an automatically scheduled task.
                store.conn.execute("UPDATE thought_questions SET status='paused' WHERE id=?", (cur.lastrowid,))
            row = store.conn.execute("SELECT * FROM thought_questions WHERE id=?", (cur.lastrowid,)).fetchone()
            activity.assess(store, row, args)
            event(store, "question_recorded", row["id"], {"origin": origin})
            return {"status": "recorded", "question": self.expose(row, full=True)}
        if name in {"thought_update", "thought_resolve"}:
            row = self.get(args["ref"])
            # Retain historical provenance at save time even when its body was
            # not expanded this window. Catalogue isolation is not a licence to
            # launder an already source-dependent legacy question.
            basis = merge_basis(basis, json.loads(row["basis"]))
            if name == "thought_update":
                status = args["status"]
                if status not in {"open", "waiting_user", "waiting_source", "waiting_permission",
                                  "waiting_budget", "paused", "closed"}:
                    raise ValueError("疑问状态无效")
                if self.context.get('run_id') and status == 'open' and row['status'] != 'open':
                    text(args.get('activation_reason', ''), 240)
                    if not args.get('next_step', '').strip():
                        raise ValueError('重新激活须提供独立价值activation_reason与可执行next_step')
                wait = self._wait_condition(args.get("wait_condition"))
                if status.startswith("waiting"):
                    if wait is None:
                        raise ValueError("等待状态须附等待条件（类型与原因）")
                    expected_kind = {"waiting_user": "user_feedback", "waiting_source": "source",
                                     "waiting_permission": "permission", "waiting_budget": "budget"}[status]
                    if wait.kind.value != expected_kind and not (expected_kind == "source" and wait.kind.value == "material"):
                        raise ValueError("等待条件类型与状态不一致")
                elif wait is not None:
                    raise ValueError("只有等待状态可以附等待条件")
                progress = text(args.get("progress", row["progress"]), 3000, empty=True)
                next_step = text(args.get("next_step", row["next_step"]), 1000, empty=True)
                store.conn.execute("UPDATE thought_questions SET status=?,progress=?,next_step=?,basis=?,"
                    "version=version+1,updated_at=? WHERE id=?", (status, progress, next_step, dump(basis), now, row["id"]))
                if status.startswith("waiting"):
                    store.conn.execute("INSERT INTO v2_question_waits(question_id,kind,reason,context_hint,related_refs,"
                        "after_user_turn_id,not_before_at,set_at) VALUES(?,?,?,?,?,?,?,?) "
                        "ON CONFLICT(question_id) DO UPDATE SET kind=excluded.kind,reason=excluded.reason,"
                        "context_hint=excluded.context_hint,related_refs=excluded.related_refs,"
                        "after_user_turn_id=excluded.after_user_turn_id,not_before_at=excluded.not_before_at,"
                        "set_at=excluded.set_at",
                        (row["id"], wait.kind.value, wait.reason, wait.context_hint, "[]",
                         self.context.get("user_turn_id"), wait.not_before_at, now))
                else:
                    store.conn.execute("DELETE FROM v2_question_waits WHERE question_id=?", (row["id"],))
                if args.get("family_ref"):
                    family_row = self.get(args["family_ref"])
                    store.conn.execute("INSERT INTO v2_question_families(question_id,family_id,reason,set_at) "
                        "VALUES(?,?,?,?) ON CONFLICT(question_id) DO UPDATE SET family_id=excluded.family_id,"
                        "reason=excluded.reason,set_at=excluded.set_at",
                        (row["id"], activity.family_of(store, family_row['id']), "模型提议的显式关联", now))
                activity.assess(store, row, args)
                event(store, "question_updated", row["id"], {"status": status})
            else:
                conclusion, reason = text(args["conclusion"], 4000), text(args["reason"], 1000)
                kind = args["kind"]
                if kind not in {"stance", "knowledge", "none"}:
                    raise ValueError("成果类型无效")
                remaining = text(args.get("remaining", ""), 1000, empty=True)
                next_status = args.get("next_status") or ("resolved" if not remaining else None)
                if next_status is None:
                    raise ValueError("还有剩余事项时须明确 next_status：仍可执行给 open（附下一步），"
                                     "等外部条件给 waiting_*（附等待条件），或 paused/closed")
                if next_status not in {"open", "waiting_user", "waiting_source", "waiting_permission",
                                       "waiting_budget", "paused", "closed", "resolved"}:
                    raise ValueError("后续状态无效")
                wait = self._wait_condition(args.get("wait_condition"))
                if next_status.startswith("waiting"):
                    if wait is None:
                        raise ValueError("等待状态须附等待条件（类型与原因）")
                    expected_kind = {"waiting_user": "user_feedback", "waiting_source": "source",
                                     "waiting_permission": "permission", "waiting_budget": "budget"}[next_status]
                    if wait.kind.value != expected_kind and not (expected_kind == "source" and wait.kind.value == "material"):
                        raise ValueError("等待条件类型与后续状态不一致")
                old = None
                if args.get("replaces_ref"):
                    if kind != "stance" or args["replaces_ref"] not in self.context.get("loaded_stances", set()):
                        raise ValueError("修订前先展开当前观点，并选择思辨成果")
                    old = self.get_view(args["replaces_ref"])
                from .autonomy_v2 import build
                from .autonomy_v2.models import OutcomeDraft, QuestionStatus as _QS
                ctx = execution_context(store, self.context)
                stack = build(store=store)
                draft = OutcomeDraft(question_id=row["id"], expected_question_version=row["version"],
                                     kind=kind, conclusion=conclusion, reason=reason,
                                     next_status=_QS(next_status), remaining=remaining,
                                     wait_condition=wait,
                                     uncertainty=text(args.get("uncertainty", "当前采纳的解释，尚未独立验证"), 1000),
                                     next_response=text(args.get("next_response", "待相关情境检验"), 1000),
                                     create_independent_view=bool(args.get("create_independent_view")))
                revision = stack.views.commit_outcome(ctx, draft, basis=basis, old_memory=old)
                stack.waiting.apply_outcome_wait(ctx, draft)
                event(store, "outcome_committed", row["id"],
                      {"memory_id": revision.memory_id if revision else None, "kind": kind,
                       "next_status": next_status, "partial": bool(remaining)})
            updated = store.conn.execute("SELECT * FROM thought_questions WHERE id=?", (row["id"],)).fetchone()
            return {"status": "committed", "question": self.expose(updated),
                    "notice": "任务原题保留；本次提交不自动载入旧推演全文。"}
        if name == "contact_propose":
            if self.context.get("run_id"):
                raise ValueError("自主窗口用 activity_finish.communication 一并提交交流决定")
            motive = args["motive"]
            if motive not in {"progress", "request", "share"}:
                raise ValueError("联系动机无效")
            config = settings(store)
            if not config["proactive_enabled"] or (not autonomy_policy.TEST_CONTROLS_ONLY and not config[motive]):
                raise ValueError("此类主动联系当前未启用")
            invitation_text = text(args["invitation_text"], 240)
            from .autonomy_v2 import build
            ctx = execution_context(store, self.context)
            stack = build(store=store)
            intent = self._contact_intent(args, reason=text(args["reason"], 500))
            attempt = stack.invitations.prepare(ctx, intent, invitation_text=invitation_text)
            return {"status": "prepared", "invitation_ref": f"invitation:{attempt.id}@{attempt.version}",
                    "intent_ref": f"intent:{attempt.intent_id}@{attempt.intent_version}", "sent": False,
                    "message": "邀请已准备。实际投递由宿主在允许时段与额度内完成；未接受前不展开正文。"}
        raise ValueError("未知思想命令")

    @staticmethod
    def _finish_result(receipt, communication):
        return {"status": "recorded", "run_id": receipt.run_id,
                "substantive_progress": receipt.substantive_progress,
                "remaining_family_windows": receipt.remaining_family_windows,
                "resulting_status": receipt.resulting_status.value if receipt.resulting_status else None,
                "reason": receipt.reason, "communication": communication}

    def _plan_contact(self, args):
        from .autonomy_v2 import build
        from .autonomy_v2.models import CommunicationPlan, ContactIntent, FinishDecision, ProgressKind, ProgressReport

        if self.context.get("activity_mode") != activity.CONTACT_MODE or self.context.get("user_turn_id"):
            raise ValueError("主动交流计划只在对应自主活动内使用")
        ctx = execution_context(self.store, self.context)
        stack = build(store=self.store)
        existing = stack.communication.get(ctx)
        if existing is not None and existing["decision"] != "unknown":
            return {"status": "finished", "communication": existing}
        decision = args["decision"]
        basis = current_basis(self.store, self.context)
        intent = None
        reason = text(args.get("purpose", ""), 240)
        if decision == "none":
            if set(args) != {"decision", "purpose"}:
                raise ValueError("没有想聊的话题时，只说明不联系的原因")
        elif decision in {"invite", "later"}:
            why_now = text(args.get("why_now", ""), 240)
            ref = args.get("intent_ref")
            if ref:
                intent = self.intents.get(ref)
                if intent is None or "topic" in args:
                    raise ValueError("沿用本轮可重新判断的意图时，请只引用该话题")
            else:
                topic = text(args.get("topic", ""), 80)
                if len(topic.splitlines()) != 1:
                    raise ValueError("请用一句话概括话题")
                intent = ContactIntent(id="ci_" + uuid.uuid4().hex[:16], version=1, scope=ctx.scope,
                    motive="chat", topic=topic, reason=reason, content_refs=(), state="active", basis=basis)
            reason = f"{reason}。时机：{why_now}"
        else:
            raise ValueError("交流决定无效")
        plan = CommunicationPlan(decision=decision, reason=reason, intent=intent,
            invitation_text="现在方便聊两句吗？我有个话题想和你聊。" if decision == "invite" else "")
        report = ProgressReport(decision=FinishDecision.DONE, progress_kind=ProgressKind.NONE,
                                summary="完成本轮交流判断")
        stack.activity_finish(ctx, None, report, communication=plan, basis=basis)
        return {"status": "finished", "communication": stack.communication.get(ctx)}

    def _contact_intent(self, args, *, reason):
        from dataclasses import replace
        from .autonomy_v2.models import ContactIntent
        old = self.intents.get(args.get("intent_ref"))
        if args.get("intent_ref") and old is None:
            raise ValueError("请使用本轮可重新判断的意图索引")
        motive = args.get("motive", old.motive if old else "share")
        if old is None and "motive" not in args:
            raise ValueError("新联系意图须说明 motive：progress/request/share")
        if old is not None and old.motive == "chat":
            raise ValueError("日常话题请在主动交流模式中重新判断")
        if motive not in {"progress", "request", "share"}:
            raise ValueError("联系动机无效")
        topic = text(args.get("topic", old.topic if old else ""), 80)
        row = self.get(args["ref"]) if args.get("ref") else None
        view_ref = args.get("view_ref")
        if view_ref and view_ref not in self.views:
            raise ValueError("请先检索取得真实观点引用")
        refs = ([f"question:{row['id']}@{row['version']}"] if row else []) + ([view_ref] if view_ref else [])
        if old is not None:
            if not refs:
                # Merely reconsidering an index must not rewrite its content or
                # consume its version. The window reason lives in the receipt.
                if motive != old.motive or topic != old.topic:
                    raise ValueError("修改意图内容时须提供当前问题或观点引用")
                return old
            return replace(old, motive=motive, topic=topic, reason=reason, content_refs=tuple(refs),
                           question_id=row["id"] if row else None,
                           question_version=row["version"] if row else None, view_id=view_ref)
        if not refs:
            raise ValueError("联系意图须关联问题或观点；正文在接受后载入")
        return ContactIntent(id="ci_" + uuid.uuid4().hex[:16], version=1,
            scope=execution_context(self.store, self.context).scope, motive=motive,
            topic=topic, reason=reason, content_refs=tuple(refs), state="active",
            question_id=row["id"] if row else None,
            question_version=row["version"] if row else None, view_id=view_ref)

    def _resolve_evidence(self, ref):
        """Resolve exact loaded capabilities, retaining original speakers."""
        from .autonomy_v2.models import EvidenceKind, EvidenceRef
        from .context_memory import version
        if not isinstance(ref, str) or not ref:
            raise ValueError("证据引用无效；请逐字使用工具返回的ref，说明写在summary")
        session = self.context.get("memory_session")
        if ref.startswith("history:"):
            history = session.history if session is not None else None
            if history is None or ref not in history.loaded or ref not in history.issued:
                raise ValueError("历史引用须本轮history_load成功；使用原样ref，不拼附注")
            from .context_history import _version
            identity, digest = history.issued[ref]
            rows = history.rows(user_id=identity)
            if not rows or _version(rows[0]) != digest:
                raise ValueError("已读历史版本已变化，请重新取得正文")
            refs = []
            for role in ("user", "assistant"):
                turn = self.store.conn.execute("SELECT * FROM turns WHERE id=?", (rows[0][role + "_id"],)).fetchone()
                if turn is None or turn["role"] != role:
                    raise ValueError("历史说话者已变化")
                refs.append(EvidenceRef(kind=EvidenceKind.USER_MESSAGE if role == "user" else EvidenceKind.HISTORICAL_AI_SPEECH,
                                       identity=str(turn["id"]), version=turn_version(turn), content_fingerprint=""))
            return tuple(refs)
        if ref.startswith(("memory:", "stance:")):
            if ref.startswith("memory:"):
                target = session.loaded.get(ref) if session is not None else None
            else:
                target = self.views.get(ref) if ref in self.context.get("loaded_stances", set()) else None
            if target is None:
                raise ValueError("记忆或观点须本轮真实展开；使用原样ref，不拼附注")
            mid, digest = target
            memory = self.store.get_memory(mid)
            if memory is None or version(memory) != digest:
                raise ValueError("已读记忆版本已变化")
            return (EvidenceRef(kind=EvidenceKind.SELF_VIEW if memory["kind"] == "stance" else EvidenceKind.USER_REPORT,
                                identity=str(mid), version=digest, content_fingerprint=""),)
        packets = self.store.source_packets_for([ref], scope="gui") if ref in self.context.get("packet_ids", []) else []
        if packets:
            return (EvidenceRef(kind=EvidenceKind.SOURCE_CONTENT, identity=ref,
                                version=packets[0]["version"], content_fingerprint=""),)
        raise ValueError("不支持或未载入的引用；使用本轮memory/history/stance工具的原样ref或已读packet_id")

    def _wait_condition(self, payload):
        if payload is None:
            return None
        from .autonomy_v2.models import WaitCondition, WaitKind
        if (not isinstance(payload, dict)
                or set(payload) - {"kind", "reason", "context_hint"}
                or payload.get("kind") not in {"user_feedback", "source", "permission", "budget", "material"}
                or not isinstance(payload.get("reason"), str) or not payload["reason"].strip()
                or len(payload["reason"]) > 500):
            raise ValueError("等待条件须包含类型与原因")
        hint = payload.get("context_hint", "")
        return WaitCondition(kind=WaitKind(payload["kind"]), reason=payload["reason"].strip(),
                             context_hint=hint if isinstance(hint, str) else "")


def tools(scope, *, free_interest=False, contact=False):
    if scope != "gui":
        return []
    if contact:
        return [tool for tool in sdk_group("thought")
                if tool["function"]["name"] in {"contact_plan", "thought_search", "thought_load"}]
    excluded = {"contact_plan"} if free_interest else {"contact_plan", "activity_browse"}
    return [tool for tool in sdk_group("thought") if tool["function"]["name"] not in excluded]
