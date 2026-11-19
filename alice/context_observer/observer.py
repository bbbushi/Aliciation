"""ContextObserver 采集接口与空实现（SDK 补充设计 v1 §公共 API 草图）。

层级：会话代际/渠道 → 用户请求 → 模型调用 → 尝试；父子关系保留，
不把同请求多次调用合并成最后一次。SDK 不选择记忆、不裁剪消息、
不调用模型、不新增重试；观察故障不替换业务异常。
"""
from __future__ import annotations

import copy
import itertools
import json
import threading
import time
import uuid

from . import freeze
from .contract import (ATTEMPT_OK, EVENT_ATTEMPT_FINISHED, EVENT_ATTEMPT_STARTED,
                       EVENT_BLOCK_REGISTERED, EVENT_CALL_PREPARED, EVENT_OBSERVATION_GAP,
                       EVENT_REQUEST_FINISHED, EVENT_REQUEST_STARTED, EVENT_RESPONSE_STARTED,
                       EVENT_SELECTION_DECIDED, EVENT_REPLY_POSTPROCESSED, envelope)

CALL_PARAMETER_WHITELIST_NOTE = "final parameters frozen at the model-client boundary"


class CapturePolicy:
    """采集政策：默认只记有界元数据；正文模式由宿主显式开启（有限轮次策略在 sink）。"""

    def __init__(self, *, text_capture: str = "none", head_chars: int = 2000,
                 body_max_chars: int = 200000, max_events_per_request: int = 5000):
        if text_capture not in ("none", "head", "full"):
            raise ValueError("text_capture 须为 none/head/full")
        self.text_capture = text_capture
        self.head_chars = max(0, int(head_chars))
        self.body_max_chars = max(1, int(body_max_chars))
        self.max_events_per_request = max(16, int(max_events_per_request))


class ContextObserver:
    """进程级采集入口；sink 由宿主提供（空实现见 NullObserver）。"""

    def __init__(self, sink, policy: CapturePolicy | None = None):
        self._sink = sink
        self._policy = policy or CapturePolicy()
        self._lock = threading.Lock()
        self._request_seq = itertools.count(1)

    @property
    def policy(self) -> CapturePolicy:
        return self._policy

    def begin_request(self, *, request_id, session_epoch=None, scope=None,
                      parent_request_id=None) -> "RequestTrace":
        with self._lock:
            request_seq = next(self._request_seq)
        trace = RequestTrace(self, request_id=request_id, session_epoch=session_epoch,
                             scope=scope, request_seq=request_seq,
                             parent_request_id=parent_request_id)
        trace._emit(EVENT_REQUEST_STARTED, {
            "parent_request_id": parent_request_id})
        return trace


