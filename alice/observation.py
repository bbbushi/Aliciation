"""Bounded, read-only observer queries. Caller must hold ChatService's lock.

No Store construction, mutation, recall, maintenance or model calls belong here.
Snapshot fingerprints describe sampled stored values, not a durable event stream.
"""
from __future__ import annotations

import hashlib
import heapq
import json
import os
import re
import time
from pathlib import Path

from . import config as paths


class ObservationError(ValueError):
    """Fixed user-facing messages; never include database, path or provider text."""


def context_basis(store, *, limit="10"):
    """Read host-recorded assembly metadata; no recall, counters or model calls."""
    count = _number(limit, default=10, maximum=20)
    if count < 1:
        raise ObservationError("记录条数须为1至20")
    items = []
    for row in store.conn.execute(
            "SELECT id,ts,detail FROM turn_metrics WHERE module='context_basis' ORDER BY id DESC LIMIT ?",
            (count,)):
        if len(row["detail"]) > 64000:
            continue
        try:
            value = json.loads(row["detail"])
        except (ValueError, TypeError):
            continue
        if isinstance(value, dict):
            items.append({"id": row["id"], "recorded_at": row["ts"], "basis": value})
    return {"items": items}


def llm_calls(store, *, limit="100", request_id=None):
    """Receipt-only public projection, including queued observations after failures."""
    count = _number(limit, default=100, maximum=500)
    if count < 1 or (request_id is not None and
                     (not isinstance(request_id, str) or len(request_id) > 160)):
        raise ObservationError("调用观察参数无效")
    items = []
    # Bounded scan; filtering does not execute maintenance or flush pending rows.
    for row in store.conn.execute(
            "SELECT detail FROM turn_metrics WHERE module LIKE 'llm_call:%' ORDER BY id DESC LIMIT 2000"):
        try:
            event = json.loads(row["detail"])
        except (ValueError, TypeError):
            continue
        if isinstance(event, dict) and (request_id is None or event.get("request_id") == request_id):
            items.append({**event, "storage": "committed"})
    with store._llm_event_lock:
        items += [{**event, "storage": "pending"} for event in store._llm_events
                  if request_id is None or event.get("request_id") == request_id]
        dropped = store._llm_events_dropped
    items.sort(key=lambda e: e.get("ts", 0), reverse=True)
    return {"items": items[:count], "scan_limit": 2000, "pending_dropped": dropped,
            "notice": "无正文调用回执；usage未知不等于零，pending未承诺跨崩溃持久。"}


def context_requests(store, *, limit="20", request_id=None):
    """按请求聚合的上下文观测投影（SDK 补充设计 v1 §存储与查询合同）。

    只读：不清队列、不 flush、不触发维护或模型调用。pending 不承诺跨崩溃持久。
    """
    count = _number(limit, default=20, maximum=100)
    if request_id is not None and (not isinstance(request_id, str) or len(request_id) > 160):
        raise ObservationError("观察参数无效")
    events = []
    for row in store.conn.execute(
            "SELECT detail FROM turn_metrics WHERE module='ctx_obs' ORDER BY id DESC LIMIT 8000"):
        if len(row["detail"]) > 131072:
            continue
        try:
            event = json.loads(row["detail"])
        except (ValueError, TypeError):
            continue
        if isinstance(event, dict):
            events.append({**event, "storage": "committed"})
    with store._ctx_obs_lock:
        events.extend({**event, "storage": "pending"} for event in store._ctx_obs_events)
        dropped = store._ctx_obs_dropped
    if request_id is not None:
        events = [event for event in events if event.get("request_id") == request_id]
    from .context_observation import body_reason
    requests = _aggregate_requests(events, count)
    for request in requests:
        retained = {"stored", "head", "on_disk"}
        captured = (any(m.get("body") in retained for call in request["calls"] for m in call["messages"]["order"])
                    or any((call.get("input") or {}).get("body") in retained for call in request["calls"])
                    or any(b.get("body_state") in retained for b in request["blocks"]))
        reason = body_reason(store, request["request_id"], captured=captured, ts=request.get("first_event_at"))
        if not captured and request.get("body_capture_reason") not in (None, "rolling_capture"):
            reason = request["body_capture_reason"]
        for block in request["blocks"]:
            if reason or not block.get("body_state"):
                block["body_state"] = reason or "historical_not_captured"
        for call in request["calls"]:
            if call.get("input") and (reason or call["input"].get("body") == "none"):
                call["input"]["body"] = reason or "historical_not_captured"
            for message in call["messages"]["order"]:
                if reason or message.get("body") == "none":
                    message["body"] = reason or "historical_not_captured"
    return {"requests": requests, "pending_dropped": dropped,
            "notice": "快照为应用提交给模型客户端的参数投影，不是 HTTP 原始字节或供应商内部上下文；"
                      "无正文处如实显示未留存。"}


