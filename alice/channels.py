"""Versioned channel requests. All database access belongs to ChatService's RLock.

The condition protects only admission/cache state. Never wait for the Store lock
while holding it. A reservation spans HTTP acceptance and worker completion;
control operations use the same reservation, so reset cannot replace its Store.
"""
from __future__ import annotations

import copy
import hashlib
import hmac
import json
import re
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime


class ChannelError(ValueError):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code = status, code


def _bad(message="渠道请求格式无效"):
    return ChannelError(400, "invalid_request", message)


def _id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", value):
        raise _bad("请求标识无效")
    return value


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                      separators=(",", ":")).encode("utf-8")).hexdigest()


def _text(value):
    try:
        valid = isinstance(value, str) and bool(value.strip()) and len(value.encode("utf-8")) <= 12000
    except UnicodeError:
        valid = False
    if not valid:
        raise _bad("文本为空、编码无效或超过 12000 字节")
    return value


def normalize_worker_target(value):
    """A GUI routing identity, never a task body, permission or DSH session ID."""
    if value is None:
        return None
    if (not isinstance(value, dict) or set(value) != {'collaborator', 'task_id'}
            or value.get('collaborator') != 'dsh-agent'):
        raise _bad('协作引用目标无效')
    return {'collaborator': 'dsh-agent', 'task_id': _id(value.get('task_id'))}


SCHEMA = """
CREATE TABLE IF NOT EXISTS channel_requests (
 request_id TEXT PRIMARY KEY, session_epoch TEXT NOT NULL, channel_id TEXT NOT NULL,
 source_key TEXT NOT NULL UNIQUE, source_json TEXT NOT NULL, payload_hash TEXT NOT NULL,
 status TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL,
 user_turn_id INTEGER, assistant_turn_id INTEGER, error_code TEXT,
 maintenance_status TEXT NOT NULL DEFAULT 'pending', reply_hash TEXT
);
CREATE TABLE IF NOT EXISTS channel_turns (
 turn_id INTEGER PRIMARY KEY, request_id TEXT NOT NULL, channel_id TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS channel_requests_user_turn ON channel_requests(user_turn_id);
CREATE INDEX IF NOT EXISTS channel_turns_request ON channel_turns(request_id);
CREATE TABLE IF NOT EXISTS channel_deliveries (
 request_id TEXT NOT NULL, segment_index INTEGER NOT NULL,
 segment_hash TEXT NOT NULL, reply_hash TEXT NOT NULL, status TEXT NOT NULL,
 platform_message_id TEXT, error_code TEXT, updated_at REAL NOT NULL,
 PRIMARY KEY(request_id, segment_index)
);
"""


