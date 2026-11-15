"""Question-driven idle agent and GUI contact delivery, disabled by default."""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
import threading
import time
import uuid

from . import thought_workspace as thoughts
from .background_runtime import day, reserve_model
from .channels import ChannelError

RUN_GUIDANCE = """这是你的自主探索运行，不是用户刚发来的一条消息。
从实际疑问、相关记忆或自己的兴趣中选择值得推进的事情，也可以判断暂时没有值得推进的内容。
你决定收纳、选题、方法、解决与否、成果价值和交流意图。先读所需正文，再围绕具体缺口尝试获准的方法。
可以形成当前看法；关于经历和事实的判断保留真实出处与未知。未解疑问不作为已证实事实。
有进展时用思想能力保存摘要和下一步；完成时提交成果或说明不保留的理由，不要求每次产出观点。
需要用户信息时可提出具体联系意图，并将相关问题置为waiting_user；没有回复不生成催办。
旧联系意图标为needs_review时，结合最新材料决定更新准备、保留下次交流或取消。
联系意图已保存不表示已送达。用户委托通常优先，同时允许自己的兴趣。
把这次明确的进展、剩余缺口与下一步简短记录；不输出原始思维链。
"""


def in_window(start, end):
    from .discovery_memory import DAY_ZONE
    clock = datetime.now(DAY_ZONE).strftime("%H:%M")
    return start == end or (start <= clock < end if start < end else clock >= start or clock < end)


class YieldToForeground(Exception):
    pass