def _aggregate_requests(events, count: int) -> list[dict]:
    """把事件流聚合为请求→调用→尝试的投影；不合并同请求多次调用。"""
    order: dict[str, dict] = {}
    for event in sorted(events, key=lambda e: (e.get("request_seq") or 0, e.get("seq_no") or 0)):
        rid = str(event.get("request_id") or "?")
        bucket = order.get(rid)
        if bucket is None:
            bucket = {"request_id": rid, "request_seq": event.get("request_seq"),
                      "session_epoch": event.get("session_epoch"), "scope": event.get("scope"),
                      "storage": event.get("storage"), "first_event_at": event.get("ts"),
                      "last_event_at": event.get("ts"), "event_count": 0, "calls": {},
                      "call_order": [], "blocks": [], "selections": [], "gaps": [],
                      "reply_postprocess": None, "reply_sections": None, "finished": False,
                      "delivery": None, "assistant_turn_id": None}
            order[rid] = bucket
        bucket["last_event_at"] = max(bucket["last_event_at"] or 0, event.get("ts") or 0)
        bucket["event_count"] += 1
        etype, data = event.get("type"), event.get("data") or {}
        if etype == "request_started":
            bucket["body_capture_reason"] = data.get("body_capture_reason")
        elif etype == "block_registered":
            bucket["blocks"].append({k: data.get(k) for k in
                ("seq", "kind", "name", "chars", "content_hash", "estimated_tokens", "body_state")})
        elif etype == "selection_decided":
            bucket["selections"].append({k: data.get(k) for k in ("source_id", "included", "reason")})
        elif etype == "call_prepared":
            call = {"call_id": data.get("call_id"), "purpose": data.get("purpose"),
                    "tool_round": data.get("tool_round"),
                    "messages": {"count": (data.get("messages") or {}).get("count"),
                                 "total_chars": (data.get("messages") or {}).get("total_chars"),
                                 "order": [{**{k: v for k, v in entry.items() if k != "content"},
                                            "body": (data.get(f"messages.order.{entry['seq']}.body") or {}).get("state", entry.get("body"))}
                                           for entry in (data.get("messages") or {}).get("order", [])]},
                    "input": ({**{k: v for k, v in data["input"].items() if k != "content"},
                               "body": (data.get("input.body") or {}).get("state", data["input"].get("body"))}
                              if isinstance(data.get("input"), dict) else None),
                    "tools": [t.get("name") for t in (data.get("tools") or {}).get("items", [])],
                    "parameters": data.get("parameters"),
                    "bindings": data.get("bindings"), "messages_hash": data.get("messages_hash"),
                    "attempts": []}
            bucket["calls"][call["call_id"]] = call
            bucket["call_order"].append(call["call_id"])
        elif etype in ("attempt_started", "response_started", "attempt_finished"):
            call = bucket["calls"].get((event.get("parent") or {}).get("call_id"))
            if call is None:
                continue
            if etype == "attempt_started":
                call["attempts"].append({"attempt": data.get("attempt"), "response_started": False,
                                         "status": "entered"})
            elif etype == "response_started":
                if call["attempts"]:
                    call["attempts"][-1]["response_started"] = True
            else:
                if call["attempts"]:
                    last = call["attempts"][-1]
                    last.update({k: data.get(k) for k in
                                 ("status", "usage", "usage_state", "duration_ms", "error_type",
                                  "fallback", "model", "model_source")})
        elif etype == "reply_postprocessed":
            if data.get("kind") == "reply_sections":
                bucket["reply_sections"] = data.get("detail")
            else:
                bucket["reply_postprocess"] = data
        elif etype == "request_finished":
            bucket["finished"] = True
            bucket["delivery"] = data.get("delivery")
            bucket["assistant_turn_id"] = data.get("assistant_turn_id")
            bucket["sdk_dropped"] = data.get("dropped_in_sdk")
        elif etype == "observation_gap":
            bucket["gaps"].append(data)
    items = []
    for bucket in sorted(order.values(), key=lambda b: (b["first_event_at"] or 0, b["request_seq"] or 0), reverse=True)[:count]:
        bucket["calls"] = [bucket["calls"][cid] for cid in bucket.pop("call_order")]
        items.append(bucket)
    return items


