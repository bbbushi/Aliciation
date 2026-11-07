"""Bound, successful-stage checkpoints for an explicitly resumed persona import.

This is an audit store, not a source of runtime persona state. Callers hold the
service operation lock, supply the current binding, and validate stage semantics
before ``succeed``. A reservation is persisted before any paid work; interrupted
attempts stay spent. Old import JSON files are never promoted to checkpoints.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import stat
from pathlib import Path

from .persona_transaction import _plain_stat, validate_op_id, write_record

POLICY = "persona_success_checkpoints_v1"
STAGES = ("expression", "extraction", "adoption", "candidate")
LIMITS = {"expression": 1, "extraction": 2, "adoption": 2, "candidate": None}
MAX_BYTES = 32 * 1024 * 1024
FILENAME = "preparation_checkpoint.json"
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_ERROR_TYPE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,99}\Z")


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _digest(value) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("人格准备检查点含重复字段")
        result[key] = value
    return result


def _invalid_constant(_value):
    raise ValueError("人格准备检查点含无效数值")


def _read(path):
    info = _plain_stat(path)
    if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_BYTES:
        raise ValueError("人格准备检查点不是普通文件或超过读取上限")
    with path.open("rb") as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("人格准备检查点超过读取上限")
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_strict_object,
                          parse_constant=_invalid_constant)
    except (UnicodeError, ValueError) as exc:
        raise ValueError("人格准备检查点损坏；不能继承旧审议或刷新重试额度") from exc


def read_record(path: Path) -> dict:
    """Read a private companion record with the checkpoint's strict limits."""
    value = _read(Path(path))
    if not isinstance(value, dict):
        raise ValueError("人格准备配套记录必须是对象")
    return value


def _directory(path, *, create=False):
    if create:
        path.mkdir(parents=True, exist_ok=True)
    if not stat.S_ISDIR(_plain_stat(path).st_mode):
        raise ValueError("人格准备检查点目录无效")


def _stage_digest(stage, payload_digest, dependency_digest, binding_digest, attempt):
    return _digest({"stage": stage, "payload_digest": payload_digest,
                    "dependency_digest": dependency_digest,
                    "binding_digest": binding_digest, "attempt": attempt})


def _validate(record, op_id, binding):
    if (not isinstance(record, dict)
            or set(record) != {"policy", "op_id", "binding", "binding_digest", "revision",
                               "attempts", "stages", "content"}
            or record["policy"] != POLICY or record["op_id"] != op_id
            or type(record["revision"]) is not int or record["revision"] < 0
            or not isinstance(record["binding"], dict)
            or record["binding_digest"] != _digest(record["binding"])):
        raise ValueError("人格准备检查点身份或绑定记录损坏")
    if _json(record["binding"]) != _json(binding):
        raise ValueError("原稿、审查标准、配置或当前人格/会话已变化；不能续跑旧导入")
    attempts, saved = record["attempts"], record["stages"]
    if (not isinstance(attempts, dict) or set(attempts) != set(STAGES)
            or not isinstance(saved, dict) or set(saved) - set(STAGES)):
        raise ValueError("人格准备检查点阶段记录损坏")
    content = record["content"]
    if (not isinstance(content, dict) or set(content) != {"used", "mode"}
            or type(content["used"]) is not int or content["used"] not in (0, 1)
            or (content["used"] == 0 and content["mode"] is not None)
            or (content["used"] == 1 and content["mode"] not in ("mandatory", "optional"))):
        raise ValueError("人格内容修复额度记录损坏")
    dependency = record["binding_digest"]
    prior_success = True
    for stage in STAGES:
        rows = attempts[stage]
        if (not isinstance(rows, list) or LIMITS[stage] is not None and len(rows) > LIMITS[stage]
                or rows and not prior_success):
            raise ValueError("人格准备检查点阶段额度或依赖顺序损坏")
        for number, row in enumerate(rows, 1):
            if (not isinstance(row, dict) or set(row) != {"number", "status", "error_type"}
                    or type(row["number"]) is not int or row["number"] != number
                    or row["status"] not in ("reserved", "failed", "interrupted", "succeeded")
                    or row["status"] in ("reserved", "succeeded") and number != len(rows)
                    or row["status"] == "failed" and (
                        not isinstance(row["error_type"], str) or not _ERROR_TYPE.fullmatch(row["error_type"]))
                    or row["status"] != "failed" and row["error_type"] is not None):
                raise ValueError("人格准备检查点尝试记录损坏")
        if stage not in saved:
            if any(row["status"] == "succeeded" for row in rows):
                raise ValueError("人格准备检查点缺少已成功阶段的成品")
            prior_success = False
            continue
        item = saved[stage]
        if (not prior_success or not rows or rows[-1]["status"] != "succeeded"
                or not isinstance(item, dict)
                or set(item) != {"payload", "payload_digest", "dependency_digest", "digest", "attempt"}
                or type(item["attempt"]) is not int or item["attempt"] != len(rows)
                or item["dependency_digest"] != dependency
                or not isinstance(item["payload_digest"], str)
                or not _HASH.fullmatch(item["payload_digest"])
                or item["payload_digest"] != _digest(item["payload"])
                or item["digest"] != _stage_digest(stage, item["payload_digest"], dependency,
                                                   record["binding_digest"], item["attempt"])):
            raise ValueError("人格准备检查点成品摘要或前置阶段不一致")
        dependency = item["digest"]
    if content["used"] and not attempts["expression"]:
        raise ValueError("人格内容修复额度缺少表达准备尝试")


