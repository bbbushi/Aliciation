"""Read-only projections and short-lived local observation grants."""
from __future__ import annotations

import hashlib
import re
import secrets
import threading
import time

from .channels import ChannelError

GRANT_SECONDS = 900
READS = {
    "schedule": set(), "context": {"request_id"}, "calls": {"request_id"},
    "body": {"request_id", "hash"}, "memory": set(), "memory_detail": {"id"}, "memory_status": {"id"},
    "questions": set(), "persona_operations": set(), "reply_cache": {"request_id"},
    "context_status": set(), "context_call": {"request_id", "call_id"},
}
BODY_READS = {"body", "memory_detail", "memory_status", "reply_cache", "context_call"}
REQUIRED = {"body": {"request_id", "hash"}, "memory_detail": {"id"}, "memory_status": {"id"},
            "reply_cache": {"request_id"}, "context_call": {"request_id", "call_id"}}


def read(service, topic, params, *, bodies=False, scope=None):
    if (not isinstance(topic, str) or topic not in READS or not isinstance(params, dict)
            or set(params) - READS[topic] or REQUIRED.get(topic, set()) - set(params)):
        raise ChannelError(400, "invalid_read", "观察请求参数无效")
    if topic in BODY_READS and not bodies:
        raise ChannelError(403, "body_not_granted", "这个连接没有正文读取权限")
    if any(not isinstance(value, str) or not 1 <= len(value) <= 160 for value in params.values()):
        raise ChannelError(400, "invalid_read", "观察参数长度无效")
    if topic == "schedule":
        return service.schedule_diagnostics()
    if topic == "context":
        return service.observer_context_requests(scope=scope, limit="12", **params)
    if topic == "context_status":
        return service.lab_context_status(scope=scope, bodies=bodies)
    if topic == "context_call":
        return service.observer_context_call(scope=scope, **params)
    if topic == "calls":
        return service.observer_llm_calls(scope=scope, limit="40", **params)
    if topic == "persona_operations":
        return service.observer_persona_operations(scope=scope, limit="20")
    if topic == "body":
        return service.observer_context_body(scope=scope, **params)
    if topic == "reply_cache":
        return service.observer_reply_cache(scope=scope, **params)
    if topic == "memory_detail":
        return service.observer_detail(scope=scope, view="memories", id=params["id"])
    if topic == "memory_status":
        return service.observer_memory_available(scope=scope, id=params["id"])
    return service.lab_data_metadata(topic, scope=scope, include_titles=bodies)


class ObservationGrant:
    def __init__(self, service):
        self.service = service
        self._lock = threading.Lock()
        self._ticket = None

    def issue(self, mode):
        if mode not in ("metadata", "body"):
            raise ChannelError(400, "invalid_grant", "请选择元数据或正文")
        token = secrets.token_urlsafe(32)
        expires = time.time() + GRANT_SECONDS
        with self._lock:
            self._ticket = (self._digest(token), self.service.observation_scope(), mode, expires)
            self.service.cfg.context._lab_capture = (lambda: self._valid_body()) if mode == "body" else None
        return {"mode": mode, "expires_at": expires, "token": token}

    def revoke(self):
        with self._lock:
            self._ticket = None
            self.service.cfg.context._lab_capture = None

    def _valid_body(self):
        with self._lock:
            current = self._ticket
            return bool(current and current[2] == "body" and current[3] > time.time()
                        and current[1] == self.service.observation_scope())

    @staticmethod
    def _digest(token):
        return hashlib.sha256(token.encode("ascii")).digest()

    def _authorize(self, token):
        if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
            raise ChannelError(401, "invalid_grant", "观察授权无效或已过期")
        with self._lock:
            current = self._ticket
            if (not current or current[3] <= time.time()
                    or current[1] != self.service.observation_scope()
                    or not secrets.compare_digest(current[0], self._digest(token))):
                raise ChannelError(401, "invalid_grant", "观察授权无效或已过期")
            return current

    def read(self, token, topic, params):
        grant = self._authorize(token)
        result = read(self.service, topic, params, bodies=grant[2] == "body", scope=grant[1])
        if self._authorize(token) is not grant:
            raise ChannelError(401, "invalid_grant", "观察授权已变化")
        return {"mode": grant[2], "expires_at": grant[3], "result": result}