def context_body(store, *, content_hash=None, request_id=None, max_chars=64000):
    """按 hash 读正文；核心校验（插件设计 §6）：须绑定请求归属，裸 hash 不开放。"""
    from .context_observation import read_body, request_hashes
    if request_id is None:
        return {"stored": False, "reason": "binding_required"}
    hashes = request_hashes(store, request_id)
    if hashes is None:
        return {"stored": False, "reason": "request_unknown"}
    if content_hash not in hashes:
        return {"stored": False, "reason": "hash_not_in_request"}
    # Metadata ownership is necessary but not sufficient: require captured evidence.
    request = next((r for r in context_requests(store, request_id=request_id)["requests"]
                    if r["request_id"] == request_id), None)
    if request is None:
        return {"stored": False, "reason": "request_unknown"}
    states = [b["body_state"] for b in request["blocks"] if b.get("content_hash") == content_hash]
    states += [m.get("body") for call in request["calls"] for m in call["messages"]["order"]
               if m.get("content_hash") == content_hash]
    states += [call["input"].get("body") for call in request["calls"]
               if call.get("input") and call["input"].get("content_hash") == content_hash]
    if not any(s in ("stored", "head", "on_disk") for s in states):
        return {"stored": False, "reason": states[0] if states else "historical_not_captured"}
    return read_body(store, content_hash, request_id=request_id, limit=max_chars)


