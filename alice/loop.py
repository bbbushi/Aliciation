"""对话主循环：单一永久会话 + recall / remember 工具。"""
from __future__ import annotations

import json
import time
from datetime import datetime
from types import SimpleNamespace

from . import extraction, persona, retrieval, skills
from .config import Config
from .llm import chat, thinking_disabled
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


def _stream_round(client, cfg: Config, messages: list[dict], on_delta, on_reasoning=None,
                  usage_out: dict | None = None):
    """流式调用一轮；返回与非流式同构的 message，内容/思考增量分别经回调实时推出。"""
    kwargs: dict = {
        "model": cfg.llm.chat_model,
        "messages": messages,
        "temperature": 0.8,
        "tools": TOOLS,
        "stream": True,
    }
    if thinking_disabled(cfg):
        kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
    if "deepseek" in (cfg.llm.base_url or ""):
        # deepseek 支持流式携带 usage；其他平台未必支持该参数，按平台启用
        kwargs["stream_options"] = {"include_usage": True}
    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    calls: dict[int, dict] = {}
    for chunk in client.chat.completions.create(**kwargs):
        u = getattr(chunk, "usage", None)
        if u is not None and usage_out is not None:
            usage_out["prompt"] = usage_out.get("prompt", 0) + (u.prompt_tokens or 0)
            usage_out["completion"] = usage_out.get("completion", 0) + (u.completion_tokens or 0)
        if not getattr(chunk, "choices", None):
            continue
        delta = chunk.choices[0].delta
        if delta is None:
            continue
        piece = getattr(delta, "reasoning_content", None) or getattr(delta, "reasoning", None)
        if piece:
            reasoning_parts.append(piece)
            if on_reasoning:
                on_reasoning(piece)
        piece = getattr(delta, "content", None)
        if piece:
            content_parts.append(piece)
            on_delta(piece)
        for tc in getattr(delta, "tool_calls", None) or []:
            slot = calls.setdefault(tc.index, {"id": None, "name": "", "arguments": ""})
            if tc.id:
                slot["id"] = tc.id
            if tc.function:
                if tc.function.name:
                    slot["name"] = tc.function.name
                if tc.function.arguments:
                    slot["arguments"] += tc.function.arguments
    return SimpleNamespace(
        content="".join(content_parts),
        reasoning="".join(reasoning_parts),
        tool_calls=[
            SimpleNamespace(
                id=slot["id"] or f"call_{i}",
                function=SimpleNamespace(name=slot["name"], arguments=slot["arguments"]),
            )
            for i, slot in sorted(calls.items())
        ] or None,
    )


def _record_usage(usage_out: dict | None, resp) -> None:
    if usage_out is None:
        return
    u = getattr(resp, "usage", None)
    if u is not None:
        usage_out["prompt"] = usage_out.get("prompt", 0) + (u.prompt_tokens or 0)
        usage_out["completion"] = usage_out.get("completion", 0) + (u.completion_tokens or 0)


def chat_turn(store: Store, client, cfg: Config, user_text: str,
              ts: float | None = None, now: float | None = None,
              on_delta=None, on_tool=None, on_reasoning=None,
              usage_out: dict | None = None, on_persist=None,
              request_id=None, on_request_progress=None, on_maintenance_warning=None) -> str:
    ts = ts if ts is not None else time.time()
    store.add_turn("user", user_text, ts=ts, request_id=request_id)
    if on_request_progress is not None:
        on_request_progress()
    messages = retrieval.build_messages(store, cfg, persona.persona_md() or "", now=now)
    reply = ""
    for _ in range(4):  # 工具调用轮数上限
        if on_delta is None:
            resp = chat(client, cfg, messages, tools=TOOLS)
            msg = resp.choices[0].message
            _record_usage(usage_out, resp)
        else:
            try:
                msg = _stream_round(client, cfg, messages, on_delta, on_reasoning, usage_out)
            except Exception:
                # 流式通道异常（含不支持 stream+tools 的客户端）→ 回退非流式；
                # 已推出的增量由调用方用最终回复覆盖
                resp = chat(client, cfg, messages, tools=TOOLS)
                msg = resp.choices[0].message
                _record_usage(usage_out, resp)
                r = getattr(msg, "reasoning_content", None) or getattr(msg, "reasoning", None)
                if r and on_reasoning:
                    on_reasoning(r)
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
            if on_tool:
                on_tool(tc.function.name)
            result = dispatch_tool(store, tc.function.name, tc.function.arguments)
            messages = messages + [{"role": "tool", "tool_call_id": tc.id, "content": result}]
    else:
        reply = "（我这一轮转晕了，再说一遍好吗？）"
    reply = reply or "（沉默）"
    assistant_id = store.add_turn("assistant", reply, ts=ts + 1, request_id=request_id)  # 与用户消息同刻，+1 保持轮内先后
    if on_request_progress is not None:
        on_request_progress()
    if on_persist is not None:
        on_persist(assistant_id)
    try:
        extraction.extract_pending(store, client, cfg)  # 未到阈值时静默跳过
    except Exception as e:  # 提取失败不影响当轮对话
        print(f"（记忆提取暂失败：{type(e).__name__}）")
        if on_maintenance_warning is not None:
            on_maintenance_warning("extraction_failed")
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
