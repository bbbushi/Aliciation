"""Alice 侧观测适配（SDK 补充设计 v1 §模块与依赖/存储与查询合同）。

把通用 SDK 事件接进现有收据队列与安全落盘边界；正文快照存本机专属
观察目录（user/ 已 gitignore），按内容 hash 复用完整块，元数据与正文
分开保存。观察失败不重试模型、不改变聊天结果；读取不触发 flush 或清理。
"""
from __future__ import annotations

import json
import math
import os
import re
import tempfile
import threading
import time
from collections import deque
from pathlib import Path

from .context_observer import CapturePolicy, ContextObserver, NullObserver

META_MODULE = "ctx_obs"
EVENTS_MAXLEN = 4000
BODY_DIR_LIMIT_BYTES = 50 * 1024 * 1024   # 基础设计 §原文与存储：首版 50MiB
BODY_RETENTION_SECONDS = 7 * 86400        # 7 天
BODY_MAX_CHARS = 200000
_SWEEP_EVERY = 64
BODY_REQUEST_LIMIT = 10  # Rolling retained requests, not a lifetime capture quota.
_body_lock = threading.RLock()


def policy_for(cfg) -> CapturePolicy:
    """正文模式由配置显式开启；默认只记有界元数据（基础设计 §原文与存储）。"""
    grant = getattr(cfg.context, "_lab_capture", None)
    capture = "full" if getattr(cfg.context, "observer_body_capture", False) or grant and grant() else "none"
    return CapturePolicy(text_capture=capture, body_max_chars=BODY_MAX_CHARS)


def _index_path(store):
    return store._ctx_obs_dir / "captures.json"


def _read_index(store):
    path = _index_path(store)
    if path.is_symlink() or path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError("invalid capture index")
    value = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(value, dict) or value.get("version") != 2
            or not isinstance(value.get("requests"), list) or len(value["requests"]) > BODY_REQUEST_LIMIT):
        raise ValueError("invalid capture index")
    seen = set()
    for row in value["requests"]:
        if (not isinstance(row, dict) or not isinstance(row.get("request_id"), str)
                or not 1 <= len(row["request_id"]) <= 160 or row["request_id"] in seen
                or type(row.get("ts")) not in (int, float) or not math.isfinite(row["ts"])
                or not isinstance(row.get("hashes"), dict)
                or any(not re.fullmatch(r"[a-f0-9]{64}", h) or state not in
                       {"on_disk", "write_failed", "body_budget_exceeded", "not_retained"}
                       for h, state in row["hashes"].items())):
            raise ValueError("invalid capture index")
        seen.add(row["request_id"])
    return value


