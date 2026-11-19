"""Question-driven idle agent and GUI contact delivery, disabled by default."""
from __future__ import annotations

import json
import threading
import time
import uuid
from itertools import count

from . import thought_workspace as thoughts
from . import activity, autonomy_policy
from .background_runtime import day, reserve_model
from .channels import ChannelError

RUN_GUIDANCE = """这是你的自主探索运行，不是用户刚发来的一条消息。
从实际疑问、相关记忆或自己的兴趣中选择值得推进的事情，也可以判断暂时没有值得推进的内容，选择休息或等待。
你决定收纳、选题、方法、解决与否、成果价值和交流意图。研究前用activity_plan简述：这是什么、有什么价值、为什么现在思考、做到哪里就停，再围绕该缺口使用工具。
定题前按本窗口允许的方式查看索引；explore才执行研究，defer/rest可直接收尾。兴趣与审美也有价值，不必编造用户需求；还有额度或还能延伸不是继续的理由。
先对照recent_activity_plans，已有定性解释够用时可结束，精确数字未知可以保留未知。相似问题优先关联related_ref复用，不因换标题、换模式就重新研究。
可以形成当前看法；关于经历和事实的判断保留真实出处与未知。未解疑问不作为已证实事实。
有实质变化时用思想能力保存摘要与下一步；等待外部条件时明确等待类型与原因，不靠反复改写制造进展。
窗口结束前用 activity_finish 提交一次结束回执：说明决定（continue/wait/rest/done）与变化类型（evidence/action/inference/rewrite/none）；选择休息时，时长按用户设置执行。
同时用goal_result对照选题：目标是否达到、哪个认识改变、继续是否值得及理由；改写旧结论选restatement。目标达到或继续价值低时收住，旁支先收纳为paused线索，不默认加入活跃队列。
回执引用逐字使用已载入材料的ref；历史往返由宿主按说话者拆分，自有观点可作参考但不是外部新证据。
动作使用工具返回的action_receipt.ref，不用函数名或描述拼造ID。表述润色不算实质进展。
以宿主task_anchor里的原任务为目标推进。未命中只报告本次查询未找到，受限未返回只报告尚未读到；保留目标和下一步，不把材料使用说明推成素材没有原型或原任务无需完成。
任务目录与task摘要不等于已读旧推演；仅需复查旧推演时才用thought_load(detail=full)。
历史AI自述可以参考表达，但不能证明真实做过或对用户有效。具体活动自述以本窗口工具与运行账本为依据，语言习惯本身可直接采用。
每个工作窗口的 activity_finish 同时提交 communication：invite（现在邀请）、later（留待以后）、none（无需联系），附简短理由。
先判断是否有值得告诉用户的新结果、共同兴趣或需要用户补充的信息，再区分现在邀请还是留待以后；重复解释处境、润色旧话不必联系，也无需凑联系次数。
invite/later 关联已保存的问题ref、观点view_ref，或沿用 eligible_contact_intents 的intent_ref；新内容先收纳，再提交结束回执。
invite 另附 invitation_text，只简短询问现在是否方便；later 只保留意图不排队发送。即使当前联系受阻，也如实表达意图，由宿主核对权限与投递条件。
邀请不携带成果正文，用户接受后宿主才会载入展开材料；没有回复不催办。后台不单独调用 contact_propose。
contact_waits表示正在等待用户；沉默、忙碌时先做独立的事情或发呆，不另换主题连续邀请；恢复以真实相关回应或用户约定为准，测试放宽额度不解除等待。
收纳或更新具体事项时，可用其调度参数说明所属活动、价值、紧迫性及有依据的截止时间。
同一批材料上的推演窗口有限：先想清楚再写，窗口用尽时等待新证据而不是换个说法再来一轮。
不输出原始思维链。
"""

FREE_INTEREST_GUIDANCE = """这是没有指定目标的兴趣窗。已有明确好奇可以直接定题；否则先用activity_browse浏览自己想看的记忆、疑问或兴趣，再用activity_plan定下具体题目。
近期对话和上轮选题主要帮你避开重复，不是本轮待办。看到某条疑问卡在外部条件时，保留原等待条件；先看看另一库能否给出独立的新方向，再判断整个窗口是否值得继续。
没有值得推进的方向可以搁置或休息，不必扫完所有库。浏览只读本机索引，定题前最多再展开一项；定题后按原权限执行。"""


def in_window(start, end):
    return activity.in_window(start, end)