def context_call(store, *, request_id=None, call_id=None):
    """Read one real frozen call, without assembling from current user state."""
    if (not isinstance(request_id, str) or not 1 <= len(request_id) <= 160
            or not isinstance(call_id, str) or not 1 <= len(call_id) <= 160):
        raise ObservationError("请选择请求与调用")
    projection = context_requests(store, request_id=request_id)
    request = next((r for r in projection["requests"] if r["request_id"] == request_id), None)
    call = next((c for c in request["calls"] if c["call_id"] == call_id), None) if request else None
    if call is None:
        raise ObservationError("调用记录不存在或已离开观测窗口")
    from .context_observation import read_body
    def retained_body(entry, limit=200000):
        # Entry belongs to this exact call in the already checked projection;
        # read_body additionally verifies durable request/hash capture evidence.
        if entry.get("body") not in {"stored", "head", "on_disk"}:
            return {"stored": False, "reason": entry.get("body", "historical_not_captured")}
        return read_body(store, entry.get("content_hash"), request_id=request_id, limit=limit)
    snapshot = call.get("input") or {}
    loaded = retained_body(snapshot) if snapshot else {}
    if loaded.get("stored") and not loaded.get("truncated"):
        try:
            value = json.loads(loaded["text"])
            if value.get("format") == "ctx-input/1" and isinstance(value.get("messages"), list):
                return {"request_id": request_id, "call_id": call_id, "complete": True,
                        "coverage": "snapshot", "purpose": call["purpose"], "tool_round": call["tool_round"],
                        "input": value, "call": call, "notice": projection["notice"]}
        except (ValueError, TypeError, AttributeError):
            loaded = {"reason": "snapshot_unreadable"}
    # Older captures only froze content strings; retain their original order
    # and placeholders, not a speculative complete message envelope.
    messages, remaining = [], 2_000_000
    for entry in call["messages"]["order"]:
        record = {k: v for k, v in entry.items() if k != "content"}
        body = (retained_body(entry, min(200000, remaining))
                if remaining > 0 else {"stored": False, "reason": "view_budget_exceeded"})
        if body.get("stored"):
            record.update(content=body["text"], retained=True, truncated=body.get("truncated", False))
            remaining -= len(body["text"])
        else:
            record.update(retained=False, reason=body.get("reason", entry.get("body")))
        messages.append(record)
    return {"request_id": request_id, "call_id": call_id, "complete": False,
            "coverage": "messages_only", "purpose": call["purpose"], "tool_round": call["tool_round"],
            "snapshot_reason": loaded.get("reason", snapshot.get("body", "historical_not_captured")),
            "messages": messages, "call": call, "notice": projection["notice"]}


def persona_operations(store, *, limit="20"):

    """Read bounded import journal metadata; no recovery, migration or content replay."""
    count = _number(limit, default=20, maximum=50)
    if count < 1:
        raise ObservationError("记录条数无效")
    from .persona_transaction import public_status
    projection = public_status(store, limit=count)
    items = projection["items"]
    reset = None
    path = paths.DATA_DIR / "reset-op.json"
    try:
        from .persona_transaction import _plain_stat
        _plain_stat(paths.DATA_DIR)
        info = _plain_stat(path)
        if info.st_size > 4 * 1024 * 1024:
            raise ValueError()
        with path.open("rb") as stream:
            payload = stream.read(4 * 1024 * 1024 + 1)
        if len(payload) > 4 * 1024 * 1024:
            raise ValueError()
        rec = json.loads(payload.decode("utf-8"))
        if not isinstance(rec, dict):
            raise ValueError()
        reset = {key: rec.get(key) if re.fullmatch(r"[0-9a-f]{32}", str(rec.get(key))) else None
                 for key in ("op_id", "old_epoch", "new_epoch")}
        reset["phase"] = rec.get("phase") if rec.get("phase") in {
            "moving", "archived", "installing", "completed", "rolled_back"} else "unknown"
        reset["archive"] = rec.get("archive") if re.fullmatch(
            r"[0-9]{8}-[0-9]{6}(?:-[0-9]+)?", str(rec.get("archive"))) else None
    except FileNotFoundError:
        pass
    except (OSError, ValueError, TypeError):
        reset = {"phase": "journal_unreadable"}
    from .expression_update import public_status as expression_status
    return {**projection, "expression_updates": expression_status(store, limit=count),
            "reset": reset, "notice": "只读操作元数据，不触发恢复；启动已被恢复阻止时此接口不可用。"}


PAGE_SIZE = 25
MANIFEST_LIMIT = 2000
TEXT_HASH_LIMIT = 16000
DETAIL_SIZE = 8000
FILE_DETAIL_LIMIT = 4 * 1024 * 1024
HISTORY_NAME = re.compile(r"v[0-9]{3,12}_[0-9]{8}(?:_[0-9]{6}_[0-9a-f]{6})?\.md\Z")
MEMORY_META = (
    "id, created_at, layer, kind, salience, pressure, mention_count, "
    "use_count, last_used_at, promoted_at, origin, scope, expires_at, lifecycle, provenance_status"
)
SKILL_META = "id, created_at, use_count, last_used_at"

