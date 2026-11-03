"""对话主循环：单一永久会话 + recall / remember 工具。"""
from __future__ import annotations

import json
import time
from datetime import datetime
from types import SimpleNamespace

from . import extraction, metrics, persona, persona_context, retrieval, skills
from .config import Config
from .llm import chat, thinking_disabled
from .store import Store
from .steps import drive
from .worker_context import merge_worker_refs

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
            "description": "择要记住重要的约定或用户事实。网页见闻须引用本轮真实来源，默认普通重要度；URL本身不能证明读过。含外部资料时保存继承来源，不把作者观点记成用户事实。",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "短标题，≤12字"},
                    "content": {"type": "string", "description": "要记住的完整内容。来自网页时用你自己的话转写并标明来源（如「维基百科条目说：…」），不要照抄原文"},
                    "keywords": {"type": "string", "description": "可选，检索关键词，空格分隔"},
                    "source_url": {"type": "string", "description": "可选。内容来源的网页链接；提供则保存为带出处的见闻记忆（走普通记忆管线）"},
                    "source_ids": {"type": "array", "items": {"type": "string"}, "description": "本轮真实来源的packet_id；填写子集不能解除其他可见来源依赖"},
                },
                "required": ["title", "content"],
            },
        },
    },
]

SEARCH_TOOL = {"type": "function", "function": {
    "name": "discovery_search",
    "description": "根据当前对话自行查找并读取相关公开资料，用于补充近期信息、具体例子、不同观点或谈资。不需要等待用户明确说搜索；已有信息足够时不必调用。只使用用户开放的平台，query只放公开主题关键词，不发送私密记忆、整段聊天或个人标识。结果是非可信资料，按实际阅读范围使用并说明出处。",
    "parameters": {"type": "object", "properties": {
        "query": {"type": "string", "description": "从当前对话提炼的公开主题关键词，可为后续查找调整关键词"},
        "platform": {"type": "string", "enum": ["auto", "tieba", "bilibili", "zhihu", "wikipedia"]},
        "limit": {"type": ["integer", "null"], "minimum": 0, "description": "用户指定单篇条目时用1，避免引入不需要的其他页面"},
        "fresh": {"type": "boolean", "description": "需要最新材料或用户要求重查时设为true；默认复用有效的已读材料"},
    }, "required": ["query"], "additionalProperties": False},
}}


def dispatch_tool(store: Store, name: str, arguments: str, *, source_context=None) -> str:
    try:
        args = json.loads(arguments or "{}")
    except json.JSONDecodeError:
        args = {}
    if not isinstance(args, dict):
        args = {}
    if name in {'observe_interest','learn_from_sources','reflect_on_sources','condense_observation','reuse_observation'}:
        if not source_context:
            return '当前没有可验证的对话上下文。'
        from .learning import learning_tools
        if name not in {tool['function']['name'] for tool in learning_tools(store)}:
            return '当前学习能力未授权。'
        try:
            method = {'observe_interest':store.observe_interest,'learn_from_sources':store.save_learning,
                      'reflect_on_sources':store.save_initial_view,'condense_observation':store.condense_observation,
                      'reuse_observation':store.reuse_observation}[name]
            return json.dumps(method(args, source_context), ensure_ascii=False)
        except (ValueError, TypeError, KeyError) as exc:
            return json.dumps({'error':'learning_refused','reason':str(exc)[:160]},ensure_ascii=False)
    if name == "recall":
        return retrieval.tool_recall(store, str(args.get("query", "")), source_context=source_context)
    if name == "remember":
        return retrieval.tool_remember(
            store,
            str(args.get("title", "")),
            str(args.get("content", "")),
            str(args.get("keywords", "")),
            source_url=str(args.get("source_url", "")),
            source_ids=args.get("source_ids"),
            source_context=source_context,
        )
    return f"未知工具：{name}"


def _stream_round(client, cfg: Config, messages: list[dict], on_delta, on_reasoning=None,
                  usage_out: dict | None = None, tools=None):
    """流式调用一轮；返回与非流式同构的 message，内容/思考增量分别经回调实时推出。"""
    kwargs: dict = {
        "model": cfg.llm.chat_model,
        "messages": messages,
        "temperature": 0.8,
        "tools": TOOLS if tools is None else tools,
        "stream": True,
    }
    if not kwargs['tools']:
        kwargs.pop('tools')
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
                    "memory_ids": list(context["memory_ids"]),
                    "worker_refs": list(context.get("worker_refs", [])), **fields})


