"""上下文观测 SDK（补充设计 v1）：可嵌入采集核心，仅标准库。

观察上下文形成和使用过程；不选择记忆、不裁剪消息、不调用模型、
不改变权限、不重试请求。关闭观察与观察故障时，聊天按原有业务规则运行。
本包不访问 Alice 的 Store、人格格式、模型厂商或网页——那些在适配层
（alice/context_observation.py）完成。
"""
from .contract import (ATTEMPT_CANCELLED, ATTEMPT_ERROR, ATTEMPT_OK,
                       ATTEMPT_STREAM_INTERRUPTED, BODY_NONE, BODY_STORED, BODY_TOO_LARGE,
                       BODY_UNSERIALIZABLE, SCHEMA_VERSION, content_hash)
from .observer import (Attempt, CapturePolicy, ContextObserver, NullAttempt, NullObserver,
                       NullPreparedCall, NullTrace, PreparedCall, RequestTrace)
from .sink import ListSink, Sink

__all__ = [
    "SCHEMA_VERSION", "ATTEMPT_OK", "ATTEMPT_ERROR", "ATTEMPT_CANCELLED",
    "ATTEMPT_STREAM_INTERRUPTED", "BODY_NONE", "BODY_STORED", "BODY_TOO_LARGE",
    "BODY_UNSERIALIZABLE", "content_hash",
    "CapturePolicy", "ContextObserver", "NullObserver", "RequestTrace", "NullTrace",
    "PreparedCall", "NullPreparedCall", "Attempt", "NullAttempt", "Sink", "ListSink",
]