class ChannelCoordinator:
    def __init__(self, service):
        self.service = service
        self.condition = threading.Condition()
        self.slot = None
        self.active_channel = None
        self.active_request = None
        self.closing = False
        self.ready = False
        self.worker = None
        self.cache = {}
        self.instance_id = uuid.uuid4().hex
        self.bind_store()

    def bind_store(self):
        """Called only at startup or while a reset control reservation is held."""
        conn = self.service.store.conn
        conn.executescript(SCHEMA)
        with conn:
            # Additive v1 migration: a historical receipt binds original reply
            # bytes, never the scope/expiry-dependent text displayed today.
            columns = {row[1] for row in conn.execute("PRAGMA table_info(channel_requests)")}
            if "reply_hash" not in columns:
                conn.execute("ALTER TABLE channel_requests ADD COLUMN reply_hash TEXT")
            conn.execute("INSERT OR IGNORE INTO meta(key,value) VALUES('channel_schema_version','1')")
            conn.execute("INSERT OR IGNORE INTO meta(key,value) VALUES('session_epoch',?)", (uuid.uuid4().hex,))
            if conn.execute("SELECT value FROM meta WHERE key='channel_schema_version'").fetchone()[0] != "1":
                raise ValueError("渠道数据库版本不兼容")
            epoch = conn.execute("SELECT value FROM meta WHERE key='session_epoch'").fetchone()[0]
            conn.execute("UPDATE channel_requests SET status=CASE WHEN assistant_turn_id IS NULL "
                         "THEN 'interrupted' ELSE 'completed_with_warning' END, "
                         "error_code='process_interrupted', maintenance_status='interrupted',updated_at=? "
                         "WHERE status IN ('accepted','user_committed','running','generated')", (time.time(),))
        with self.condition:
            self.epoch = epoch
            self.cache.clear()
            self.ready = False

    def enabled(self):
        cfg = self.service.cfg.channels
        return cfg.enabled and cfg.qq_owner.enabled

    def authenticate(self, token):
        if not self.enabled() or not isinstance(token, str) or not 32 <= len(token) <= 256:
            return None
        expected = self.service.cfg.channels.qq_owner.token_sha256
        try:
            actual = hashlib.sha256(token.encode("utf-8")).hexdigest()
        except UnicodeError:
            return None
        return "qq-owner" if hmac.compare_digest(actual, expected) else None

    def authorize(self, channel_id):
        if channel_id != "qq-owner" or not self.enabled():
            raise ChannelError(403, "channel_disabled", "QQ 渠道未启用或无权访问")

    def set_ready(self, ready):
        with self.condition:
            self.ready = bool(ready)

    def activity(self):
        with self.condition:
            state = "draining" if self.closing else "busy" if self.slot else "ready" if self.ready else "not_ready"
            return {"status": state, "protocol_version": 1, "instance_id": self.instance_id,
                    "session_epoch": self.epoch, "active_channel": self.active_channel,
                    "channel": self.active_channel, "request_id": self.active_request,
                    "channels_enabled": self.enabled(), "max_text_bytes": 12000}

    def reserve(self, channel, request_id=None, *, wait=False, notify_foreground=True):
        with self.condition:
            while self.slot is not None and not self.closing:
                if not wait:
                    raise ChannelError(409, "busy", "Alice 正在处理另一项请求，请稍后再试")
                self.condition.wait()
            if self.closing:
                raise ChannelError(503, "draining", "Alice 正在关闭，未接受新请求")
            token = object()
            self.slot, self.active_channel, self.active_request = token, channel, request_id
            discovery = getattr(self.service, "_discovery", None)
            if discovery is not None and notify_foreground:
                discovery.foreground_started()
            return token

    def release(self, token):
        with self.condition:
            if self.slot is token:
                self.slot = self.active_channel = self.active_request = None
                self.condition.notify_all()

    @contextmanager
    def control(self):
        token = self.reserve("control", wait=True)
        try:
            yield
        finally:
            self.release(token)

    def begin_shutdown(self):
        with self.condition:
            self.closing = True
            self.condition.notify_all()

    def drain(self):
        self.begin_shutdown()
        with self.condition:
            while self.slot is not None:
                self.condition.wait()
            worker = self.worker
        if worker is not None and worker.ident is not None and worker is not threading.current_thread():
            worker.join()

    def check_epoch(self, epoch):
        if epoch != self.epoch:
            raise ChannelError(409, "session_changed", "会话已重置，请在本机确认后重新连接")

    def normalize(self, channel_id, payload):
        self.authorize(channel_id)
        if not isinstance(payload, dict) or set(payload) != {"channel_id", "session_epoch", "request_id", "source", "text"}:
            raise _bad()
        if payload["channel_id"] != channel_id:
            raise ChannelError(403, "forbidden", "请求不属于此渠道")
        epoch = payload["session_epoch"]
        self.check_epoch(epoch)
        request_id = _id(payload["request_id"])
        text = _text(payload["text"])
        source = payload["source"]
        required = {"provider", "app_id", "chat_type", "sender_id", "conversation_id", "message_id", "platform_time"}
        if (not isinstance(source, dict) or not required <= set(source)
                or set(source) - required - {"event_id", "msg_idx"}
                or any(not isinstance(v, str) or not v or len(v) > 256
                       or any(ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF for c in v)
                       for v in source.values())):
            raise _bad("消息来源字段无效")
        owner = self.service.cfg.channels.qq_owner
        if (source["provider"] != owner.provider or source["app_id"] != owner.app_id
                or source["chat_type"] != "c2c" or source["sender_id"] != owner.sender_id
                or source["conversation_id"] != "c2c:" + owner.sender_id):
            raise ChannelError(403, "forbidden", "仅接受已绑定所有者的 QQ 私聊")
        try:
            stamp = datetime.fromisoformat(source["platform_time"].replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                raise ValueError()
            timestamp = stamp.timestamp()
        except (ValueError, OverflowError):
            raise _bad("平台消息时间无效") from None
        identity = [source[k] for k in ("provider", "app_id", "chat_type", "conversation_id", "sender_id", "message_id")]
        return {"request_id": request_id, "session_epoch": epoch, "channel_id": channel_id,
                "text": text, "source": dict(source), "source_key": _digest([epoch, *identity]),
                "payload_hash": _digest([identity, text]), "timestamp": timestamp}

    def gui_request(self, text, request_id, epoch, *, search_intent=None, worker_target=None):
        text = _text(text)
        target = normalize_worker_target(worker_target)
        from .dialogue_search import normalize_intent
        intent = normalize_intent(text, search_intent)
        with self.condition:
            epoch = self.epoch if epoch is None else epoch
            self.check_epoch(epoch)
        request_id = _id(request_id) if request_id is not None else uuid.uuid4().hex
        source = {"provider": "local", "chat_type": "owner", **({"search_intent": intent} if intent else {})}
        payload_hash = _digest([text, intent]) if intent else _digest(text)
        if target is not None:
            source['worker_target'] = target
            payload_hash = _digest({'format': 'alice-gui-worker-target-v1', 'text': text,
                                    'search_intent': intent, 'worker_target': target})
        return {"request_id": request_id, "session_epoch": epoch, "channel_id": "gui",
                "text": text, "search_intent": intent, "worker_target": target,
                "source": source, "source_key": _digest([epoch, "gui", request_id]), "payload_hash": payload_hash}

    def _reply_hash_locked(self, row):
        """Freeze legacy metadata without ever hashing an expired source shadow.

        New assistant turns set this hash in their original Store transaction.
        Existing delivery registrations are authoritative for legacy requests.
        """
        if row["reply_hash"]:
            return row["reply_hash"]
        conn = self.service.store.conn
        hashes = [r[0] for r in conn.execute(
            "SELECT DISTINCT reply_hash FROM channel_deliveries WHERE request_id=?", (row["request_id"],))]
        if len(hashes) > 1:
            return None  # Conflicting historical plans cannot establish identity.
        digest = hashes[0] if hashes else None
        if digest is None and row["assistant_turn_id"] is not None:
            attachment = conn.execute("SELECT content FROM source_turns WHERE turn_id=?",
                                      (row["assistant_turn_id"],)).fetchone()
            if attachment is not None:
                original = attachment["content"] or None
            else:
                turn = conn.execute("SELECT content FROM turns WHERE id=? AND role='assistant'",
                                    (row["assistant_turn_id"],)).fetchone()
                original = turn["content"] if turn else None
            if original is not None:
                digest = hashlib.sha256(original.encode("utf-8")).hexdigest()
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            return None
        with conn:
            conn.execute("UPDATE channel_requests SET reply_hash=? WHERE request_id=? AND reply_hash IS NULL",
                         (digest, row["request_id"]))
        return digest

    def _reply_sendable_locked(self, row):
        """Live capability check, separate from immutable historical identity."""
        turn_id = row["assistant_turn_id"]
        if turn_id is None or not self.service.store.conn.execute(
                "SELECT 1 FROM turns WHERE id=? AND role='assistant'", (turn_id,)).fetchone():
            return False
        worker = self.service.store.worker_turn_status(turn_id, scope=row['channel_id'])
        if worker is not None:
            return worker['worker_available']
        attachment = self.service.store.conn.execute("SELECT * FROM source_turns WHERE turn_id=?", (turn_id,)).fetchone()
        if attachment is None:
            return True  # An ordinary reply may itself equal the shadow text.
        return bool(attachment["content"] and attachment["scope"] == row["channel_id"]
                    and attachment["expires_at"] > time.time()
                    and self.service.store.source_packets_for(
                        json.loads(attachment["source_ids_json"]), scope=row["channel_id"]))

    def _view_locked(self, row):
        result = {key: row[key] for key in ("request_id", "session_epoch", "channel_id", "status", "created_at", "updated_at",
                                          "user_turn_id", "assistant_turn_id", "error_code", "maintenance_status")}
        result["reply"] = None
        if row['channel_id'] == 'gui':
            result['worker_target'] = self.service._workers.worker_target_view_locked(
                normalize_worker_target(json.loads(row['source_json']).get('worker_target')), row['session_epoch'])
        result["reply_hash"] = self._reply_hash_locked(row)
        result["delivery_allowed"] = self._reply_sendable_locked(row)
        if row["assistant_turn_id"] is not None:
            turn = self.service.store.conn.execute("SELECT content FROM turns WHERE id=? AND role='assistant'",
                                                   (row["assistant_turn_id"],)).fetchone()
            if turn:
                result["reply"] = self.service.store.turn_display(row["assistant_turn_id"], scope=row["channel_id"])
        return result

    def invalidate_source_cache(self):
        """Source attachments must never survive revocation through cached replies."""
        with self.condition:
            for identity in list(self.cache):
                if self.cache[identity].get("source_bound"):
                    del self.cache[identity]

    def publish_locked(self, request_id):
        row = self.service.store.conn.execute("SELECT * FROM channel_requests WHERE request_id=?", (request_id,)).fetchone()
        if row is None:
            return None
        view = self._view_locked(row)
        source_bound = bool(row["assistant_turn_id"] and self.service.store.turn_source_ids(row["assistant_turn_id"]))
        source_bound = source_bound or bool(view.get('worker_target'))
        cached_view = copy.deepcopy(view)
        if source_bound:
            # Targets also need fresh availability; cached metadata cannot grant
            # interaction after archive, deletion, disconnect or revocation.
            cached_view["reply"] = None
        with self.condition:
            self.cache[request_id] = {"view": cached_view, "payload_hash": row["payload_hash"],
                                      "source_key": row["source_key"], "source": json.loads(row["source_json"]),
                                      "source_bound": source_bound}
            while len(self.cache) > 64:
                self.cache.pop(next(iter(self.cache)))
        return view

    def lookup(self, request):
        """Unknown while Store is busy is not proof of nonexistence."""
        with self.condition:
            cached = self.cache.get(request["request_id"])
            if cached is None:
                cached = next((r for r in self.cache.values() if r["source_key"] == request["source_key"]), None)
            if cached and not cached.get("source_bound"):
                return self._match(request, cached["view"], cached["payload_hash"])
        if not self.service._lock.acquire(blocking=False):
            raise ChannelError(503, "status_unavailable", "请求状态暂不可确认，请继续查询原请求")
        try:
            self.check_epoch(request["session_epoch"])
            row = self.service.store.conn.execute("SELECT * FROM channel_requests WHERE request_id=? OR source_key=?",
                                                 (request["request_id"], request["source_key"])).fetchone()
            if row:
                return self._match(request, self.publish_locked(row["request_id"]), row["payload_hash"])
            return None
        finally:
            self.service._lock.release()

    def _match(self, request, view, payload_hash):
        self.check_epoch(request["session_epoch"])
        if view["channel_id"] != request["channel_id"] or not hmac.compare_digest(payload_hash, request["payload_hash"]):
            raise ChannelError(409, "request_conflict", "相同消息标识的内容或来源不一致")
        return copy.deepcopy(view)

    def accept_locked(self, request):
        self.check_epoch(request["session_epoch"])
        now = time.time()
        with self.service.store.conn:
            self.service.store.conn.execute(
                "INSERT INTO channel_requests(request_id,session_epoch,channel_id,source_key,source_json,payload_hash,"
                "status,created_at,updated_at) VALUES(?,?,?,?,?,?,'accepted',?,?)",
                (request["request_id"], self.epoch, request["channel_id"], request["source_key"],
                 json.dumps(request["source"], ensure_ascii=False), request["payload_hash"], now, now))
        return self.publish_locked(request["request_id"])

    def finish_locked(self, request_id, *, error_code=None, warning=False):
        conn = self.service.store.conn
        row = conn.execute("SELECT assistant_turn_id FROM channel_requests WHERE request_id=?", (request_id,)).fetchone()
        if row is None:
            return
        generated = row[0] is not None
        state = "failed" if not generated else "completed_with_warning" if error_code or warning else "completed"
        with conn:
            conn.execute("UPDATE channel_requests SET status=?,error_code=?,maintenance_status=?,updated_at=? WHERE request_id=?",
                         (state, error_code, "warning" if error_code or warning else "completed", time.time(), request_id))
        self.publish_locked(request_id)

    def status(self, channel_id, request_id):
        self.authorize(channel_id)
        _id(request_id)
        with self.condition:
            cached = self.cache.get(request_id)
            if cached and not cached.get("source_bound") and cached["view"]["channel_id"] == channel_id and self._current_owner(cached["source"]):
                return copy.deepcopy(cached["view"])
        if not self.service._lock.acquire(blocking=False):
            raise ChannelError(503, "status_unavailable", "请求状态暂不可确认，请继续查询原请求")
        try:
            row = self.service.store.conn.execute("SELECT * FROM channel_requests WHERE request_id=? AND channel_id=?",
                                                 (request_id, channel_id)).fetchone()
            if row is None or not self._current_owner(json.loads(row["source_json"])):
                raise ChannelError(404, "not_found", "本会话中没有该渠道的请求")
            return copy.deepcopy(self.publish_locked(request_id))
        finally:
            self.service._lock.release()

    def _current_owner(self, source):
        owner = self.service.cfg.channels.qq_owner
        return (source.get("provider") == "qq-official" and source.get("app_id") == owner.app_id
                and source.get("chat_type") == "c2c" and source.get("sender_id") == owner.sender_id)

    def submit(self, channel_id, payload):
        request = self.normalize(channel_id, payload)
        prior = self.lookup(request)
        if prior is not None:
            return 200, prior
        age = time.time() - request["timestamp"]
        if not -60 <= age <= 300:
            raise ChannelError(409, "message_expired", "消息已过接收时限或时间无效，未调用模型")
        token = self.reserve(channel_id, request["request_id"])
        try:
            with self.service._lock:
                self.check_epoch(request["session_epoch"])
                if not self.ready:
                    raise ChannelError(409, "not_ready", "请先在本机完成 Alice 人格初始化")
                prior = self.lookup(request)
                if prior is not None:
                    return 200, prior
                result = self.accept_locked(request)
            worker = threading.Thread(target=self._run, args=(token, request), name="alice-channel", daemon=False)
            with self.condition:
                self.worker = worker
            try:
                worker.start()
            except BaseException:
                with self.condition:
                    if self.worker is worker:
                        self.worker = None
                with self.service._lock:
                    self.finish_locked(request["request_id"], error_code="worker_start_failed")
                raise
            token = None  # Worker owns the reservation from now on.
            return 202, result
        finally:
            if token is not None:
                self.release(token)

    def _run(self, token, request):
        try:
            with self.service._lock:
                self.check_epoch(request["session_epoch"])
                with self.condition:
                    if self.slot is not token:
                        raise ChannelError(409, "reservation_lost", "请求已失效")
            self.service._send_locked(request, owner_token=token)
        except BaseException:
            with self.service._lock:
                if request["session_epoch"] == self.epoch:
                    try:
                        self.finish_locked(request["request_id"], error_code="processing_failed")
                    except Exception:
                        # Disk failure cannot be made durable; leave the previously
                        # committed phase for startup recovery and stop new work.
                        self.begin_shutdown()
        finally:
            self.release(token)

    def delivery(self, channel_id, payload):
        self.authorize(channel_id)
        required = {"request_id", "session_epoch", "segment_index", "segment_hash", "reply_hash", "status"}
        if not isinstance(payload, dict) or not required <= set(payload) or set(payload) - required - {"platform_message_id", "error_code"}:
            raise _bad()
        _id(payload["request_id"])
        self.check_epoch(payload["session_epoch"])
        index, state = payload["segment_index"], payload["status"]
        if (type(index) is not int or not 0 <= index < 3 or not isinstance(state, str)
                or state not in {"ready", "sending", "platform_confirmed", "failed", "send_unknown"}):
            raise _bad()
        for key in ("segment_hash", "reply_hash"):
            if not isinstance(payload[key], str) or not re.fullmatch(r"[0-9a-f]{64}", payload[key]):
                raise _bad()
        for key in ("platform_message_id", "error_code"):
            v = payload.get(key)
            if v is not None and (not isinstance(v, str) or len(v) > 256
                                  or any(ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF for c in v)):
                raise _bad()
        if state == "platform_confirmed" and not payload.get("platform_message_id"):
            raise _bad("平台确认回执需要真实消息标识")
        if not self.service._lock.acquire(blocking=False):
            raise ChannelError(503, "status_unavailable", "发送记录暂不可写，请保留本机发送账本后重试")
        try:
            self.check_epoch(payload["session_epoch"])
            conn = self.service.store.conn
            request = conn.execute("SELECT * FROM channel_requests WHERE request_id=? AND channel_id=?",
                                   (payload["request_id"], channel_id)).fetchone()
            if request is None or not self._current_owner(json.loads(request["source_json"])):
                raise ChannelError(404, "not_found", "本会话中没有该渠道的请求")
            original_hash = self._reply_hash_locked(request)
            if original_hash is None or not hmac.compare_digest(original_hash, payload["reply_hash"]):
                raise ChannelError(409, "reply_conflict", "发送记录不匹配原回复")
            # ready only registers immutable plan metadata. Every sending call,
            # including a repeated sending status after QQ 401/429, is a new
            # authorization check. Terminal receipts record facts, not permission.
            if state == "sending" and not self._reply_sendable_locked(request):
                raise ChannelError(409, "source_unavailable", "回复依赖的来源已过期或不再允许用于该渠道，未发分段已停止")
            old = conn.execute("SELECT * FROM channel_deliveries WHERE request_id=? AND segment_index=?",
                               (payload["request_id"], index)).fetchone()
            if old:
                if old["segment_hash"] != payload["segment_hash"] or old["reply_hash"] != payload["reply_hash"]:
                    raise ChannelError(409, "delivery_conflict", "分段内容不可变更")
                if (old["platform_message_id"] and payload.get("platform_message_id")
                        and old["platform_message_id"] != payload["platform_message_id"]):
                    raise ChannelError(409, "delivery_conflict", "平台消息回执不可变更")
                # Bridge may crash after persisting local `sending` but before its
                # HTTP `sending` receipt reaches us. Conservatively preserve unknown.
                # A confirmed message ID is also a historical fact: accepting
                # ready -> confirmed does not grant a fresh dispatch permission.
                transitions = {"ready": {"sending", "platform_confirmed", "failed", "send_unknown"}, "sending": {"platform_confirmed", "failed", "send_unknown"},
                               "failed": {"sending"}, "send_unknown": {"platform_confirmed"}, "platform_confirmed": set()}
                if state != old["status"] and state not in transitions[old["status"]]:
                    raise ChannelError(409, "delivery_conflict", "发送状态不能回退")
            elif state != "ready":
                raise ChannelError(409, "delivery_conflict", "请先登记固定分段")
            with conn:
                conn.execute("INSERT INTO channel_deliveries VALUES(?,?,?,?,?,?,?,?) "
                             "ON CONFLICT(request_id,segment_index) DO UPDATE SET status=excluded.status,"
                             "platform_message_id=COALESCE(excluded.platform_message_id,channel_deliveries.platform_message_id),"
                             "error_code=excluded.error_code,updated_at=excluded.updated_at",
                             (payload["request_id"], index, payload["segment_hash"], payload["reply_hash"], state,
                              payload.get("platform_message_id"), payload.get("error_code"), time.time()))
            return {"request_id": payload["request_id"], "segment_index": index, "status": state}
        finally:
            self.service._lock.release()
