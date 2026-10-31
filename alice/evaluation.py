"""A1/R1 evaluation repository and strict P1 material validation.

Every repository call is owned by ChatService and runs under its original lock.
This module does not construct a Store/client, call a model, or mutate memories.
Only references, hashes, spans, enumerated codes and bounded metadata persist.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import re
import time
import uuid

from .metrics_prompts import EXTRACTION_SYSTEM, RUBRIC_VERSION, VERDICT_SYSTEM
from .store import text_score

RETRIEVAL_VERSION = "p1-bigram-conversation-all-layers-v2"
SCHEMA_VERSION = 1
MAX_INVENTORY = 2000
MAX_FIELD = 16000
MAX_REPLY = 16000
MAX_CLAIMS = 10
MAX_MATERIAL = 24000
MAX_SOURCE_CHARS = 2_000_000
MIN_SCORE = 0.12
TOP_K = 5
HASH_FIELDS = ("title", "content", "keywords", "created_at", "layer", "kind", "salience", "pressure")
TERMINAL = frozenset(("completed", "skipped_no_claims", "incomplete", "failed", "cancelled", "interrupted", "evidence_unavailable", "deleted"))
VERDICTS = frozenset(("supported", "unsupported", "contradicted", "unjudgeable"))
REASONS = frozenset(("none", "explicit_support", "no_support", "explicit_conflict", "conflicting_evidence",
                     "ambiguous_reference", "outside_scope", "capture_incomplete", "evidence_changed",
                     "turn_changed", "input_limit", "no_evidence", "budget", "cancelled", "interrupted",
                     "failed", "response_invalid", "network", "chat_failed", "scope_changed", "client_unavailable",
                     "shutdown", "reset", "worker_busy", "evaluation_failed", "configuration_changed", "deleted"))
_HEX = re.compile(r"[0-9a-f]{32}\Z")


class EvaluationError(ValueError):
    """Fixed user-facing validation message; no source/provider text."""


class EvidenceUnavailable(EvaluationError):
    pass


class EvaluationIncomplete(EvaluationError):
    pass


def _dump(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(value) -> str:
    return hashlib.sha256(_dump(value).encode("utf-8")).hexdigest()


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _memory_hash(row) -> str:
    return _hash({key: row[key] for key in HASH_FIELDS})


def _id(value) -> str:
    if not isinstance(value, str) or not _HEX.fullmatch(value):
        raise EvaluationError("核查编号无效")
    return value


def _int(value, low=0, high=2147483647) -> int:
    if type(value) is not int or not low <= value <= high:
        raise EvaluationError("核查数值参数无效")
    return value


def _day() -> str:
    return datetime.now(timezone(timedelta(hours=8))).date().isoformat()


def _json_object(raw: str, max_chars=32000) -> dict:
    if not isinstance(raw, str) or len(raw) > max_chars:
        raise EvaluationError("模型结果超过限制或格式无效")
    def pairs(items):
        obj = {}
        for key, value in items:
            if key in obj:
                raise EvaluationError("模型结果含重复字段")
            obj[key] = value
        return obj
    try:
        result = json.loads(raw, object_pairs_hook=pairs,
                            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, TypeError, RecursionError):
        raise EvaluationError("模型结果不是严格 JSON") from None
    if type(result) is not dict:
        raise EvaluationError("模型结果必须是对象")
    return result


def _unique_span(text: str, quote: str) -> tuple[int, int]:
    if not isinstance(quote, str) or not quote or len(quote) > MAX_FIELD:
        raise EvaluationError("模型引用为空或超出范围")
    start = text.find(quote)
    if start < 0 or text.find(quote, start + 1) >= 0:
        raise EvaluationError("模型引用必须唯一且与原文完全一致")
    return start, start + len(quote)


def extraction_messages(reply: str) -> list[dict]:
    if not isinstance(reply, str) or not reply or len(reply) > MAX_REPLY:
        raise EvaluationIncomplete("回复为空或超过 16000 字，本次核查不执行")
    return [{"role": "system", "content": EXTRACTION_SYSTEM},
            {"role": "user", "content": _dump({"reply": reply})}]


def parse_claims(raw: str, reply: str) -> list[dict]:
    data = _json_object(raw)
    if set(data) != {"claims", "overflow"} or type(data["claims"]) is not list or type(data["overflow"]) is not bool:
        raise EvaluationError("主张抽取字段无效")
    if data["overflow"] or len(data["claims"]) > MAX_CLAIMS:
        raise EvaluationIncomplete("主张超过 10 条，本次核查不完整")
    result, spans = [], []
    for index, item in enumerate(data["claims"], 1):
        if type(item) is not dict or set(item) != {"quote", "kind"} or item["kind"] not in ("memory", "out_of_scope"):
            raise EvaluationError("主张抽取类型无效")
        start, end = _unique_span(reply, item["quote"])
        if any(start < old_end and old_start < end for old_start, old_end in spans):
            raise EvaluationError("主张范围重复或重叠")
        spans.append((start, end))
        result.append({"ordinal": index, "start": start, "end": end, "kind": item["kind"]})
    return result


def verdict_messages(material: dict) -> list[dict]:
    encoded = _dump(material)
    if len(encoded) > MAX_MATERIAL:
        raise EvaluationIncomplete("核查输入超过 24000 字，本次核查不完整")
    return [{"role": "system", "content": VERDICT_SYSTEM}, {"role": "user", "content": encoded}]


def parse_verdicts(raw: str, material: dict) -> list[dict]:
    data = _json_object(raw)
    if set(data) != {"verdicts"} or type(data["verdicts"]) is not list:
        raise EvaluationError("裁定结果字段无效")
    cases = {claim["ordinal"]: claim for claim in material["claims"]}
    if len(data["verdicts"]) != len(cases):
        raise EvaluationError("裁定遗漏或增加了主张")
    result, seen = [], set()
    reason_map = {"supported": {"explicit_support"}, "unsupported": {"no_support"},
                  "contradicted": {"explicit_conflict"},
                  "unjudgeable": {"conflicting_evidence", "ambiguous_reference", "outside_scope"}}
    for item in data["verdicts"]:
        if type(item) is not dict or set(item) != {"ordinal", "verdict", "reason_code", "citations"}:
            raise EvaluationError("裁定结果结构无效")
        ordinal = _int(item["ordinal"], 1, MAX_CLAIMS)
        verdict = item["verdict"]
        if ordinal not in cases or ordinal in seen or not isinstance(verdict, str) or verdict not in VERDICTS:
            raise EvaluationError("裁定引用的主张无效")
        if item["reason_code"] not in reason_map[verdict]:
            raise EvaluationError("裁定理由代码无效")
        seen.add(ordinal)
        candidates = {row["memory_id"]: row for row in cases[ordinal]["candidates"]}
        citations = item["citations"]
        if type(citations) is not list or len(citations) > TOP_K or (verdict in ("supported", "contradicted") and not citations):
            raise EvaluationError("裁定缺少有效证据引用")
        clean, linked = [], set()
        for citation in citations:
            if type(citation) is not dict or set(citation) != {"memory_id", "quote"}:
                raise EvaluationError("证据引用结构无效")
            memory_id = _int(citation["memory_id"], 1, 9223372036854775807)
            if memory_id not in candidates or memory_id in linked:
                raise EvaluationError("证据不属于本次候选或重复引用")
            start, end = _unique_span(candidates[memory_id]["content"], citation["quote"])
            linked.add(memory_id)
            clean.append({"memory_id": memory_id, "start": start, "end": end})
        result.append({"ordinal": ordinal, "verdict": verdict, "reason_code": item["reason_code"], "citations": clean})
    return result


_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS evaluation_meta (key TEXT PRIMARY KEY,value TEXT NOT NULL)",
    """CREATE TABLE IF NOT EXISTS evaluation_runs (
      id TEXT PRIMARY KEY, action_id TEXT UNIQUE NOT NULL, dataset_id TEXT NOT NULL,
      created_at REAL NOT NULL, captured_at REAL, finished_at REAL, status TEXT NOT NULL,
      reason_code TEXT NOT NULL DEFAULT 'none', turn_id INTEGER, reply_hash TEXT,
      model TEXT NOT NULL, rubric_version TEXT NOT NULL, retrieval_version TEXT NOT NULL,
      inventory_hash TEXT, inventory_total INTEGER NOT NULL DEFAULT 0, complete INTEGER NOT NULL DEFAULT 0,
      config_revision TEXT, revealed_at REAL)""",
    "CREATE INDEX IF NOT EXISTS evaluation_runs_created ON evaluation_runs(created_at DESC,id DESC)",
    """CREATE TABLE IF NOT EXISTS evaluation_inventory (
      run_id TEXT NOT NULL,memory_id INTEGER NOT NULL,content_hash TEXT NOT NULL,
      PRIMARY KEY(run_id,memory_id))""",
    """CREATE TABLE IF NOT EXISTS evaluation_claims (
      run_id TEXT NOT NULL,ordinal INTEGER NOT NULL,start INTEGER NOT NULL,end INTEGER NOT NULL,
      kind TEXT NOT NULL,state TEXT NOT NULL,verdict TEXT,reason_code TEXT,revealed_at REAL,
      PRIMARY KEY(run_id,ordinal))""",
    """CREATE TABLE IF NOT EXISTS evaluation_retrievals (
      run_id TEXT NOT NULL,ordinal INTEGER NOT NULL,query_hash TEXT NOT NULL,version TEXT NOT NULL,
      threshold REAL NOT NULL,top_k INTEGER NOT NULL,scope TEXT NOT NULL,
      matched_total INTEGER NOT NULL,top_k_truncated INTEGER NOT NULL,status TEXT NOT NULL,
      PRIMARY KEY(run_id,ordinal))""",
    """CREATE TABLE IF NOT EXISTS evaluation_evidence (
      run_id TEXT NOT NULL,ordinal INTEGER NOT NULL,memory_id INTEGER NOT NULL,
      content_hash TEXT NOT NULL,rank INTEGER NOT NULL,score REAL NOT NULL,
      citation_start INTEGER,citation_end INTEGER,PRIMARY KEY(run_id,ordinal,memory_id))""",
    """CREATE TABLE IF NOT EXISTS evaluation_labels (
      id TEXT PRIMARY KEY,run_id TEXT NOT NULL,ordinal INTEGER NOT NULL,version INTEGER NOT NULL,
      verdict TEXT NOT NULL,revealed INTEGER NOT NULL,created_at REAL NOT NULL,
      UNIQUE(run_id,ordinal,version))""",
    """CREATE TABLE IF NOT EXISTS evaluation_calibrations (
      id TEXT PRIMARY KEY,created_at REAL NOT NULL,model TEXT NOT NULL,rubric_version TEXT NOT NULL,
      retrieval_version TEXT NOT NULL,config_revision TEXT,minimum_agreement REAL NOT NULL,sample_refs TEXT NOT NULL,
      matrix TEXT NOT NULL,summary TEXT NOT NULL,approved INTEGER NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS evaluation_attempts (
      id TEXT PRIMARY KEY,run_id TEXT NOT NULL,stage TEXT NOT NULL,day TEXT NOT NULL,
      dispatched_at REAL NOT NULL,finished_at REAL,state TEXT NOT NULL,
      prompt_tokens INTEGER,completion_tokens INTEGER,UNIQUE(run_id,stage))""",
    "CREATE TABLE IF NOT EXISTS evaluation_dayquota (day TEXT PRIMARY KEY,used INTEGER NOT NULL)",
)


class EvaluationStore:
    def __init__(self, conn):
        self.conn = conn
        with self._transaction():
            for statement in _SCHEMA:
                conn.execute(statement)
            version = conn.execute("SELECT value FROM evaluation_meta WHERE key='schema_version'").fetchone()
            if version and version[0] != str(SCHEMA_VERSION):
                raise EvaluationError("核查记录版本不兼容，请保留数据并检查版本")
            for key, value in (("schema_version", str(SCHEMA_VERSION)), ("dataset_id", uuid.uuid4().hex), ("daily_limit", "0")):
                conn.execute("INSERT OR IGNORE INTO evaluation_meta(key,value) VALUES(?,?)", (key, value))
            self.dataset_id = conn.execute("SELECT value FROM evaluation_meta WHERE key='dataset_id'").fetchone()[0]
            _id(self.dataset_id)
            placeholders = ",".join("?" for _ in TERMINAL)
            conn.execute(f"UPDATE evaluation_runs SET status='interrupted',reason_code='interrupted',finished_at=? WHERE status NOT IN ({placeholders})",
                         (time.time(), *TERMINAL))
            conn.execute("UPDATE evaluation_attempts SET state='unknown',finished_at=? WHERE state='dispatched'", (time.time(),))

    @contextmanager
    def _transaction(self):
        if self.conn.in_transaction:
            raise EvaluationError("核查事务边界冲突")
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.conn.commit()
        except BaseException:
            self.conn.rollback()
            raise

    def _run(self, run_id, *, include_deleted=False) -> dict:
        row = self.conn.execute("SELECT * FROM evaluation_runs WHERE id=? AND dataset_id=?", (_id(run_id), self.dataset_id)).fetchone()
        if row is None:
            raise EvaluationError("核查任务不存在或不属于当前数据库")
        if row["status"] == "deleted" and not include_deleted:
            raise EvaluationError("该核查记录已删除，无法查看、标注或继续执行")
        return dict(row)

    def _summary(self, row) -> dict:
        row = dict(row)
        return {key: row[key] for key in ("id", "action_id", "dataset_id", "created_at", "captured_at", "status", "reason_code", "turn_id", "model", "rubric_version", "retrieval_version", "config_revision", "complete", "inventory_total")} | {
            "attempts": self.conn.execute("SELECT COUNT(*) FROM evaluation_attempts WHERE run_id=?", (row["id"],)).fetchone()[0]}

    def settings(self) -> dict:
        limit = int(self.conn.execute("SELECT value FROM evaluation_meta WHERE key='daily_limit'").fetchone()[0])
        day = _day()
        row = self.conn.execute("SELECT used FROM evaluation_dayquota WHERE day=?", (day,)).fetchone()
        used = row[0] if row else 0
        return {"dataset_id": self.dataset_id, "daily_limit": limit, "day": day,
                "used_today": used, "remaining": max(0, limit - used),
                "rubric_version": RUBRIC_VERSION, "retrieval_version": RETRIEVAL_VERSION}

    def set_limit(self, limit: int) -> dict:
        _int(limit, 0, 100)
        with self._transaction():
            self.conn.execute("UPDATE evaluation_meta SET value=? WHERE key='daily_limit'", (str(limit),))
        return self.settings()

    def arm(self, action_id: str, model: str, config_revision=None) -> dict:
        _id(action_id)
        if not isinstance(model, str) or not model or len(model) > 256 or any(ord(ch) < 32 for ch in model):
            raise EvaluationError("核查模型名称无效")
        if config_revision is not None and (not isinstance(config_revision, str) or not re.fullmatch(r"[0-9a-f]{64}", config_revision)):
            raise EvaluationError("核查配置校验值无效")
        existing = self.conn.execute("SELECT * FROM evaluation_runs WHERE action_id=?", (action_id,)).fetchone()
        if existing is not None:
            return self._summary(existing)
        with self._transaction():
            if self.settings()["remaining"] < 2:
                raise EvaluationError("核查默认关闭或今日剩余额度不足 2 次，请先设置费用许可")
            placeholders = ",".join("?" for _ in TERMINAL)
            if self.conn.execute(f"SELECT 1 FROM evaluation_runs WHERE status NOT IN ({placeholders}) LIMIT 1", tuple(TERMINAL)).fetchone():
                raise EvaluationError("已有待核查或执行中的任务，请先完成或取消")
            run_id = uuid.uuid4().hex
            self.conn.execute("INSERT INTO evaluation_runs(id,action_id,dataset_id,created_at,status,model,rubric_version,retrieval_version,config_revision) VALUES(?,?,?,?,?,?,?,?,?)",
                              (run_id, action_id, self.dataset_id, time.time(), "armed", model, RUBRIC_VERSION, RETRIEVAL_VERSION, config_revision))
        return self._summary(self._run(run_id))

    def capture(self, run_id: str) -> dict:
        row = self.conn.execute("SELECT * FROM evaluation_runs WHERE id=? AND dataset_id=?", (_id(run_id), self.dataset_id)).fetchone()
        if row is None or row["status"] != "armed":
            raise EvaluationError("该单次核查已消费、取消或不存在")
        run_id = row["id"]
        total = self.conn.execute("SELECT COUNT(*) FROM memories WHERE origin='conversation' AND provenance_status='valid'").fetchone()[0]
        inventory, complete, source_chars = [], total <= MAX_INVENTORY, 0
        deadline = time.monotonic() + 1.0
        if complete:
            fields = ",".join("substr(" + field + ",1,16001) AS " + field if field in ("title", "content", "keywords") else field for field in HASH_FIELDS)
            for memory in self.conn.execute(f"SELECT id,{fields} FROM memories WHERE origin='conversation' AND provenance_status='valid' ORDER BY id LIMIT ?", (MAX_INVENTORY + 1,)):
                source_chars += sum(len(memory[field]) for field in ("title", "content", "keywords"))
                if source_chars > MAX_SOURCE_CHARS or time.monotonic() > deadline or any(len(memory[field]) > MAX_FIELD for field in ("title", "content", "keywords")):
                    complete = False
                    break
                inventory.append((memory["id"], _memory_hash(memory)))
        with self._transaction():
            self.conn.executemany("INSERT INTO evaluation_inventory(run_id,memory_id,content_hash) VALUES(?,?,?)",
                                  ((run_id, memory_id, digest) for memory_id, digest in inventory))
            self.conn.execute("UPDATE evaluation_runs SET status=?,reason_code=?,captured_at=?,inventory_hash=?,inventory_total=?,complete=? WHERE id=?",
                              ("captured" if complete else "incomplete", "none" if complete else "capture_incomplete",
                               time.time(), _hash(inventory), total, int(complete), run_id))
        return self._summary(self._run(run_id))

    def bind_turn(self, run_id: str, turn_id: int) -> dict:
        run = self._run(run_id)
        if run["status"] not in ("captured", "incomplete") or run["turn_id"] is not None:
            raise EvaluationError("核查无法再次绑定回复")
        _int(turn_id, 1, 9223372036854775807)
        turn = self.conn.execute("SELECT substr(content,1,16001),length(content) FROM turns WHERE id=? AND role='assistant'", (turn_id,)).fetchone()
        if turn is None:
            raise EvaluationError("核查必须绑定实际已保存的助手回复")
        source_turn = self.conn.execute("SELECT 1 FROM source_turns WHERE turn_id=?", (turn_id,)).fetchone() is not None
        complete = run["complete"] and turn[1] <= MAX_REPLY and bool(turn[0]) and not source_turn
        with self._transaction():
            self.conn.execute("UPDATE evaluation_runs SET turn_id=?,reply_hash=?,status=?,reason_code=?,complete=? WHERE id=?",
                              (turn_id, _text_hash(turn[0]) if turn[1] <= MAX_REPLY else None, "queued" if complete else "incomplete",
                               "none" if complete else ("outside_scope" if source_turn else
                               run["reason_code"] if not run["complete"] else "input_limit"), int(complete), run_id))
        return self._summary(self._run(run_id))

    def fail(self, run_id: str, status: str, reason_code: str) -> None:
        run = self._run(run_id, include_deleted=True)
        if status not in TERMINAL or status in ("completed", "skipped_no_claims", "deleted"):
            raise EvaluationError("核查终止状态无效")
        if run["status"] in TERMINAL:
            return
        reason_code = reason_code if reason_code in REASONS else "failed"
        with self._transaction():
            self.conn.execute("UPDATE evaluation_runs SET status=?,reason_code=?,finished_at=? WHERE id=?", (status, reason_code, time.time(), run_id))

    def cancel(self, run_id: str) -> None:
        self.fail(run_id, "cancelled", "cancelled")

    def delete_run(self, run_id: str) -> dict:
        """Remove only P1 material; retain quota/attempts and an idempotency tombstone.

        Service holds the same lock used for worker stages. The deleted terminal
        state and child cleanup commit together, so old workers cannot recreate
        material. A dispatched attempt may still finish and update its usage.
        """
        run = self._run(run_id, include_deleted=True)
        already_deleted = run["status"] == "deleted"
        if not already_deleted:
            with self._transaction():
                self.conn.execute(
                    "UPDATE evaluation_runs SET status='deleted',reason_code='deleted',finished_at=?,"
                    "captured_at=NULL,turn_id=NULL,reply_hash=NULL,model='',rubric_version='',"
                    "retrieval_version='',inventory_hash=NULL,inventory_total=0,complete=0,"
                    "config_revision=NULL,revealed_at=NULL WHERE id=? AND dataset_id=?",
                    (time.time(), run_id, self.dataset_id),
                )
                for table in ("evaluation_claims", "evaluation_evidence", "evaluation_inventory",
                              "evaluation_retrievals", "evaluation_labels"):
                    self.conn.execute(f"DELETE FROM {table} WHERE run_id=?", (run_id,))
                # Preserve historical aggregate records, but remove this run's hashes
                # and label references. Revocation is sticky, even if other samples
                # would still pass a later validation or threshold.
                rows = self.conn.execute(
                    "SELECT id,sample_refs,summary FROM evaluation_calibrations WHERE instr(sample_refs,?)>0",
                    (run_id,),
                )
                for row in rows:
                    refs = json.loads(row["sample_refs"])
                    if not any(ref.get("run_id") == run_id for ref in refs):
                        continue
                    remaining = [ref for ref in refs if ref.get("run_id") != run_id]
                    summary = json.loads(row["summary"])
                    summary["invalidated"] = "source_record_deleted"
                    self.conn.execute(
                        "UPDATE evaluation_calibrations SET sample_refs=?,summary=?,approved=0 WHERE id=?",
                        (_dump(remaining), _dump(summary), row["id"]),
                    )
        return {"id": run_id, "deleted": True, "already_deleted": already_deleted, "quota_preserved": True}

    def cancel_pending(self, reason_code="shutdown") -> None:
        reason = reason_code if reason_code in REASONS else "cancelled"
        placeholders = ",".join("?" for _ in TERMINAL)
        with self._transaction():
            self.conn.execute(f"UPDATE evaluation_runs SET status='cancelled',reason_code=?,finished_at=? WHERE status NOT IN ({placeholders})",
                              (reason, time.time(), *TERMINAL))
            if reason == "reset":
                # Old dataset is about to be archived; its client outcome can no longer
                # be committed through the new dataset's Store.
                self.conn.execute("UPDATE evaluation_attempts SET state='unknown',finished_at=? WHERE state='dispatched'", (time.time(),))

    def _live(self, run: dict, *, include_memories=False):
        if not run["complete"] or run["turn_id"] is None:
            raise EvidenceUnavailable("核查范围不完整或回复尚未绑定")
        if self.conn.execute("SELECT 1 FROM source_turns WHERE turn_id=?", (run["turn_id"],)).fetchone():
            raise EvidenceUnavailable("这轮包含外部资料，不能作为普通记忆主张核查材料")
        turn = self.conn.execute("SELECT substr(content,1,16001),length(content) FROM turns WHERE id=? AND role='assistant'", (run["turn_id"],)).fetchone()
        if turn is None or turn[1] > MAX_REPLY or _text_hash(turn[0]) != run["reply_hash"]:
            raise EvidenceUnavailable("原回复已不可用，核查不再可复核")
        inventory = self.conn.execute("SELECT memory_id,content_hash FROM evaluation_inventory WHERE run_id=? ORDER BY memory_id", (run["id"],)).fetchall()
        if len(inventory) != run["inventory_total"] or _hash([(row[0], row[1]) for row in inventory]) != run["inventory_hash"]:
            raise EvidenceUnavailable("原证据资格清单不完整")
        memories = []
        deadline = time.monotonic() + 1.0
        fields = ",".join("substr(m." + field + ",1,16001) AS " + field if field in ("title", "content", "keywords") else "m." + field for field in HASH_FIELDS)
        rows = self.conn.execute(f"SELECT m.id,{fields},i.content_hash FROM evaluation_inventory i JOIN memories m ON m.id=i.memory_id WHERE i.run_id=? AND m.origin='conversation' AND m.provenance_status='valid' ORDER BY m.id", (run["id"],))
        checked, source_chars = 0, 0
        for row in rows:
            checked += 1
            source_chars += sum(len(row[field]) for field in ("title", "content", "keywords"))
            if (source_chars > MAX_SOURCE_CHARS or time.monotonic() > deadline or any(len(row[field]) > MAX_FIELD for field in ("title", "content", "keywords"))
                    or _memory_hash(row) != row["content_hash"]):
                raise EvidenceUnavailable("原资格范围中的记忆已变化或无法完整复核")
            if include_memories:
                memories.append(dict(row))
        if checked != run["inventory_total"]:
            raise EvidenceUnavailable("原资格范围中的记忆已删除，核查证据不可用")
        return turn[0], memories

    def get_work(self, run_id: str) -> dict:
        run = self._run(run_id)
        if run["status"] in TERMINAL:
            raise EvidenceUnavailable("核查任务已结束，不能再次付费执行")
        reply, _ = self._live(run)
        return {"reply": reply, "turn_id": run["turn_id"]}

    def reserve_attempt(self, run_id: str, stage: str) -> str:
        run = self._run(run_id)
        expected = {"extract": ("queued",), "judge": ("retrieving",)}
        if stage not in expected or run["status"] not in expected[stage]:
            raise EvaluationError("核查阶段已执行、取消或状态不匹配")
        if stage == "judge" and not self.conn.execute(
                "SELECT 1 FROM evaluation_claims c JOIN evaluation_evidence e ON e.run_id=c.run_id AND e.ordinal=c.ordinal "
                "WHERE c.run_id=? AND c.state='pending' LIMIT 1", (run_id,)).fetchone():
            raise EvaluationError("尚无完整非空候选集，不发送裁定请求")
        self._live(run)
        with self._transaction():
            settings = self.settings()
            if settings["remaining"] < 1:
                raise EvaluationError("今日核查调用额度已用尽或许可已关闭")
            if self.conn.execute("SELECT COUNT(*) FROM evaluation_attempts WHERE run_id=?", (run_id,)).fetchone()[0] >= 2:
                raise EvaluationError("该次核查已达到两次请求上限")
            attempt_id = uuid.uuid4().hex
            self.conn.execute("INSERT INTO evaluation_attempts(id,run_id,stage,day,dispatched_at,state) VALUES(?,?,?,?,?,'dispatched')", (attempt_id, run_id, stage, settings["day"], time.time()))
            self.conn.execute("INSERT INTO evaluation_dayquota(day,used) VALUES(?,1) ON CONFLICT(day) DO UPDATE SET used=used+1", (settings["day"],))
            self.conn.execute("UPDATE evaluation_runs SET status=? WHERE id=?", ("extracting" if stage == "extract" else "judging", run_id))
        return attempt_id

    def finish_attempt(self, attempt_id: str, usage: dict | None, state: str) -> None:
        _id(attempt_id)
        if state not in ("received", "completed", "failed", "unknown", "cancelled"):
            raise EvaluationError("核查调用状态无效")
        prompt = completion = None
        if usage is not None:
            prompt = usage.get("prompt_tokens", usage.get("prompt"))
            completion = usage.get("completion_tokens", usage.get("completion"))
            if prompt is not None:
                _int(prompt)
            if completion is not None:
                _int(completion)
        with self._transaction():
            self.conn.execute("UPDATE evaluation_attempts SET state=?,finished_at=?,prompt_tokens=?,completion_tokens=? WHERE id=? AND state='dispatched'", (state, time.time(), prompt, completion, attempt_id))

    def save_claims(self, run_id: str, claims: list[dict]) -> None:
        run = self._run(run_id)
        if run["status"] != "extracting" or type(claims) is not list or len(claims) > MAX_CLAIMS:
            raise EvaluationError("核查主张提交状态无效")
        reply, _ = self._live(run)
        seen, spans = set(), []
        for claim in claims:
            if set(claim) != {"ordinal", "start", "end", "kind"} or claim["kind"] not in ("memory", "out_of_scope"):
                raise EvaluationError("核查主张结构无效")
            ordinal = _int(claim["ordinal"], 1, MAX_CLAIMS)
            start, end = _int(claim["start"], 0, len(reply)), _int(claim["end"], 1, len(reply))
            if start >= end or ordinal in seen or any(start < b and a < end for a, b in spans):
                raise EvaluationError("核查主张范围无效或重复")
            seen.add(ordinal)
            spans.append((start, end))
        with self._transaction():
            self.conn.executemany("INSERT INTO evaluation_claims(run_id,ordinal,start,end,kind,state) VALUES(?,?,?,?,?,?)", ((run_id, c["ordinal"], c["start"], c["end"], c["kind"], "out_of_scope" if c["kind"] == "out_of_scope" else "pending") for c in claims))
            self.conn.execute("UPDATE evaluation_runs SET status=?,finished_at=? WHERE id=?", ("retrieving" if claims else "skipped_no_claims", None if claims else time.time(), run_id))

    def material(self, run_id: str) -> dict:
        run = self._run(run_id)
        if run["status"] != "retrieving":
            raise EvaluationError("核查检索阶段无效")
        reply, memories = self._live(run, include_memories=True)
        claims = self.conn.execute("SELECT * FROM evaluation_claims WHERE run_id=? ORDER BY ordinal", (run_id,)).fetchall()
        material, retrievals, links, states = {"claims": []}, [], [], []
        deadline, over_limit = time.monotonic() + 1.0, False
        for claim in claims:
            if claim["kind"] != "memory":
                continue
            query = reply[claim["start"]:claim["end"]]
            ranked = []
            for memory in memories:
                if time.monotonic() > deadline:
                    over_limit = True
                    break
                score = text_score(query, memory["title"] + " " + memory["keywords"] + " " + memory["content"])
                if score >= MIN_SCORE:
                    ranked.append((score, memory))
            if over_limit:
                break
            ranked.sort(key=lambda pair: (-pair[0], pair[1]["id"]))
            matched_total = len(ranked)
            ranked = ranked[:TOP_K]
            retrievals.append((run_id, claim["ordinal"], _text_hash(query), RETRIEVAL_VERSION, MIN_SCORE, TOP_K,
                               "pre_turn_conversation_all_layers", matched_total, int(matched_total > TOP_K),
                               "candidates" if ranked else "evidence_insufficient"))
            states.append(("pending" if ranked else "evidence_insufficient", run_id, claim["ordinal"]))
            candidates = []
            for rank, (score, memory) in enumerate(ranked, 1):
                candidates.append({"memory_id": memory["id"], "title": memory["title"], "content": memory["content"]})
                links.append((run_id, claim["ordinal"], memory["id"], memory["content_hash"], rank, score))
            if candidates:
                material["claims"].append({"ordinal": claim["ordinal"], "text": query, "candidates": candidates})
            if len(_dump(material)) > MAX_MATERIAL:
                over_limit = True
                break
        incomplete = over_limit or not material["claims"]
        if over_limit:
            # Partial scans/candidate subsets are never presented as complete evidence.
            links = []
            retrievals = [(*values[:-1], "incomplete") for values in retrievals]
            states = [("incomplete", run_id, claim["ordinal"]) for claim in claims if claim["kind"] == "memory"]
        with self._transaction():
            self.conn.executemany("INSERT INTO evaluation_retrievals(run_id,ordinal,query_hash,version,threshold,top_k,scope,matched_total,top_k_truncated,status) VALUES(?,?,?,?,?,?,?,?,?,?)", retrievals)
            self.conn.executemany("INSERT INTO evaluation_evidence(run_id,ordinal,memory_id,content_hash,rank,score) VALUES(?,?,?,?,?,?)", links)
            self.conn.executemany("UPDATE evaluation_claims SET state=? WHERE run_id=? AND ordinal=?", states)
            if incomplete:
                self.conn.execute("UPDATE evaluation_runs SET status='incomplete',reason_code=?,finished_at=? WHERE id=?", ("input_limit" if over_limit else "no_evidence", time.time(), run_id))
        return {"claims": []} if incomplete else material

    def finish_verdicts(self, run_id: str, verdicts: list[dict]) -> None:
        run = self._run(run_id)
        if run["status"] != "judging" or type(verdicts) is not list or not 1 <= len(verdicts) <= MAX_CLAIMS:
            raise EvaluationError("裁定提交状态无效")
        reply, memories = self._live(run, include_memories=True)
        by_id = {memory["id"]: memory for memory in memories}
        expected = {row[0] for row in self.conn.execute("SELECT ordinal FROM evaluation_claims WHERE run_id=? AND state='pending'", (run_id,))}
        for verdict in verdicts:
            if type(verdict) is not dict or set(verdict) != {"ordinal", "verdict", "reason_code", "citations"}:
                raise EvaluationError("裁定提交字段无效")
            _int(verdict["ordinal"], 1, MAX_CLAIMS)
        if {v["ordinal"] for v in verdicts} != expected or len(verdicts) != len(expected):
            raise EvaluationError("裁定数量与待核查主张不符")
        clean = []
        reason_map = {"supported": {"explicit_support"}, "unsupported": {"no_support"}, "contradicted": {"explicit_conflict"}, "unjudgeable": {"conflicting_evidence", "ambiguous_reference", "outside_scope"}}
        for verdict in verdicts:
            if set(verdict) != {"ordinal", "verdict", "reason_code", "citations"} or verdict["verdict"] not in VERDICTS or verdict["reason_code"] not in reason_map[verdict["verdict"]]:
                raise EvaluationError("裁定提交字段无效")
            if type(verdict["citations"]) is not list or len(verdict["citations"]) > TOP_K:
                raise EvaluationError("裁定证据数量无效")
            candidate_ids = {row[0] for row in self.conn.execute("SELECT memory_id FROM evaluation_evidence WHERE run_id=? AND ordinal=?", (run_id, verdict["ordinal"]))}
            if not candidate_ids or (verdict["verdict"] in ("supported", "contradicted") and not verdict["citations"]):
                raise EvaluationError("裁定缺少候选证据")
            seen = set()
            for citation in verdict["citations"]:
                if set(citation) != {"memory_id", "start", "end"} or citation["memory_id"] not in candidate_ids or citation["memory_id"] in seen:
                    raise EvaluationError("裁定证据编号无效")
                seen.add(citation["memory_id"])
                content = by_id[citation["memory_id"]]["content"]
                start, end = _int(citation["start"], 0, len(content)), _int(citation["end"], 1, len(content))
                if start >= end:
                    raise EvaluationError("裁定证据范围无效")
            clean.append(verdict)
        with self._transaction():
            for verdict in clean:
                self.conn.execute("UPDATE evaluation_claims SET state='judged',verdict=?,reason_code=? WHERE run_id=? AND ordinal=?", (verdict["verdict"], verdict["reason_code"], run_id, verdict["ordinal"]))
                for citation in verdict["citations"]:
                    self.conn.execute("UPDATE evaluation_evidence SET citation_start=?,citation_end=? WHERE run_id=? AND ordinal=? AND memory_id=?", (citation["start"], citation["end"], run_id, verdict["ordinal"], citation["memory_id"]))
            self.conn.execute("UPDATE evaluation_runs SET status='completed',finished_at=? WHERE id=?", (time.time(), run_id))

    def list_runs(self, before=None, status_filter="all") -> dict:
        if status_filter not in ("all", "active", "finished"):
            raise EvaluationError("核查状态筛选仅支持 all、active、finished")
        params, where = [self.dataset_id], "dataset_id=? AND status!='deleted'"
        if status_filter != "all":
            placeholders = ",".join("?" for _ in TERMINAL)
            where += f" AND status {'NOT IN' if status_filter == 'active' else 'IN'} ({placeholders})"
            params.extend(TERMINAL)
        if before:
            # Retain cursor coordinates after deletion: an already displayed page
            # can continue without repeating or losing rows at the deleted anchor.
            anchor = self._run(before, include_deleted=True)
            where += " AND (created_at<? OR (created_at=? AND id<?))"
            params.extend((anchor["created_at"], anchor["created_at"], anchor["id"]))
        rows = self.conn.execute(f"SELECT * FROM evaluation_runs WHERE {where} ORDER BY created_at DESC,id DESC LIMIT 26", params).fetchall()
        items = [self._summary(row) for row in rows[:25]]
        return {"items": items, "next_before": items[-1]["id"] if len(rows) > 25 else None,
                "status_filter": status_filter}

    def _latest_label(self, run_id: str, ordinal: int):
        row = self.conn.execute("SELECT id,version,verdict,revealed,created_at FROM evaluation_labels WHERE run_id=? AND ordinal=? ORDER BY version DESC LIMIT 1", (run_id, ordinal)).fetchone()
        return dict(row) if row else None

    def detail(self, run_id: str, reveal=False) -> dict:
        if type(reveal) is not bool:
            raise EvaluationError("揭盲选项无效")
        run = self._run(run_id)
        available, reply, memories = True, "", []
        try:
            reply, memories = self._live(run, include_memories=True)
        except EvidenceUnavailable:
            available = False
        by_id = {row["id"]: row for row in memories}
        rows = self.conn.execute("SELECT * FROM evaluation_claims WHERE run_id=? ORDER BY ordinal", (run_id,)).fetchall()
        claims = []
        for row in rows:
            sources, citations = [], []
            retrieval = self.conn.execute("SELECT version,threshold,top_k,scope,matched_total,top_k_truncated,status FROM evaluation_retrievals WHERE run_id=? AND ordinal=?", (run_id, row["ordinal"])).fetchone()
            if available:
                for link in self.conn.execute("SELECT * FROM evaluation_evidence WHERE run_id=? AND ordinal=? ORDER BY rank", (run_id, row["ordinal"])):
                    memory = by_id[link["memory_id"]]
                    sources.append({"memory_id": memory["id"], "title": memory["title"], "content": memory["content"], "hash": link["content_hash"]})
                    if link["citation_start"] is not None:
                        citations.append({"memory_id": memory["id"], "start": link["citation_start"], "end": link["citation_end"]})
            claims.append({"ordinal": row["ordinal"], "start": row["start"], "end": row["end"],
                           "kind": row["kind"], "text": reply[row["start"]:row["end"]] if available else "",
                           "state": row["state"] if available else "evidence_unavailable", "candidates": sources,
                           "retrieval": dict(retrieval) if retrieval else None,
                           "label": self._latest_label(run_id, row["ordinal"]),
                           **({"verdict": {"verdict": row["verdict"], "reason_code": row["reason_code"], "citations": citations}} if reveal and available and row["verdict"] else {})})
        actually_revealed = reveal and available and any(row["verdict"] is not None for row in rows)
        if actually_revealed:
            with self._transaction():
                self.conn.execute("UPDATE evaluation_runs SET revealed_at=COALESCE(revealed_at,?) WHERE id=?", (time.time(), run_id))
                self.conn.execute("UPDATE evaluation_claims SET revealed_at=COALESCE(revealed_at,?) WHERE run_id=? AND verdict IS NOT NULL", (time.time(), run_id))
        summary = self._summary(run)
        summary["evidence_available"] = available
        summary["revealed"] = bool(run["revealed_at"] is not None or actually_revealed)
        return {"run": summary, "claims": claims, "calibrated": self._calibration_for(run) if available else False}

    def label(self, run_id: str, ordinal: int, verdict: str, revealed: bool) -> dict:
        run = self._run(run_id)
        _int(ordinal, 1, MAX_CLAIMS)
        if verdict not in VERDICTS or type(revealed) is not bool:
            raise EvaluationError("人工标签类别或揭盲状态无效")
        self._live(run)
        claim = self.conn.execute("SELECT * FROM evaluation_claims WHERE run_id=? AND ordinal=?", (run_id, ordinal)).fetchone()
        if claim is None or claim["state"] != "judged":
            raise EvaluationError("该主张尚无可独立核验的裁定材料")
        latest = self._latest_label(run_id, ordinal)
        with self._transaction():
            self.conn.execute("INSERT INTO evaluation_labels(id,run_id,ordinal,version,verdict,revealed,created_at) VALUES(?,?,?,?,?,?,?)",
                              (uuid.uuid4().hex, run_id, ordinal, (latest["version"] + 1) if latest else 1,
                               verdict, int(revealed or run["revealed_at"] is not None or claim["revealed_at"] is not None), time.time()))
        return self.detail(run_id)

    def calibrate(self, run_ids: list[str], minimum_agreement: float, attest: bool) -> dict:
        if type(attest) is not bool or not attest:
            raise EvaluationError("校准必须由人工明确确认独立标注和适用条件")
        if type(run_ids) is not list or not 20 <= len(run_ids) <= 100 or len(set(run_ids)) != len(run_ids):
            raise EvaluationError("校准需 20 至 100 个不同回复任务")
        if type(minimum_agreement) not in (int, float) or not math.isfinite(minimum_agreement) or not 0 < minimum_agreement <= 1:
            raise EvaluationError("人工准入阈值须在 0 与 1 之间")
        matrix = {human: {model: 0 for model in sorted(VERDICTS)} for human in sorted(VERDICTS)}
        refs, turns, versions, classes = [], set(), set(), set()
        deadline = time.monotonic() + 3.0
        for run_id in run_ids:
            if time.monotonic() > deadline:
                raise EvaluationError("校准材料完整复核超时，请减少本批任务数量后重试")
            run = self._run(run_id)
            self._live(run)
            if run["status"] != "completed":
                raise EvaluationError("校准只能使用已完成且证据有效的任务")
            versions.add((run["model"], run["rubric_version"], run["retrieval_version"], run["config_revision"]))
            turns.add(run["turn_id"])
            # Predeclared selection rule: one case per distinct reply, selected by ordinal
            # before inspecting labels or agreement. Multiple claims are not independent trials.
            claim = self.conn.execute("SELECT ordinal,verdict FROM evaluation_claims WHERE run_id=? AND state='judged' ORDER BY ordinal LIMIT 1", (run_id,)).fetchone()
            label = self._latest_label(run_id, claim["ordinal"]) if claim else None
            if not label or label["revealed"]:
                raise EvaluationError("每个校准回复的首个已裁定主张都须有未揭盲人工标签")
            matrix[label["verdict"]][claim["verdict"]] += 1
            classes.add(label["verdict"])
            refs.append({"run_id": run_id, "ordinal": claim["ordinal"], "label_id": label["id"],
                         "inventory_hash": run["inventory_hash"], "reply_hash": run["reply_hash"]})
        if len(versions) != 1 or len(turns) < 20 or len(refs) < 20 or not {"supported", "unsupported", "contradicted"} <= classes:
            raise EvaluationError("校准需同一版本、20个独立回复且人工标签覆盖支持、未支持、相悖三类")
        model, rubric, retrieval, config_revision = next(iter(versions))
        if rubric != RUBRIC_VERSION or retrieval != RETRIEVAL_VERSION:
            raise EvaluationError("校准材料版本已过期")
        n = len(refs)
        correct = sum(matrix[category][category] for category in VERDICTS)
        proportion, z = correct / n, 1.96
        denominator = 1 + z * z / n
        center = (proportion + z * z / (2 * n)) / denominator
        half = z * math.sqrt(proportion * (1 - proportion) / n + z * z / (4 * n * n)) / denominator
        summary = {"n": n, "independent_turns": len(turns), "agreement": proportion,
                   "agreement_interval": [max(0, center - half), min(1, center + half)],
                   "minimum_agreement": float(minimum_agreement),
                   "selection": "lowest_judged_ordinal_per_reply",
                   "interval_assumption": "descriptive_wilson_assuming_independent_selected_reply_cases",
                   "scope": "candidate_set_verdict_agreement_only",
                   "not_calibrated": ["extraction_recall", "retrieval_recall", "all_reply_honesty"]}
        approved = proportion >= minimum_agreement
        calibration_id = uuid.uuid4().hex
        with self._transaction():
            self.conn.execute("INSERT INTO evaluation_calibrations(id,created_at,model,rubric_version,retrieval_version,config_revision,minimum_agreement,sample_refs,matrix,summary,approved) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                              (calibration_id, time.time(), model, rubric, retrieval, config_revision, minimum_agreement,
                               _dump(refs), _dump(matrix), _dump(summary), int(approved)))
        return {"id": calibration_id, "approved": approved, "valid": True, "matrix": matrix, **summary}

    def _calibration_for(self, run: dict):
        row = self.conn.execute("SELECT * FROM evaluation_calibrations WHERE model=? AND rubric_version=? AND retrieval_version=? AND config_revision IS ? ORDER BY created_at DESC LIMIT 1",
                                (run["model"], run["rubric_version"], run["retrieval_version"], run["config_revision"])).fetchone()
        if row is None:
            return False
        return self._calibration_view(row)

    def calibrations(self) -> dict:
        rows = self.conn.execute("SELECT * FROM evaluation_calibrations ORDER BY created_at DESC LIMIT 25").fetchall()
        # Validate one current record per version group; old records remain historical.
        items, checked = [], set()
        deadline = time.monotonic() + 3.0
        for row in rows:
            key = (row["model"], row["rubric_version"], row["retrieval_version"], row["config_revision"])
            if key in checked or time.monotonic() > deadline:
                items.append({"id": row["id"], "created_at": row["created_at"], "model": row["model"],
                              "rubric_version": row["rubric_version"], "retrieval_version": row["retrieval_version"],
                              "config_revision": row["config_revision"], "matrix": json.loads(row["matrix"]),
                              **json.loads(row["summary"]), "valid": False, "approved": False,
                              "status": "historical_not_revalidated"})
            else:
                items.append(self._calibration_view(row, deadline=deadline))
                checked.add(key)
        return {"items": items}

    def _calibration_view(self, row, *, deadline=None):
        deadline = deadline if deadline is not None else time.monotonic() + 3.0
        summary = json.loads(row["summary"])
        valid = (not summary.get("invalidated") and row["rubric_version"] == RUBRIC_VERSION
                 and row["retrieval_version"] == RETRIEVAL_VERSION)
        limited = False
        try:
            for ref in json.loads(row["sample_refs"]) if valid else ():
                if time.monotonic() > deadline:
                    valid, limited = False, True
                    break
                sample = self._run(ref["run_id"])
                self._live(sample)
                label = self._latest_label(ref["run_id"], ref["ordinal"])
                if (not label or label["id"] != ref["label_id"] or label["revealed"]
                        or sample["inventory_hash"] != ref["inventory_hash"] or sample["reply_hash"] != ref["reply_hash"]
                        or (sample["model"], sample["rubric_version"], sample["retrieval_version"], sample["config_revision"])
                        != (row["model"], row["rubric_version"], row["retrieval_version"], row["config_revision"])):
                    valid = False
                    break
        except (EvaluationError, ValueError, KeyError, TypeError):
            valid = False
        return {"id": row["id"], "created_at": row["created_at"], "model": row["model"],
                "rubric_version": row["rubric_version"], "retrieval_version": row["retrieval_version"],
                "config_revision": row["config_revision"],
                "valid": valid, "approved": bool(row["approved"]) and valid,
                "status": "validation_limited" if limited else ("valid" if valid else "expired"), "matrix": json.loads(row["matrix"]),
                **summary}
