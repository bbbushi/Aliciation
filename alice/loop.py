"""对话主循环：单一永久会话 + recall / remember 工具。"""
from __future__ import annotations

import json
import time
from datetime import datetime

from . import extraction, persona, retrieval, skills
from .config import Config
from .llm import chat
from .store import Store

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "recall",
            "description": "查询仓库与长期记忆，返回相关记忆全文。当话题可能涉及较早的往事、或用户问你还记不记得什么时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "检索关键词或短语"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remember",
            "description": "主动记住一件重要的事（约定、用户重要事实等）。会被标记为重要，不受七日线清理。",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "短标题，≤12字"},
                    "content": {"type": "string", "description": "要记住的完整内容"},
                    "keywords": {"type": "string", "description": "可选，检索关键词，空格分隔"},
                },
                "required": ["title", "content"],
            },
        },
    },
]


def dispatch_tool(store: Store, name: str, arguments: str) -> str:
    try:
        args = json.loads(arguments or "{}")
    except json.JSONDecodeError:
        args = {}
    if name == "recall":
        return retrieval.tool_recall(store, str(args.get("query", "")))
    if name == "remember":
        return retrieval.tool_remember(
            store,
            str(args.get("title", "")),
            str(args.get("content", "")),
            str(args.get("keywords", "")),
        )
    return f"未知工具：{name}"


def chat_turn(store: Store, client, cfg: Config, user_text: str,
              ts: float | None = None, now: float | None = None) -> str:
    ts = ts if ts is not None else time.time()
    store.add_turn("user", user_text, ts=ts)
    messages = retrieval.build_messages(store, cfg, persona.persona_md() or "", now=now)
    reply = ""
    for _ in range(4):  # 工具调用轮数上限
        resp = chat(client, cfg, messages, tools=TOOLS)
        msg = resp.choices[0].message
        if not msg.tool_calls:
            reply = (msg.content or "").strip()
            break
        messages = messages + [
            {
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                    }
                    for tc in msg.tool_calls
                ],
            }
        ]
        for tc in msg.tool_calls:
            result = dispatch_tool(store, tc.function.name, tc.function.arguments)
            messages = messages + [{"role": "tool", "tool_call_id": tc.id, "content": result}]
    else:
        reply = "（我这一轮转晕了，再说一遍好吗？）"
    store.add_turn("assistant", reply or "（沉默）", ts=ts + 1)  # 与用户消息同刻，+1 保持轮内先后
    try:
        extraction.extract_pending(store, client, cfg)  # 未到阈值时静默跳过
    except Exception as e:  # 提取失败不影响当轮对话
        print(f"（记忆提取暂失败：{e}）")
    return reply


def _continuity(store: Store) -> str:
    rows = store.recent_turns(2)
    if not rows:
        return "这是你们的第一句话。"
    last = rows[-1]
    gap_h = (time.time() - last["ts"]) / 3600
    snippet = last["content"][:40].replace("\n", " ")
    t = datetime.fromtimestamp(last["ts"]).strftime("%m-%d %H:%M")
    return f"上次说到（{t}，{gap_h:.0f} 小时前）：「{snippet}」"


def run_chat(store: Store, client, cfg: Config) -> None:
    print(f"=== {cfg.persona.name} ===")
    print(_continuity(store))
    print("（/exit 退出）")
    try:
        while True:
            try:
                text = input("\n你> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not text:
                continue
            if text in {"/exit", "/quit", "/q"}:
                break
            if text == "/memory":
                for layer in ("active", "warehouse", "longterm"):
                    print(f"{layer}: {store.count_in_layer(layer)} 条")
                continue
            reply = chat_turn(store, client, cfg, text)
            print(f"\n{cfg.persona.name}> {reply}")
            for watcher in (persona.evolve_if_due, skills.distill_if_due):
                try:
                    msg = watcher(store, client, cfg)
                    if msg:
                        print(f"（{msg}）")
                except Exception as e:  # 后台任务失败不影响对话
                    print(f"（{watcher.__name__} 暂失败：{e}）")
    finally:
        try:
            n = extraction.extract_pending(store, client, cfg, force=True)
            if n:
                print(f"（退出前提取了 {n} 条新记忆）")
        except Exception as e:
            print(f"（退出前记忆提取失败：{e}）")
