"""Alice 侧观测适配（SDK 补充设计 v1 §模块与依赖/存储与查询合同）。

把通用 SDK 事件接进现有收据队列与安全落盘边界；正文快照存本机专属
观察目录（user/ 已 gitignore），按内容 hash 复用完整块，元数据与正文
分开保存。观察失败不重试模型、不改变聊天结果；读取不触发 flush 或清理。
"""
from __future__ import annotations

import json
import threading
import time
from collections import deque
from pathlib import Path

from . import config as paths
from .context_observer import CapturePolicy, ContextObserver, NullObserver

META_MODULE = "ctx_obs"
EVENTS_MAXLEN = 4000
BODY_DIR_LIMIT_BYTES = 50 * 1024 * 1024   # 基础设计 §原文与存储：首版 50MiB
BODY_RETENTION_SECONDS = 7 * 86400        # 7 天
BODY_MAX_CHARS = 200000
_SWEEP_EVERY = 64
BODY_REQUEST_LIMIT = 10  # O01：正文采集按用户请求计数的上限（与 7 天/50MiB 并列）

_global_lock = threading.Lock()
_global_observer: ContextObserver | NullObserver | None = None
_global_policy: CapturePolicy | None = None
_global_state: dict = {}


def policy_for(cfg) -> CapturePolicy:
    """正文模式由配置显式开启；默认只记有界元数据（基础设计 §原文与存储）。"""
    capture = "full" if getattr(cfg.context, "observer_body_capture", False) else "none"
    return CapturePolicy(text_capture=capture, body_max_chars=BODY_MAX_CHARS)


def observer_for(cfg, body_capture=None) -> ContextObserver | NullObserver:
    """进程级单例；关闭观察时返回空实现，聊天照常运行。

    body_capture：请求边界算出的本次正文采集许可（O01 十请求上限）。
    None 沿用配置；False 时本请求强制只记元数据——不写正文、不冒充完整。
    """
    global _global_observer, _global_policy
    if not getattr(cfg.context, "observer_enabled", True):
        return NullObserver()
    policy = policy_for(cfg)
    if body_capture is False:
        policy = CapturePolicy(text_capture="none", body_max_chars=BODY_MAX_CHARS)
    with _global_lock:
        if _global_observer is None or _global_policy != policy:
            _global_policy = policy
            _global_observer = ContextObserver(sink=AliceSink(), policy=policy)
        return _global_observer


def _body_capture_allowed(store, cfg) -> bool:
    """按用户请求计数并返回本请求是否仍可采集正文。

    计数持久化于 meta，重启不清零；开关处于关闭期时计数归零，
    重新开启后从零重新计满十个用户请求。达到上限后第 11 个请求起
    只记元数据，聊天与观测本体不受影响。
    """
    used = int(store.meta_get("ctx_obs_body_used") or 0)
    if not getattr(cfg.context, "observer_body_capture", False):
        if used:
            store.meta_set("ctx_obs_body_used", "0")
        return False
    if used >= BODY_REQUEST_LIMIT:
        return False
    store.meta_set("ctx_obs_body_used", str(used + 1))
    return True


def begin_request(store, cfg, *, request_id, scope, session_epoch=None,
                  parent_request_id=None):
    """主对话入口：返回 RequestTrace（或空实现）。观察故障不影响业务。"""
    try:
        if not getattr(cfg.context, "observer_enabled", True):
            return NullObserver().begin_request(request_id=request_id)
        allowed = True
        if getattr(cfg.context, "observer_body_capture", False):
            allowed = _body_capture_allowed(store, cfg)
        observer = observer_for(cfg, body_capture=allowed)
        if session_epoch is None:
            session_epoch = store.meta_get("session_epoch")
        return observer.begin_request(request_id=request_id, session_epoch=session_epoch,
                                      scope=scope, parent_request_id=parent_request_id)
    except Exception:
        from .context_observer import NullObserver
        return NullObserver().begin_request(request_id=request_id)