class Checkpoint:
    """A single import's immutable successes and persistent attempt ledger."""

    def __init__(self, root: Path, binding: dict, *, resume=False):
        self.root = Path(root)
        self.op_id = validate_op_id(self.root.name)
        if not isinstance(binding, dict) or not binding:
            raise ValueError("人格准备检查点需要非空输入绑定")
        # Round-trip rejects non-JSON values and prevents shared mutable input.
        self.binding = json.loads(_json(binding))
        self.path = self.root / FILENAME
        _directory(self.root, create=not resume)
        if resume:
            if not self.path.exists():
                raise ValueError("该导入没有可续跑的成功检查点；旧审计不能替代验收成品")
            self._record = _read(self.path)
            _validate(self._record, self.op_id, self.binding)
            if "expression" not in self._record["stages"]:
                raise ValueError("表达尚未验收成功，不能从检查点续跑或刷新内容修复额度")
            self._disk_digest = _digest(self._record)
            # A process interruption consumes the reserved attempt. Never replay
            # a call whose result may have been lost, or overwrite its audit.
            changed = copy.deepcopy(self._record)
            interrupted = False
            for rows in changed["attempts"].values():
                if rows and rows[-1]["status"] == "reserved":
                    rows[-1]["status"] = "interrupted"
                    interrupted = True
            if interrupted:
                self._save(changed)
        else:
            if self.path.exists():
                raise ValueError("导入已有准备账本；必须显式续跑，不能覆盖或重置额度")
            self._record = {
                "policy": POLICY, "op_id": self.op_id, "binding": self.binding,
                "binding_digest": _digest(self.binding), "revision": 0,
                "attempts": {stage: [] for stage in STAGES}, "stages": {},
                "content": {"used": 0, "mode": None},
            }
            _validate(self._record, self.op_id, self.binding)
            self._check_size(self._record)
            write_record(self.path, self._record)
            self._disk_digest = _digest(self._record)

    @staticmethod
    def _check_size(value):
        # Match write_record's pretty encoding, not only its shorter canonical
        # digest form, so a saved file is always readable under the same cap.
        if len(json.dumps(value, ensure_ascii=False, indent=1, allow_nan=False).encode("utf-8")) > MAX_BYTES:
            raise ValueError("人格准备检查点超过存储上限")

    def _save(self, value):
        # Service serialization is authoritative; this also catches a stale
        # object or external audit edit before any allowance can be overwritten.
        if _digest(_read(self.path)) != self._disk_digest:
            raise ValueError("人格准备检查点已变化；拒绝用旧账本覆盖新尝试")
        value["revision"] = self._record["revision"] + 1
        _validate(value, self.op_id, self.binding)
        self._check_size(value)
        write_record(self.path, value)
        self._record = value
        self._disk_digest = _digest(value)

    def _check_stage(self, stage):
        if stage not in STAGES:
            raise ValueError("人格准备检查点阶段无效")

    def get(self, stage):
        self._check_stage(stage)
        item = self._record["stages"].get(stage)
        return copy.deepcopy(item["payload"]) if item is not None else None

    def reserve(self, stage) -> Path:
        """Spend the allowance before the caller invokes the stage's model."""
        self._check_stage(stage)
        saved = self._record["stages"]
        if stage in saved:
            raise ValueError("已成功阶段不可重新生成；应继承已验证成品")
        if any(key not in saved for key in STAGES[:STAGES.index(stage)]):
            raise ValueError("前置阶段尚未验收，不能继续人格准备")
        rows = self._record["attempts"][stage]
        if rows and rows[-1]["status"] == "reserved":
            raise ValueError("人格准备阶段已有未结束的尝试")
        if LIMITS[stage] is not None and len(rows) >= LIMITS[stage]:
            raise ValueError("该人格准备阶段的固定尝试额度已用尽，续跑不会重置额度")
        number = len(rows) + 1
        stage_root = self.root / "stages" / stage
        _directory(self.root / "stages", create=True)
        _directory(stage_root, create=True)
        attempt_root = stage_root / ("attempt-" + str(number))
        if attempt_root.exists():
            raise ValueError("人格准备尝试目录已存在；不能覆盖原审计")
        record = copy.deepcopy(self._record)
        record["attempts"][stage].append({"number": number, "status": "reserved", "error_type": None})
        self._save(record)
        # Failure here still leaves the persisted reservation spent.
        attempt_root.mkdir()
        return attempt_root

    def succeed(self, stage, payload):
        self._check_stage(stage)
        rows = self._record["attempts"][stage]
        if stage in self._record["stages"] or not rows or rows[-1]["status"] != "reserved":
            raise ValueError("人格准备成功回执没有对应的未结束尝试")
        copied = json.loads(_json(payload))
        if copied is None:
            raise ValueError("人格准备成功成品不能为空")
        index = STAGES.index(stage)
        previous = self._record["stages"].get(STAGES[index - 1]) if index else None
        dependency = previous["digest"] if previous else self._record["binding_digest"]
        payload_digest = _digest(copied)
        record = copy.deepcopy(self._record)
        record["stages"][stage] = {
            "payload": copied, "payload_digest": payload_digest, "dependency_digest": dependency,
            "digest": _stage_digest(stage, payload_digest, dependency,
                                    record["binding_digest"], len(rows)), "attempt": len(rows),
        }
        record["attempts"][stage][-1]["status"] = "succeeded"
        self._save(record)

    def failure(self, stage, exc):
        self._check_stage(stage)
        rows = self._record["attempts"][stage]
        if not rows or rows[-1]["status"] != "reserved":
            return  # A reservation failure must not mutate a completed stage.
        error_type = type(exc).__name__
        if not _ERROR_TYPE.fullmatch(error_type):
            error_type = "Exception"
        record = copy.deepcopy(self._record)
        record["attempts"][stage][-1].update(status="failed", error_type=error_type)
        self._save(record)

    def consume_content_attempt(self, mode):
        if mode not in {"mandatory", "optional"}:
            raise ValueError("人格内容修复模式无效")
        rows = self._record["attempts"]["expression"]
        if not rows or rows[-1]["status"] != "reserved":
            raise ValueError("人格内容修复必须属于正在执行的表达准备")
        if self._record["content"]["used"]:
            raise ValueError("本次导入的唯一内容修复额度已用尽")
        record = copy.deepcopy(self._record)
        record["content"] = {"used": 1, "mode": mode}
        self._save(record)

    def status(self):
        """Body-free metadata; successful expression is not installed persona."""
        saved = self._record["stages"]
        next_stage = next((stage for stage in STAGES if stage not in saved), "install")
        remaining = {stage: None if limit is None else limit - len(self._record["attempts"][stage])
                     for stage, limit in LIMITS.items()}
        pending = any(rows and rows[-1]["status"] == "reserved"
                      for rows in self._record["attempts"].values())
        resumable = ("expression" in saved and not pending and
                     (next_stage == "install" or remaining[next_stage] is None or remaining[next_stage] > 0))
        return {"resume_op_id": self.op_id, "resumable": resumable, "can_resume": resumable,
                "next_stage": next_stage,
                "successful_stages": [stage for stage in STAGES if stage in saved],
                "remaining": remaining, "content_remaining": 1 - self._record["content"]["used"],
                "content_mode": self._record["content"]["mode"], "policy": POLICY}


def open_checkpoint(root: Path, binding: dict, *, resume=False) -> Checkpoint:
    return Checkpoint(root, binding, resume=resume)
