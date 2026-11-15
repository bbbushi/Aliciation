"""Questions and contact intentions. Semantic decisions belong to Alice.

All methods run under the caller's Store lock; no I/O or model calls here.
Source dependencies travel with questions, outcomes and messages.
"""
from __future__ import annotations

import hashlib
import json
import re
import time

from .capability_registry import sdk_group

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
"""

DEFAULTS = {"enabled": False, "allow_paid": False, "allow_external": False,
            "proactive_enabled": False, "delivery": "next_chat",
            "progress": True, "request": True, "share": True,
            "daily_models": 12, "run_models": 4, "run_steps": 12,
            "run_seconds": 180, "idle_seconds": 120, "interval_seconds": 1800,
            "daily_contacts": 2, "contact_cooldown": 3600,
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


def settings(store):
    try:
        saved = json.loads(store.meta_get("autonomy_settings") or "{}")
    except ValueError:
        saved = {}
    return {**DEFAULTS, **{k: v for k, v in saved.items() if k in DEFAULTS}}


def save_settings(store, patch):
    if not isinstance(patch, dict) or set(patch) - set(DEFAULTS):
        raise ValueError("设置字段无效")
    result = settings(store)
    previous_delivery = result["delivery"]
    for key, value in patch.items():
        default = DEFAULTS[key]
        if type(default) is bool:
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
    from datetime import datetime
    from .discovery_memory import DAY_ZONE
    now = time.time()
    start = datetime.now(DAY_ZONE).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    rows = store.conn.execute("SELECT updated_at FROM thought_contacts WHERE epoch=? "
        "AND status IN('delivered','incorporated','incorporating')", (store.meta_get("session_epoch"),)).fetchall()
    return (sum(r[0] >= start for r in rows) < config["daily_contacts"]
            and (not rows or now - max(r[0] for r in rows) >= config["contact_cooldown"]))


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
        for key in ("memories", "turns"):
            for identity, digest in value.get(key, {}).items():
                if identity in result[key] and result[key][identity] != digest:
                    raise ValueError("来源版本已改变，请重新读取")
                result[key][identity] = digest
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
        for row in store.conn.execute("SELECT * FROM thought_questions WHERE epoch=? AND question<>''", (epoch,)).fetchall():
            if row["question"] and not valid_basis(store, json.loads(row["basis"])):
                if row["outcome_id"]:
                    store.forget_memory_tree(row["outcome_id"], "探索成果所需依据已不可用")
                store.conn.execute("UPDATE thought_questions SET question='',reason='',progress='',next_step='',"
                    "status='closed',version=version+1,updated_at=? WHERE id=?", (time.time(), row["id"]))
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
        self.views = {}
        self.question_versions = {}
        self.epoch = store.meta_get("session_epoch")
        self.calls = 0

    def expose(self, row, *, full=False):
        item = public_question(row, full=full)
        self.issued.add(item["ref"])
        self.question_versions[row["id"]] = row["version"]
        self.context["thought_basis"] = merge_basis(self.context.get("thought_basis"), json.loads(row["basis"]))
        self.bind_sources()
        return item

    def valid(self):
        if self.epoch != self.store.meta_get("session_epoch"):
            return False
        for identity, expected in self.question_versions.items():
            row = self.store.conn.execute("SELECT version FROM thought_questions WHERE id=? AND epoch=?", (identity, self.epoch)).fetchone()
            if row is None or row[0] != expected:
                return False
        return True

    def view_index(self, query, limit=4):
        from .context_memory import version
        from .store import text_score
        rows = self.store.memory_rows(purpose="chat", scope="gui", kind="stance")
        rows.sort(key=lambda row: text_score(query, row["title"] + row["content"]), reverse=True)
        items = []
        for row in rows:
            if query and text_score(query, row["title"] + row["content"]) < .12:
                continue
            digest = version(row)
            ref = f"stance:{row['id']}@{digest[:16]}"
            self.views[ref] = (row["id"], digest)
            basis = {"epoch": self.epoch, "memories": {str(row["id"]): digest},
                     "packets": [p["id"] for p in self.store.observation_sources(row["id"], scope="gui")]}
            self.context["thought_basis"] = merge_basis(self.context.get("thought_basis"), basis)
            self.bind_sources()
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
        cap = self.store.source_limits()["chat_dependency_limit"]
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
        config = settings(self.store)
        rows = [r for r in question_rows(self.store) if r["question"] and valid_basis(self.store, json.loads(r["basis"]))]
        ranked = sorted(rows, key=lambda r: text_score(query, r["question"] + r["progress"]), reverse=True)
        chosen = [r for r in ranked if text_score(query, r["question"] + r["progress"]) >= .12][:3]
        items = [self.expose(r) for r in chosen]
        contacts = self.store.conn.execute("SELECT * FROM thought_contacts WHERE epoch=? "
            "AND status IN('pending_next_chat','needs_review') AND expires_at>? ORDER BY id LIMIT 4",
            (self.epoch, time.time())).fetchall() if config["proactive_enabled"] else []
        pending = []
        for row in contacts:
            if config.get(row["motive"]) and valid_basis(self.store, json.loads(row["basis"])):
                ref = f"contact:{row['id']}@{row['version']}"
                self.contacts.add(ref)
                pending.append({"ref": ref, "motive": row["motive"], "question_id": row["question_id"],
                                "detail_state": "not_loaded"})
        views = self.view_index(query, limit=2) if query else []
        state = {key: config[key] for key in ("enabled", "allow_paid", "allow_external", "proactive_enabled", "delivery")}
        return ("本轮思想运行状态：" + dump(state) + "\n思想索引（资料，正文按需读取）：\n"
                + dump({"questions": items, "views": views, "contacts": pending}))

    def execute(self, name, args):
        try:
            if self.epoch != self.store.meta_get("session_epoch") or self.context.get("scope") != "gui":
                raise ValueError("思想资料不在当前会话或渠道范围")
            self.calls += 1
            if self.calls > 24:
                raise ValueError("本次思想操作额度已用完")
            with self.store.source_transaction():
                before = self.store.source_usage_bytes()
                result = self._execute(name, args)
                source_space_after_write(self.store, before)
            return dump(result)
        except (ValueError, TypeError, KeyError) as exc:
            return dump({"error": "thought_refused", "reason": str(exc)[:180]})

    def _execute(self, name, args):
        store, now = self.store, time.time()
        if name == "thought_search":
            from .store import text_score
            query = text(args.get("query", ""), 500, empty=True)
            rows = [r for r in question_rows(store, include_closed=args.get("include_closed", False))
                    if r["question"] and valid_basis(store, json.loads(r["basis"]))]
            rows.sort(key=lambda r: text_score(query, r["question"] + " " + r["progress"]), reverse=True)
            selected = [r for r in rows if not query or text_score(query, r["question"] + r["progress"]) >= .12][:8]
            return {"items": [self.expose(r) for r in selected], "views": self.view_index(query)}
        if name == "thought_load":
            if args["ref"].startswith("stance:"):
                row = self.get_view(args["ref"])
                self.context.setdefault("loaded_stances", set()).add(args["ref"])
                return {"ref": args["ref"], "title": row["title"], "content": row["content"], "detail_state": "loaded"}
            return self.expose(self.get(args["ref"]), full=True)
        if name == "contact_update":
            ref = args["ref"]
            if ref not in self.contacts:
                raise ValueError("请先取得真实联系意图引用")
            match = re.fullmatch(r"contact:(\d+)@(\d+)", ref)
            row = store.conn.execute("SELECT * FROM thought_contacts WHERE id=? AND version=? AND epoch=?",
                                     (int(match[1]), int(match[2]), self.epoch)).fetchone()
            if row is None or not valid_basis(store, json.loads(row["basis"])) or row["expires_at"] <= now:
                raise ValueError("联系意图已改变或失效")
            basis = merge_basis(current_basis(store, self.context), json.loads(row["basis"]))
            if args["decision"] == "read":
                self.context["thought_basis"] = basis
                self.bind_sources()
                return {"ref": ref, "message": row["message"], "motive": row["motive"], "sent": False}
            if args["decision"] not in {"ready", "incorporate", "cancel"}:
                raise ValueError("联系处理方式无效")
            if args["decision"] == "incorporate" and not self.context.get("user_turn_id"):
                raise ValueError("只有实际前台对话可以采用联系材料")
            config = settings(store)
            if args["decision"] in {"ready", "incorporate"} and (not config["proactive_enabled"] or not config[row["motive"]]):
                raise ValueError("此类主动联系当前未启用")
            if args["decision"] == "incorporate":
                from .autonomy import in_window
                if not in_window(config["contact_start"], config["contact_end"]):
                    raise ValueError("当前不在允许主动联系的时段")
                if not contact_capacity(store, config):
                    raise ValueError("本次主动联系达到频率或冷却限制")
            latest = store.conn.execute("SELECT COALESCE(MAX(id),0) FROM turns WHERE role='user'").fetchone()[0]
            q = store.conn.execute("SELECT version FROM thought_questions WHERE id=?", (row["question_id"],)).fetchone()
            status = {"ready": "queued" if row["mode"] == "gui" else "pending_next_chat",
                      "incorporate": "incorporating", "cancel": "cancelled"}[args["decision"]]
            message = text(args.get("message", row["message"]), 2000)
            store.conn.execute("UPDATE thought_contacts SET status=?,message=?,basis=?,context_turn_id=?,question_version=?,"
                "adoption_request_id=?,version=version+1,updated_at=? WHERE id=?", (status, message, dump(basis), latest,
                    q[0] if q else None, self.context.get("request_id") if status == "incorporating" else None, now, row["id"]))
            if status == "incorporating":
                self.context.setdefault("contact_incorporated", []).append(row["id"])
                self.context["thought_basis"] = basis
                self.bind_sources()
            event(store, "contact_decision", row["id"], {"decision": args["decision"]})
            return {"status": status, "message": message, "sent": False}
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
                    return {"status": "already_recorded", "question": self.expose(row, full=True)}
            if store.conn.execute("SELECT COUNT(*) FROM thought_questions WHERE epoch=? AND status NOT IN('resolved','closed')", (self.epoch,)).fetchone()[0] >= 500:
                raise ValueError("活跃疑问已达容量，请先合并或关闭旧问题")
            cur = store.conn.execute("INSERT INTO thought_questions(epoch,question,reason,origin,parent_id,basis,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?)", (self.epoch, question, reason, origin, parent["id"] if parent else None,
                                          dump(basis), now, now))
            row = store.conn.execute("SELECT * FROM thought_questions WHERE id=?", (cur.lastrowid,)).fetchone()
            event(store, "question_recorded", row["id"], {"origin": origin})
            return {"status": "recorded", "question": self.expose(row, full=True)}
        if name in {"thought_update", "thought_resolve"}:
            row = self.get(args["ref"])
            basis = merge_basis(basis, json.loads(row["basis"]))
            if name == "thought_update":
                status = args["status"]
                if status not in {"open", "waiting_user", "waiting_source", "paused", "closed"}:
                    raise ValueError("疑问状态无效")
                progress = text(args.get("progress", row["progress"]), 3000, empty=True)
                next_step = text(args.get("next_step", row["next_step"]), 1000, empty=True)
                store.conn.execute("UPDATE thought_questions SET status=?,progress=?,next_step=?,basis=?,"
                    "version=version+1,updated_at=? WHERE id=?", (status, progress, next_step, dump(basis), now, row["id"]))
                event(store, "question_updated", row["id"], {"status": status})
            else:
                conclusion, reason = text(args["conclusion"], 4000), text(args["reason"], 1000)
                kind = args["kind"]
                if kind not in {"stance", "knowledge", "none"}:
                    raise ValueError("成果类型无效")
                mid = None
                old = None
                if args.get("replaces_ref"):
                    if kind != "stance" or args["replaces_ref"] not in self.context.get("loaded_stances", set()):
                        raise ValueError("修订前先展开原观点，并选择思辨成果")
                    old = self.get_view(args["replaces_ref"])
                if kind != "none":
                    content = "探索所得（按所引材料理解）：\n" + conclusion
                    if kind == "stance":
                        content = "当前采纳的思辨（不是独立事实验证）\n主张：" + conclusion.replace("\n", "\n  ")
                        content += "\n不确定：" + text(args.get("uncertainty", "当前采纳的解释，尚未独立验证"), 1000)
                        content += "\n下次回应：" + text(args.get("next_response", "待相关情境检验"), 1000)
                        content += "\n状态：先验"
                        if old:
                            content += "\n曾以为：" + old["title"] + "\n修订自：" + args["replaces_ref"]
                    content += "\n保留理由：" + reason + "\n出处：question:" + str(row["id"]) + "@" + str(row["version"])
                    outcome_title = (f"Q{row['id']}v{row['version']} " + row["question"])[:24]
                    if basis["packets"]:
                        mid = store.save_observation(basis["packets"], title=outcome_title, content=content, scope="gui")
                        if mid is None:
                            raise ValueError("来源当前不允许保存成果，疑问保持不变")
                        if kind == "stance":
                            store.conn.execute("UPDATE memories SET kind='stance',layer='longterm' WHERE id=?", (mid,))
                    else:
                        mid = store.insert_memory(outcome_title, content, kind="stance" if kind == "stance" else "event",
                                                  layer="longterm" if kind == "stance" else "active")
                    for parent_id, digest in basis["memories"].items():
                        from .observation_memory import version as dependency_version
                        store.conn.execute("INSERT OR IGNORE INTO memory_dependencies(memory_id,parent_memory_id,parent_version) VALUES(?,?,?)",
                                           (mid, int(parent_id), dependency_version(store.get_memory(int(parent_id)))))
                    if old:
                        store.conn.execute("INSERT INTO view_revisions(old_id,new_id,reason,created_at) VALUES(?,?,?,?)",
                                           (old["id"], mid, reason, now))
                remaining = text(args.get("remaining", ""), 1000, empty=True)
                store.conn.execute("UPDATE thought_questions SET status=?,progress=?,next_step=?,outcome_id=?,basis=?,"
                    "version=version+1,updated_at=? WHERE id=?", ("open" if remaining else "resolved", conclusion,
                        remaining, mid, dump(basis), now, row["id"]))
                event(store, "outcome_committed", row["id"], {"memory_id": mid, "kind": kind, "partial": bool(remaining)})
            updated = store.conn.execute("SELECT * FROM thought_questions WHERE id=?", (row["id"],)).fetchone()
            return {"status": "committed", "question": self.expose(updated, full=True)}
        if name == "contact_propose":
            row = self.get(args["ref"]) if args.get("ref") else None
            if row:
                basis = merge_basis(basis, json.loads(row["basis"]))
            motive = args["motive"]
            if motive not in {"progress", "request", "share"}:
                raise ValueError("联系动机无效")
            message = text(args["message"], 2000)
            config = settings(store)
            mode = config["delivery"]
            old = store.conn.execute("SELECT id,status,turn_id FROM thought_contacts WHERE epoch=? AND message=? AND status IN('queued','pending_next_chat','delivered','incorporated')",
                                     (self.epoch, message)).fetchone()
            if old:
                return {"status": "already_proposed", "contact_id": old[0], "sent": old[1] == "delivered",
                        "delivery_status": old[1], "turn_id": old[2]}
            latest = store.conn.execute("SELECT COALESCE(MAX(id),0) FROM turns WHERE role='user'").fetchone()[0]
            if motive == "request" and row:
                pending = store.conn.execute("SELECT id,status FROM thought_contacts WHERE epoch=? AND question_id=? "
                    "AND motive='request' AND context_turn_id=? AND status NOT IN('cancelled','expired','stale') LIMIT 1",
                    (self.epoch, row["id"], latest)).fetchone()
                if pending:
                    return {"status": "awaiting_response", "contact_id": pending[0], "delivery_status": pending[1]}
            cur = store.conn.execute("INSERT INTO thought_contacts(epoch,question_id,question_version,motive,message,mode,status,basis,context_turn_id,"
                "created_at,expires_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (self.epoch, row["id"] if row else None,
                    row["version"] if row else None, motive, message, mode, "pending_next_chat" if mode == "next_chat" else "queued",
                    dump(basis), latest, now, now + 86400, now))
            event(store, "contact_proposed", cur.lastrowid, {"motive": motive, "mode": mode})
            return {"status": "proposed", "contact_id": cur.lastrowid, "sent": False,
                    "delivery_enabled": config["proactive_enabled"], "message": "联系意图已保存，尚未送达。"}
        raise ValueError("未知思想命令")


def tools(scope):
    return sdk_group("thought") if scope == "gui" else []