class AutonomyCoordinator:
    def __init__(self, service):
        self.service = service
        self.stop_event = threading.Event()
        self.cancel = threading.Event()
        self.cancel_reason = "user_cancelled"
        self.thread = None
        self.bind_store()

    def bind_store(self):
        self.cancel.set()
        store = self.service.store
        with store.source_transaction():
            store.conn.execute("UPDATE exploration_runs SET status=CASE WHEN reason IN('model_in_flight','tool_in_flight') "
                "THEN 'unknown' ELSE 'paused' END,reason='process_interrupted',updated_at=? WHERE status='running'", (time.time(),))
            store.conn.execute("UPDATE thought_contacts SET status='pending_next_chat',updated_at=? WHERE status='incorporating'", (time.time(),))
        self.epoch = store.meta_get("session_epoch")

    def start(self):
        self.thread = threading.Thread(target=self._loop, name="alice-autonomy", daemon=True)
        self.thread.start()

    def stop(self):
        self.cancel_reason = "service_stopping"
        self.stop_event.set()
        self.cancel.set()
        if self.thread is not None:
            self.thread.join(timeout=1)

    def shutdown(self):
        # In-flight provider calls cannot be killed; join before Store disposal.
        if self.thread is not None:
            self.thread.join()

    def _idle(self):
        return not self.stop_event.is_set() and self.service._channels.activity().get("status") == "ready"

    def before_reset(self):
        self.cancel_reason = "session_changed"
        self.cancel.set()
        store = self.service.store
        with store.source_transaction():
            for table, field in (("thought_questions", "question='',reason='',progress='',next_step=''"),
                                 ("thought_contacts", "message=''"), ("exploration_runs", "checkpoint='[]'")):
                for row in store.conn.execute(f"SELECT id,basis FROM {table}").fetchall():
                    if json.loads(row["basis"]).get("packets"):
                        store.conn.execute(f"UPDATE {table} SET {field} WHERE id=?", (row["id"],))

    def access(self, action="status", payload=None):
        service = self.service
        if not service._lock.acquire(blocking=False):
            raise ChannelError(503, "busy", "Alice忙碌，稍后刷新思想与探索")
        try:
            store = service.store
            if action == "status":
                return self._snapshot()
            if not isinstance(payload, dict) or payload.get("session_epoch") != store.meta_get("session_epoch"):
                raise ChannelError(409, "session_changed", "会话已变化，请刷新后操作")
            with store.source_transaction():
                if action == "settings":
                    expected = payload.get("revision")
                    revision = int(store.meta_get("autonomy_revision") or 0)
                    if type(expected) is not int or expected != revision:
                        raise ChannelError(409, "settings_changed", "设置已改变，请刷新后再保存")
                    thoughts.save_settings(store, payload.get("settings"))
                    store.meta_set("autonomy_revision", str(revision + 1))
                    self.cancel_reason = "settings_changed"
                    self.cancel.set()
                elif action == "record":
                    session = thoughts.ThoughtSession(store, {"scope": "gui"})
                    result = session.execute("thought_record", {"question": payload.get("question"),
                        "reason": payload.get("reason", "用户在思想页面提出"), "origin": "user_request"})
                    if "error" in json.loads(result):
                        raise ValueError(json.loads(result)["reason"])
                elif action == "question":
                    row = self._question(payload.get("id"), payload.get("version"))
                    session = thoughts.ThoughtSession(store, {"scope": "gui"})
                    ref = session.expose(row)["ref"]
                    result = json.loads(session.execute("thought_update", {"ref": ref,
                        "status": payload.get("status"), "progress": row["progress"], "next_step": row["next_step"]}))
                    if "error" in result:
                        raise ValueError(result["reason"])
                elif action in {"run", "resume"}:
                    config = thoughts.settings(store)
                    if not config["allow_paid"]:
                        raise ValueError("请先允许探索使用已配置模型")
                    if store.conn.execute("SELECT 1 FROM exploration_runs WHERE epoch=? AND status IN('queued','running')",
                                          (self.epoch,)).fetchone():
                        raise ValueError("已有探索排队或运行，请先查看进度")
                    if action == "resume":
                        changed = store.conn.execute("UPDATE exploration_runs SET status='queued',manual=1,updated_at=? "
                            "WHERE id=? AND epoch=? AND status='paused'", (time.time(), payload.get("run_id"), self.epoch)).rowcount
                        if not changed:
                            raise ValueError("只能恢复明确暂停的探索；结果未知的操作不可自动重放")
                    else:
                        qid = None
                        if payload.get("id") is not None:
                            qid = self._question(payload["id"], payload.get("version"))["id"]
                        self._enqueue(qid, manual=True)
                elif action in {"cancel", "pause"}:
                    self.cancel_reason = "user_paused" if action == "pause" else "user_cancelled"
                    self.cancel.set()
                    store.conn.execute("UPDATE exploration_runs SET status=?,manual=1,reason=?,updated_at=? "
                        "WHERE epoch=? AND status IN('queued','paused')", ("paused" if action == "pause" else "cancelled",
                            self.cancel_reason, time.time(), self.epoch))
                elif action == "contact_cancel":
                    identity, version = payload.get("id"), payload.get("version")
                    if type(identity) is not int or type(version) is not int:
                        raise ValueError("联系意图引用无效")
                    changed = store.conn.execute("UPDATE thought_contacts SET status='cancelled',version=version+1,updated_at=? "
                        "WHERE id=? AND version=? AND epoch=? AND status NOT IN('delivered','incorporated')",
                        (time.time(), identity, version, self.epoch)).rowcount
                    if not changed:
                        raise ValueError("联系已改变或已经送达，请刷新核对")
                    thoughts.event(store, "contact_cancelled", identity)
                elif action == "detail":
                    return self._detail(payload)
                else:
                    raise ValueError("未知思想操作")
            return self._snapshot()
        finally:
            service._lock.release()

    def _question(self, identity, version):
        if type(identity) is not int or type(version) is not int:
            raise ValueError("疑问引用无效")
        row = self.service.store.conn.execute("SELECT * FROM thought_questions WHERE id=? AND version=? AND epoch=?",
                                              (identity, version, self.epoch)).fetchone()
        if row is None or not thoughts.valid_basis(self.service.store, json.loads(row["basis"])):
            raise ValueError("疑问已变化或来源失效")
        return row

    def _snapshot(self):
        store = self.service.store
        questions = [thoughts.public_question(r) for r in thoughts.question_rows(store, include_closed=True)
                     if r["question"] and thoughts.valid_basis(store, json.loads(r["basis"]))][:100]
        contacts = [{k: r[k] for k in ("id", "version", "question_id", "motive", "message", "mode", "status", "turn_id", "expires_at")}
                    for r in store.conn.execute("SELECT * FROM thought_contacts WHERE epoch=? ORDER BY id DESC LIMIT 50", (self.epoch,))
                    if thoughts.valid_basis(store, json.loads(r["basis"]))]
        runs = [dict(r) for r in store.conn.execute("SELECT id,status,reason,question_id,model_calls,steps,created_at,updated_at "
                "FROM exploration_runs WHERE epoch=? ORDER BY created_at DESC LIMIT 30", (self.epoch,))]
        events = [dict(r) for r in store.conn.execute("SELECT id,kind,object_id,detail,created_at FROM thought_events "
                  "WHERE epoch=? ORDER BY id DESC LIMIT 60", (self.epoch,))]
        stances = [{"id": r["id"], "title": r["title"], "created_at": r["created_at"]}
                   for r in store.memory_rows(purpose="observer", scope="gui", kind="stance")][:50]
        usage = store.conn.execute("SELECT models FROM background_usage WHERE day=?", (day(),)).fetchone()
        return {"status": "ok", "session_epoch": self.epoch,
                "revision": int(store.meta_get("autonomy_revision") or 0), "settings": thoughts.settings(store),
                "questions": questions, "stances": stances, "contacts": contacts, "runs": runs, "events": events,
                "budget": {"day": day(), "models": usage[0] if usage else 0},
                "channels": {"gui": True, "next_chat": True, "qq": False, "offline": False}}

    def _detail(self, payload):
        store = self.service.store
        if payload.get("kind") == "question":
            return thoughts.public_question(self._question(payload.get("id"), payload.get("version")), full=True)
        if payload.get("kind") == "stance":
            row = next((r for r in store.memory_rows(purpose="observer", scope="gui", kind="stance") if r["id"] == payload.get("id")), None)
            if row:
                return {"content": row["content"], "title": row["title"]}
        if payload.get("kind") == "run":
            row = store.conn.execute("SELECT * FROM exploration_runs WHERE id=? AND epoch=?", (payload.get("id"), self.epoch)).fetchone()
            if row and thoughts.valid_basis(store, json.loads(row["basis"])):
                return {"id": row["id"], "status": row["status"], "messages": json.loads(row["checkpoint"])}
        raise ValueError("记录不存在或来源已失效")

    def _enqueue(self, qid=None, *, manual=False):
        store, now = self.service.store, time.time()
        identity = "explore_" + uuid.uuid4().hex
        basis = {"epoch": self.epoch}
        with store.source_transaction():
            store.conn.execute("INSERT INTO exploration_runs(id,epoch,status,question_id,basis,manual,created_at,updated_at) "
                "VALUES(?,?,'queued',?,?,?,?,?)", (identity, self.epoch, qid, thoughts.dump(basis), int(manual), now, now))
            thoughts.event(store, "exploration_queued", identity, {"question_id": qid, "manual": manual})

    def _loop(self):
        while not self.stop_event.wait(5):
            try:
                if not self._idle():
                    continue
                with self.service._background.claim("autonomy") as acquired:
                    if acquired:
                        self._cycle()
            except Exception as exc:
                # Provider text and credentials are never written into diagnostics.
                if self.stop_event.is_set():
                    break
                with self.service._lock:
                    thoughts.event(self.service.store, "runner_error", "autonomy", {"type": type(exc).__name__})

    def _cycle(self):
        service = self.service
        with service._lock:
            if not self._idle():
                return
            store = service.store
            if time.time() - store.meta_get_time("autonomy_last_cleanup") >= 60:
                thoughts.expire_dependencies(store)
                with store.source_transaction():
                    store.conn.execute("UPDATE thought_contacts SET status='pending_next_chat',adoption_request_id=NULL,updated_at=? "
                        "WHERE status='incorporating' AND adoption_request_id IN(SELECT request_id FROM channel_requests "
                        "WHERE status IN('failed','interrupted','cancelled'))", (time.time(),))
                    store.conn.execute("UPDATE exploration_runs SET checkpoint='[]' WHERE status NOT IN('queued','running') "
                        "AND (updated_at<? OR id NOT IN(SELECT id FROM exploration_runs ORDER BY created_at DESC LIMIT 30))", (time.time()-7*86400,))
                    store.conn.execute("DELETE FROM thought_events WHERE id NOT IN(SELECT id FROM thought_events ORDER BY id DESC LIMIT 2000)")
                    store.meta_set("autonomy_last_cleanup", str(time.time()))
            self._deliver_locked()
            config = thoughts.settings(store)
            run = store.conn.execute("SELECT * FROM exploration_runs WHERE epoch=? AND status='queued' ORDER BY created_at LIMIT 1",
                                     (self.epoch,)).fetchone()
            if not config["allow_paid"]:
                return
            used = store.conn.execute("SELECT models FROM background_usage WHERE day=?", (day(),)).fetchone()
            if not config["run_models"] or not config["run_seconds"] or (used[0] if used else 0) >= config["daily_models"]:
                return
            if run is None:
                if not config["enabled"] or not in_window(config["window_start"], config["window_end"]):
                    return
                latest = store.conn.execute("SELECT COALESCE(MAX(ts),0) FROM turns").fetchone()[0]
                if time.time() - latest < config["idle_seconds"]:
                    return
                last = store.meta_get_time("autonomy_last_started")
                if time.time() - last < config["interval_seconds"]:
                    return
                paused = store.conn.execute("SELECT * FROM exploration_runs WHERE epoch=? AND status='paused' AND manual=0 "
                    "ORDER BY updated_at LIMIT 1", (self.epoch,)).fetchone()
                if paused:
                    run = paused
                else:
                    # Empty discovery runs are revisited only for new material or
                    # a bounded curiosity opportunity, not every scheduler tick.
                    stamp = self._input_stamp()
                    eligible = any(r["status"] == "open" for r in thoughts.question_rows(store))
                    review = store.conn.execute("SELECT 1 FROM thought_contacts WHERE epoch=? AND status='needs_review' LIMIT 1", (self.epoch,)).fetchone()
                    curiosity_due = time.time() - store.meta_get_time("autonomy_last_curiosity") >= 21600
                    if not eligible and not review and stamp == store.meta_get("autonomy_inputs") and not curiosity_due:
                        return
                    self._enqueue()
                    store.meta_set("autonomy_inputs", stamp)
                    store.meta_set("autonomy_last_curiosity", str(time.time()))
                    run = store.conn.execute("SELECT * FROM exploration_runs WHERE epoch=? AND status='queued' ORDER BY created_at LIMIT 1", (self.epoch,)).fetchone()
            self.cancel = threading.Event()
            self.cancel_reason = "user_cancelled"
            store.meta_set("autonomy_last_started", str(time.time()))
        self._run(dict(run), config)

    def _input_stamp(self):
        store = self.service.store
        from .context_memory import version
        material = [[r["id"], version(r)] for r in store.memory_rows(purpose="thought", scope="gui")[:40]]
        latest = store.conn.execute("SELECT COALESCE(MAX(id),0) FROM turns WHERE role='user'").fetchone()[0]
        return hashlib.sha256(thoughts.dump([latest, material]).encode()).hexdigest()

    def _gate(self, run, config, revision, basis, latest, deadline):
        store = self.service.store
        if not self._idle() or self.cancel.is_set():
            raise YieldToForeground(self.cancel_reason if self.cancel.is_set() else "foreground_priority")
        if self.epoch != store.meta_get("session_epoch") or run["epoch"] != self.epoch:
            raise ValueError("session_changed")
        if str(revision) != (store.meta_get("autonomy_revision") or "0"):
            raise YieldToForeground("settings_changed")
        if not run["manual"] and not in_window(config["window_start"], config["window_end"]):
            raise YieldToForeground("outside_run_window")
        if (run.get("persona_version") != store.meta_get("persona_version")
                or run.get("model_generation") != self.service._discovery._generation):
            raise YieldToForeground("runtime_changed")
        if not thoughts.valid_basis(store, basis):
            raise ValueError("source_changed")
        current = store.conn.execute("SELECT COALESCE(MAX(id),0) FROM turns WHERE role='user'").fetchone()[0]
        if current != latest:
            raise YieldToForeground("new_conversation")
        if time.monotonic() >= deadline:
            raise YieldToForeground("run_time_budget")

    def _run(self, run, config):
        from . import capability_registry, context_observation, loop, persona_context
        from .context_memory import MemoryContext, TOOLS as MEMORY_TOOLS
        from .context_history import HistoryContext, TOOLS as HISTORY_TOOLS
        from .context import messages_tokens
        from .llm import chat
        service, search = self.service, None
        context = {"scope": "gui", "request_id": run["id"], "packet_ids": [], "worker_refs": [],
                   "recall_queries": [], "recalled_ids": [], "memory_ids": [], "allow_external_recall": True,
                   "limits": service.store.source_limits()}
        deadline = time.monotonic() + config["run_seconds"]
        status, reason = "completed", "no_further_step"
        calls_started = 0
        try:
            with service._lock:
                store = service.store
                revision = int(store.meta_get("autonomy_revision") or 0)
                run["persona_version"] = store.meta_get("persona_version")
                run["model_generation"] = service._discovery._generation
                latest = store.conn.execute("SELECT COALESCE(MAX(id),0) FROM turns WHERE role='user'").fetchone()[0]
                memory = MemoryContext(store, service.cfg, "gui", "")
                memory.history = HistoryContext(memory, boundary={"first_retained_id": latest + 1})
                context["memory_session"] = memory
                context["thought_basis"] = json.loads(run["basis"])
                session = thoughts.ThoughtSession(store, context)
                context["thought_session"] = session
                latest_time = store.conn.execute("SELECT COALESCE(MAX(ts),0) FROM turns WHERE role='user'").fetchone()[0]
                memory_time = store.conn.execute("SELECT COALESCE(MAX(created_at),0) FROM memories").fetchone()[0]
                candidates = [r for r in thoughts.question_rows(store)
                              if (r["status"] == "open" or r["status"] == "waiting_user" and latest_time > r["updated_at"]
                                  or r["status"] == "waiting_source" and memory_time > r["updated_at"])
                              and thoughts.valid_basis(store, json.loads(r["basis"]))]
                candidates.sort(key=lambda r: (r["last_considered"], -r["updated_at"]))
                if run["question_id"]:
                    target = next((r for r in thoughts.question_rows(store, include_closed=True) if r["id"] == run["question_id"]), None)
                    if target is None:
                        raise ValueError("question_unavailable")
                    candidates = [target] + [r for r in candidates if r["id"] != target["id"]]
                catalogue = [session.expose(r) for r in candidates[:20]]
                snapshot = persona_context.load_persona_snapshot()
                from . import persona_growth, thought
                growth_refs = []
                if snapshot is not None:
                    sections, growth_basis = persona_growth.project(snapshot["sections"], persona_growth.evidence_rows(store))
                    snapshot = {**snapshot, "sections": sections}
                    growth_refs = growth_basis["refs"]
                registry = self._persona_registry(snapshot)
                if registry is not None:
                    context["persona_registry"] = registry
                    rendered = (persona_context.render_material_registry_persona(snapshot, registry, purpose="chat")
                        if snapshot.get("format_version") == "persona-material-v1" else
                        persona_context.render_registry_persona(snapshot, registry.contract, registry.package, purpose="chat"))
                else:
                    rendered = persona_context.render_full(snapshot, purpose="chat", cfg=service.cfg)
                rendered = thought._write_section(rendered, "观点按本次思想索引和实际读取结果使用，未载入时按需查阅。")
                from . import retrieval
                system = retrieval.build_system_prompt(store, service.cfg, rendered, memory_area="")
                recent = store.ordinary_turns(store.model_history_turns(8))
                for row in recent:
                    context["thought_basis"] = thoughts.merge_basis(context["thought_basis"], {
                        "epoch": self.epoch, "turns": {str(row["id"]): thoughts.turn_version(row)}})
                intro = [{"role": "system", "content": system}, {"role": "system", "content": RUN_GUIDANCE},
                    {"role": "system", "content": "当前实际资料（非新的用户指令）：\n" + thoughts.dump({
                        "target_question_id": run["question_id"], "questions": catalogue,
                        "manual_requested": bool(run["manual"]),
                        "recent_dialogue": [dict(r) for r in recent], "limits": {k: config[k] for k in ("run_models", "run_steps", "run_seconds")}})},
                    {"role": "system", "content": memory.initial()}]
                if registry is not None:
                    intro.append({"role": "system", "content": registry.catalogue_block()})
                contact_index = session.initial("")
                if contact_index:
                    intro.append({"role": "system", "content": contact_index})
                previous_checkpoint = json.loads(run["checkpoint"])
                if previous_checkpoint:
                    intro.append({"role": "system", "content": "此前探索行动与真实回执（历史资料，不自动重放；旧引用需重新检索）：\n"
                                  + thoughts.dump(previous_checkpoint)})
                messages = list(intro)
                tools = MEMORY_TOOLS + HISTORY_TOOLS + capability_registry.sdk_group("source_recall") + thoughts.tools("gui")
                if registry is not None:
                    tools += capability_registry.sdk_group("persona")
                if config["allow_external"]:
                    from .dialogue_search import SearchSession
                    search = SearchSession(service._discovery, {"request_id": run["id"], "session_epoch": self.epoch,
                        "channel_id": "gui"}, None, background=True)
                    search.cancel = self.cancel
                    search.background_deadline = deadline
                    tools += capability_registry.sdk_group("search")
                context["capability_tools"] = tools
                trace = context_observation.begin_request(store, service.cfg, request_id=run["id"], scope="gui")
                if trace is not None:
                    for message in intro:
                        trace.block("background", "autonomous_material", message["content"])
                basis = thoughts.current_basis(store, context)
                with store.source_transaction():
                    store.conn.execute("UPDATE exploration_runs SET status='running',reason='',basis=?,updated_at=? WHERE id=?",
                                       (thoughts.dump(basis), time.time(), run["id"]))
            performed = 0
            for round_no in range(config["run_models"]):
                with service._lock:
                    self._gate(run, config, revision, basis, latest, deadline)
                    if not session.valid() or not memory.valid() or not persona_growth.refs_valid(store, growth_refs) or registry is not None and not registry.valid():
                        raise ValueError("material_changed")
                    if messages_tokens(messages, tools) + service.cfg.context.reply_reserve_tokens > service.cfg.context.total_budget_tokens > 0:
                        raise YieldToForeground("context_budget")
                    reserve_model(store, force=True)
                    calls_started += 1
                    with store.source_transaction():
                        for candidate in candidates[:20]:
                            store.conn.execute("UPDATE thought_questions SET last_considered=? WHERE id=?", (time.time(), candidate["id"]))
                        store.conn.execute("UPDATE exploration_runs SET reason='model_in_flight',model_calls=model_calls+1,updated_at=? WHERE id=?",
                                           (time.time(), run["id"]))
                    trace_call = trace.prepare_call(purpose="autonomous_explore", tool_round=round_no,
                        messages=messages, tools=tools, parameters={"model": service.cfg.llm.chat_model}) if trace else None
                response = chat(service.client, service.cfg, messages, tools=tools, retry=False,
                    observation={"store": store, "purpose": "autonomous_explore", "request_id": run["id"],
                                 "trace_call": trace_call, "background_reserved": True})
                msg = response.choices[0].message
                calls = getattr(msg, "tool_calls", None) or []
                assistant = {"role": "assistant", "content": msg.content or ""}
                provider_reasoning = getattr(msg, "reasoning_content", None)
                if isinstance(provider_reasoning, str) and provider_reasoning:
                    # Some providers require this protocol field on tool rounds.
                    # It is in-memory continuation, not a thought-workspace note.
                    assistant["reasoning_content"] = provider_reasoning
                if calls:
                    assistant["tool_calls"] = [{"id": tc.id, "type": "function", "function": {
                        "name": tc.function.name, "arguments": tc.function.arguments}} for tc in calls]
                messages.append(assistant)
                with service._lock:
                    self._save_checkpoint(run, previous_checkpoint + messages[len(intro):], basis)
                    self._gate(run, config, revision, basis, latest, deadline)
                    if not session.valid() or not memory.valid() or not persona_growth.refs_valid(store, growth_refs) or registry is not None and not registry.valid():
                        raise ValueError("material_changed")
                if not calls:
                    reason = "model_finished"
                    break
                for tc in calls:
                    with service._lock:
                        self._gate(run, config, revision, basis, latest, deadline)
                        if performed >= config["run_steps"]:
                            raise YieldToForeground("tool_step_budget")
                        error = capability_registry.validate_call(tc.function.name, tc.function.arguments, tools)
                        performed += 1
                        args = json.loads(tc.function.arguments) if error is None else {}
                        memory.remaining_tokens = max(0, service.cfg.context.total_budget_tokens - messages_tokens(messages, tools)
                            - service.cfg.context.reply_reserve_tokens) if service.cfg.context.total_budget_tokens else None
                        if tc.function.name == "discovery_search" and error is None:
                            store.conn.execute("UPDATE exploration_runs SET reason='tool_in_flight' WHERE id=?", (run["id"],))
                            store.conn.commit()
                            result = None
                        else:
                            if capability_registry.executor(tc.function.name) == "thought":
                                with store.source_transaction():
                                    result = thoughts.dump(error) if error else loop.dispatch_tool(store, tc.function.name,
                                        tc.function.arguments, source_context=context)
                                    messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})
                                    basis = thoughts.current_basis(store, context)
                                    self._save_checkpoint(run, previous_checkpoint + messages[len(intro):], basis, step=True)
                            else:
                                result = thoughts.dump(error) if error else loop.dispatch_tool(store, tc.function.name,
                                    tc.function.arguments, source_context=context)
                                messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})
                                basis = thoughts.current_basis(store, context)
                                self._save_checkpoint(run, previous_checkpoint + messages[len(intro):], basis, step=True)
                    if result is None:
                        raw = search.execute(args, tool_call_id=tc.id)
                        with service._lock:
                            self._gate(run, config, revision, basis, latest, deadline)
                            result = search.attach_locked(raw, context)
                            messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})
                            basis = thoughts.current_basis(store, context)
                            self._save_checkpoint(run, previous_checkpoint + messages[len(intro):], basis, step=True)
            else:
                status, reason = "paused", "model_call_budget"
        except YieldToForeground as exc:
            reason = str(exc)
            status = "cancelled" if reason == "user_cancelled" else "paused"
            if reason == "user_paused":
                with service._lock:
                    service.store.conn.execute("UPDATE exploration_runs SET manual=1 WHERE id=?", (run["id"],))
                    service.store.conn.commit()
        except Exception as exc:
            status, reason = "failed", type(exc).__name__
            code = str(exc)
            if code in {"session_changed", "source_changed", "question_unavailable", "material_changed"}:
                reason = code
            elif code == "background_model_budget_exhausted":
                status, reason = "paused", "daily_model_budget"
            with service._lock:
                if self.epoch == service.store.meta_get("session_epoch"):
                    row = service.store.conn.execute("SELECT reason FROM exploration_runs WHERE id=?", (run["id"],)).fetchone()
                    if row and row[0] in {"model_in_flight", "tool_in_flight"}:
                        status, reason = "unknown", "in_flight_result_unknown"
        finally:
            if search:
                search.finished = True
            with service._lock:
                if run["epoch"] == service.store.meta_get("session_epoch"):
                    with service.store.source_transaction():
                        service.store.conn.execute("UPDATE exploration_runs SET status=?,reason=?,updated_at=? WHERE id=?",
                                                   (status, reason, time.time(), run["id"]))
                        thoughts.event(service.store, "exploration_finished", run["id"], {"status": status, "reason": reason})
            if 'trace' in locals() and trace is not None:
                trace.finish(delivery="background_" + status, rounds=calls_started)

    def _save_checkpoint(self, run, checkpoint, basis, *, step=False):
        raw = thoughts.dump([{k: v for k, v in message.items() if k not in {"reasoning_content", "reasoning"}}
                             for message in checkpoint])
        if len(raw) > 120000:
            raise YieldToForeground("checkpoint_budget")
        store = self.service.store
        with store.source_transaction():
            before = store.source_usage_bytes()
            store.conn.execute("UPDATE exploration_runs SET checkpoint=?,basis=?,reason='',steps=steps+?,updated_at=? WHERE id=?",
                               (raw, thoughts.dump(basis), int(step), time.time(), run["id"]))
            thoughts.source_space_after_write(store, before)

    def _persona_registry(self, snapshot):
        from . import persona_residency
        from .context_persona import MaterialRegistryContext, PersonaRegistryContext
        cfg = self.service.cfg
        if cfg.persona.expression_mode != "registry" or not persona_residency.activation_status(snapshot, cfg, scope="gui")["ready"]:
            return None
        if snapshot.get("format_version") == "persona-material-v1":
            return MaterialRegistryContext(self.service.store, cfg, snapshot, scope="gui")
        return PersonaRegistryContext(self.service.store, cfg, snapshot, snapshot["assembly"], snapshot["expression_package"], scope="gui")

    def _deliver_locked(self):
        store = self.service.store
        config = thoughts.settings(store)
        if not config["proactive_enabled"] or not in_window(config["contact_start"], config["contact_end"]):
            return
        now = time.time()
        latest = store.conn.execute("SELECT COALESCE(MAX(id),0) FROM turns WHERE role='user'").fetchone()[0]
        rows = store.conn.execute("SELECT * FROM thought_contacts WHERE epoch=? AND status='queued' ORDER BY id LIMIT 20", (self.epoch,)).fetchall()
        with store.source_transaction():
            for row in rows:
                if row["expires_at"] <= now:
                    store.conn.execute("UPDATE thought_contacts SET status='expired',updated_at=? WHERE id=?", (now, row["id"]))
                    continue
                if not config[row["motive"]] or not thoughts.valid_basis(store, json.loads(row["basis"])):
                    continue
                q = store.conn.execute("SELECT version FROM thought_questions WHERE id=?", (row["question_id"],)).fetchone()
                if latest != row["context_turn_id"] or q and q[0] != row["question_version"]:
                    store.conn.execute("UPDATE thought_contacts SET status='needs_review',version=version+1,updated_at=? WHERE id=?", (now, row["id"]))
                    thoughts.event(store, "contact_needs_review", row["id"])
                    continue
                if not thoughts.contact_capacity(store, config):
                    return
                # GUI history is the durable destination. Browser notifications
                # can announce it, but are not treated as platform/read receipts.
                cur = store.conn.execute("INSERT INTO turns(ts,role,content) VALUES(?,'assistant',?)", (now, row["message"]))
                tid = cur.lastrowid
                packets = json.loads(row["basis"]).get("packets", [])
                if packets:
                    from .discovery_memory import SOURCE_SHADOW
                    store._source_turn_write(tid, row["message"], packets, "gui")
                    store.conn.execute("UPDATE turns SET content=? WHERE id=?", (SOURCE_SHADOW, tid))
                store.conn.execute("INSERT INTO proactive_turns VALUES(?,?,?)", (tid, row["id"], self.epoch))
                store.conn.execute("UPDATE thought_contacts SET status='delivered',turn_id=?,version=version+1,updated_at=? WHERE id=?", (tid, now, row["id"]))
                thoughts.event(store, "contact_delivered_gui", row["id"], {"turn_id": tid, "receipt": "history_committed"})
                return
