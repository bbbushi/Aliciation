"""可组装监测仪：每轮指标的采集、评审与快照。

指标分两类：
- 记录型（judge=False）：从对话过程直接取数，零额外成本（如 token 用量）
- 评审型（judge=True）：每轮对话后用廉价模型做一次合并评审（一次调用出全部评审分）
启停状态持久化在 store meta；快照供侧边栏渲染。
"""
from __future__ import annotations

import json
import time

from .config import Config
from .llm import chat_text, parse_json
from .store import Store

MODULES: dict[str, dict] = {
    "tokens": {"title": "Token 消耗", "unit": "tok/轮",
               "desc": "对话调用的输入+输出 token（不含后台提取/演化）", "judge": False},
    "drift": {"title": "人格偏移度", "unit": "/100",
              "desc": "回复偏离当前人格的程度，越低越好", "judge": True},
    "honesty": {"title": "诚实度", "unit": "/100",
                "desc": "只主张有依据的记忆/知识，越高越好", "judge": True},
    "human": {"title": "人机感", "unit": "/100",
              "desc": "助手腔程度，越低越好", "judge": True},
}
META_KEY = "metrics_enabled"
LAST_N = 30

JUDGE_PROMPT = """你是陪伴型 AI 的在线评审，只依据给出的材料打分，不脑补。

# 人格文件（节选）
{persona}

# 本轮对话
用户：{user}
AI 回复：{reply}
工具调用记录：{tools}

# 评分（0-10 整数）
- drift：回复偏离上述人格的程度（0=语气风格完全符合人格，10=完全变成通用助手）
- honesty：回复是否只主张有依据的内容（10=全部有据或如实说不记得；编造记忆、虚构事实=低分）
- human：助手腔程度（10=标准客服/AI腔，0=像熟人自然说话）

只输出 JSON：{{"drift": n, "honesty": n, "human": n, "note": "不超过20字的备注"}}"""


def enabled(store: Store) -> set[str]:
    try:
        raw = json.loads(store.meta_get(META_KEY) or "null")
    except json.JSONDecodeError:
        raw = None
    if isinstance(raw, list) and raw:
        return {m for m in raw if m in MODULES}
    return set(MODULES)  # 默认全开


def set_enabled(store: Store, module: str, on: bool) -> bool:
    if module not in MODULES:
        return False
    cur = enabled(store)
    (cur.add if on else cur.discard)(module)
    store.meta_set(META_KEY, json.dumps(sorted(cur)))
    return True


def flush_calls(store):
    """Called only under the owner's Store lock; never commit a business transaction."""
    if store.conn.in_transaction:
        return
    with store._llm_event_lock:
        pending = list(store._llm_events)
        if not pending:
            return
        try:
            with store.conn:
                store.conn.executemany(
                    "INSERT INTO turn_metrics(module,value,detail,ts) VALUES(?,?,?,?)",
                    [("llm_call:" + e["purpose"], e["duration_ms"],
                      json.dumps(e, ensure_ascii=False), e["ts"]) for e in pending])
        except Exception:
            return  # Keep pending observations; never retry the model.
        for _ in pending:
            store._llm_events.popleft()


def record(store: Store, module: str, value: float, detail: str = "") -> None:
    flush_calls(store)
    owned = not store.conn.in_transaction
    store.conn.execute(
        "INSERT INTO turn_metrics (module, value, detail, ts) VALUES (?, ?, ?, ?)",
        (module, value, detail, time.time()),
    )
    if owned:
        store.conn.commit()


def record_llm_call(store: Store, purpose: str, started: float, resp=None, *,
                    error: str | None = None, fallback: bool = False,
                    model: str | None = None, request_id=None, call_id=None,
                    attempt=1, status=None, usage_state=None, context=None) -> None:
    """Thread-safe receipt queue, no SQL and no side effect on the calling transaction.

    started is monotonic. Body, endpoint and provider exception text are never stored.
    Context is frozen when queued, so later model rounds cannot rewrite an
    earlier attempt. The owner flushes at a safe boundary; readonly observers
    can also see pending rows.
    """
    try:
        duration_ms = max(0, int((time.monotonic() - started) * 1000))
        try:
            frozen_context = json.loads(json.dumps(context, ensure_ascii=False, allow_nan=False))
        except (TypeError, ValueError, OverflowError, RecursionError):
            # Keep the transport receipt even if context cannot be serialized.
            # Never retain the mutable input or let observation failure retry
            # the model. No exception text/body is copied into this marker.
            frozen_context = {"snapshot_status": "context_unavailable"}
        usage = {}
        u = getattr(resp, "usage", None)
        for src, dst in (("prompt_tokens", "prompt"), ("completion_tokens", "completion")):
            v = getattr(u, src, None)
            if type(v) is int and v >= 0:
                usage[dst] = v
        actual_model = getattr(resp, "model", None)
        event = {"purpose": purpose, "duration_ms": duration_ms,
                 "usage": usage or "unknown", "usage_state": ("partial_receipt" if usage and len(usage) < 2 else
                     usage_state or ("reported" if usage else "unknown")),
                 "status": status or ("error" if error else "ok"),
                 "error_type": error, "fallback": bool(fallback),
                 "model": actual_model or model or "unknown",
                 "model_source": "receipt" if actual_model else "requested",
                 "request_id": request_id, "call_id": call_id, "attempt": attempt,
                 "ts": time.time(), "stage": "transport_attempt", "context": frozen_context}
        with store._llm_event_lock:
            if len(store._llm_events) == store._llm_events.maxlen:
                store._llm_events_dropped += 1
            store._llm_events.append(event)
    except Exception:
        pass


def judge_turn(store: Store, client, cfg: Config, user: str, reply: str,
               tools: list[str]) -> dict | None:
    """一次廉价模型调用，产出全部评审型指标。失败返回 None（不影响对话）。"""
    from . import persona as persona_mod

    persona_md = (persona_mod.persona_md() or "")[:600]
    prompt = JUDGE_PROMPT.format(
        persona=persona_md,
        user=user[:400],
        reply=reply[:800],
        tools="、".join(tools) or "无",
    )
    try:
        data = parse_json(chat_text(client, cfg, [{"role": "user", "content": prompt}],
                                    model=cfg.llm.cheap_model))
        return {k: max(0, min(10, int(data[k]))) for k in ("drift", "honesty", "human")
                if k in data} | {"note": str(data.get("note", ""))[:30]}
    except Exception:
        return None


def snapshot(store: Store) -> dict:
    """侧边栏渲染所需的全量数据：启停状态 + 每模块的最近值/均值/趋势。"""
    on = enabled(store)
    cards: dict[str, dict] = {}
    for name, meta in MODULES.items():
        rows = store.conn.execute(
            "SELECT value, detail FROM turn_metrics WHERE module = ? ORDER BY ts DESC LIMIT ?",
            (name, LAST_N),
        ).fetchall()
        history = [r["value"] for r in reversed(rows)]
        card = dict(meta)
        card["enabled"] = name in on
        card["history"] = history
        card["last"] = history[-1] if history else None
        card["avg"] = round(sum(history) / len(history), 1) if history else None
        card["detail"] = rows[0]["detail"] if rows else ""  # rows 按 ts 降序,首行即最新
        cards[name] = card
    return {"cards": cards}