def _write_index(store, value):
    path = _index_path(store)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix="captures-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            os.chmod(temporary, 0o600)
            json.dump(value, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def _migrate_index(store):
    """Adopt only real historical captures; never reconstruct body=none messages."""
    if _index_path(store).exists():
        _read_index(store)
        return
    requests = {}
    for row in store.conn.execute("SELECT detail FROM turn_metrics WHERE module='ctx_obs' ORDER BY id DESC LIMIT 8000"):
        try:
            event = json.loads(row["detail"])
            rid, ts, data = event["request_id"], event["ts"], event.get("data", {})
            if time.time() - ts > BODY_RETENTION_SECONDS:
                continue
            entries = ([(data, data.get("block.body"))] if data.get("body_state") == "stored" else []) + [
                (m, data.get(f"messages.order.{m['seq']}.body"))
                for m in (data.get("messages") or {}).get("order", []) if m.get("body") == "stored"]
            if isinstance(data.get("input"), dict) and data["input"].get("body") == "stored":
                entries.append((data["input"], data.get("input.body")))
            for entry, receipt in entries:
                h = entry.get("content_hash", "").removeprefix("sha256:")
                if not re.fullmatch(r"[a-f0-9]{64}", h):
                    continue
                recorded = receipt.get("state") if isinstance(receipt, dict) else None
                if recorded not in {"on_disk", "write_failed", "body_budget_exceeded"}:
                    continue
                rec = requests.setdefault(rid, {"request_id": rid, "ts": ts, "hashes": {}})
                rec["ts"] = min(rec["ts"], ts)
                if rec["hashes"].get(h) == "on_disk":
                    continue
                rec["hashes"][h] = (recorded if recorded in {"write_failed", "body_budget_exceeded"} else
                    "on_disk" if (_body_dir(store) / h[:2] / (h + ".txt")).is_file() else "not_retained")
        except (ValueError, KeyError, TypeError):
            continue
    _write_index(store, {"version": 2, "requests": sorted(requests.values(), key=lambda r: r["ts"])[-BODY_REQUEST_LIMIT:]})


def _admit_body_request(store, request_id):
    with _body_lock:
        if store._ctx_obs_closed:
            raise ValueError("observation owner closed")
        value = _read_index(store)
        if not any(r["request_id"] == request_id for r in value["requests"]):
            value["requests"].append({"request_id": request_id, "ts": time.time(), "hashes": {}})
            value["requests"] = value["requests"][-BODY_REQUEST_LIMIT:]
            _write_index(store, value)
            _sweep_bodies_locked(store)


def begin_request(store, cfg, *, request_id, scope, session_epoch=None,
                  parent_request_id=None):
    """主对话入口：返回 RequestTrace（或空实现）。观察故障不影响业务。"""
    try:
        if store._ctx_obs_closed:
            return NullObserver().begin_request(request_id=request_id)
        allowed = policy_for(cfg).text_capture == "full"
        if allowed:
            try:
                _admit_body_request(store, request_id)
            except (OSError, ValueError, TypeError):
                allowed = False  # Metadata must survive an unavailable body index.
                with _body_lock:
                    failures = store._ctx_obs_failures
                    failures[request_id] = "capture_index_unavailable"
                    while len(failures) > BODY_REQUEST_LIMIT:
                        failures.pop(next(iter(failures)))
        if not allowed and not getattr(cfg.context, "observer_enabled", True):
            return NullObserver().begin_request(request_id=request_id)
        policy = CapturePolicy(text_capture="full" if allowed else "none", body_max_chars=BODY_MAX_CHARS)
        if session_epoch is None:
            session_epoch = store.meta_get("session_epoch")
        return store._ctx_observer.begin_request(request_id=request_id, session_epoch=session_epoch,
                                                 scope=scope, parent_request_id=parent_request_id, policy=policy)
    except Exception:
        return NullObserver().begin_request(request_id=request_id)


class AliceSink:
    """把 SDK 事件接进宿主：元数据进收据队列，正文进专属快照存储。"""

    def __init__(self, store):
        self._store = store
        self._sweep_counter = 0

    def submit(self, event: dict) -> None:
        store = self._store
        with _body_lock:
            if store._ctx_obs_closed:
                return
            try:
                index = _read_index(store)
                record = next((r for r in index["requests"] if r["request_id"] == event.get("request_id")), None)
            except (OSError, ValueError, TypeError):
                index, record = None, {"disabled_reason": "capture_index_unavailable"}
            failure = store._ctx_obs_failures.get(event.get("request_id"))
            if failure:
                record = {"disabled_reason": failure}
            if event.get("type") == "request_started":
                event = {**event, "data": {**event.get("data", {}),
                         "body_capture_reason": record.get("disabled_reason", "rolling_capture") if record else "capture_disabled"}}
            body_state = self._store_bodies(event, record)
            if body_state is not None and index is not None:
                _write_index(store, index)
            if body_state is not None:
                event = {**event, "data": {**event.get("data", {}), **body_state}}
            with store._ctx_obs_lock:
                if len(store._ctx_obs_events) == store._ctx_obs_events.maxlen:
                    store._ctx_obs_dropped += 1
                store._ctx_obs_events.append(event)
            self._sweep_counter += 1
            if self._sweep_counter % _SWEEP_EVERY == 0:
                _sweep_bodies_locked(store)

    def _store_bodies(self, event: dict, record) -> dict | None:
        """正文按 hash 复用落盘；超额/失败不截断冒充完整，改记状态。"""
        data = event.get("data") or {}
        states: dict[str, dict] = {}
        changed = False
        if data.get("body_state") == "stored" and isinstance(data.get("content"), str):
            # 块登记事件：正文在 data 顶层
            states["block.body"] = self._write_body(data, record)
            data.pop("content", None)
            changed = True
        for key, item in list(data.items()):
            if key == "messages" and isinstance(item, dict):
                for entry in item.get("order", []):
                    if entry.get("body") == "stored" and isinstance(entry.get("content"), str):
                        states[f"messages.order.{entry['seq']}.body"] = self._write_body(entry, record)
                        entry.pop("content", None)
                        changed = True
            elif isinstance(item, dict) and item.get("body") == "stored" and isinstance(item.get("content"), str):
                states[f"{key}.body"] = self._write_body(item, record)
                item.pop("content", None)
                changed = True
        return states if changed else None

    def _write_body(self, entry: dict, record) -> dict:
        if record is None:
            return {"state": "rolling_window_evicted"}
        if record.get("disabled_reason"):
            return {"state": record["disabled_reason"]}
        result = self._write_file(entry)
        digest = (entry.get("content_hash") or "").removeprefix("sha256:")
        if re.fullmatch(r"[a-f0-9]{64}", digest):
            record["hashes"][digest] = result["state"]
        return result

    def _write_file(self, entry: dict) -> dict:
        digest = (entry.get("content_hash") or "").split(":", 1)[-1]
        if not digest or len(digest) < 16:
            return {"state": "hash_unavailable"}
        try:
            directory = _body_dir(self._store)
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            path = directory / digest[:2] / (digest + ".txt")
            if path.is_symlink():
                return {"state": "write_failed"}
            if not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                if _dir_size(directory) + len(entry["content"].encode("utf-8")) > BODY_DIR_LIMIT_BYTES:
                    return {"state": "body_budget_exceeded"}
                path.write_text(entry["content"], encoding="utf-8")
                path.chmod(0o600)
            os.utime(path, None)  # Shared bytes are still live for this new capture.
            return {"state": "on_disk", "ref": digest}
        except OSError:
            return {"state": "write_failed"}


# ---- 正文目录管理（读取不触发清理；清理只挂在写入路径的偶数步） ----

def _body_dir(store) -> Path:
    return store._ctx_obs_dir / "bodies"


def _dir_size(directory: Path) -> int:
    total = 0
    try:
        for path in directory.rglob("*.txt"):
            try:
                total += path.stat().st_size
            except OSError:
                continue
    except OSError:
        return BODY_DIR_LIMIT_BYTES  # 无法盘点时按已满处理，不写新正文
    return total


def _sweep_bodies_locked(store, now: float | None = None) -> dict:
    """期限与容量维护：只在写入路径偶发触发；返回清理摘要（供测试）。"""
    now = now if now is not None else time.time()
    removed = expired = 0
    directory = _body_dir(store)
    if not directory.is_dir():
        return {"removed": 0, "expired": 0}
    index = _read_index(store)  # Corrupt index: no destructive guessing.
    live = {h for r in index["requests"] if now - r["ts"] <= BODY_RETENTION_SECONDS
            for h, state in r["hashes"].items() if state == "on_disk"}
    victims = []
    for path in directory.rglob("*.txt"):
        try:
            stat = path.stat()
        except OSError:
            continue
        if path.stem not in live or now - stat.st_mtime > BODY_RETENTION_SECONDS:
            victims.append((stat.st_mtime, path))
    for _, path in sorted(victims):
        try:
            path.unlink()
            expired += 1
        except OSError:
            continue
    while _dir_size(directory) > BODY_DIR_LIMIT_BYTES:
        remaining = sorted(
            ((p.stat().st_mtime, p) for p in directory.rglob("*.txt") if p.is_file()),
            key=lambda pair: pair[0])
        if not remaining:
            break
        _, oldest = remaining[0]
        try:
            oldest.unlink()
            removed += 1
        except OSError:
            break
    return {"removed": removed, "expired": expired}


def body_reason(store, request_id, *, captured=True, ts=None):
    """Read-only per-request eligibility; hash reuse never extends an old request."""
    try:
        with _body_lock:
            value = _read_index(store)
    except (OSError, ValueError, TypeError):
        return "capture_index_unavailable"
    row = next((r for r in value["requests"] if r["request_id"] == request_id), None)
    if row is not None:
        return "retention_expired" if time.time() - row["ts"] > BODY_RETENTION_SECONDS else None
    if not captured:
        return "historical_not_captured"
    if ts is not None and time.time() - ts > BODY_RETENTION_SECONDS:
        return "retention_expired"
    return "rolling_window_evicted"


def read_body(store, content_hash: str, *, request_id=None, limit: int = 64000) -> dict:
    """按 hash 读正文；无正文明确“未留存”，不用今天的数据补画历史。"""
    if not isinstance(content_hash, str) or not content_hash.startswith("sha256:"):
        return {"stored": False, "reason": "invalid_hash"}
    digest = content_hash.split(":", 1)[-1]
    if len(digest) < 16 or any(ch not in "0123456789abcdef" for ch in digest):
        return {"stored": False, "reason": "invalid_hash"}
    if request_id is None:
        return {"stored": False, "reason": "binding_required"}
    reason = body_reason(store, request_id)
    if reason:
        return {"stored": False, "reason": reason}
    with _body_lock:
        try:
            rec = next(r for r in _read_index(store)["requests"] if r["request_id"] == request_id)
            state = rec["hashes"].get(digest)
        except StopIteration:
            return {"stored": False, "reason": "rolling_window_evicted"}
        except (OSError, ValueError, TypeError):
            return {"stored": False, "reason": "capture_index_unavailable"}
    if state != "on_disk":
        return {"stored": False, "reason": state or "historical_not_captured"}
    path = _body_dir(store) / digest[:2] / (digest + ".txt")
    try:
        if path.is_symlink() or not path.is_file():
            return {"stored": False, "reason": "not_retained"}
        if time.time() - path.stat().st_mtime > BODY_RETENTION_SECONDS:
            return {"stored": False, "reason": "retention_expired"}
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {"stored": False, "reason": "unreadable"}
    from .context_observer.contract import content_hash as frozen_hash
    if frozen_hash(text) != content_hash:
        return {"stored": False, "reason": "hash_mismatch"}
    return {"stored": True, "hash": content_hash, "text": text[:limit],
            "truncated": len(text) > limit, "chars": len(text)}


def request_hashes(store, request_id) -> set[str] | None:
    """某请求事件里登记过的正文 hash（committed+pending）；请求未知返回 None。

    插件设计 §6：正文读取须验证请求/调用/块的实际归属，不能仅凭内容
    hash 开放全部快照；hash 只用于内容比较。
    """
    if not isinstance(request_id, str) or not 1 <= len(request_id) <= 160:
        return None
    hashes: set[str] = set()
    found = False

    def _collect(event) -> None:
        nonlocal found
        if not isinstance(event, dict) or event.get("request_id") != request_id:
            return
        found = True
        data = event.get("data") or {}
        if isinstance(data.get("content_hash"), str):
            hashes.add(data["content_hash"])
        for entry in (data.get("messages") or {}).get("order", []):
            if isinstance(entry, dict) and isinstance(entry.get("content_hash"), str):
                hashes.add(entry["content_hash"])
        snapshot = data.get("input")
        if isinstance(snapshot, dict) and isinstance(snapshot.get("content_hash"), str):
            hashes.add(snapshot["content_hash"])

    try:
        for row in store.conn.execute(
                "SELECT detail FROM turn_metrics WHERE module='ctx_obs' ORDER BY id DESC LIMIT 8000"):
            if len(row["detail"]) > 131072:
                continue
            try:
                _collect(json.loads(row["detail"]))
            except (ValueError, TypeError):
                continue
        with store._ctx_obs_lock:
            for event in store._ctx_obs_events:
                _collect(event)
    except Exception:
        return None
    return hashes if found else None


def body_quota(store) -> dict:
    """正文额度状态（插件 status 用）；只读盘点，不触发清理。"""
    out = {"body_dir_bytes": _dir_size(_body_dir(store)),
           "limit_bytes": BODY_DIR_LIMIT_BYTES,
           "retention_days": BODY_RETENTION_SECONDS // 86400,
           "policy": "滚动保留最近10个采集请求 · 7天 · 50MiB；持续采集新请求，不截断后冒充完整"}
    try:
        with _body_lock:
            used = sum(time.time() - r["ts"] <= BODY_RETENTION_SECONDS for r in _read_index(store)["requests"])
        out["requests"] = {"used": used, "limit": BODY_REQUEST_LIMIT,
                           "rolling": True, "exhausted": False}
    except (OSError, ValueError, TypeError):
        out["requests"] = {"used": None, "limit": BODY_REQUEST_LIMIT, "rolling": True,
                           "reason": "capture_index_unavailable"}
    return out


def plugin_status(cfg, store) -> dict:
    """功能发现（插件设计 §6）：插件/采集状态、协议版本与正文额度。"""
    from .context_observer import SCHEMA_VERSION
    return {"enabled": bool(getattr(cfg.plugins, "context_inspector_enabled", False)),
            "protocol_version": SCHEMA_VERSION, "interface_version": 1,
            "capture": {"metadata": bool(getattr(cfg.context, "observer_enabled", True)),
                        "body": bool(getattr(cfg.context, "observer_body_capture", False))},
            "quota": body_quota(store) if getattr(cfg.context, "observer_body_capture", False) else None}


# ---- 落盘边界：与 metrics.flush_calls 同一安全边界调用 ----

def flush(store) -> None:
    """仅在持有宿主 Store 锁且不在业务事务内时落账；失败保留待写事件。"""
    if getattr(store, "_ctx_obs_events", None) is None or store.conn.in_transaction:
        return
    with store._ctx_obs_lock:
        pending = list(store._ctx_obs_events)
        if not pending:
            return
        try:
            with store.conn:
                store.conn.executemany(
                    "INSERT INTO turn_metrics(module,value,detail,ts) VALUES(?,?,?,?)",
                    [(META_MODULE, event.get("seq_no", 0),
                      json.dumps(event, ensure_ascii=False), event.get("ts") or time.time())
                     for event in pending])
        except Exception:
            return  # 证据可缺失；不重试模型、不影响业务事务
        for _ in pending:
            store._ctx_obs_events.popleft()


def install(store, db_path: Path) -> None:
    """Attach the event queue and capture directory to the database owner."""
    db_path = db_path.parent.resolve() / db_path.name
    store._ctx_obs_dir = (db_path.parent / "observation" if db_path == db_path.with_name("alice.db")
                          else db_path.with_name(db_path.name + ".observation"))
    store._ctx_obs_lock = threading.Lock()
    store._ctx_obs_events = deque(maxlen=EVENTS_MAXLEN)
    store._ctx_obs_dropped = 0
    store._ctx_obs_closed = False
    store._ctx_obs_failures = {}
    store._ctx_observer = ContextObserver(sink=AliceSink(store))
    try:
        with _body_lock:
            _migrate_index(store)
    except (OSError, ValueError, TypeError):
        pass  # Body failures do not prevent opening Alice or collecting metadata.


def stop_capture(store) -> None:
    with _body_lock:
        store._ctx_obs_closed = True
