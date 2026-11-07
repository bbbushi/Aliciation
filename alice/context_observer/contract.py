"""上下文观测事件合同（SDK 补充设计 v1 §事件合同与状态）。

事件是不可变 JSON 兼容 dict，信封与数据体分离。规范化与 hash 基于
明确版本的表示，用于内容比较与快照定位，不充当网络发送证明。
本模块不访问 Store、网络或文件；仅标准库。
"""
from __future__ import annotations

import hashlib
import json
import math
import uuid

SCHEMA_VERSION = "ctx-obs/1"

# 核心事件（设计 §事件合同）：请求生命周期 × 组装登记 × 调用/尝试 × 缺口
EVENT_REQUEST_STARTED = "request_started"
EVENT_BLOCK_REGISTERED = "block_registered"
EVENT_SELECTION_DECIDED = "selection_decided"
EVENT_CALL_PREPARED = "call_prepared"
EVENT_ATTEMPT_STARTED = "attempt_started"
EVENT_RESPONSE_STARTED = "response_started"
EVENT_ATTEMPT_FINISHED = "attempt_finished"
EVENT_REPLY_POSTPROCESSED = "reply_postprocessed"
EVENT_REQUEST_FINISHED = "request_finished"
EVENT_OBSERVATION_GAP = "observation_gap"

# 尝试状态（进入调用≠远端完整接收；流式各态分立，不互相推断）
ATTEMPT_OK = "ok"
ATTEMPT_ERROR = "error"
ATTEMPT_CANCELLED = "cancelled"
ATTEMPT_STREAM_INTERRUPTED = "stream_interrupted"

BODY_NONE = "none"            # 政策未采集正文
BODY_STORED = "stored"        # 正文随事件提交，由 sink 落专属存储
BODY_TOO_LARGE = "too_large"  # 超过单条上限，不截断后冒充完整
BODY_UNSERIALIZABLE = "body_unserializable"

PARAM_WHITELIST = (
    "model", "temperature", "top_p", "max_tokens", "stop", "seed",
    "stream", "stream_options", "response_format", "tool_choice",
    "extra_body", "presence_penalty", "frequency_penalty",
)


def canonical_json(value) -> str | None:
    """版本化规范化表示（排序键、紧缩分隔、禁 NaN）；不可序列化返回 None。"""
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False, default=_reject)
    except (TypeError, ValueError, OverflowError, RecursionError):
        return None


def _reject(_value):
    raise TypeError("unserializable")


def content_hash(value) -> str | None:
    """基于规范化表示的 sha256，带 schema 版本前缀；不可序列化返回 None。"""
    canonical = canonical_json(value)
    if canonical is None:
        return None
    return "sha256:" + hashlib.sha256((SCHEMA_VERSION + "\n" + canonical).encode("utf-8")).hexdigest()


def new_event_id() -> str:
    return "ce_" + uuid.uuid4().hex


def finite_int(value) -> int | None:
    """供应商数字容错：非有限整数一律未知，不填零。"""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value >= 0 else None


def envelope(event_type: str, *, request_seq: int, seq_no: int, request_id,
             session_epoch, scope, data: dict, parent: dict | None = None) -> dict:
    """事件信封：schema 版本、进程内序号、请求身份与父子关系（不合并多次调用）。"""
    env = {
        "schema_version": SCHEMA_VERSION,
        "event_id": new_event_id(),
        "type": event_type,
        "request_seq": request_seq,
        "seq_no": seq_no,
        "request_id": request_id,
        "session_epoch": session_epoch,
        "scope": scope,
        "ts": None,  # 由观测方统一打点，SDK 不假设时钟语义
        "data": data,
    }
    if parent is not None:
        env["parent"] = {k: parent[k] for k in parent if k in ("request_id", "call_id", "attempt")}
    return env