# These scope fields are host-written JSON arrays of fixed channel IDs. Exact
# quoted membership works without depending on an optional SQLite JSON module.
# Every necessary source must still be usable; a valid subset is insufficient.
GUI_MEMORY = """(
 memories.origin='conversation' OR (
  memories.origin IN ('external','external_derived')
  AND memories.expires_at>? AND instr(memories.scope,'"gui"')>0
  AND EXISTS(SELECT 1 FROM source_memory_dependencies d WHERE d.memory_id=memories.id)
  AND NOT EXISTS(
   SELECT 1 FROM source_memory_dependencies d LEFT JOIN source_packets p ON p.id=d.packet_id
   WHERE d.memory_id=memories.id AND
    (p.id IS NULL OR p.revoked!=0 OR p.expires_at<=? OR instr(p.scope_json,'"gui"')=0)
  )
 )
 OR (
  memories.origin IN ('external','external_derived') AND memories.lifecycle='memory'
  AND memories.provenance_status='valid' AND instr(memories.scope,'"gui"')>0
  AND (memories.expires_at IS NULL OR memories.expires_at>strftime('%s','now'))
  AND EXISTS(SELECT 1 FROM memory_evidence_links l WHERE l.memory_id=memories.id)
  AND NOT EXISTS(
   SELECT 1 FROM memory_evidence_links l LEFT JOIN retained_evidence e ON e.id=l.evidence_id
   WHERE l.memory_id=memories.id AND (e.id IS NULL OR e.revoked!=0
    OR (e.expires_at IS NOT NULL AND e.expires_at<=strftime('%s','now')) OR instr(e.scope_json,'"gui"')=0)
  )
 )
)"""


def _number(value, *, default=None, maximum=9223372036854775807) -> int | None:
    if value is None or value == "":
        return default
    if isinstance(value, bool) or not re.fullmatch(r"[0-9]{1,19}", str(value)):
        raise ObservationError("观察参数无效")
    result = int(value)
    if result > maximum:
        raise ObservationError("观察参数超出范围")
    return result


def _filename(value) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str) or not HISTORY_NAME.fullmatch(value):
        raise ObservationError("历史快照编号无效")
    return value


def _history_order(name: str) -> tuple[int, str, str]:
    version, date = name[:-3].split("_", 1)
    return int(version[1:]), date, name


def _safe_file(path: Path) -> bool:
    # A file-shaped symlink must not turn this endpoint into arbitrary file access.
    return not paths.PERSONA_DIR.is_symlink() and not path.is_symlink() and path.is_file()


def _head(path: Path, limit: int) -> dict:
    if not path.exists():
        return {"text": "", "truncated": False}
    if not _safe_file(path):
        raise ObservationError("观察文件不可用")
    with path.open("r", encoding="utf-8") as stream:
        text = stream.read(limit + 1)
    return {"text": text[:limit], "truncated": len(text) > limit}


def _log_tail() -> dict:
    path = paths.PERSONA_CHANGELOG
    if not path.exists():
        return {"text": "", "truncated": False}
    if not _safe_file(path):
        raise ObservationError("变更记录不可用")
    with path.open("rb") as stream:
        stream.seek(0, 2)
        size = stream.tell()
        start = max(0, size - 64004)
        stream.seek(start)
        raw = stream.read(64004)
    # Only the leading partial UTF-8 code point may be omitted at a byte boundary.
    if start:
        while raw and raw[0] & 0xC0 == 0x80:
            raw = raw[1:]
    text = raw.decode("utf-8")
    return {"text": text[-16000:], "truncated": start > 0 or len(text) > 16000}


