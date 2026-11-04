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


def record(store: Store, module: str, value: float, detail: str = "") -> None:
    store.conn.execute(
        "INSERT INTO turn_metrics (module, value, detail, ts) VALUES (?, ?, ?, ?)",
        (module, value, detail, time.time()),
    )
    store.conn.commit()


def record_llm_call(store: Store, purpose: str, started: float, resp=None, *,
                    error: str | None = None, fallback: bool = False,
                    model: str | None = None) -> None:
    """Step1 分调用观测（剩余工作评估 §2.3）：每次模型调用的用途/耗时/usage/结果。
    供应商未返回的数据记未知（不记零）；观测失败不阻塞原调用。value=毫秒。"""
    try:
        now = time.time()
        usage = {}
        if resp is not None:
            u = getattr(resp, "usage", None)
            if u is not None:
                for src, dst in (("prompt_tokens", "prompt"), ("completion_tokens", "completion")):
                    v = getattr(u, src, None)
                    if type(v) is int and v >= 0:
                        usage[dst] = v
        detail = json.dumps({
            "purpose": purpose, "duration_ms": int((now - started) * 1000),
            "usage": usage or "unknown", "status": "error" if error else "ok",
            "error_type": error, "fallback": bool(fallback),
            "model": model or "unknown"}, ensure_ascii=False)[:400]
        store.conn.execute(
            "INSERT INTO turn_metrics (module, value, detail, ts) VALUES (?, ?, ?, ?)",
            (f"llm_call:{purpose}", int((now - started) * 1000), detail, now))
        store.conn.commit()
    except Exception:
        pass  # 观测失败不影响调用本身


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