class AliceSink:
    """把 SDK 事件接进宿主：元数据进收据队列，正文进专属快照存储。"""

    def __init__(self):
        self._sweep_counter = 0

    def submit(self, event: dict) -> None:
        store = _global_state.get("store")
        if store is None:
            return
        body_state = self._store_bodies(event)
        if body_state is not None:
            event = {**event, "data": {**event.get("data", {}), **body_state}}
        with store._ctx_obs_lock:
            if len(store._ctx_obs_events) == store._ctx_obs_events.maxlen:
                store._ctx_obs_dropped += 1
            store._ctx_obs_events.append(event)
        self._sweep_counter += 1
        if self._sweep_counter % _SWEEP_EVERY == 0:
            sweep_bodies()

    def _store_bodies(self, event: dict) -> dict | None:
        """正文按 hash 复用落盘；超额/失败不截断冒充完整，改记状态。"""
        data = event.get("data") or {}
        if _global_policy is None or _global_policy.text_capture == "none":
            return None
        states: dict[str, dict] = {}
        changed = False
        if data.get("body_state") == "stored" and isinstance(data.get("content"), str):
            # 块登记事件：正文在 data 顶层
            states["block.body"] = self._write_body(data)
            data.pop("content", None)
            changed = True
        for key, item in list(data.items()):
            if key == "messages" and isinstance(item, dict):
                for entry in item.get("order", []):
                    if entry.get("body") == "stored" and isinstance(entry.get("content"), str):
                        states[f"messages.order.{entry['seq']}.body"] = self._write_body(entry)
                        entry.pop("content", None)
                        changed = True
            elif isinstance(item, dict) and item.get("body") == "stored" and isinstance(item.get("content"), str):
                states[f"{key}.body"] = self._write_body(item)
                item.pop("content", None)
                changed = True
        return states if changed else None

    def _write_body(self, entry: dict) -> dict:
        digest = (entry.get("content_hash") or "").split(":", 1)[-1]
        if not digest or len(digest) < 16:
            return {"state": "hash_unavailable"}
        try:
            directory = _body_dir()
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / digest[:2] / (digest + ".txt")
            if not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                if _dir_size(directory) + len(entry["content"]) > BODY_DIR_LIMIT_BYTES:
                    return {"state": "body_budget_exceeded"}
                path.write_text(entry["content"], encoding="utf-8")
            return {"state": "on_disk", "ref": digest}
        except OSError:
            return {"state": "write_failed"}


# ---- 正文目录管理（读取不触发清理；清理只挂在写入路径的偶数步） ----

def _body_dir() -> Path:
    return Path(paths.DATA_DIR) / "observation" / "bodies"


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


def sweep_bodies(now: float | None = None) -> dict:
    """期限与容量维护：只在写入路径偶发触发；返回清理摘要（供测试）。"""
    now = now if now is not None else time.time()
    removed = expired = 0
    directory = _body_dir()
    if not directory.is_dir():
        return {"removed": 0, "expired": 0}
    victims = []
    for path in directory.rglob("*.txt"):
        try:
            stat = path.stat()
        except OSError:
            continue
        if now - stat.st_mtime > BODY_RETENTION_SECONDS:
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


def read_body(content_hash: str, *, limit: int = 64000) -> dict:
    """按 hash 读正文；无正文明确“未留存”，不用今天的数据补画历史。"""
    if not isinstance(content_hash, str) or not content_hash.startswith("sha256:"):
        return {"stored": False, "reason": "invalid_hash"}
    digest = content_hash.split(":", 1)[-1]
    if len(digest) < 16 or any(ch not in "0123456789abcdef" for ch in digest):
        return {"stored": False, "reason": "invalid_hash"}
    path = _body_dir() / digest[:2] / (digest + ".txt")
    try:
        if path.is_symlink() or not path.is_file():
            return {"stored": False, "reason": "not_retained"}
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {"stored": False, "reason": "unreadable"}
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


def body_quota(store=None) -> dict:
    """正文额度状态（插件 status 用）；只读盘点，不触发清理。"""
    out = {"body_dir_bytes": _dir_size(_body_dir()),
           "limit_bytes": BODY_DIR_LIMIT_BYTES,
           "retention_days": BODY_RETENTION_SECONDS // 86400,
           "policy": "10个用户请求 · 7天 · 50MiB，以先到者为界；达到上限不截断后冒充完整"}
    if store is not None:
        used = int(store.meta_get("ctx_obs_body_used") or 0)
        out["requests"] = {"used": used, "limit": BODY_REQUEST_LIMIT,
                           "remaining": max(0, BODY_REQUEST_LIMIT - used),
                           "exhausted": used >= BODY_REQUEST_LIMIT,
                           "reset": "关闭 observer_body_capture 开关即重置计数；重新开启后重新计满10个用户请求"}
    return out


def plugin_status(cfg, store=None) -> dict:
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


def install(store) -> None:
    """宿主启动时安装队列与全局 sink 绑定（幂等）。"""
    store._ctx_obs_lock = threading.Lock()
    store._ctx_obs_events = deque(maxlen=EVENTS_MAXLEN)
    store._ctx_obs_dropped = 0
    _global_state["store"] = store