def tool_receipt(result, call):
    try:
        start = result.index("{")
        body = json.loads(result[start:])
    except (ValueError, TypeError, AttributeError):
        return result
    if not isinstance(body, dict):
        return result
    body["action_receipt"] = {"ref": call.id, "tool": call.function.name,
                              "outcome": body.get("error") or body.get("status") or "returned",
                              "notice": "实际工具调用回执；被拒、未读或搜索未命中不证明目标不存在。"}
    return result[:start] + thoughts.dump(body)


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
            activity.normalize_legacy_idle(store)
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
            store.conn.execute("UPDATE v2_activity_plans SET object='',value='',why_now='',stop_condition='',"
                               "next_action='',result=NULL")
            for table, field in (("thought_questions", "question='',reason='',progress='',next_step=''"),
                                 ("thought_contacts", "message=''"), ("exploration_runs", "checkpoint='[]'")):
                for row in store.conn.execute(f"SELECT id,basis FROM {table}").fetchall():
                    if json.loads(row["basis"]).get("packets"):
                        store.conn.execute(f"UPDATE {table} SET {field} WHERE id=?", (row["id"],))
                        if table == "thought_questions":
                            store.conn.execute("DELETE FROM thought_agenda WHERE question_id=?", (row["id"],))

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
                    if not activity.awake(store):
                        store.conn.execute("UPDATE exploration_runs SET status='paused',reason='sleeping',updated_at=? "
                            "WHERE epoch=? AND manual=0 AND status='queued'", (time.time(), self.epoch))
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
                    activity.clear_idle(store, reason="explicit_task")
                    config = thoughts.settings(store)
                    if not config["allow_paid"] or (autonomy_policy.TEST_CONTROLS_ONLY and config["wake_state"] != "awake"):
                        raise ValueError("请先打开后台总开关，允许调用已配置模型")
                    if store.conn.execute("SELECT 1 FROM exploration_runs WHERE epoch=? AND status IN('queued','running')",
                                          (self.epoch,)).fetchone():
                        raise ValueError("已有探索排队或运行，请先查看进度")
                    if action == "resume":
                        old = store.conn.execute("SELECT * FROM exploration_runs WHERE id=? AND epoch=?",
                            (payload.get("run_id"), self.epoch)).fetchone()
                        info = activity.run_info(store, payload.get("run_id"))
                        if not autonomy_policy.TEST_CONTROLS_ONLY and info and not config["activities"][info["activity"]]["seconds"]:
                            raise ValueError("该活动时长为0，请先开放")
                        if old and not thoughts.valid_basis(store, json.loads(old["basis"])):
                            raise ValueError("原探索资料已变化，请开始新的活动")
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
                elif action == "contact_reply":
                    # Direct user action from the panel. Acceptance binds to a real
                    # chat message, so buttons only carry defer/decline.
                    identity, version, decision = payload.get("id"), payload.get("version"), payload.get("decision")
                    if (not isinstance(identity, str) or type(version) is not int
                            or decision not in {"defer", "decline"}):
                        raise ValueError("按钮仅支持稍后或不聊；接受请在聊天中自然回应")
                    from .autonomy_v2 import build
                    from .autonomy_v2.models import ContactReply, ReplyDecision
                    context = thoughts.execution_context(store, {"request_id": "panel_reply", "scope": "gui"})
                    build(store=store).contact_reply(context, ContactReply(
                        attempt_id=identity, expected_attempt_version=version,
                        decision=ReplyDecision(decision), reason="面板按钮操作"))
                elif action == "intent_cancel":
                    identity, version = payload.get("id"), payload.get("version")
                    if not isinstance(identity, str) or type(version) is not int:
                        raise ValueError("联系意图引用无效")
                    changed = store.conn.execute("UPDATE v2_contact_intents SET state='closed',version=version+1,updated_at=? "
                        "WHERE id=? AND version=? AND epoch=? AND state='active'",
                        (time.time(), identity, version, self.epoch)).rowcount
                    if not changed:
                        raise ValueError("联系意图已改变或已结束，请刷新核对")
                    with store.source_transaction():
                        store.conn.execute("UPDATE v2_invitation_attempts SET state='withdrawn',version=version+1,updated_at=? "
                            "WHERE intent_id=? AND epoch=? AND state IN('prepared','awaiting_reply')", (time.time(), identity, self.epoch))
                    thoughts.event(store, "intent_cancelled", identity)
                elif action == "migration_prepare":
                    from .autonomy_v2 import build
                    from .autonomy_v2.models import Scope
                    plan = build(store=store).prepare_migration(Scope(session_epoch=self.epoch, channel_id="gui"))
                    stored = {"identity": plan.identity, "legacy_contact_ids": list(plan.legacy_contact_ids),
                              "question_ids": list(plan.question_ids), "memory_ids": list(plan.memory_ids),
                              "compatibility_notes": list(plan.compatibility_notes)}
                    store.meta_set("autonomy_migration_plan", thoughts.dump(stored))
                    thoughts.event(store, "migration_prepared", plan.identity,
                                   {"contacts": len(plan.legacy_contact_ids), "questions": len(plan.question_ids)})
                elif action == "migration_apply":
                    stored = json.loads(store.meta_get("autonomy_migration_plan") or "null")
                    if not isinstance(stored, dict) or payload.get("identity") != stored.get("identity"):
                        raise ValueError("请先准备迁移计划，再按当前计划执行")
                    from .autonomy_v2 import build
                    from .autonomy_v2.models import MigrationPlan, Scope
                    plan = MigrationPlan(identity=stored["identity"],
                                         scope=Scope(session_epoch=self.epoch, channel_id="gui"),
                                         from_contract="autonomy-v1", to_contract="autonomy-v2", data_format=1,
                                         question_ids=tuple(stored["question_ids"]),
                                         memory_ids=tuple(stored["memory_ids"]),
                                         legacy_contact_ids=tuple(stored["legacy_contact_ids"]),
                                         compatibility_notes=tuple(stored["compatibility_notes"]))
                    build(store=store).apply_migration(plan)
                    store.meta_set("autonomy_migration_plan", "")
                elif action == "idle_rest":
                    period = activity.enter_idle(store, "user_requested_rest")
                    if period is None:
                        raise ValueError("发呆时长为0，主动休息已停用")
                    self.cancel_reason = "user_paused"
                    self.cancel.set()
                elif action == "idle_wake":
                    activity.clear_idle(store, reason="user_requested")
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

    def schedule_diagnostics(self):
        service = self.service
        if not service._lock.acquire(blocking=False):
            raise ChannelError(503, "busy", "Alice忙碌，稍后读取调度记录")
        try:
            store = service.store
            config = thoughts.settings(store)
            current = activity.weights(store, config)
            records = []
            for row in store.conn.execute(
                    "SELECT r.id,r.created_at,a.activity,a.selection FROM exploration_runs r "
                    "JOIN autonomous_activity_runs a ON a.run_id=r.id "
                    "WHERE r.epoch=? ORDER BY r.created_at DESC LIMIT 30", (self.epoch,)):
                selection = json.loads(row["selection"])
                records.append({"id": row["id"], "at": row["created_at"], "activity": row["activity"],
                                **{key: selection[key] for key in (
                                    "reason", "modes", "draw_weights", "previous_activity", "dry_windows",
                                    "blocked_modes", "idle_seconds") if key in selection}})
            return {"protocol": "alice-lab/schedule-v1", "session_epoch": self.epoch,
                    "current": {key: value for key, value in current.items() if key != "input_stamp"},
                    "records": records}
        finally:
            service._lock.release()

    def _snapshot(self):
        from .autonomy_v2.planning import ActivityPlanning
        from .autonomy_v2.contact_wait import ContactWait
        store = self.service.store
        questions = [{**thoughts.public_question(r), 'value_waiting': ActivityPlanning(store).parked(r)}
                     for r in thoughts.question_rows(store, include_closed=True)
                     if r["question"] and thoughts.valid_basis(store, json.loads(r["basis"]))][:100]
        contacts = [{k: r[k] for k in ("id", "version", "question_id", "motive", "message", "mode", "status", "turn_id", "expires_at")}
                    for r in store.conn.execute("SELECT * FROM thought_contacts WHERE epoch=? ORDER BY id DESC LIMIT 50", (self.epoch,))
                    if thoughts.valid_basis(store, json.loads(r["basis"]))]
        runs = [dict(r) for r in store.conn.execute("SELECT id,status,reason,question_id,model_calls,steps,created_at,updated_at "
                "FROM exploration_runs WHERE epoch=? ORDER BY created_at DESC LIMIT 30", (self.epoch,))]
        for run in runs:
            run["activity"] = activity.run_info(store, run["id"])
            run["activity_plan"] = ActivityPlanning(store).get(run["id"])
            receipt = store.conn.execute("SELECT * FROM v2_progress_receipts WHERE run_id=?", (run["id"],)).fetchone()
            run["finish_receipt"] = dict(receipt) if receipt else None
        events = [dict(r) for r in store.conn.execute("SELECT id,kind,object_id,detail,created_at FROM thought_events "
                  "WHERE epoch=? ORDER BY id DESC LIMIT 60", (self.epoch,))]
        stances = [{"id": r["id"], "title": r["title"], "created_at": r["created_at"]}
                   for r in thoughts.current_stance_rows(store, store.memory_rows(purpose="observer", scope="gui", kind="stance"))][:50]
        usage = store.conn.execute("SELECT models FROM background_usage WHERE day=?", (day(),)).fetchone()
        config = thoughts.settings(store)
        lifecycle = activity.lifecycle(config)
        scheduler = activity.weights(store, config)
        idle = activity.active_idle(store)
        active = next((r for r in runs if r["status"] in {"running", "queued"}), None)
        if active and active["status"] == "running":
            wait = "running"
        elif not self._idle():
            wait = "foreground_priority"
        elif lifecycle["effective"] == "sleeping" and not active:
            wait = lifecycle["reason"]
        elif not config["allow_paid"]:
            wait = "model_permission_required"
        elif not autonomy_policy.TEST_CONTROLS_ONLY and (usage[0] if usage else 0) >= config["daily_models"]:
            wait = "daily_model_budget"
        elif not autonomy_policy.TEST_CONTROLS_ONLY and (not config["run_models"] or not config["run_steps"] or not config["run_seconds"]):
            wait = "run_budget_disabled"
        elif active:
            wait = "queued"
        elif idle:
            wait = "idle_rest"
        elif not any(m["weight"] for m in scheduler["modes"].values() if m != "idle"):
            wait = "waiting_material"
        else:
            latest = store.conn.execute("SELECT COALESCE(MAX(ts),0) FROM turns").fetchone()[0]
            wait = "ready_for_test" if autonomy_policy.TEST_CONTROLS_ONLY else "waiting_idle" if time.time()-latest < config["idle_seconds"] else "waiting_interval"
        scheduler["status"] = wait
        scheduler["idle"] = {"active": idle is not None, "until_at": idle["until_at"] if idle else None,
                             "reason": idle["reason"] if idle else None}
        intents = [{k: r[k] for k in ("id", "version", "motive", "topic", "reason", "state",
                                      "question_id", "next_eligible_at", "legacy_contact_id", "updated_at")}
                   for r in store.conn.execute("SELECT * FROM v2_contact_intents WHERE epoch=? ORDER BY updated_at DESC LIMIT 50", (self.epoch,))]
        invitations = [{k: r[k] for k in ("id", "version", "intent_id", "state", "invitation_text", "prepared_at",
                                          "delivered_at", "expires_at", "invitation_turn_id", "reply_user_turn_id",
                                          "expansion_turn_id", "deferred_reason", "updated_at")}
                       for r in store.conn.execute("SELECT * FROM v2_invitation_attempts WHERE epoch=? ORDER BY updated_at DESC LIMIT 50", (self.epoch,))]
        for invitation in invitations:
            invitation["topic"] = next((i["topic"] for i in intents if i["id"] == invitation["intent_id"]), "")
        from .autonomy_v2 import build
        from .autonomy_v2.models import Scope
        stack = build(store=store)
        scope = Scope(session_epoch=self.epoch, channel_id="gui")
        for intent in intents:
            stored_intent = stack.invitations.repository.get_intent(scope, intent["id"])
            if intent["state"] == "active":
                intent["blockers"] = stack.invitations.delivery_blockers(scope, stored_intent)
            try:
                stack.invitations.host.validate_intent(scope, stored_intent)
            except ValueError:
                intent["reason"] = "意图已结束或资料已变化，原理由不再展示"
        for invitation in invitations:
            if invitation["state"] == "prepared":
                intent = stack.invitations.repository.get_intent(scope, invitation["intent_id"])
                invitation["blockers"] = stack.invitations.delivery_blockers(scope, intent,
                    attempt_id=invitation["id"])
        decisions = [stack.communication.get_for_run(self.epoch, r[0]) for r in store.conn.execute(
            "SELECT run_id FROM v2_contact_decisions WHERE epoch=? ORDER BY created_at DESC LIMIT 50", (self.epoch,))]
        for run in runs:
            run["communication_receipt"] = stack.communication.get_for_run(self.epoch, run["id"])
        plan_stored = json.loads(store.meta_get("autonomy_migration_plan") or "null")
        return {"status": "ok", "session_epoch": self.epoch,
                "revision": int(store.meta_get("autonomy_revision") or 0), "settings": config,
                "lifecycle": lifecycle, "scheduler": scheduler,
                "questions": questions, "stances": stances, "contacts": contacts, "runs": runs, "events": events,
                "intents": intents, "invitations": invitations, "communication_decisions": decisions,
                "contact_waits": ContactWait(store).rows(scope),
                "migration_plan": plan_stored,
                "budget": {"day": day(), "models": usage[0] if usage else 0},
                "channels": {"gui": True, "next_chat": True, "qq": False, "offline": False}}

    def _detail(self, payload):
        store = self.service.store
        if payload.get("kind") == "question":
            row = self._question(payload.get("id"), payload.get("version"))
            from dataclasses import asdict
            from .autonomy_v2.adapters import question_from_row
            question = question_from_row(store, row)
            return {**thoughts.public_question(row, full=True), "scheduling": activity.assessment(store, row),
                    "family_id": question.family_id, "material": asdict(question.material),
                    "wait_condition": asdict(question.wait_condition) if question.wait_condition else None,
                    "remaining_family_windows": activity.remaining_windows(store, row, thoughts.settings(store)["same_material_windows"])}
        if payload.get("kind") == "stance":
            row = next((r for r in store.memory_rows(purpose="observer", scope="gui", kind="stance") if r["id"] == payload.get("id")), None)
            if row:
                return {"content": row["content"], "title": row["title"]}
        if payload.get("kind") == "run":
            row = store.conn.execute("SELECT * FROM exploration_runs WHERE id=? AND epoch=?", (payload.get("id"), self.epoch)).fetchone()
            info = activity.run_info(store, row["id"]) if row else None
            if row and info and info["activity"] == "idle":
                # Rest has no source-derived body. Support older basis={} rows
                # without relaxing the licence checks on real work records.
                if (not row["id"].startswith("idle_") or row["question_id"] is not None
                        or row["model_calls"] or row["steps"]):
                    raise ValueError("发呆记录与实际执行信息不一致")
                selection = info.get("selection") or {}
                return {"id": row["id"], "kind": "idle", "status": row["status"],
                        "reason": row["reason"], "model_calls": 0, "steps": 0,
                        "messages": [], "finish_receipt": None,
                        "activity": {"activity": "idle", "started_at": info["started_at"],
                                     "deadline": info["deadline"], "selection": selection}}
            if row and thoughts.valid_basis(store, json.loads(row["basis"])):
                from .autonomy_v2 import build
                from .autonomy_v2.planning import ActivityPlanning
                return {"id": row["id"], "status": row["status"], "messages": json.loads(row["checkpoint"]),
                        "activity": info,
                        "communication_receipt": build(store=store).communication.get_for_run(self.epoch, row["id"]),
                        "activity_plan": ActivityPlanning(store).get(row["id"]),
                        "finish_receipt": dict(receipt) if (receipt := store.conn.execute(
                            "SELECT * FROM v2_progress_receipts WHERE run_id=?", (row["id"],)).fetchone()) else None}
        raise ValueError("记录不存在或来源已失效")

    def _enqueue(self, qid=None, *, manual=False, mode=None, selection=None):
        store, now = self.service.store, time.time()
        if mode is None:
            question = store.conn.execute("SELECT * FROM thought_questions WHERE id=? AND epoch=?", (qid, self.epoch)).fetchone() if qid else None
            mode, selection = activity.choose(store, thoughts.settings(store), question=question)
        if mode is None:
            raise ValueError("暂无可推进活动；请留下具体问题或等待新资料")
        if mode == "idle":
            activity.enter_idle(store, selection["reason"], selection=selection)
            raise ValueError("当前无可推进事项，已进入发呆；稍后会重新判断")
        qid = qid or selection.get("question_id")
        identity = "explore_" + uuid.uuid4().hex
        basis = {"epoch": self.epoch}
        with store.source_transaction():
            store.conn.execute("INSERT INTO exploration_runs(id,epoch,status,question_id,basis,manual,created_at,updated_at) "
                "VALUES(?,?,'queued',?,?,?,?,?)", (identity, self.epoch, qid, thoughts.dump(basis), int(manual), now, now))
            thoughts.event(store, "exploration_queued", identity, {"question_id": qid, "manual": manual})
            activity.register(store, identity, mode, selection)
            if qid:
                row = store.conn.execute("SELECT * FROM thought_questions WHERE id=? AND epoch=?", (qid, self.epoch)).fetchone()
                if row is not None:
                    # Charge the family window for this real execution at admission.
                    activity.consume_window(store, row, identity,
                                            max(1, min(int(thoughts.settings(store).get("same_material_windows") or 2), 20)))

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
            expired_idle = store.conn.execute("SELECT until_at FROM v2_activity_idle WHERE scope='gui'").fetchone()
            if expired_idle is not None and expired_idle[0] <= time.time():
                activity.clear_idle(store)  # Expiry permits re-scheduling; it never forces work.
            if activity.active_idle(store):
                return  # Voluntary rest: no delivery, no model call, host timer only.
            self._deliver_invitations_locked()
            config = thoughts.settings(store)
            run = store.conn.execute("SELECT * FROM exploration_runs WHERE epoch=? AND status='queued' ORDER BY created_at LIMIT 1",
                                     (self.epoch,)).fetchone()
            if not config["allow_paid"] or (autonomy_policy.TEST_CONTROLS_ONLY and config["wake_state"] != "awake"):
                return
            used = store.conn.execute("SELECT models FROM background_usage WHERE day=?", (day(),)).fetchone()
            if not autonomy_policy.TEST_CONTROLS_ONLY and (not config["run_models"] or not config["run_steps"] or not config["run_seconds"] or (used[0] if used else 0) >= config["daily_models"]):
                return
            if run is not None and not run["manual"] and not activity.awake(store):
                with store.source_transaction():
                    store.conn.execute("UPDATE exploration_runs SET status='paused',reason='sleeping',updated_at=? WHERE id=?",
                                       (time.time(), run["id"]))
                return
            if run is None:
                if not activity.awake(store):
                    return
                latest = store.conn.execute("SELECT COALESCE(MAX(ts),0) FROM turns").fetchone()[0]
                if not autonomy_policy.TEST_CONTROLS_ONLY and time.time() - latest < config["idle_seconds"]:
                    return
                if not autonomy_policy.TEST_CONTROLS_ONLY and time.time() - store.meta_get_time("autonomy_last_started") < config["interval_seconds"]:
                    return
                mode, selection = activity.choose(store, config)
                if mode is None:
                    return
                if mode == "idle":
                    activity.enter_idle(store, selection["reason"], selection=selection)
                    return
                self._enqueue(manual=False, mode=mode, selection=selection)
                run = store.conn.execute("SELECT * FROM exploration_runs WHERE epoch=? AND status='queued' ORDER BY created_at LIMIT 1",
                                         (self.epoch,)).fetchone()
            info = activity.run_info(store, run["id"])
            if info is None:  # Old queued/resumed run: retain its task, add mode metadata.
                target = store.conn.execute("SELECT * FROM thought_questions WHERE id=? AND epoch=?", (run["question_id"], self.epoch)).fetchone()
                mode, selection = activity.choose(store, config, question=target)
                if mode is None:
                    with store.source_transaction():
                        store.conn.execute("UPDATE exploration_runs SET status='paused',reason='waiting_material' WHERE id=?", (run["id"],))
                    return
                with store.source_transaction():
                    activity.register(store, run["id"], mode, selection)
                info = activity.run_info(store, run["id"])
            if not autonomy_policy.TEST_CONTROLS_ONLY and not config["activities"][info["activity"]]["seconds"]:
                with store.source_transaction():
                    store.conn.execute("UPDATE exploration_runs SET status='paused',reason='mode_disabled',updated_at=? WHERE id=?", (time.time(), run["id"]))
                return
            self.cancel = threading.Event()
            self.cancel_reason = "user_cancelled"
            store.meta_set("autonomy_last_started", str(time.time()))
            admitted = {**dict(run), "settings_revision": int(store.meta_get("autonomy_revision") or 0)}
        self._run(admitted, config)

    def _gate(self, run, config, revision, basis, latest, deadline):
        store = self.service.store
        if not self._idle() or self.cancel.is_set():
            raise YieldToForeground(self.cancel_reason if self.cancel.is_set() else "foreground_priority")
        if self.epoch != store.meta_get("session_epoch") or run["epoch"] != self.epoch:
            raise ValueError("session_changed")
        if str(revision) != (store.meta_get("autonomy_revision") or "0"):
            raise YieldToForeground("settings_changed")
        if (autonomy_policy.TEST_CONTROLS_ONLY or not run["manual"]) and not activity.awake(store):
            raise YieldToForeground("sleeping")
        if (run.get("persona_version") != store.meta_get("persona_version")
                or run.get("model_generation") != self.service._discovery._generation):
            raise YieldToForeground("runtime_changed")
        if not thoughts.valid_basis(store, basis):
            raise ValueError("source_changed")
        current = store.conn.execute("SELECT COALESCE(MAX(id),0) FROM turns WHERE role='user'").fetchone()[0]
        if current != latest:
            raise YieldToForeground("new_conversation")
        if time.monotonic() >= deadline or time.time() >= run.get("wall_deadline", float("inf")):
            raise YieldToForeground("run_time_budget")

    def _run(self, run, config):
        from . import capability_registry, context_observation, loop, persona_context
        from .context_memory import MemoryContext, TOOLS as MEMORY_TOOLS
        from .context_history import HistoryContext, TOOLS as HISTORY_TOOLS
        from .context import messages_tokens
        from .llm import chat
        service, search = self.service, None
        context = {"scope": "gui", "request_id": run["id"], "run_id": run["id"], "packet_ids": [], "worker_refs": [],
                   "recall_queries": [], "recalled_ids": [], "memory_ids": [], "allow_external_recall": True,
                   "manual_requested": bool(run["manual"]), "autonomy_test": autonomy_policy.TEST_CONTROLS_ONLY,
                   "limits": autonomy_policy.source_limits(service.store)}
        with service._lock:
            info = activity.run_info(service.store, run["id"])
        mode = info["activity"]
        permitted = config["activities"][mode]["tools"]
        seconds = None if autonomy_policy.TEST_CONTROLS_ONLY else min(config["run_seconds"], config["activities"][mode]["seconds"])
        deadline = float("inf") if seconds is None else time.monotonic() + seconds
        run["wall_deadline"] = float("inf") if seconds is None else time.time() + seconds
        status, reason = "completed", "no_further_step"
        calls_started = 0
        try:
            with service._lock:
                store = service.store
                revision = run["settings_revision"]
                if run["epoch"] != store.meta_get("session_epoch"):
                    raise ValueError("session_changed")
                run["persona_version"] = store.meta_get("persona_version")
                run["model_generation"] = service._discovery._generation
                latest = store.conn.execute("SELECT COALESCE(MAX(id),0) FROM turns WHERE role='user'").fetchone()[0]
                memory = MemoryContext(store, service.cfg, "gui", "")
                memory.testing = autonomy_policy.TEST_CONTROLS_ONLY
                memory.history = HistoryContext(memory, boundary={"first_retained_id": latest + 1})
                context["memory_session"] = memory
                context["thought_basis"] = json.loads(run["basis"])
                session = thoughts.ThoughtSession(store, context)
                context["thought_session"] = session
                candidates = [r for r in activity.candidates(store) if activity.assessment(store, r)["activity"] == mode]
                candidates.sort(key=lambda r: (r["last_considered"], -r["updated_at"]))
                if run["question_id"]:
                    target = next((r for r in thoughts.question_rows(store, include_closed=True) if r["id"] == run["question_id"]), None)
                    if target is None or not thoughts.valid_basis(store, json.loads(target["basis"])):
                        raise ValueError("question_unavailable")
                    if not run["manual"] and target["id"] not in {r["id"] for r in candidates}:
                        raise ValueError("question_unavailable")
                    candidates = [target] + [r for r in candidates if r["id"] != target["id"]]
                catalogue = [session.expose(r) for r in candidates[:20]]
                target = next((r for r in candidates if r["id"] == run["question_id"]), None)
                from .autonomy_v2.planning import ActivityPlanning
                planning = ActivityPlanning(store)
                free_interest = (mode == "interest" and run["question_id"] is None
                                 and (not run["manual"] or info["selection"].get("free_interest_browse")))
                if free_interest:
                    context["interest_browse"] = {"sources": set(), "peeked": False, "memory": "memory" in permitted}
                task_anchor = ({"question_id": target["id"], "goal": target["question"], "origin": target["origin"],
                                "notice": "收纳时的原任务，保留至有真实成果或用户明确变更；不是用户逐字引文。"}
                               if target is not None else None)
                snapshot = persona_context.load_persona_snapshot()
                from . import persona_growth, thought
                growth_refs = []
                if snapshot is not None:
                    sections, growth_basis = persona_growth.project(snapshot["sections"], persona_growth.evidence_rows(store))
                    snapshot = {**snapshot, "sections": sections}
                    growth_refs = growth_basis["refs"]
                registry = self._persona_registry(snapshot)
                if registry is not None:
                    registry.testing = autonomy_policy.TEST_CONTROLS_ONLY
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
                guidance = RUN_GUIDANCE + "\n" + activity.GUIDANCE[mode]
                if free_interest:
                    guidance += "\n" + FREE_INTEREST_GUIDANCE
                else:
                    guidance += "\n可先用thought_search/thought_load了解任务。"
                intro = [{"role": "system", "content": system}, {"role": "system", "content": guidance},
                    {"role": "system", "content": "当前实际资料（非新的用户指令）：\n" + thoughts.dump({
                        "lifecycle": activity.lifecycle(config), "activity": mode,
                        "selection": {key: value for key, value in info["selection"].items()
                                      if key not in {"draw_weights", "previous_activity"}},
                        "permitted_tool_groups": permitted,
                        "window_seconds": seconds,
                        "interests": [dict(r) for r in store.conn.execute("SELECT id,topic,summary FROM discovery_interests WHERE enabled=1 AND scope_json LIKE '%gui%' LIMIT 20")] if mode == "interest" and not free_interest else [],
                        "target_question_id": run["question_id"], "task_anchor": task_anchor, "questions": catalogue,
                        "current_activity_plan": planning.get(run["id"]),
                        "recent_activity_plans": planning.recent(self.epoch, limit=3),
                        "actual_window": {"run_id": run["id"], "activity": mode, "execution_state": "starting",
                                          "notice": "本窗口刚开始；做过的活动以随后真实工具回执为准。"},
                        "manual_requested": bool(run["manual"]),
                        "recent_dialogue": [dict(r) for r in recent], "limits": {k: None if autonomy_policy.TEST_CONTROLS_ONLY else config[k] for k in ("run_models", "run_steps", "run_seconds")},
                        "test_controls_only": autonomy_policy.TEST_CONTROLS_ONLY,
                        "test_policy_notice": "当前测试跳过运行额度/时段/间隔；选题价值、用户等待和工具权限仍生效，可以休息。" if autonomy_policy.TEST_CONTROLS_ONLY else None})},
                    {"role": "system", "content": ("记忆可在找题时按需浏览。" if free_interest else memory.initial())
                     if "memory" in permitted else "记忆读取本模式未开放。"}]
                if registry is not None and "persona" in permitted:
                    intro.append({"role": "system", "content": registry.catalogue_block()})
                contact_index = session.initial("")
                if contact_index:
                    intro.append({"role": "system", "content": contact_index})
                previous_checkpoint = json.loads(run["checkpoint"])
                if previous_checkpoint:
                    intro.append({"role": "system", "content": "此前探索行动与真实回执（历史资料，不自动重放；旧引用需重新检索）：\n"
                                  + thoughts.dump(previous_checkpoint)})
                messages = list(intro)
                tools = [tool for tool in thoughts.tools("gui", free_interest=free_interest)
                         if tool["function"]["name"] not in {"contact_propose", "contact_reply", "contact_availability"}]
                if "memory" in permitted:
                    tools += MEMORY_TOOLS
                if "history" in permitted:
                    tools += HISTORY_TOOLS
                if "sources" in permitted:
                    tools += capability_registry.sdk_group("source_recall")
                if registry is not None and "persona" in permitted:
                    tools += capability_registry.sdk_group("persona")
                if config["allow_external"] and "search" in permitted:
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
                    started = time.time()
                    selection = {**info["selection"], "free_interest_browse": bool(free_interest),
                                 "exposed_commands": [t["function"]["name"] for t in tools],
                                 "window_seconds": seconds, "lifecycle": activity.lifecycle(config)}
                    store.conn.execute("UPDATE autonomous_activity_runs SET charged=0,started_at=?,deadline=?,input_stamp=?,selection=? WHERE run_id=?",
                        (started, 0 if seconds is None else started + seconds, activity.input_stamp(store), thoughts.dump(selection), run["id"]))
                    store.conn.execute("UPDATE exploration_runs SET status='running',reason='',basis=?,updated_at=? WHERE id=?",
                                       (thoughts.dump(basis), time.time(), run["id"]))
            performed = 0
            for round_no in (count() if autonomy_policy.TEST_CONTROLS_ONLY else range(config["run_models"])):
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
                        if not autonomy_policy.TEST_CONTROLS_ONLY and performed >= config["run_steps"]:
                            raise YieldToForeground("tool_step_budget")
                        error = capability_registry.validate_call(tc.function.name, tc.function.arguments, tools)
                        args = json.loads(tc.function.arguments or "{}") if error is None else {}
                        if error is None:
                            error = planning.tool_error(run['id'], tc.function.name, args, context.get("interest_browse"))
                        performed += 1
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
                                    result = tool_receipt(result, tc)
                                    messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})
                                    basis = thoughts.current_basis(store, context)
                                    self._save_checkpoint(run, previous_checkpoint + messages[len(intro):], basis, step=True)
                            else:
                                result = thoughts.dump(error) if error else loop.dispatch_tool(store, tc.function.name,
                                    tc.function.arguments, source_context=context)
                                result = tool_receipt(result, tc)
                                messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})
                                basis = thoughts.current_basis(store, context)
                                self._save_checkpoint(run, previous_checkpoint + messages[len(intro):], basis, step=True)
                    if result is None:
                        raw = search.execute(args, tool_call_id=tc.id)
                        with service._lock:
                            self._gate(run, config, revision, basis, latest, deadline)
                            result = tool_receipt(search.attach_locked(raw, context), tc)
                            messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})
                            basis = thoughts.current_basis(store, context)
                            self._save_checkpoint(run, previous_checkpoint + messages[len(intro):], basis, step=True)
                    if tc.function.name == "activity_finish" and not json.loads(result).get("error"):
                        reason = "activity_finished"
                        break
                if reason == "activity_finished":
                    break
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
                    self._default_finish_receipt(run["id"])
            if 'trace' in locals() and trace is not None:
                trace.finish(delivery="background_" + status, rounds=calls_started)

    def _default_finish_receipt(self, run_id):
        """No receipt from the model: record actual execution, claim no progress.

        Never a paid follow-up call; the tools' real results stay on disk.
        """
        store = self.service.store
        try:
            from .autonomy_v2 import build
            from .autonomy_v2.models import FinishDecision, ProgressKind, ProgressReport
            stack = build(store=store)
            row = store.conn.execute("SELECT model_calls,steps FROM exploration_runs WHERE id=?", (run_id,)).fetchone()
            executed = bool(row and (row["model_calls"] or row["steps"]))
            context = thoughts.execution_context(store, {"request_id": run_id, "run_id": run_id, "scope": "gui"})
            basis_row = store.conn.execute("SELECT basis FROM exploration_runs WHERE id=?", (run_id,)).fetchone()
            with store.source_transaction():
                stack.progress.finish(context, None, ProgressReport(
                    decision=FinishDecision.CONTINUE, progress_kind=ProgressKind.NONE,
                    summary="宿主补记：窗口结束未收到结束回执，不确认实质进展"), actual_execution=executed)
                stack.communication.finish(context, None,
                    basis=json.loads(basis_row[0]) if basis_row else {})
        except Exception as exc:  # Receipt bookkeeping must not mask the run status.
            thoughts.event(store, "activity_receipt_failed", run_id, {"type": type(exc).__name__})

    def _save_checkpoint(self, run, checkpoint, basis, *, step=False):
        raw = thoughts.dump([{k: v for k, v in message.items() if k not in {"reasoning_content", "reason"}}
                             for message in checkpoint])
        if len(raw) > 120000:
            raise YieldToForeground("checkpoint_budget")
        store = self.service.store
        with store.source_transaction():
            before = store.source_usage_bytes()
            if (not step and checkpoint and checkpoint[-1].get("role") == "assistant"
                    and not checkpoint[-1].get("tool_calls") and checkpoint[-1].get("content", "").strip()):
                activity.charge(store, run["id"])
            if step and checkpoint and checkpoint[-1].get("role") == "tool":
                raw_result = checkpoint[-1]["content"]
                try:
                    result = json.loads(raw_result[raw_result.index("{"):])
                    meaningful = not result.get("error") and result.get("status") not in {"blocked", "denied", "unavailable", "unknown_ref"}
                except (ValueError, TypeError, AttributeError):
                    meaningful = False
                if meaningful:
                    # v2: a successful write records actual execution only;
                    # substantive progress comes solely from finish receipts.
                    activity.charge(store, run["id"])
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

    def _deliver_invitations_locked(self):
        """Deliver at most one prepared short invitation per cycle.

        Legacy full-text queued contacts are deliberately no longer delivered:
        the old bypass is removed; rows stay as historical records pending the
        explicit migration to re-prepareable intents.
        """
        store = self.service.store
        from .autonomy_v2 import build
        from .autonomy_v2.models import Scope
        scope = Scope(session_epoch=self.epoch, channel_id="gui")
        stack = build(store=store)
        stack.invitations.expire(scope)
        if activity.active_idle(store):
            return  # Rest pauses prepared but undelivered autonomous invitations.
        row = store.conn.execute("SELECT id FROM v2_invitation_attempts WHERE epoch=? AND state='prepared' "
                                 "ORDER BY created_at LIMIT 1", (self.epoch,)).fetchone()
        if row is None:
            return
        context = thoughts.execution_context(store, {"request_id": "invitation_delivery", "scope": "gui"})
        attempt = stack.invitations.repository.get_attempt(scope, row[0])
        stack.invitations.deliver(context, attempt)