class RequestTrace:
    """一次用户请求的观测轨迹。所有登记方法只记录事实，不改变业务状态。"""

    def __init__(self, observer: ContextObserver, *, request_id, session_epoch, scope,
                 request_seq: int, parent_request_id=None):
        self._observer = observer
        self._policy = observer.policy
        self._sink = observer._sink
        self.request_id = request_id
        self.session_epoch = session_epoch
        self.scope = scope
        self.request_seq = request_seq
        self.parent_request_id = parent_request_id
        self._lock = threading.Lock()
        self._seq_no = itertools.count(1)
        self._blocks: list[dict] = []
        self._counts: dict[str, int] = {}
        self._dropped = 0
        self._finished = False

    # ---- 组装接入 ----

    def block(self, kind: str, name: str, content, *, source: dict | None = None,
              estimated_tokens: int | None = None) -> int | None:
        """登记一个装配块；登记不向模型新增消息。返回块序号（空实现为 None）。

        content 始终留在请求内注册表供调用边界做位置匹配；是否进入事件
        由采集政策决定（默认只记 hash 与长度）。
        """
        frozen = freeze.freeze_text(content, capture="none", head_chars=0, body_max_chars=1)
        entry = {"seq": len(self._blocks), "kind": str(kind), "name": str(name)[:160],
                 "chars": frozen.get("chars"), "content_hash": frozen.get("content_hash"),
                 "estimated_tokens": estimated_tokens, "source": _bounded_source(source),
                 "content": content if isinstance(content, str) else None}
        if self._policy.text_capture != "none" and isinstance(content, str):
            body = freeze.freeze_text(content, capture=self._policy.text_capture,
                                      head_chars=self._policy.head_chars,
                                      body_max_chars=self._policy.body_max_chars)
            entry["body_state"] = body.get("body")
            if body.get("content") is not None:
                entry["content"] = content
            elif body.get("head") is not None:
                entry["head"] = body["head"]
        self._blocks.append(entry)
        data = {k: v for k, v in entry.items() if k != "content"}
        if entry.get("body_state") == "stored":
            data["content"] = entry["content"]  # 由 sink 落专属存储后剥离
        self._emit(EVENT_BLOCK_REGISTERED, data)
        return entry["seq"]

    def selection(self, source_id, included: bool, reason: str) -> None:
        """登记一条选择决策；只记决策路径实际考虑过的候选。"""
        self._emit(EVENT_SELECTION_DECIDED, {
            "source_id": str(source_id)[:200], "included": bool(included),
            "reason": str(reason)[:120]})

    def selections(self, pairs) -> None:
        for source_id, included, reason in pairs:
            self.selection(source_id, included, reason)

    # ---- 调用接入 ----

    def prepare_call(self, *, purpose: str, tool_round: int = 0, messages=None,
                     tools=None, parameters: dict | None = None,
                     call_id: str | None = None, capture_body: bool = True) -> "PreparedCall":
        """在实际调用前登记一次调用；最终参数在 freeze_final 边界补齐后才发出事件。"""
        call_id = call_id or ("cc_" + uuid.uuid4().hex)
        return PreparedCall(self, call_id=call_id, purpose=str(purpose)[:80],
                            tool_round=int(tool_round), messages=messages,
                            tools=tools, base_parameters=dict(parameters or {}), capture_body=capture_body)

    def reply_processed(self, *, kind: str, detail: dict | None = None) -> None:
        """回复后处理事实：变换审查、回退或替换是否发生（不重跑审查）。"""
        data = {"kind": str(kind)[:80]}
        if isinstance(detail, dict):
            data["detail"] = _bounded(detail, 2000)
        self._emit(EVENT_REPLY_POSTPROCESSED, data)

    def gap(self, where: str, reason: str, detail: dict | None = None) -> None:
        """观测缺口：绑定不符、序列化失败、写入丢失等；不伪造位置或内容。"""
        data = {"where": str(where)[:80], "reason": str(reason)[:120]}
        if isinstance(detail, dict):
            data["detail"] = _bounded(detail, 2000)
        self._emit(EVENT_OBSERVATION_GAP, data)

    def finish(self, *, assistant_turn_id=None, delivery: str | None = None,
               rounds: int | None = None) -> None:
        with self._lock:
            if self._finished:
                return
            self._finished = True
        self._emit(EVENT_REQUEST_FINISHED, {
            "assistant_turn_id": assistant_turn_id, "delivery": delivery,
            "rounds": rounds, "blocks": len(self._blocks),
            "events": dict(self._counts), "dropped_in_sdk": self._dropped})
        self._blocks = []

    # ---- 内部 ----

    def _emit(self, event_type: str, data: dict, parent: dict | None = None) -> None:
        with self._lock:
            if self._finished and event_type != EVENT_REQUEST_FINISHED:
                self._dropped += 1
                return
            if self._counts.get(event_type, 0) >= max(4, self._policy.max_events_per_request // 8):
                # 单类事件上限：选择/块类可能高频，超出按 SDK 内丢弃计数，不阻塞业务。
                if event_type in (EVENT_SELECTION_DECIDED, EVENT_BLOCK_REGISTERED):
                    self._dropped += 1
                    return
            total = sum(self._counts.values())
            if total >= self._policy.max_events_per_request:
                self._dropped += 1
                return
            self._counts[event_type] = self._counts.get(event_type, 0) + 1
            seq_no = next(self._seq_no)
        env = envelope(event_type, request_seq=self.request_seq, seq_no=seq_no,
                       request_id=self.request_id, session_epoch=self.session_epoch,
                       scope=self.scope, data=data, parent=parent)
        env["ts"] = time.time()
        try:
            self._sink.submit(env)
        except Exception:
            # sink 故障不进入业务异常面；证据缺失是可记录状态。
            with self._lock:
                self._dropped += 1


class PreparedCall:
    """一次模型调用的冻结载体；SDK 自身不发送请求。"""

    def __init__(self, trace: RequestTrace, *, call_id, purpose, tool_round,
                 messages, tools, base_parameters: dict, capture_body: bool = True):
        self._trace = trace
        self._policy = trace._policy
        self._capture_body = capture_body
        self.call_id = call_id
        self.purpose = purpose
        self.tool_round = tool_round
        self._messages = messages
        self._tools = tools
        self._base_parameters = base_parameters
        self._final: dict | None = None
        self._frozen_messages: dict | None = None
        self._frozen_tools: dict | None = None
        self._frozen_parameters: dict | None = None
        self._bindings: list[dict] = []
        self._attempt = itertools.count(1)
        self._emitted = False

    def freeze_final(self, kwargs: dict) -> None:
        """在模型客户端边界冻结最终 SDK 参数并发出 call_prepared（同一事件只发一次）。"""
        with _guard(self._trace, "call.freeze_final"):
            if self._emitted:
                return
            self._final = dict(kwargs or {})
            self._messages = self._final.get("messages", self._messages)
            self._tools = self._final.get("tools", self._tools)
            capture = self._policy.text_capture if self._capture_body else "none"
            self._frozen_messages = freeze.freeze_messages(
                self._messages, capture=capture, head_chars=self._policy.head_chars,
                body_max_chars=self._policy.body_max_chars)
            self._frozen_tools = freeze.freeze_tools(self._tools)
            self._frozen_parameters = freeze.freeze_parameters(self._final)
            # Preserve the original message envelopes (including tool_calls),
            # tools and covered parameters. Same capture/retention limits apply.
            input_snapshot = {"body": "none"}
            if capture != "none":
                input_text = freeze.canonical_json({"format": "ctx-input/1",
                    "messages": self._messages, "tools": self._tools,
                    "parameters": self._frozen_parameters})
                input_snapshot = freeze.freeze_text(input_text, capture=capture,
                    head_chars=self._policy.head_chars, body_max_chars=self._policy.body_max_chars)
            self._bindings, match_gaps = freeze.match_bindings(self._messages, self._trace._blocks)
            binding_gaps = freeze.validate_bindings(self._bindings, self._frozen_messages["order"])
            self._trace._emit(EVENT_CALL_PREPARED, {
                "call_id": self.call_id, "purpose": self.purpose,
                "tool_round": self.tool_round,
                "messages": {k: v for k, v in self._frozen_messages.items()},
                "tools": self._frozen_tools,
                "input": input_snapshot,
                "parameters": self._frozen_parameters,
                "bindings": self._bindings,
                "messages_hash": _messages_hash(self._frozen_messages)}, parent={
                    "request_id": self._trace.request_id, "call_id": self.call_id})
            for gap in match_gaps + binding_gaps:
                self._trace.gap("call.prepare_call.bindings", gap.get("reason", "invalid"), gap)
            self._emitted = True

    def request_kwargs(self) -> dict | None:
        """独立参数副本：本次快照与本次请求对应；不可复制时返回 None（业务用自己的参数）。"""
        if self._final is None:
            return None
        try:
            return json.loads(json.dumps(self._final, ensure_ascii=False, allow_nan=False))
        except (TypeError, ValueError, OverflowError, RecursionError):
            return None

    def begin_attempt(self, number: int | None = None) -> "Attempt":
        return Attempt(self, number=number if number is not None else next(self._attempt))


class Attempt:
    """一次真实尝试：进入调用、收到响应、结束三态分立，不互相推断。"""

    def __init__(self, call: PreparedCall, *, number: int):
        self._trace = call._trace
        self._parent = {"request_id": call._trace.request_id,
                        "call_id": call.call_id, "attempt": number}
        self.number = number
        self._response_started = False
        self._finished = False
        self._trace._emit(EVENT_ATTEMPT_STARTED, {"attempt": number}, parent=self._parent)

    def response_started(self) -> None:
        if self._response_started or self._finished:
            return
        self._response_started = True
        self._trace._emit(EVENT_RESPONSE_STARTED, {}, parent=self._parent)

    def finish(self, *, status: str = ATTEMPT_OK, usage: dict | None = None,
               error_type: str | None = None, duration_ms: int | None = None,
               fallback: bool = False, model: str | None = None,
               model_source: str | None = None, usage_state: str | None = None) -> None:
        if self._finished:
            return
        self._finished = True
        self._trace._emit(EVENT_ATTEMPT_FINISHED, {
            "attempt": self.number, "status": str(status)[:40],
            "usage": _bounded(usage or {}, 400), "usage_state": usage_state,
            "error_type": (str(error_type)[:80] if error_type else None),
            "duration_ms": duration_ms, "fallback": bool(fallback),
            "model": (str(model)[:120] if model else None),
            "model_source": model_source}, parent=self._parent)


class NullObserver:
    """空实现：接口同形，零事件。关闭观察或观察故障时业务照常运行。"""

    policy = CapturePolicy()

    def begin_request(self, *, request_id, session_epoch=None, scope=None,
                      parent_request_id=None) -> "NullTrace":
        return NullTrace()


class NullTrace:
    request_id = None
    session_epoch = None
    scope = None

    def block(self, *args, **kwargs):
        return None

    def selection(self, *args, **kwargs):
        return None

    def selections(self, pairs):
        return None

    def prepare_call(self, *, purpose, tool_round=0, messages=None, tools=None,
                     parameters=None, call_id=None, capture_body=True):
        return NullPreparedCall(messages=messages, tools=tools, parameters=parameters)

    def reply_processed(self, *args, **kwargs):
        return None

    def gap(self, *args, **kwargs):
        return None

    def finish(self, *args, **kwargs):
        return None


class NullPreparedCall:
    def __init__(self, *, messages, tools, parameters):
        self.call_id = None
        self.purpose = ""
        self.tool_round = 0
        self._kwargs = dict(parameters or {})

    def freeze_final(self, kwargs: dict) -> None:
        self._kwargs = dict(kwargs or {})

    def request_kwargs(self) -> dict | None:
        # 浅副本：空实现下业务参数本就由调用方持有，不再深拷贝。
        return {k: (copy.deepcopy(v) if k in ("messages", "tools") else v)
                for k, v in self._kwargs.items()}

    def begin_attempt(self, number: int | None = None):
        return NullAttempt()


class NullAttempt:
    def response_started(self) -> None:
        return None

    def finish(self, **kwargs) -> None:
        return None


class _guard:
    """把观察侧异常收进缺口事件；业务异常与业务返回值永不被替换。"""

    def __init__(self, trace, where: str):
        self._trace, self._where = trace, where

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc is not None:
            try:
                self._trace.gap(self._where, type(exc).__name__[:80])
            except Exception:
                pass
            return True  # swallow observation-side failures
        return False


def _bounded_source(source) -> dict | None:
    if not isinstance(source, dict):
        return None
    return {str(k)[:60]: (str(v)[:200] if isinstance(v, str) else v)
            for k, v in list(source.items())[:8]}


def _bounded(value, limit: int):
    text = json.dumps(value, ensure_ascii=False, default=str)
    return value if len(text) <= limit else {"truncated": True, "preview": text[:limit]}


def _messages_hash(frozen_messages: dict) -> str | None:
    from .contract import content_hash
    return content_hash([(entry.get("role"), entry.get("content_hash"))
                         for entry in frozen_messages.get("order", [])])