def snapshot(store, cfg) -> dict:
    sampled_at = time.time()
    counts = {layer: store.count_in_layer(layer) for layer in ("active", "warehouse", "longterm")}
    items = []
    rows = store.conn.execute(
        f"SELECT {MEMORY_META}, substr(title,1,?) AS hash_title, "
        "substr(content,1,?) AS hash_content, substr(keywords,1,?) AS hash_keywords, "
        "length(title) AS title_chars, length(content) AS content_chars, "
        f"length(keywords) AS keywords_chars FROM memories WHERE {GUI_MEMORY} ORDER BY id DESC LIMIT ?",
        (TEXT_HASH_LIMIT, TEXT_HASH_LIMIT, TEXT_HASH_LIMIT, sampled_at, sampled_at, MANIFEST_LIMIT),
    )
    for row in rows:
        item = dict(row)
        texts = [item.pop("hash_" + field) for field in ("title", "content", "keywords")]
        lengths = [item.pop(field + "_chars") for field in ("title", "content", "keywords")]
        item["title"] = texts[0][:160]
        item["title_truncated"] = lengths[0] > 160
        item["text_hash"] = hashlib.sha256(
            json.dumps([texts, lengths], ensure_ascii=False).encode("utf-8")
        ).hexdigest()
        item["text_truncated"] = any(n > TEXT_HASH_LIMIT for n in lengths)
        items.append(item)

    visible_total = store.conn.execute(f"SELECT COUNT(*) FROM memories WHERE {GUI_MEMORY}",
                                       (sampled_at, sampled_at)).fetchone()[0]
    cursor = int(store.meta_get("extract_cursor", "0") or 0)
    pending = store.conn.execute("SELECT COUNT(*) FROM turns WHERE id>?", (cursor,)).fetchone()[0]
    batch = store.conn.execute("SELECT role FROM turns WHERE id>? ORDER BY id LIMIT 80", (cursor,)).fetchall()
    evolve = store.meta_get_time("last_evolve_at")
    deliberate = store.meta_get_time("last_deliberate_at")
    distill = store.meta_get_time("last_distill_at")
    maintain = store.meta_get_time("last_consolidate_at")
    person = _head(paths.PERSONA_FILE, 64000)
    person.update(version=int(store.meta_get("persona_version", "0") or 0),
                  seeded_at=store.meta_get_time("persona_seeded_at"))
    return {
        "sampled_at": sampled_at,
        "memory_counts": counts,
        "manifest": {"items": items, "limit": MANIFEST_LIMIT,
                     "total": sum(counts.values()), "visible_total": visible_total,
                     "scope_limited": visible_total != sum(counts.values()),
                     "complete": sum(counts.values()) <= MANIFEST_LIMIT and visible_total == sum(counts.values())},
        "persona": person,
        "changelog": _log_tail(),
        "progress": {
            "extraction": {"cursor": cursor, "pending_messages": pending, "batch_messages": len(batch),
                           "batch_user_messages": sum(row["role"] == "user" for row in batch),
                           "threshold": cfg.memory.extract_every_turns},
            "evolution": {"anchor": evolve, "count": store.count_memories_since(evolve),
                          "threshold": cfg.persona.evolve_every_memories},
            "deliberation": {"anchor": deliberate or evolve,
                             "count": store.count_memories_since(deliberate or evolve, kind="event"),
                             "threshold": cfg.memory.deliberate_every_memories,
                             "uses_evolution_anchor": not bool(deliberate)},
            "distillation": {"anchor": distill, "count": store.count_memories_since(distill),
                             "threshold": cfg.memory.distill_every_memories},
            "maintenance": {"last_at": maintain, "due": sampled_at - maintain >= 3600,
                            "active_days": cfg.context.active_days, "survival_days": cfg.context.survival_days,
                            "survival_min_uses": cfg.context.survival_min_uses,
                            "longterm_capacity": cfg.context.longterm_capacity},
        },
    }


def lab_memory_metadata(store, *, include_titles=False):
    now = time.time()
    fields = MEMORY_META + (",substr(title,1,160) AS title" if include_titles else "")
    rows = store.conn.execute(f"SELECT {fields} FROM memories WHERE {GUI_MEMORY} "
                              "ORDER BY id DESC LIMIT 40", (now, now))
    return {"counts": {layer: store.count_in_layer(layer) for layer in ("active", "warehouse", "longterm")},
            "items": [dict(row) for row in rows]}


