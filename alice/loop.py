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
            "description": "主动记住一件重要的事（约定、用户重要事实等）。会被标记为重要，不受七日线清理。如果内容来自某个网页（你读过、或用户给了链接），提供 source_url——会作为带出处的见闻保存，进入你的普通记忆管线。",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "短标题，≤12字"},
                    "content": {"type": "string", "description": "要记住的完整内容。来自网页时用你自己的话转写并标明来源（如「维基百科条目说：…」），不要照抄原文"},
                    "keywords": {"type": "string", "description": "可选，检索关键词，空格分隔"},
                    "source_url": {"type": "string", "description": "可选。内容来源的网页链接；提供则保存为带出处的见闻记忆（走普通记忆管线）"},
                },
                "required": ["title", "content"],
            },
        },
    },
]


def dispatch_tool(store: Store, name: str, arguments: str, *, source_context=None) -> str:
    try:
        args = json.loads(arguments or "{}")
    except json.JSONDecodeError:
        args = {}
    if not isinstance(args, dict):
        args = {}
    if name == "recall":
        return retrieval.tool_recall(store, str(args.get("query", "")), source_context=source_context)
    if name == "remember":
        return retrieval.tool_remember(
            store,
            str(args.get("title", "")),
            str(args.get("content", "")),
            str(args.get("keywords", "")),
            source_url=str(args.get("source_url", "")),
            source_context=source_context,
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


def _record_source_usage(out: dict | None, context: dict, **fields) -> None:
    if out is not None:
        out.update({"packet_ids": list(context["packet_ids"]),
                    "recalled_ids": list(context["recalled_ids"]),
                    "recall_queries": list(context["recall_queries"]),
                    "memory_ids": list(context["memory_ids"]), **fields})


def chat_turn(store: Store, client, cfg: Config, user_text: str,
              ts: float | None = None, now: float | None = None,
              on_delta=None, on_tool=None, on_reasoning=None,
              usage_out: dict | None = None, on_persist=None,
              request_id=None, on_request_progress=None, on_maintenance_warning=None,
              source_packet_ids=(), source_scope="gui", source_usage_out: dict | None = None,
              source_allow_external_recall=False) -> str:
    ts = ts if ts is not None else time.time()
    if source_usage_out is not None:
        # Reused output dictionaries must not report a previous successful turn
        # when source validation or persistence fails before this one starts.
        source_usage_out.update({"offered_ids": [], "packet_ids": [], "recalled_ids": [],
                                 "recall_queries": [], "memory_ids": [], "user_turn_id": None,
                                 "assistant_turn_id": None, "reply_saved": False})
    # Caller selects explicit/relevant material. Never pick background cards
    # simply because a permanent conversation happens to be active.
    source_limits = store.source_limits()
    packet_ids = list(dict.fromkeys(source_packet_ids))
    for key in ("chat_material_limit", "chat_dependency_limit"):
        limit = source_limits[key]
        if limit is not None and len(packet_ids) > limit:
            raise ValueError("本轮外部资料数量超过用户配置的限制")
    packets = store.source_packets_for(packet_ids, scope=source_scope) if packet_ids else []
    if packet_ids and not packets:
        raise ValueError("所选资料已过期或不允许在此渠道使用")
    user_id = store.add_turn("user", user_text, ts=ts, request_id=request_id,
                             source_packet_ids=packet_ids, source_scope=source_scope)
    source_context = {"scope": source_scope, "packet_ids": packet_ids, "user_turn_id": user_id,
                      "recall_queries": [], "recalled_ids": [], "memory_ids": [],
                      "allow_external_recall": source_allow_external_recall is True,
                      "limits": source_limits}
    if source_usage_out is not None:
        source_usage_out.update({"offered_ids": list(packet_ids), "packet_ids": list(packet_ids),
                                 "recalled_ids": [], "user_turn_id": user_id, "reply_saved": False})
    if on_request_progress is not None:
        on_request_progress()
    messages = retrieval.build_messages(store, cfg, persona.persona_md() or "", now=now,
                                        source_packets=packets, current_user_text=user_text)
    reply = ""
    for _ in range(4):  # 工具调用轮数上限
        if source_context["packet_ids"] and not store.source_packets_for(
            source_context["packet_ids"], scope=source_scope,
        ):
            reply = "这轮参考的资料已到期或不再获准使用，需要重新读取后再谈。"
            break
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
                if source_context["packet_ids"] and not store.source_packets_for(
                    source_context["packet_ids"], scope=source_scope,
                ):
                    reply = "这轮参考的资料已到期或不再获准使用，需要重新读取后再谈。"
                    break
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
            try:
                result = dispatch_tool(store, tc.function.name, tc.function.arguments,
                                       source_context=source_context)
            finally:
                # A failed recall can have already persisted its source boundary.
                # Report it even if a later counter/tool operation raises.
                _record_source_usage(source_usage_out, source_context)
            messages = messages + [{"role": "tool", "tool_call_id": tc.id, "content": result}]
    else:
        reply = "（我这一轮转晕了，再说一遍好吗？）"
    if source_context["packet_ids"] and not store.source_packets_for(
        source_context["packet_ids"], scope=source_scope,
    ):
        # A model response may arrive after the source licence expired. Do not
        # publish that derived body as a newly saved reply.
        reply = "这轮参考的资料已到期或不再获准使用，需要重新读取后再谈。"
    reply = reply or "（沉默）"
    assistant_id = store.add_turn("assistant", reply, ts=ts + 1, request_id=request_id,
                                  source_packet_ids=source_context["packet_ids"], source_scope=source_scope)
    _record_source_usage(source_usage_out, source_context, assistant_turn_id=assistant_id, reply_saved=True)
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