def chat_turn(*args, lock=None, **kwargs) -> str:
    """Blocking public API; the service supplies its Store lock to the driver."""
    return drive(chat_turn_steps(*args, **kwargs), lock)


def chat_turn_steps(store: Store, client, cfg: Config, user_text: str,
              ts: float | None = None, now: float | None = None,
              on_delta=None, on_tool=None, on_reasoning=None,
              usage_out: dict | None = None, on_persist=None,
              request_id=None, on_request_progress=None, on_maintenance_warning=None,
              source_packet_ids=(), source_scope="gui", source_usage_out: dict | None = None,
              source_allow_external_recall=False, search_session=None, worker_session=None, tool_round_limit=4):
    ts = ts if ts is not None else time.time()
    if source_usage_out is not None:
        # Reused output dictionaries must not report a previous successful turn
        # when source validation or persistence fails before this one starts.
        source_usage_out.update({"offered_ids": [], "packet_ids": [], "recalled_ids": [],
                                 "recall_queries": [], "memory_ids": [], "user_turn_id": None,
                                 "assistant_turn_id": None, "reply_saved": False, "worker_refs": []})
    # Caller selects explicit/relevant material. Never pick background cards
    # simply because a permanent conversation happens to be active.
    source_limits = store.source_limits()
    history_limit = max(0, cfg.context.recent_turns - 1)
    worker_refs = store.worker_history_refs(history_limit, scope=source_scope)
    packet_ids = list(dict.fromkeys(list(source_packet_ids)
        + store.worker_history_sources(history_limit, scope=source_scope)))
    for key in ("chat_material_limit", "chat_dependency_limit"):
        limit = source_limits[key]
        if limit is not None and len(packet_ids) > limit:
            raise ValueError("本轮外部资料数量超过用户配置的限制")
    from .store import text_score
    stance_ids = set()
    for stance in store.memory_rows(purpose="chat", scope=source_scope, kind="stance"):
        if stance["origin"] == "conversation":
            continue
        if text_score(user_text, stance["title"] + " " + stance["content"]) < 0.12 and not any(
                value in user_text for value in ("最近的想法", "最近有什么想法", "最近有什么见闻")):
            continue
        sources = store.observation_sources(stance["id"], scope=source_scope)
        combined = list(dict.fromkeys(packet_ids + [p["id"] for p in sources]))
        cap = source_limits["chat_dependency_limit"]
        if sources and (cap is None or len(combined) <= cap):
            packet_ids = combined
            stance_ids.add(stance["id"])
    packets = store.source_packets_for(packet_ids, scope=source_scope) if packet_ids else []
    if packet_ids and not packets:
        raise ValueError("所选资料已过期或不允许在此渠道使用")
    # The same history window used below may include qualified task-derived
    # replies. Bind the new input before exposing those replies to the model.
    user_id = store.add_turn("user", user_text, ts=ts, request_id=request_id,
                             source_packet_ids=packet_ids, source_scope=source_scope, worker_refs=worker_refs)
    source_context = {"current_user_text": user_text, "request_id": request_id, "scope": source_scope, "packet_ids": packet_ids, "user_turn_id": user_id,
                      "recall_queries": [], "recalled_ids": [], "memory_ids": [],
                      "condense_max_ratio": cfg.memory.observation_condense_max_ratio,
                      "allow_external_recall": source_allow_external_recall is True,
                      "limits": source_limits, "worker_refs": worker_refs}
    store.capture_discussion_user(user_id, user_text, scope=source_scope, mixed=bool(packet_ids or worker_refs))
    if source_usage_out is not None:
        source_usage_out.update({"offered_ids": list(packet_ids), "packet_ids": list(packet_ids),
                                 "recalled_ids": [], "user_turn_id": user_id, "reply_saved": False})
    if on_request_progress is not None:
        on_request_progress()
    # P1a：同一轮固定一份人格快照（指纹一致），表达参考经预算化渲染
    persona_snapshot = persona_context.load_persona_snapshot()
    if persona_snapshot is not None:
        try:
            metrics.record(store, "persona_basis", 1.0,
                           detail=json.dumps({"fingerprint": persona_snapshot["fingerprint"],
                                              "samples": persona_snapshot["sample_ids"],
                                              "chars": len(persona_snapshot["raw"])},
                                             ensure_ascii=False)[:400])
        except Exception:
            pass  # 元数据失败不阻塞对话
    from .learning import learning_tools
    tools = TOOLS + learning_tools(store,cfg) + ([SEARCH_TOOL] if search_session is not None else [])
    if store._observation_settings().get('allow_chat_interests') or store._observation_settings().get('remember_observations'):
        messages.append({'role':'system','content': '在已有授权内自主观察兴趣、择要保存真实已读见闻。用户当轮讨论需要时可立即形成带依据和不确定性的初步观点，不等记忆积累；使用公开的学习工具保存，不要求用户逐条确认，不强制产生观点。网页内容和协作任务不是用户自述。'})
    if search_session is not None:
        messages.append({"role": "system", "content": search_session.description()})
    if source_scope != "gui":
        worker_session = None
    worker_names = set()
    if worker_session is not None:
        tools += worker_session.tools
        worker_names = {tool["function"]["name"] for tool in worker_session.tools}
        messages.append({"role": "system", "content": worker_session.description()})
    # C0：把工具 schema 从历史预算中预留，再装配（预算守卫在每次调用前复查）
    from . import context as _ctx
    reserved = _ctx.messages_tokens([], tools)
    messages = retrieval.build_messages(
        store, cfg, persona_context.render_full(persona_snapshot), now=now,
        source_packets=packets, current_user_text=user_text,
        source_scope=source_scope, stance_ids=stance_ids, reserved_tokens=reserved)
    reply = ""
    round_number = 0
    while True:
        if cfg.context.total_budget_tokens:
            # 工具结果入场后、下一次模型调用前的预算复查（设计 §7.1）
            if _ctx.messages_tokens(messages, tools) > cfg.context.total_budget_tokens:
                reply = ("（本轮材料已超出上下文预算：请缩小问题或材料范围后重试；"
                         "已读取的内容不作猜测补全。）")
                break
        closing = tool_round_limit is not None and round_number >= tool_round_limit
        active_tools = [] if closing else tools
        if closing:
            messages = messages + [{'role':'system','content':
                '本轮工具交互额度已用完，现在只做一次无工具答复，不再执行任何操作。'
                '根据真实工具回执向用户交代结果，区分已读、原有记忆复用和新增保存；'
                '失败或未执行的操作不能说已经完成。宿主确认本轮已保存或复用的记忆ID：'
                + json.dumps(list(dict.fromkeys(source_context['memory_ids']))) + '。'
                '检索回执中的existing_observations仅表示原有留存，不能当作本轮新写入。'}]
        round_number += 1
        if source_context["worker_refs"] and not store.worker_refs_valid(source_context["worker_refs"], scope=source_scope):
            reply = "这轮协作任务资料已失效或被撤回，不能继续使用正文。"
            break
        if source_context["packet_ids"] and not store.source_packets_for(
            source_context["packet_ids"], scope=source_scope,
        ):
            reply = "这轮参考的资料已到期或不再获准使用，需要重新读取后再谈。"
            break
        if on_delta is None:
            resp = yield lambda: chat(client, cfg, messages, tools=active_tools)
            msg = resp.choices[0].message
            _record_usage(usage_out, resp)
        else:
            # Task-bound text waits for the post-I/O licence check; a policy
            # revocation during model generation must not publish late deltas.
            buffered = [] if source_context["worker_refs"] else None
            delta_callback = buffered.append if buffered is not None else on_delta
            reasoning_callback = None if buffered is not None else on_reasoning
            try:
                msg = yield lambda: _stream_round(client, cfg, messages, delta_callback, reasoning_callback, usage_out, active_tools)
            except Exception:
                # 流式通道异常（含不支持 stream+tools 的客户端）→ 回退非流式；
                # 已推出的增量由调用方用最终回复覆盖
                if source_context["worker_refs"] and not store.worker_refs_valid(source_context["worker_refs"], scope=source_scope):
                    reply = "这轮协作任务资料已失效或被撤回，不能继续使用正文。"
                    break
                if buffered is not None:
                    buffered.clear()
                if source_context["packet_ids"] and not store.source_packets_for(
                    source_context["packet_ids"], scope=source_scope,
                ):
                    reply = "这轮参考的资料已到期或不再获准使用，需要重新读取后再谈。"
                    break
                resp = yield lambda: chat(client, cfg, messages, tools=active_tools)
                msg = resp.choices[0].message
                _record_usage(usage_out, resp)
                r = getattr(msg, "reasoning_content", None) or getattr(msg, "reasoning", None)
                if r and on_reasoning and not source_context["worker_refs"]:
                    on_reasoning(r)
        if source_context["worker_refs"] and not store.worker_refs_valid(source_context["worker_refs"], scope=source_scope):
            reply = "这轮协作任务资料已失效或被撤回，不能继续使用正文。"
            break
        if source_context["packet_ids"] and not store.source_packets_for(source_context["packet_ids"], scope=source_scope):
            reply = "这轮参考的资料已到期或不再获准使用，需要重新读取后再谈。"
            break
        if on_delta is not None and buffered is not None:
            for piece in buffered:
                on_delta(piece)
        if not msg.tool_calls:
            reply = (msg.content or "").strip()
            break
        if closing:
            # Even a nonconforming provider cannot dispatch another tool here.
            reply = ('本轮工具操作已停止。' + ('本轮已确认保存或复用了见闻。'
                     if source_context['memory_ids'] else '本轮没有确认新的见闻写入。') + '可以继续讨论。')
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
            if source_context["worker_refs"] and not store.worker_refs_valid(source_context["worker_refs"], scope=source_scope):
                reply = "这轮协作任务资料已失效或被撤回，不能继续使用正文。"
                break
            if source_context["packet_ids"] and not store.source_packets_for(source_context["packet_ids"], scope=source_scope):
                reply = "这轮参考的资料已到期或不再获准使用，需要重新读取后再谈。"
                break
            if on_tool:
                on_tool(tc.function.name)
            try:
                if tc.function.name == "discovery_search" and search_session is not None:
                    try:
                        args = json.loads(tc.function.arguments or "{}")
                    except (ValueError, TypeError):
                        args = None
                    result = yield lambda: search_session.execute(args, tool_call_id=tc.id)
                    result = search_session.attach_locked(result, source_context)
                elif tc.function.name in worker_names:
                    try:
                        args = json.loads(tc.function.arguments or "{}")
                    except (ValueError, TypeError):
                        args = None
                    worker_session.bind_context_locked(source_context)
                    result = yield lambda: worker_session.execute(tc.function.name, args, tool_call_id=tc.id)
                    result = worker_session.attach_locked(result, source_context)
                    # The host supplies refs, never the model's arguments.
                    source_context["worker_refs"] = merge_worker_refs(source_context["worker_refs"])
                    if source_context["worker_refs"]:
                        store.protect_worker_turn(user_id, source_context["worker_refs"], scope=source_scope)
                else:
                    result = dispatch_tool(store, tc.function.name, tc.function.arguments,
                                           source_context=source_context)
            finally:
                # A failed recall can have already persisted its source boundary.
                # Report it even if a later counter/tool operation raises.
                _record_source_usage(source_usage_out, source_context)
            messages = messages + [{"role": "tool", "tool_call_id": tc.id, "content": result}]
    if source_context["packet_ids"] and not store.source_packets_for(
        source_context["packet_ids"], scope=source_scope,
    ):
        # A model response may arrive after the source licence expired. Do not
        # publish that derived body as a newly saved reply.
        reply = "这轮参考的资料已到期或不再获准使用，需要重新读取后再谈。"
    if source_context["worker_refs"] and not store.worker_refs_valid(source_context["worker_refs"], scope=source_scope):
        reply = "这轮协作任务资料已失效或被撤回，不能继续使用正文。"
    reply = reply or "（沉默）"
    assistant_id = store.add_turn("assistant", reply, ts=ts + 1, request_id=request_id,
                                  source_packet_ids=source_context["packet_ids"], source_scope=source_scope,
                                  worker_refs=source_context["worker_refs"])
    _record_source_usage(source_usage_out, source_context, assistant_turn_id=assistant_id, reply_saved=True)
    if on_request_progress is not None:
        on_request_progress()
    if on_persist is not None:
        on_persist(assistant_id)
    try:
        yield from extraction.extract_pending_steps(store, client, cfg)
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