def list_items(store, *, view="memories", layer="all", kind="all", q="", before=None, ceiling=None) -> dict:
    if view not in ("memories", "tombstones", "skills", "history"):
        raise ObservationError("观察列表类型无效")
    if layer not in ("all", "active", "warehouse", "longterm") or kind not in ("all", "event", "stance"):
        raise ObservationError("观察筛选无效")
    if not isinstance(q, str) or len(q) > 200:
        raise ObservationError("检索文字最多 200 字")
    if view == "history":
        return _history_list(q=q, before=before, ceiling=ceiling)
    before = _number(before)
    ceiling = _number(ceiling)
    if before is not None and ceiling is None:
        raise ObservationError("翻页缺少观察上界，请刷新列表")
    table = {"memories": "memories", "skills": "skills", "tombstones": "memory_tombstones"}[view]
    if ceiling is None:
        ceiling = store.conn.execute(f"SELECT COALESCE(MAX(id),0) FROM {table}").fetchone()[0]
    conditions, args = ["id<=?"], [ceiling]
    if before is not None:
        conditions.append("id<?")
        args.append(before)
    if view == "memories":
        sampled_at = time.time()
        conditions.append(GUI_MEMORY)
        args.extend((sampled_at, sampled_at))
        if layer != "all":
            conditions.append("layer=?")
            args.append(layer)
        if kind != "all":
            conditions.append("kind=?")
            args.append(kind)
        fields = f"{MEMORY_META}, substr(title,1,160) AS title, substr(content,1,240) AS preview"
        fields += ", length(title)>160 AS title_truncated, length(content)>240 AS preview_truncated"
        search_fields = ("title", "content", "keywords")
    elif view == "skills":
        fields = f"{SKILL_META}, substr(title,1,160) AS title, substr(content,1,240) AS preview"
        fields += ", length(title)>160 AS title_truncated, length(content)>240 AS preview_truncated"
        search_fields = ("title", "content", "source_titles")
    else:
        fields = "id, memory_id, expired_at, substr(title,1,160) AS title, substr(reason,1,2000) AS reason"
        fields += ", (length(title)>160 OR length(reason)>2000) AS truncated"
        search_fields = ("title", "reason")
    if q:
        # Search original fields without allocating a concatenated copy of long bodies.
        conditions.append("(" + " OR ".join(f"instr(lower({field}),lower(?))>0" for field in search_fields) + ")")
        args.extend([q] * len(search_fields))
    rows = store.conn.execute(
        f"SELECT {fields} FROM {table} WHERE {' AND '.join(conditions)} ORDER BY id DESC LIMIT ?",
        (*args, PAGE_SIZE + 1),
    ).fetchall()
    items = [dict(row) for row in rows[:PAGE_SIZE]]
    return {"items": items, "next_before": items[-1]["id"] if len(rows) > PAGE_SIZE else None,
            "ceiling": ceiling, "sampled_at": time.time()}


