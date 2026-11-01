"""Worker 任务账本：任务/尝试/事件/产物/预算的持久化与状态机（契约 §二/§五）。

设计依据：docs/设计文档/协作者调度-协议契约-v1.md；开发方案 v1 §7。
本模块只做账——不启动进程、不碰文件系统、不做网络。所有方法假设调用方已持有
service 单写者锁（与 SourceStoreMixin 同约定）：短事务、锁内不等待进程或网络。
状态迁移在这里强制，非法迁移与重复键抛 WorkerLedgerError（code 供上层映射为
Alice 侧失败话术：没装/未启用/没办成 ≠ 没找到）。
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid

WORKER_SCHEMA = """
CREATE TABLE IF NOT EXISTS worker_registrations (
  name TEXT PRIMARY KEY,
  kind TEXT NOT NULL CHECK (kind IN ('agent','tool','channel')),
  adapter_id TEXT NOT NULL,
  adapter_version TEXT NOT NULL DEFAULT '',
  protocol_version INTEGER NOT NULL DEFAULT 1,
  task_schema TEXT NOT NULL DEFAULT 'artifact_task_v1',
  result_schema TEXT NOT NULL DEFAULT 'artifact_result_v1',
  provides TEXT NOT NULL DEFAULT '',
  use_when TEXT NOT NULL DEFAULT '',
  not_for TEXT NOT NULL DEFAULT '',
  permission_profile TEXT NOT NULL DEFAULT '',
  requirements TEXT NOT NULL DEFAULT '{}',
  budget_policy TEXT NOT NULL DEFAULT '{}',
  channels TEXT NOT NULL DEFAULT '["gui"]',
  enabled INTEGER NOT NULL DEFAULT 0,
  verified_at REAL,
  verify_note TEXT NOT NULL DEFAULT '',
  revision INTEGER NOT NULL DEFAULT 1,
  updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS worker_inputs (
  input_id TEXT PRIMARY KEY,
  epoch TEXT NOT NULL,
  scope TEXT NOT NULL,
  sha256 TEXT NOT NULL,
  size INTEGER NOT NULL,
  media TEXT NOT NULL DEFAULT '',
  display_name TEXT NOT NULL DEFAULT '',
  rel_path TEXT NOT NULL,
  permit TEXT NOT NULL DEFAULT 'granted',
  expires_at REAL NOT NULL,
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS worker_tasks (
  task_id TEXT PRIMARY KEY,
  epoch TEXT NOT NULL,
  scope TEXT NOT NULL,
  name TEXT NOT NULL,
  brief TEXT NOT NULL,
  input_ids TEXT NOT NULL DEFAULT '[]',
  requested_outputs TEXT NOT NULL DEFAULT '[]',
  originating_request_id TEXT NOT NULL,
  tool_call_id TEXT NOT NULL,
  params_digest TEXT NOT NULL,
  policy_revision INTEGER NOT NULL DEFAULT 1,
  exec_state TEXT NOT NULL DEFAULT 'queued' CHECK (exec_state IN (
    'queued','starting','running','collecting','succeeded',
    'failed','cancelled','interrupted','unknown')),
  cancel_intent INTEGER NOT NULL DEFAULT 0,
  draining INTEGER NOT NULL DEFAULT 0,
  validation_state TEXT NOT NULL DEFAULT 'pending' CHECK (validation_state IN ('pending','accepted','rejected')),
  review_state TEXT NOT NULL DEFAULT 'unreviewed' CHECK (review_state IN ('unreviewed','accepted','rejected')),
  delivery_state TEXT NOT NULL DEFAULT 'pending' CHECK (delivery_state IN ('pending','available','presented','withdrawn')),
  resource_group TEXT NOT NULL DEFAULT '',
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  UNIQUE (epoch, originating_request_id, tool_call_id)
);
CREATE INDEX IF NOT EXISTS idx_worker_tasks_epoch ON worker_tasks (epoch, scope, exec_state);
CREATE TABLE IF NOT EXISTS worker_attempts (
  attempt_id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL,
  seq INTEGER NOT NULL,
  go_sent INTEGER NOT NULL DEFAULT 0,
  exec_state TEXT NOT NULL DEFAULT 'starting',
  reason_code TEXT NOT NULL DEFAULT '',
  message TEXT NOT NULL DEFAULT '',
  started_at REAL NOT NULL,
  finished_at REAL
);
CREATE INDEX IF NOT EXISTS idx_worker_attempts_task ON worker_attempts (task_id, seq);
CREATE TABLE IF NOT EXISTS worker_events (
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id TEXT NOT NULL,
  attempt_id TEXT NOT NULL DEFAULT '',
  type TEXT NOT NULL,
  payload TEXT NOT NULL DEFAULT '{}',
  dedup_key TEXT UNIQUE,
  ts REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_worker_events_task ON worker_events (task_id, seq);
CREATE TABLE IF NOT EXISTS worker_artifacts (
  artifact_id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL,
  attempt_id TEXT NOT NULL DEFAULT '',
  rel_path TEXT NOT NULL,
  sha256 TEXT NOT NULL,
  size INTEGER NOT NULL,
  media TEXT NOT NULL DEFAULT '',
  delivery_state TEXT NOT NULL DEFAULT 'pending' CHECK (delivery_state IN ('pending','available','presented','withdrawn')),
  expires_at REAL,
  created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_worker_artifacts_task ON worker_artifacts (task_id);
CREATE TABLE IF NOT EXISTS worker_budget_entries (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id TEXT NOT NULL DEFAULT '',
  attempt_id TEXT NOT NULL DEFAULT '',
  kind TEXT NOT NULL CHECK (kind IN ('reserved','observed','settled','unknown')),
  unit TEXT NOT NULL CHECK (unit IN ('token','calls','currency')),
  amount REAL NOT NULL,
  resource_group TEXT NOT NULL DEFAULT '',
  idem_key TEXT UNIQUE,
  ts REAL NOT NULL
);
"""

EXEC_STATES = ("queued", "starting", "running", "collecting", "succeeded",
               "failed", "cancelled", "interrupted", "unknown")
TERMINAL_STATES = {"succeeded", "failed", "cancelled", "interrupted", "unknown"}
# 状态机：collecting 阶段不允许 cancelled——取消与结果竞争按宿主终态提交顺序裁决，
# 有效产物已发布时走 succeeded + 用户撤回交付（契约 §二）。
_ALLOWED_TRANSITIONS = {
    "queued": {"starting", "failed", "cancelled"},
    "starting": {"running", "failed", "cancelled", "interrupted", "unknown"},
    "running": {"collecting", "failed", "cancelled", "interrupted", "unknown"},
    "collecting": {"succeeded", "failed", "interrupted", "unknown"},
    "succeeded": set(), "failed": set(), "cancelled": set(),
    "interrupted": set(), "unknown": set(),
}


class WorkerLedgerError(Exception):
    """账本层拒绝。code 供上层映射为对 Alice 的失败话术（契约 §三）。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


def _now() -> float:
    return time.time()


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:20]}"


def _digest(payload: dict) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _row(row) -> dict | None:
    return {k: row[k] for k in row.keys()} if row is not None else None


class WorkerStoreMixin:
    """账本方法集合。由 Store 组合；调用方持单写者锁。"""

    # ---------- 注册项（安装描述 + 用户政策 + 人工验收事实） ----------

    def worker_register(self, entry: dict) -> dict:
        """注册/更新安装描述。策略位（enabled）与验收位（verified_at）不被覆盖；revision 递增。"""
        name = entry.get("name")
        if not isinstance(name, str) or not name.strip():
            raise WorkerLedgerError("invalid_registration", "注册项缺少有效 name")
        kind = entry.get("kind")
        if kind not in ("agent", "tool", "channel"):
            raise WorkerLedgerError("invalid_registration", "kind 必须是 agent/tool/channel")
        adapter_id = entry.get("adapter_id")
        if not isinstance(adapter_id, str) or not adapter_id.strip():
            raise WorkerLedgerError("invalid_registration", "注册项缺少 adapter_id")
        now = _now()
        old = _row(self.conn.execute("SELECT * FROM worker_registrations WHERE name=?", (name,)).fetchone())
        row = {
            "kind": kind, "adapter_id": adapter_id,
            "adapter_version": str(entry.get("adapter_version", "")),
            "protocol_version": 1,
            "task_schema": str(entry.get("task_schema", "artifact_task_v1")),
            "result_schema": str(entry.get("result_schema", "artifact_result_v1")),
            "provides": str(entry.get("provides", "")),
            "use_when": str(entry.get("use_when", "")),
            "not_for": str(entry.get("not_for", "")),
            "permission_profile": str(entry.get("permission_profile", "")),
            "requirements": json.dumps(entry.get("requirements", {}), ensure_ascii=False),
            "budget_policy": json.dumps(entry.get("budget_policy", {}), ensure_ascii=False),
            "channels": json.dumps(entry.get("channels", ["gui"]), ensure_ascii=False),
            "updated_at": now,
        }
        if old is None:
            self.conn.execute(
                "INSERT INTO worker_registrations (name, revision, updated_at, kind, adapter_id,"
                " adapter_version, protocol_version, task_schema, result_schema, provides, use_when,"
                " not_for, permission_profile, requirements, budget_policy, channels)"
                " VALUES (?,1,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (name, now, row["kind"], row["adapter_id"], row["adapter_version"],
                 row["protocol_version"], row["task_schema"], row["result_schema"],
                 row["provides"], row["use_when"], row["not_for"], row["permission_profile"],
                 row["requirements"], row["budget_policy"], row["channels"]))
        else:
            self.conn.execute(
                "UPDATE worker_registrations SET kind=?, adapter_id=?, adapter_version=?,"
                " protocol_version=?, task_schema=?, result_schema=?, provides=?, use_when=?,"
                " not_for=?, permission_profile=?, requirements=?, budget_policy=?, channels=?,"
                " revision=revision+1, updated_at=? WHERE name=?",
                (row["kind"], row["adapter_id"], row["adapter_version"], row["protocol_version"],
                 row["task_schema"], row["result_schema"], row["provides"], row["use_when"],
                 row["not_for"], row["permission_profile"], row["requirements"],
                 row["budget_policy"], row["channels"], now, name))
            # 注册内容变化使旧验收失效：重新认可前按未验收处理（契约 §三/§6.3）
            self.conn.execute("UPDATE worker_registrations SET verified_at=NULL WHERE name=?", (name,))
        self.conn.commit()
        return self.worker_entry(name)

    def worker_set_enabled(self, name: str, enabled: bool) -> None:
        """用户政策位。注册不存在 → unknown_worker。"""
        cur = self.conn.execute("UPDATE worker_registrations SET enabled=?, updated_at=? WHERE name=?",
                                (1 if enabled else 0, _now(), name))
        if cur.rowcount == 0:
            raise WorkerLedgerError("unknown_worker", f"名册中没有 {name}")
        self.conn.commit()

    def worker_mark_verified(self, name: str, ok: bool, note: str = "") -> None:
        """人工注册验收结论（注册试跑由人主持）。不通过 = 保持不可派工。"""
        if ok:
            self.conn.execute("UPDATE worker_registrations SET verified_at=?, verify_note=? WHERE name=?",
                              (_now(), note, name))
        else:
            self.conn.execute("UPDATE worker_registrations SET verified_at=NULL, verify_note=? WHERE name=?",
                              (note, name))
        self.conn.commit()

    def worker_entry(self, name: str) -> dict | None:
        row = _row(self.conn.execute("SELECT * FROM worker_registrations WHERE name=?", (name,)).fetchone())
        if row:
            row["requirements"] = json.loads(row["requirements"] or "{}")
            row["budget_policy"] = json.loads(row["budget_policy"] or "{}")
            row["channels"] = json.loads(row["channels"] or '["gui"]')
        return row

    def worker_entries(self) -> list[dict]:
        rows = self.conn.execute("SELECT name FROM worker_registrations ORDER BY name").fetchall()
        return [self.worker_entry(r["name"]) for r in rows]

    # ---------- 输入资格 ----------

    def worker_input_register(self, *, epoch: str, scope: str, sha256: str, size: int,
                              media: str, display_name: str, rel_path: str,
                              ttl_days: float = 7.0) -> str:
        input_id, now = _id("win"), _now()
        self.conn.execute(
            "INSERT INTO worker_inputs (input_id, epoch, scope, sha256, size, media, display_name,"
            " rel_path, expires_at, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (input_id, epoch, scope, sha256, int(size), media, display_name[:200],
             rel_path, now + ttl_days * 86400, now))
        self.conn.commit()
        return input_id

    def worker_input_get(self, input_id: str, *, epoch: str, scope: str, now: float | None = None) -> dict | None:
        """取输入资格：代际/渠道/有效期任一不符都视为不存在（不自动跨代际复用）。"""
        row = _row(self.conn.execute("SELECT * FROM worker_inputs WHERE input_id=?", (input_id,)).fetchone())
        now = now if now is not None else _now()
        if row and row["epoch"] == epoch and row["scope"] == scope and row["expires_at"] > now \
                and row["permit"] == "granted":
            return row
        return None

    # ---------- 任务（幂等创建 + 状态机） ----------

    def worker_task_create(self, *, epoch: str, scope: str, name: str, brief: str,
                           input_ids: list[str], requested_outputs: list[str],
                           originating_request_id: str, tool_call_id: str,
                           policy_revision: int = 1, resource_group: str = "") -> dict:
        """幂等创建：同 (epoch, request, tool_call) 且参数一致 → 返回原任务；
        同键不同参数 → request_conflict（重放不重复派工，改需求须显式取消重建）。"""
        digest = _digest({"name": name, "brief": brief, "input_ids": sorted(input_ids),
                          "requested_outputs": sorted(requested_outputs)})
        old = _row(self.conn.execute(
            "SELECT * FROM worker_tasks WHERE epoch=? AND originating_request_id=? AND tool_call_id=?",
            (epoch, originating_request_id, tool_call_id)).fetchone())
        if old is not None:
            if old["params_digest"] == digest and old["name"] == name:
                return {"task": old, "reused": True}
            raise WorkerLedgerError("request_conflict", "同一请求键已绑定不同参数")
        task_id, now = _id("wtk"), _now()
        self.conn.execute(
            "INSERT INTO worker_tasks (task_id, epoch, scope, name, brief, input_ids, requested_outputs,"
            " originating_request_id, tool_call_id, params_digest, policy_revision, resource_group,"
            " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (task_id, epoch, scope, name, brief[:8000], json.dumps(input_ids),
             json.dumps(requested_outputs), originating_request_id, tool_call_id,
             digest, policy_revision, resource_group, now, now))
        self.conn.commit()
        return {"task": self.worker_task_get(task_id), "reused": False}

    def worker_task_get(self, task_id: str, *, epoch: str | None = None, scope: str | None = None) -> dict | None:
        row = _row(self.conn.execute("SELECT * FROM worker_tasks WHERE task_id=?", (task_id,)).fetchone())
        if row is None:
            return None
        if epoch is not None and row["epoch"] != epoch:
            return None
        if scope is not None and row["scope"] != scope:
            return None
        row["input_ids"] = json.loads(row["input_ids"])
        row["requested_outputs"] = json.loads(row["requested_outputs"])
        return row

    def worker_task_transition(self, task_id: str, new_state: str, *, expect: str | None = None) -> dict:
        """强制状态机迁移。expect 指定前置态（乐观并发）；终态不可再迁移。"""
        row = self.worker_task_get(task_id)
        if row is None:
            raise WorkerLedgerError("unknown_task", f"任务不存在：{task_id}")
        cur = row["exec_state"]
        if expect is not None and cur != expect:
            raise WorkerLedgerError("state_conflict", f"期望 {expect}，实际 {cur}")
        if cur in TERMINAL_STATES:
            raise WorkerLedgerError("state_conflict", f"任务已是终态 {cur}")
        if new_state not in _ALLOWED_TRANSITIONS[cur]:
            raise WorkerLedgerError("state_conflict", f"不允许 {cur} → {new_state}")
        self.conn.execute("UPDATE worker_tasks SET exec_state=?, updated_at=? WHERE task_id=?",
                          (new_state, _now(), task_id))
        self.conn.commit()
        return self.worker_task_get(task_id)

    def worker_task_cancel_request(self, task_id: str) -> dict:
        """置取消意图。意图 ≠ 已取消；确认回收后由调用方迁移到 cancelled。"""
        row = self.worker_task_get(task_id)
        if row is None:
            raise WorkerLedgerError("unknown_task", f"任务不存在：{task_id}")
        self.conn.execute("UPDATE worker_tasks SET cancel_intent=1, updated_at=? WHERE task_id=?",
                          (_now(), task_id))
        self.conn.commit()
        return self.worker_task_get(task_id)

    def worker_task_set_draining(self, task_id: str, draining: bool) -> None:
        self.conn.execute("UPDATE worker_tasks SET draining=?, updated_at=? WHERE task_id=?",
                          (1 if draining else 0, _now(), task_id))
        self.conn.commit()

    def worker_task_set_validation(self, task_id: str, state: str) -> None:
        if state not in ("pending", "accepted", "rejected"):
            raise WorkerLedgerError("invalid_state", "validation_state 非法")
        self.conn.execute("UPDATE worker_tasks SET validation_state=?, updated_at=? WHERE task_id=?",
                          (state, _now(), task_id))
        self.conn.commit()

    def worker_task_set_review(self, task_id: str, state: str) -> None:
        if state not in ("unreviewed", "accepted", "rejected"):
            raise WorkerLedgerError("invalid_state", "review_state 非法")
        self.conn.execute("UPDATE worker_tasks SET review_state=?, updated_at=? WHERE task_id=?",
                          (state, _now(), task_id))
        self.conn.commit()

    def worker_task_set_delivery(self, task_id: str, state: str) -> None:
        if state not in ("pending", "available", "presented", "withdrawn"):
            raise WorkerLedgerError("invalid_state", "delivery_state 非法")
        self.conn.execute("UPDATE worker_tasks SET delivery_state=?, updated_at=? WHERE task_id=?",
                          (state, _now(), task_id))
        self.conn.commit()

    def worker_task_list(self, *, epoch: str, scope: str, states: list[str] | None = None) -> list[dict]:
        rows = self.conn.execute(
            "SELECT task_id FROM worker_tasks WHERE epoch=? AND scope=? ORDER BY created_at DESC",
            (epoch, scope)).fetchall()
        out = []
        for r in rows:
            t = self.worker_task_get(r["task_id"])
            if t and (states is None or t["exec_state"] in states):
                out.append(t)
        return out

    # ---------- 尝试 ----------

    def worker_attempt_create(self, task_id: str) -> dict:
        row = self.worker_task_get(task_id)
        if row is None:
            raise WorkerLedgerError("unknown_task", f"任务不存在：{task_id}")
        self.worker_task_transition(task_id, "starting", expect="queued")
        seq = self.conn.execute("SELECT COUNT(*) c FROM worker_attempts WHERE task_id=?", (task_id,)).fetchone()["c"]
        attempt_id, now = _id("wat"), _now()
        self.conn.execute(
            "INSERT INTO worker_attempts (attempt_id, task_id, seq, started_at) VALUES (?,?,?,?)",
            (attempt_id, task_id, seq + 1, now))
        self.conn.commit()
        return _row(self.conn.execute("SELECT * FROM worker_attempts WHERE attempt_id=?", (attempt_id,)).fetchone())

    def worker_attempt_set_go(self, attempt_id: str) -> None:
        cur = self.conn.execute("UPDATE worker_attempts SET go_sent=1 WHERE attempt_id=? AND go_sent=0",
                                (attempt_id,))
        if cur.rowcount == 0:
            raise WorkerLedgerError("state_conflict", "GO 只发一次")
        self.conn.commit()

    def worker_attempt_running(self, attempt_id: str) -> None:
        row = _row(self.conn.execute("SELECT * FROM worker_attempts WHERE attempt_id=?", (attempt_id,)).fetchone())
        if row is None:
            raise WorkerLedgerError("unknown_attempt", f"尝试不存在：{attempt_id}")
        self.worker_task_transition(row["task_id"], "running", expect="starting")

    def worker_attempt_finish(self, attempt_id: str, state: str, reason_code: str = "",
                              message: str = "") -> dict:
        """终态提交（结果/取消/中断的唯一入口）。任务侧迁移失败时以尝试记录为准，不掩盖。"""
        row = _row(self.conn.execute("SELECT * FROM worker_attempts WHERE attempt_id=?", (attempt_id,)).fetchone())
        if row is None:
            raise WorkerLedgerError("unknown_attempt", f"尝试不存在：{attempt_id}")
        if state not in TERMINAL_STATES:
            raise WorkerLedgerError("invalid_state", f"{state} 不是终态")
        if row["finished_at"] is not None:
            raise WorkerLedgerError("state_conflict", "尝试已有终态")
        self.conn.execute(
            "UPDATE worker_attempts SET exec_state=?, reason_code=?, message=?, finished_at=?"
            " WHERE attempt_id=?", (state, reason_code[:120], message[:2000], _now(), attempt_id))
        self.conn.commit()
        task_state = self.worker_task_get(row["task_id"])["exec_state"]
        if state == "succeeded" and task_state == "running":
            self.worker_task_transition(row["task_id"], "collecting")  # 成功必经收取段
        self.worker_task_transition(row["task_id"], state)
        return _row(self.conn.execute("SELECT * FROM worker_attempts WHERE attempt_id=?", (attempt_id,)).fetchone())

    # ---------- 事件（有界、去重，不保存终端日志） ----------

    def worker_event_append(self, task_id: str, attempt_id: str, type_: str,
                            payload: dict, dedup_key: str | None = None) -> int | None:
        try:
            cur = self.conn.execute(
                "INSERT INTO worker_events (task_id, attempt_id, type, payload, dedup_key, ts)"
                " VALUES (?,?,?,?,?,?)",
                (task_id, attempt_id, type_, json.dumps(payload, ensure_ascii=False)[:4000],
                 dedup_key, _now()))
            self.conn.commit()
            return cur.lastrowid
        except Exception:
            self.conn.rollback()
            return None  # 重复 dedup_key：幂等丢弃

    def worker_events_since(self, task_id: str, cursor: int = 0, limit: int = 100) -> list[dict]:
        rows = self.conn.execute(
            "SELECT seq, attempt_id, type, payload, ts FROM worker_events"
            " WHERE task_id=? AND seq>? ORDER BY seq LIMIT ?", (task_id, cursor, limit)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["payload"] = json.loads(d["payload"] or "{}")
            out.append(d)
        return out

    # ---------- 产物 ----------

    def worker_artifact_register(self, task_id: str, attempt_id: str, rel_path: str,
                                 sha256: str, size: int, media: str = "",
                                 ttl_days: float = 7.0) -> str:
        artifact_id, now = _id("war"), _now()
        self.conn.execute(
            "INSERT INTO worker_artifacts (artifact_id, task_id, attempt_id, rel_path, sha256,"
            " size, media, expires_at, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (artifact_id, task_id, attempt_id, rel_path, sha256, int(size), media,
             now + ttl_days * 86400, now))
        self.conn.commit()
        return artifact_id

    def worker_artifacts_for(self, task_id: str, *, epoch: str | None = None, now: float | None = None) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM worker_artifacts WHERE task_id=? ORDER BY created_at", (task_id,)).fetchall()
        now = now if now is not None else _now()
        out = []
        for r in rows:
            d = dict(r)
            task = self.worker_task_get(task_id, epoch=epoch) if epoch is not None else dict(
                self.conn.execute("SELECT epoch FROM worker_tasks WHERE task_id=?", (task_id,)).fetchone() or {})
            if epoch is not None and (not task or task.get("epoch") != epoch):
                continue  # 旧代际产物对新代际不可见（契约 §四）
            if d["expires_at"] is not None and d["expires_at"] <= now:
                continue
            out.append(d)
        return out

    def worker_artifact_set_delivery(self, artifact_id: str, state: str) -> None:
        if state not in ("pending", "available", "presented", "withdrawn"):
            raise WorkerLedgerError("invalid_state", "delivery_state 非法")
        self.conn.execute("UPDATE worker_artifacts SET delivery_state=? WHERE artifact_id=?",
                          (state, artifact_id))
        self.conn.commit()

    # ---------- 预算（最小实现：账目分列，计量能力随 D0 填） ----------

    def worker_budget_entry(self, *, kind: str, unit: str, amount: float,
                            task_id: str = "", attempt_id: str = "",
                            resource_group: str = "", idem_key: str | None = None) -> int | None:
        if kind not in ("reserved", "observed", "settled", "unknown"):
            raise WorkerLedgerError("invalid_state", "预算条目类型非法")
        try:
            cur = self.conn.execute(
                "INSERT INTO worker_budget_entries (task_id, attempt_id, kind, unit, amount,"
                " resource_group, idem_key, ts) VALUES (?,?,?,?,?,?,?,?)",
                (task_id, attempt_id, kind, unit, float(amount), resource_group, idem_key, _now()))
            self.conn.commit()
            return cur.lastrowid
        except Exception:
            self.conn.rollback()
            return None  # 幂等键重复：丢弃

    def worker_budget_sum(self, *, resource_group: str, unit: str, day: str) -> float:
        """当日某资源组某单位的 settled+reserved 合计（unknown 单列，不计零也不计入上限）。"""
        row = self.conn.execute(
            "SELECT COALESCE(SUM(amount),0) s FROM worker_budget_entries"
            " WHERE resource_group=? AND unit=? AND kind IN ('reserved','settled')"
            " AND ts>=? AND ts<?",
            (resource_group, unit, _day_start(day), _day_start(day) + 86400)).fetchone()
        return float(row["s"])


def _day_start(day: str) -> float:
    import datetime
    y, m, d = (int(x) for x in day.split("-"))
    return datetime.datetime(y, m, d, tzinfo=datetime.timezone.utc).timestamp()