def _history_list(*, q, before, ceiling) -> dict:
    before, ceiling = _filename(before), _filename(ceiling)
    if before is not None and ceiling is None:
        raise ObservationError("翻页缺少观察上界，请刷新列表")
    directory = paths.PERSONA_HISTORY_DIR
    if not directory.exists():
        return {"items": [], "next_before": None, "ceiling": ceiling, "sampled_at": time.time()}
    if directory.is_symlink() or paths.PERSONA_DIR.is_symlink():
        raise ObservationError("历史目录不可用")
    deadline = time.monotonic() + 1.0
    before_order = _history_order(before) if before is not None else None
    ceiling_order = _history_order(ceiling) if ceiling is not None else None

    def candidates(entries):
        for entry in entries:
            if time.monotonic() > deadline:
                raise ObservationError("历史条目较多，本次读取超时，请稍后重试")
            name = entry.name
            if (HISTORY_NAME.fullmatch(name) and not entry.is_symlink() and entry.is_file(follow_symlinks=False)
                    and (ceiling_order is None or _history_order(name) <= ceiling_order)
                    and (before_order is None or _history_order(name) < before_order)
                    and q.lower() in name.lower()):
                yield name

    # scandir and nlargest retain only one page even when history is large.
    with os.scandir(directory) as entries:
        names = heapq.nlargest(PAGE_SIZE + 1, candidates(entries), key=_history_order)
    if ceiling is None and names:
        ceiling = names[0]
    items = [{"filename": name, "mtime": (directory / name).stat().st_mtime} for name in names[:PAGE_SIZE]]
    return {"items": items, "next_before": items[-1]["filename"] if len(names) > PAGE_SIZE else None,
            "ceiling": ceiling, "sampled_at": time.time()}


def detail(store, *, view="memories", id=None, offset=0) -> dict:
    offset = _number(offset, default=0, maximum=2147483647)
    if view in ("memories", "skills"):
        identity = _number(id)
        if identity is None:
            raise ObservationError("缺少条目编号")
        fields = MEMORY_META if view == "memories" else SKILL_META
        extra = "keywords" if view == "memories" else "source_titles"
        where, args = "id=?", [offset + 1, DETAIL_SIZE, identity]
        if view == "memories":
            sampled_at = time.time()
            where += " AND " + GUI_MEMORY
            args.extend((sampled_at, sampled_at))
        row = store.conn.execute(
            f"SELECT {fields}, substr(title,1,160) AS title, substr({extra},1,16000) AS {extra}, "
            f"(length(title)>160 OR length({extra})>16000) AS truncated, "
            f"substr(content,?,?) AS text, length(content) AS total_chars FROM {view} WHERE {where}",
            args,
        ).fetchone()
        if row is None:
            raise ObservationError("条目已不存在、已到期或不允许在此界面查看，请刷新列表")
        metadata = dict(row)
        text, total = metadata.pop("text"), metadata.pop("total_chars")
        if view == "memories":
            metadata["source_packet_ids"] = [r[0] for r in store.conn.execute(
                "SELECT packet_id FROM source_memory_dependencies WHERE memory_id=? ORDER BY packet_id", (identity,))]
    elif view in ("persona", "history"):
        if view == "history":
            name = _filename(id)
            if name is None or paths.PERSONA_HISTORY_DIR.is_symlink():
                raise ObservationError("历史快照编号无效")
            path = paths.PERSONA_HISTORY_DIR / name
        else:
            path = paths.PERSONA_FILE
        if not _safe_file(path):
            raise ObservationError("人格或历史快照不存在")
        if path.stat().st_size > FILE_DETAIL_LIMIT:
            raise ObservationError("人格文件超过 4 MiB，详情暂不读取；请在本机检查文件")
        # Bound memory while counting characters (offsets are Unicode characters, not bytes).
        total, selected = 0, []
        with path.open("r", encoding="utf-8") as stream:
            while chunk := stream.read(DETAIL_SIZE):
                start, end = max(0, offset - total), min(len(chunk), offset + DETAIL_SIZE - total)
                if end > start:
                    selected.append(chunk[start:end])
                total += len(chunk)
                if total > FILE_DETAIL_LIMIT:
                    raise ObservationError("人格文件超过读取上限，请在本机检查文件")
        text = "".join(selected)
        metadata = {"filename": path.name, "mtime": path.stat().st_mtime,
                    "version": int(store.meta_get("persona_version", "0") or 0) if view == "persona" else None}
    else:
        raise ObservationError("观察详情类型无效")
    return {"text": text, "offset": offset, "next_offset": offset + len(text) if offset + len(text) < total else None,
            "total_chars": total, "metadata": metadata, "sampled_at": time.time()}
