"""会话服务层：GUI 与未来渠道共用的会话入口（单写者）。"""
from __future__ import annotations

import copy
import os
import re
import shutil
import threading
import time
import uuid
from contextlib import nullcontext
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from . import config as appconfig
from . import consolidation, loop, observation, persona, skills, thought
from .config import Config, load
from .channels import ChannelCoordinator, ChannelError
from .config_edit import ConfigConflict, revision, replace_llm_values, replace_channels
from .llm import make_client, safe_provider_error
from .evaluation_service import EvaluationCoordinator
from .discovery import DiscoveryCoordinator
from .collaborators import WorkerCoordinator
from .store import Store

ARCHIVE_ROOT = appconfig.DATA_DIR / "archive"  # 初始化时把旧会话整体搬到这里


def _iso(ts) -> str | None:
    """库里的 float 时间戳 → 本地可读时间（GUI 展示用）。"""
    try:
        return datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return None


def _model_provider(base_url: str) -> str:
    url = base_url.rstrip("/").lower()
    if url == "https://open.bigmodel.cn/api/paas/v4":
        return "zhipu"
    if url == "https://api.deepseek.com":
        return "deepseek"
    return "custom"


def _close_client(client) -> None:
    close = getattr(client, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def _model_config_data(llm, file_revision: str) -> dict:
    if llm.api_key:
        key_source = "file"
    elif os.environ.get(llm.api_key_env):
        key_source = "environment"
    else:
        key_source = "missing"
    return {
        "provider": _model_provider(llm.base_url),
        "base_url": llm.base_url,
        "chat_model": llm.chat_model,
        "cheap_model": llm.cheap_model,
        "key_present": key_source != "missing",
        "key_source": key_source,
        "revision": file_revision,
    }


class ChatService:
    """拥有 Store 与 client 的生命周期；所有写操作经 _lock 串行（单一永久会话语义）。"""

    def __init__(self, cfg: Config | None = None, db_path=None, client=None, *, config_path=None):
        self._config_path = Path(config_path).resolve() if config_path is not None else appconfig.USER_DIR / "config.toml"
        # 三参数注入模式不碰本机配置；只有 GUI/自主加载模式绑定写回路径（user/config.toml）。
        self._config_revision = revision(self._config_path) if config_path is not None or cfg is None else None
        self.cfg = cfg if cfg is not None else load(self._config_path)
        self.store = Store(db_path or appconfig.DB_PATH)
        self._client_owned = client is None
        try:
            self.client = client if client is not None else make_client(self.cfg)
            self._lock = threading.RLock()  # 单一永久会话语义：所有读写串行
            self._observer_scope = uuid.uuid4().hex  # Ephemeral identity; never written to Alice's data.
            saved_thinking = self.store.meta_get("thinking")
            if saved_thinking in ("on", "off"):  # GUI 里改过的思考深度优先于 config 文件
                self.cfg.llm.thinking = saved_thinking
                self.cfg.llm.disable_thinking = False
            elif self.cfg.llm.disable_thinking:
                self.cfg.llm.thinking = "off"  # 旧配置开关在 GUI 中显示真实生效状态
            self._evaluations = EvaluationCoordinator(self)
            self._channels = ChannelCoordinator(self)
            self._channels.set_ready(persona.persona_md() is not None)
            self._workers = WorkerCoordinator(self)
            self._discovery = DiscoveryCoordinator(self)
            self._discovery.start()
        except BaseException:
            try:
                self.store.close()
            except Exception:
                pass
            if self._client_owned and hasattr(self, "client"):
                _close_client(self.client)
            raise

    # ---------- 状态查询 ----------

    def _memory_stats(self) -> dict:
        s = self.store
        tomb = s.conn.execute("SELECT COUNT(*) AS c FROM memory_tombstones").fetchone()["c"]
        return {
            "active": s.count_in_layer("active"),
            "warehouse": s.count_in_layer("warehouse"),
            "longterm": s.count_in_layer("longterm"),
            "pressure": s.count_pressure(),
            "forgotten": tomb,
            "skills": s.count_skills(),
        }

    def _turn_count(self) -> int:
        """已完成的助手轮数：0 表示这场会话还没真正开始（GUI 据此决定是否显示初始化页）。"""
        row = self.store.conn.execute(
            "SELECT COUNT(*) AS c FROM turns WHERE role = 'assistant'").fetchone()
        return int(row["c"] or 0)

    def _persona_stats(self) -> dict:
        md = persona.persona_md()
        return {
            "installed": md is not None,
            "version": int(self.store.meta_get("persona_version", "0") or 0),
            "installed_at": _iso(self.store.meta_get("persona_seeded_at")),
            "chars": len(md or ""),
        }

    def state(self) -> dict:
        with self._lock:
            return {
                "name": self.cfg.persona.name,
                "needs_seed": persona.persona_md() is None,
                "tags": list(persona.TAGS),
                "continuity": loop._continuity(self.store),
                "memory": self._memory_stats(),
                "turns": self._turn_count(),
                "persona": self._persona_stats(),
            }

    def persona_view(self) -> dict:
        """当前人格全文 + 元信息 + 变更记录尾部（GUI 的人格面板用）。"""
        with self._lock:
            md = persona.persona_md() or ""
            changelog: list[str] = []
            path = appconfig.PERSONA_CHANGELOG
            if path.exists():
                lines = [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines()]
                changelog = [ln for ln in lines if ln][-6:]
            return {
                "md": md,
                "changelog": changelog,
                "turns": self._turn_count(),
                **self._persona_stats(),
            }

    def history(self, limit: int = 60) -> list[dict]:
        with self._lock:
            return [
                {"role": r["role"], "content": self._history_turn_display_locked(r["id"]), "ts": r["ts"],
                 **self._history_source_fields_locked(r["id"])}
                for r in self.store.recent_turns(limit)
            ]

    def _history_source_available_locked(self, turn_id, attachment) -> bool:
        if (not attachment["content"] or attachment["expires_at"] <= time.time()
                or attachment["scope"] not in {"gui", "qq-owner"}):
            return False
        ids = self.store.turn_source_ids(turn_id)
        if not self.store.source_packets_for(ids, scope="gui"):
            return False
        if attachment["scope"] == "qq-owner":
            channel = self.store.conn.execute("SELECT channel_id FROM channel_turns WHERE turn_id=?", (turn_id,)).fetchone()
            return bool(channel and channel[0] == "qq-owner" and self.store.source_packets_for(ids, scope="qq-owner"))
        return True

    def _history_turn_display_locked(self, turn_id):
        # The local owner's history may show their QQ answer only while every
        # dependency permits both audiences. This does not widen model/recall
        # scopes, nor allow GUI-only material to flow in the other direction.
        attachment = self.store.conn.execute(
            "SELECT scope,content,expires_at FROM source_turns WHERE turn_id=?", (turn_id,)).fetchone()
        if (attachment is not None and attachment["scope"] == "qq-owner"
                and self._history_source_available_locked(turn_id, attachment)):
            return self.store.turn_display(turn_id, scope="qq-owner")
        return self.store.turn_display(turn_id, scope="gui")

    def _history_source_fields_locked(self, turn_id) -> dict:
        discussion = self.store.discussion_status(turn_id, scope="gui")
        report = self.store.conn.execute(
            "SELECT task_id,attempt_id,outcome FROM worker_reports "
            "WHERE turn_id=? AND epoch=? AND report_state='delivered'",
            (turn_id, self._channels.epoch)).fetchone()
        report_fields = {"worker_report": {"task_id": report["task_id"], "attempt_id": report["attempt_id"],
                                            "state": report["outcome"]}} if report else {}
        worker = self.store.worker_turn_status(turn_id, scope="gui")
        if worker is not None:
            # The existing GUI history refresh tracks source_bound rows. Task
            # attachments share that invalidation mechanism, not web provenance.
            return {"source_bound": True, "source_available": worker["worker_available"],
                    "source_expires_at": worker.get("worker_expires_at"), "discussion": discussion,
                    **worker, **report_fields}
        attachment = self.store.conn.execute(
            "SELECT scope,content,expires_at FROM source_turns WHERE turn_id=?", (turn_id,)).fetchone()
        if attachment is None:
            return {"source_bound": False, "source_available": True, "source_expires_at": None,
                    "discussion": discussion, **report_fields}
        available = self._history_source_available_locked(turn_id, attachment)
        return {"source_bound": True, "source_available": bool(available),
                "source_expires_at": attachment["expires_at"], "discussion": discussion, **report_fields}

    def history_page(self, after_id=None, limit=60, session_epoch=None, source_turn_ids=None) -> dict:
        if (type(limit) is not int or not 1 <= limit <= 100 or
                (after_id is not None and (type(after_id) is not int or after_id < 0))):
            raise ChannelError(400, "invalid_request", "历史游标或数量无效")
        if source_turn_ids is None:
            source_turn_ids = []
        if (not isinstance(source_turn_ids, list) or len(source_turn_ids) > 100
                or any(type(identity) is not int or not 1 <= identity <= 9007199254740991 for identity in source_turn_ids)):
            raise ChannelError(400, "invalid_request", "来源轮次列表无效")
        source_turn_ids = list(dict.fromkeys(source_turn_ids))
        if not self._lock.acquire(blocking=False):
            raise ChannelError(503, "status_unavailable", "Alice 忙碌，稍后刷新历史")
        try:
            if session_epoch is not None:
                self._channels.check_epoch(session_epoch)
            query = ("SELECT t.*,ct.request_id,ct.channel_id FROM turns t "
                     "LEFT JOIN channel_turns ct ON ct.turn_id=t.id ")
            if after_id is None:
                rows = list(reversed(self.store.conn.execute(query + "ORDER BY t.id DESC LIMIT ?", (limit,)).fetchall()))
                more = False
            else:
                rows = self.store.conn.execute(query + "WHERE t.id>? ORDER BY t.id LIMIT ?", (after_id, limit + 1)).fetchall()
                more, rows = len(rows) > limit, rows[:limit]
            turns = []
            for row in rows:
                delivery = None
                if row["request_id"] and row["role"] == "assistant" and row["channel_id"] == "qq-owner":
                    states = [r[0] for r in self.store.conn.execute("SELECT status FROM channel_deliveries WHERE request_id=?",
                                                                  (row["request_id"],))]
                    delivery = next((s for s in ("send_unknown", "failed", "sending", "ready") if s in states),
                                    "platform_confirmed" if states else "pending")
                source_fields = self._history_source_fields_locked(row["id"])
                report = source_fields.get("worker_report")
                turns.append({"id": row["id"], "role": row["role"], "content": self._history_turn_display_locked(row["id"]), "ts": row["ts"],
                              "request_id": None if report else row["request_id"],
                              "channel": "gui" if report else row["channel_id"] or "local",
                              "sender_label": "宿主协作通知" if report else "所有者", "delivery": None if report else delivery,
                              **source_fields})
            source_updates = []
            if source_turn_ids:
                from .store import SOURCE_SHADOW

                placeholders = ",".join("?" for _ in source_turn_ids)
                present = {row["id"] for row in self.store.conn.execute(
                    f"SELECT id FROM turns WHERE id IN ({placeholders})", source_turn_ids)}
                for identity in source_turn_ids:
                    if identity not in present:
                        source_updates.append({"id": identity, "content": SOURCE_SHADOW,
                                               "source_bound": True, "source_available": False,
                                               "source_expires_at": None})
                        continue
                    source_updates.append({"id": identity, "content": self._history_turn_display_locked(identity),
                                           **self._history_source_fields_locked(identity)})
            return {"turns": turns, "session_epoch": self._channels.epoch,
                    "next_after_id": turns[-1]["id"] if turns else (after_id or 0), "has_more": more,
                    "memory": self._memory_stats(), "source_updates": source_updates}
        finally:
            self._lock.release()

    def chat_activity(self) -> dict:
        return self._channels.activity()

    def authenticate_channel(self, token):
        return self._channels.authenticate(token)

    def channel_status(self, channel_id):
        self._channels.authorize(channel_id)
        return self._channels.activity()

    def submit_channel_request(self, channel_id, payload):
        return self._channels.submit(channel_id, payload)

    def channel_request_status(self, channel_id, request_id):
        return self._channels.status(channel_id, request_id)

    def record_channel_delivery(self, channel_id, payload):
        return self._channels.delivery(channel_id, payload)

    def _observer_read(self, reader, *, scope=None, **kwargs) -> dict:
        """Read only between writers; busy never queues or accesses the shared Store."""
        if not self._lock.acquire(blocking=False):
            return {"status": "busy", "scope": self._observer_scope}
        try:
            if scope is not None and scope != self._observer_scope:
                return {"status": "reset", "scope": self._observer_scope}
            # LIMIT bounds results, not full-text scan work. Abort expensive SQL so an
            # observer search cannot hold up a subsequent chat indefinitely.
            deadline = time.monotonic() + 1.0
            self.store.conn.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
            try:
                result = reader(self.store, **kwargs)
            finally:
                self.store.conn.set_progress_handler(None, 0)
            return {"status": "ok", "scope": self._observer_scope, **result}
        except observation.ObservationError as exc:
            return {"status": "error", "scope": self._observer_scope, "error": str(exc)}
        except Exception:
            return {"status": "error", "scope": self._observer_scope, "error": "观察读取未完成，请稍后刷新或缩小检索范围"}
        finally:
            self._lock.release()

    def observer_snapshot(self, *, scope=None) -> dict:
        return self._observer_read(observation.snapshot, scope=scope, cfg=self.cfg)

    def observer_list(self, *, scope=None, **kwargs) -> dict:
        return self._observer_read(observation.list_items, scope=scope, **kwargs)

    def observer_detail(self, *, scope=None, **kwargs) -> dict:
        return self._observer_read(observation.detail, scope=scope, **kwargs)

    def evaluations(self, action: str, *, dataset_id=None, **kwargs) -> dict:
        """P1 public boundary. Opening observer never calls this write-capable surface."""
        return self._evaluations.access(action, dataset_id=dataset_id, **kwargs)

    def discovery(self, action: str = "status", payload=None) -> dict:
        """Local GUI discovery controls; channel credentials do not grant this API."""
        return self._discovery.access(action, payload)

    def collaborators(self, payload=None) -> dict:
        """Local GUI registration/policy; never grants channel credentials access."""
        return self._workers.collaborators(payload)

    def worker_tasks(self, action: str = "list", payload=None) -> dict:
        """Persistent, GUI-scoped worker records; reads never execute an agent."""
        return self._workers.tasks(action, payload)

    def worker_task_settings(self, payload) -> dict:
        """GUI-only archive/delete preferences, separate from execution grants."""
        return self._workers.management_settings(payload)

    def worker_inputs(self, payload=None) -> dict:
        """No user file may be imported until the DSH permission profile exists."""
        return self._workers.unavailable()

    def worker_artifacts(self, payload=None):
        """No arbitrary file paths or uncollected agent outputs are downloadable."""
        return self._workers.unavailable()

    def chat_search_control(self, payload, *, cancel=False) -> dict:
        """Cancellation is an event, never a wait for the conversation Store lock."""
        return self._discovery.chat_control(payload, cancel=cancel)

    def observation_memory(self, payload) -> dict:
        with self._lock:
            if (not isinstance(payload, dict) or set(payload) != {"dataset_id", "memory_id"}
                    or payload["dataset_id"] != self._channels.epoch):
                raise ChannelError(409, "session_changed", "请刷新后查看记忆")
            if type(payload["memory_id"]) is not int:
                raise ChannelError(400, "invalid_request", "记忆标识无效")
            detail = self.store.observation_detail(payload["memory_id"], scope="gui")
            if detail is None:
                raise ChannelError(404, "not_found", "这条记忆已遗忘、撤销或不在当前范围")
            return detail

    def begin_shutdown(self) -> None:
        self._channels.begin_shutdown()
        self._evaluations.begin_shutdown()
        self._discovery.begin_shutdown()
        self._workers.begin_shutdown()

    # ---------- 写操作（全部持锁） ----------

    def prepare(self) -> list[str]:
        """启动时的到期维护，对应 CLI 启动段；返回应展示给用户的事件。"""
        events: list[str] = []
        with self._channels.control():
            with self._lock:
                summary = consolidation.run_if_due(self.store, self.cfg)
                if summary and any(
                    summary.get(k) for k in ("to_warehouse", "promoted", "expired", "evicted")
                ):
                    events.append(
                        f"记忆维护：{summary['to_warehouse']} 条进入仓库，"
                        f"{len(summary['promoted'])} 条晋升长期，"
                        f"{summary['expired']} 条自然遗忘，{summary['evicted']} 条容量淘汰"
                    )
            events.extend(self._watchers_due())
        return events

    def seed(self, tags: list[str], reset: bool = False) -> dict:
        """reset=True 表示「初始化」：先归档旧会话（对话/记忆/人格），再装新人格。"""
        with self._channels.control(), self._workers.control_lock:
            if reset:
                self._workers.drain()
            with self._lock:
                if reset:
                    # 先让模型把底色写出来，成功了才清旧会话：失败时她的对话和记忆都还在
                    md = persona.generate_seed_md(self.client, self.cfg, tags)
                    self._archive_locked()
                    persona.install_persona(
                        self.store, self.cfg, md, source=f"标签：{'、'.join(tags)}")
                else:
                    persona.seed_from_tags(self.store, self.client, self.cfg, tags)
                self._channels.set_ready(persona.persona_md() is not None)
        return self.state()

    def install_persona(self, md_text: str, source: str = "用户导入", reset: bool = False) -> dict:
        with self._channels.control(), self._workers.control_lock:
            if reset:
                self._workers.drain()
            with self._lock:
                if reset:
                    self._archive_locked()
                persona.install_persona(self.store, self.cfg, md_text, source=source,
                                        client=self.client)  # v0.3：传 client 走消化流程
                self._channels.set_ready(persona.persona_md() is not None)
        return self.state()

    def reset_session(self) -> dict:
        """丢掉当前会话，回到空白的「第一次见面」。旧数据归档在 data/archive/<时间戳>/。"""
        with self._channels.control(), self._workers.control_lock:
            self._workers.drain()
            with self._lock:
                self._archive_locked()
                return self.state()

    def _archive_locked(self) -> Path | None:
        """把这场会话（对话轮次/记忆/人格）整体搬进归档目录，然后换上一张空库。"""
        self._observer_scope = uuid.uuid4().hex  # Invalidate even if archival fails halfway.
        self._channels.set_ready(False)
        self._evaluations.before_reset()
        self._discovery.before_reset()
        self._workers.before_reset()
        turns = self._turn_count()
        mem = self._memory_stats()
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        dest = ARCHIVE_ROOT / stamp
        n = 2
        while dest.exists():  # 同一秒内多次初始化也不能盖掉上一份归档
            dest = ARCHIVE_ROOT / f"{stamp}-{n}"
            n += 1
        self.store.close()
        dest.mkdir(parents=True)
        moved = False
        for name in ("alice.db", "alice.db-wal", "alice.db-shm"):
            src = appconfig.DATA_DIR / name
            if src.exists():
                shutil.move(str(src), str(dest / name))
                moved = True
        if appconfig.PERSONA_DIR.exists():
            shutil.move(str(appconfig.PERSONA_DIR), str(dest / "persona"))
            moved = True
        if not moved:
            dest.rmdir()
            self.store = Store(appconfig.DB_PATH)
            self._evaluations.bind_store()
            self._channels.bind_store()
            self._discovery.bind_store()
            self._workers.bind_store()
            return None
        (dest / "note.txt").write_text(
            f"初始化归档：{datetime.now():%Y-%m-%d %H:%M}\n"
            f"丢弃了 {turns} 轮对话、"
            f"{mem['active'] + mem['warehouse'] + mem['longterm']} 条记忆、{mem['skills']} 个技能。\n",
            encoding="utf-8",
        )
        self.store = Store(appconfig.DB_PATH)  # 新库：meta/记忆/对话全空，人格待重新安装
        self._evaluations.bind_store()
        self._channels.bind_store()
        self._discovery.bind_store()
        self._workers.bind_store()
        return dest

    def send(self, text: str, on_delta=None, on_tool=None, on_reasoning=None, *, evaluation_id=None,
             request_id=None, session_epoch=None, nonblocking=False, on_accepted=None,
             search_intent=None, on_search_progress=None) -> dict:
        """One admitted GUI turn. Existing callers retain blocking admission semantics."""
        request = self._channels.gui_request(text, request_id, session_epoch, search_intent=search_intent)
        if nonblocking:
            prior = self._channels.lookup(request)
            if prior is not None:
                return self._replayed_result(prior)
        token = self._channels.reserve("gui", request["request_id"], wait=not nonblocking)
        try:
            with self._lock:
                self._channels.check_epoch(request["session_epoch"])
                prior = self._channels.lookup(request)
                if prior is not None:
                    return self._replayed_result(prior)
                accepted = self._channels.accept_locked(request)
                if on_accepted is not None:
                    try:
                        on_accepted(accepted)
                    except Exception:
                        pass  # Disconnects must not turn acceptance into another generation.
            try:
                return self._send_locked(request, on_delta, on_tool, on_reasoning,
                    evaluation_id=evaluation_id, owner_token=token, on_search_progress=on_search_progress)
            except BaseException:
                with self._lock:
                    try:
                        self._channels.finish_locked(request["request_id"], error_code="processing_failed")
                    except Exception:
                        self._channels.begin_shutdown()
                raise
        finally:
            self._channels.release(token)

    def _replayed_result(self, prior):
        if prior["reply"] is None:
            raise ChannelError(409, "request_" + prior["status"], "该请求已有记录；请查看历史，不要自动重发")
        return {**prior, "events": [], "memory": {}, "deduplicated": True}

    def _send_locked(self, request, on_delta=None, on_tool=None, on_reasoning=None, *, evaluation_id=None,
                     owner_token=None, on_search_progress=None) -> dict:
        # Historical private name retained for the channel adapter. The driver
        # now owns each short Store step; callers must not hold the outer lock.
        from .steps import drive
        try:
            return drive(self._send_steps(request, on_delta, on_tool, on_reasoning,
                         evaluation_id=evaluation_id, owner_token=owner_token,
                         on_search_progress=on_search_progress), self._lock)
        finally:
            # Both GUI and QQ workers own searches; release their in-memory
            # results even when generation or maintenance fails.
            self._discovery.finish_chat(request["request_id"])

    def _send_steps(self, request, on_delta, on_tool, on_reasoning, *, evaluation_id,
                    owner_token, on_search_progress):
        text, request_id = request["text"], request["request_id"]
        job = None
        evaluation_warning = None
        if evaluation_id is not None:
            try:
                job = self._evaluations.capture(evaluation_id)
            except Exception:
                evaluation_warning = "P1核查未能绑定本轮（许可过期、配置变化或捕获未完成）；真实聊天继续，请查看核查记录"
        persisted, maintenance_warnings = [], []
        source_warning = None
        try:
            source_ids, source_explicit = self._discovery.select_for_chat_locked(request)
        except Exception:
            self._discovery.chat_accounting_failed()
            source_ids, source_explicit = [], False
            source_warning = "资料读取暂失败，已暂停资料共享；本轮聊天继续"
        source_usage = {}
        search_session = self._discovery.prepare_chat_locked(request, owner_token, on_search_progress)
        _, settings = self._discovery._config_locked()
        try:
            reply = yield from loop.chat_turn_steps(
                self.store, self.client, self.cfg, text,
                on_delta=on_delta, on_tool=on_tool, on_reasoning=on_reasoning,
                on_persist=persisted.append if job else None, request_id=request_id,
                on_request_progress=lambda: self._channels.publish_locked(request_id),
                on_maintenance_warning=maintenance_warnings.append,
                source_packet_ids=source_ids, source_scope=request["channel_id"],
                source_allow_external_recall=source_explicit, source_usage_out=source_usage,
                search_session=search_session, tool_round_limit=settings.get("tool_round_limit", 4),
                worker_session=self._workers.prepare_chat_locked(request),
            )
            try:
                self._discovery.record_chat_locked(request, source_usage,
                    explicit=source_explicit or bool(search_session and search_session.calls))
            except Exception:
                self._discovery.chat_accounting_failed()
                source_warning = "资料共享记录暂失败；原回复已保存，已暂停后续资料共享"
            events = yield from self._watchers_steps()
            if source_warning:
                events.append(source_warning)
            if maintenance_warnings:
                events.append("记忆提取暂失败；原回复已保存，下次维护可重试")
        except BaseException:
            if job:
                self._evaluations.abort_chat(job)
            self._channels.finish_locked(request_id, error_code="generation_failed")
            raise
        if job:
            try:
                record = self._evaluations.bind_turn(job, persisted[0])
            except Exception:
                self._evaluations.abort_chat(job)
                record = {"status": "failed"}
            queue = record["status"] == "queued"
        if evaluation_warning:
            events.append(evaluation_warning)
        result = {"reply": reply, "events": events, "memory": self._memory_stats()}
        self._channels.finish_locked(request_id, warning=bool(maintenance_warnings) or any("暂失败" in event for event in events))
        result.update(self._channels.publish_locked(request_id))
        if job:
            result["evaluation_id"] = job["id"]
        if job and queue:
            try:
                yield lambda: self._evaluations.enqueue(job)
            except Exception:
                self._evaluations.abort_chat(job)
                result["events"].append("P1核查未能排入执行，真实回复已保存；请查看核查记录")
        return result

    # ---------- 设置 ----------

    def configure_local_channel(self, binding: dict, session_epoch: str, *, enabled: bool, guard=None) -> dict:
        """Trusted local connector host only; never accept browser-supplied bindings.

        The caller has already required an explicit local confirmation. Persist
        non-secret identity/hash fields only, while the shared admission slot
        prevents an accepted turn from straddling a configuration publication.
        """
        if (not isinstance(binding, dict) or set(binding) != {"app_id", "sender_id", "token_sha256"}
                or type(enabled) is not bool
                or not isinstance(binding["token_sha256"], str)
                or not re.fullmatch(r"[0-9a-f]{64}", binding["token_sha256"])
                or any(not isinstance(binding[k], str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", binding[k])
                       for k in ("app_id", "sender_id"))):
            raise ValueError("本机连接器返回的身份无效")
        with self._channels.control(), self._lock:
            self._channels.check_epoch(session_epoch)
            # The host's operation lock spans disk commit and publication, so a
            # cancel is ordered wholly before or after this transaction. Acquire
            # it only after the service reservation/lock; host actions never wait
            # for the service while holding that operation lock.
            with guard() if guard is not None else nullcontext():
                self._check_config_revision_locked(None, required=False)
                old = self.cfg.channels.qq_owner
                if old.app_id and (old.app_id != binding["app_id"] or old.sender_id != binding["sender_id"]
                                   or old.token_sha256 != binding["token_sha256"]):
                    raise ConfigConflict("已保存另一份 QQ 绑定；请先停用原连接并按说明人工处理，不能直接覆盖")
                values = {"enabled": enabled, "protocol_version": 1, "qq_owner": {
                    "enabled": enabled, "provider": "qq-official", "chat_type": "c2c", **binding}}
                candidate = appconfig._channels(values)
                new_revision = replace_channels(self._config_path, values, self._config_revision)
                self.cfg.channels = candidate
                self._config_revision = new_revision
                self._discovery.model_config_changed()
                return {"enabled": enabled, "app_id": binding["app_id"], "owner_openid": binding["sender_id"]}

    def _check_config_revision_locked(self, supplied: str | None, *, required: bool) -> None:
        if self._config_revision is None:
            raise ValueError("此服务未绑定配置文件，无法从界面修改模型配置")
        if required and not isinstance(supplied, str):
            raise ValueError("请刷新设置后再保存配置")
        if supplied is not None and supplied != self._config_revision:
            raise ConfigConflict("配置已在别处更新，请刷新设置后重试")
        if revision(self._config_path) != self._config_revision:
            raise ConfigConflict("配置文件已被外部修改，请重启服务或手工处理后再保存")

    def _model_config_view_locked(self) -> dict:
        self._check_config_revision_locked(None, required=False)
        return _model_config_data(self.cfg.llm, self._config_revision)

    def model_config_view(self) -> dict:
        """GUI 配置视图；不回传密钥本体或可还原的掩码。"""
        with self._lock:
            return self._model_config_view_locked()

    def _candidate_config_locked(self, payload: dict, *, require_revision: bool) -> tuple[Config, dict[str, str]]:
        if not isinstance(payload, dict):
            raise ValueError("配置内容无效")
        self._check_config_revision_locked(payload.get("revision"), required=require_revision)
        provider = payload.get("provider", "custom")
        if provider not in ("zhipu", "deepseek", "custom"):
            raise ValueError("供应商选项无效")
        changes: dict[str, str] = {}
        for key, label, limit in (
            ("base_url", "接口地址", 2048),
            ("chat_model", "对话模型", 256),
            ("cheap_model", "廉价模型", 256),
        ):
            value = payload.get(key)
            if not isinstance(value, str) or not value.strip() or len(value) > limit:
                raise ValueError(f"请填写有效的{label}")
            value = value.strip()
            if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
                raise ValueError(f"{label}包含无效字符")
            changes[key] = value
        try:
            url = urlsplit(changes["base_url"])
            local_http = url.scheme == "http" and url.hostname in ("127.0.0.1", "localhost", "::1")
            valid_url = (
                (url.scheme == "https" or local_http) and bool(url.hostname)
                and not url.username and not url.password and not url.query and not url.fragment
                and "\\" not in url.netloc and url.port != 0
                and not any(ch.isspace() for ch in changes["base_url"])
            )
        except ValueError:
            valid_url = False
        if not valid_url:
            raise ValueError("接口地址须为 HTTPS，或本机回环 HTTP，且不能包含账号、查询参数或片段")
        for key in ("chat_model", "cheap_model"):
            if any(ch.isspace() for ch in changes[key]):
                raise ValueError("模型名称不能包含空白字符")

        replace_key = payload.get("replace_api_key", False)
        if type(replace_key) is not bool:
            raise ValueError("密钥替换选项无效")
        new_key = payload.get("api_key", "")
        if not isinstance(new_key, str):
            raise ValueError("API Key 格式无效")
        if replace_key:
            new_key = new_key.strip()
            if not new_key or len(new_key) > 4096 or any(ord(ch) < 32 or ord(ch) == 127 for ch in new_key):
                raise ValueError("请填写有效的新 API Key")
            changes["api_key"] = new_key
        elif new_key:
            raise ValueError("请先选择“替换密钥”，再填写新 API Key")

        candidate = copy.deepcopy(self.cfg)
        candidate.llm.base_url = changes["base_url"]
        candidate.llm.chat_model = changes["chat_model"]
        candidate.llm.cheap_model = changes["cheap_model"]
        if replace_key:
            candidate.llm.api_key = changes["api_key"]
        if not candidate.llm.resolved_key():
            raise ValueError("尚未设置 API Key，请选择“替换密钥”并填写新密钥")
        return candidate, changes

    @staticmethod
    def _probe_model_config(candidate: Config) -> tuple[object, list[str]]:
        """只用候选 client 做最小请求；不读取或写入 Alice 会话。"""
        try:
            client = make_client(candidate)
        except SystemExit:
            raise ValueError("模型客户端不可用，请检查 openai 依赖") from None
        except Exception as exc:
            raise ValueError(f"模型客户端创建失败：{safe_provider_error(exc)}") from None
        tested: list[str] = []
        try:
            for label, model in (("对话模型", candidate.llm.chat_model), ("廉价模型", candidate.llm.cheap_model)):
                if model in tested:
                    continue
                try:
                    response = client.chat.completions.create(
                        model=model,
                        messages=[{"role": "user", "content": "ping"}],
                        max_tokens=8,
                        stream=False,
                        timeout=30.0,
                    )
                except Exception as exc:
                    raise ValueError(f"{label}连接失败：{safe_provider_error(exc)}") from None
                choices = getattr(response, "choices", None)
                message = getattr(choices[0], "message", None) if isinstance(choices, list) and choices else None
                if (getattr(message, "role", None) != "assistant"
                        or not hasattr(message, "content")
                        or (message.content is not None and not isinstance(message.content, str))):
                    # Some compatible SDKs return a 2xx HTML/text response without raising.
                    # A short reasoning response may have empty content, which is still valid.
                    raise ValueError(f"{label}未返回兼容的 Chat Completions 响应，请检查接口地址")
                tested.append(model)
        except Exception:
            _close_client(client)
            raise
        return client, tested

    def test_model_config(self, payload: dict) -> dict:
        """测试正在编辑的候选配置，绝不先切换生产 client。"""
        with self._lock:
            candidate, _ = self._candidate_config_locked(payload, require_revision=False)
        client, tested = self._probe_model_config(candidate)
        _close_client(client)
        return {
            "ok": True,
            "message": "候选配置的对话模型和廉价模型均已通过最小非流式请求",
            "tested_models": tested,
        }

    def save_model_config(self, payload: dict) -> dict:
        """探测成功后写盘，再在同一把锁内发布 client 与配置。"""
        with self._lock:
            candidate, changes = self._candidate_config_locked(payload, require_revision=True)
        new_client, _ = self._probe_model_config(candidate)
        published = False
        old_client = None
        old_owned = False
        try:
            with self._channels.control(), self._lock:
                self._check_config_revision_locked(payload.get("revision"), required=True)
                candidate.llm.thinking = self.cfg.llm.thinking
                candidate.llm.disable_thinking = self.cfg.llm.disable_thinking
                result = _model_config_data(candidate.llm, self._config_revision)
                new_revision = replace_llm_values(self._config_path, changes, self._config_revision)
                old_client, old_owned = self.client, self._client_owned
                self.cfg.llm = candidate.llm
                self.client = new_client
                self._client_owned = True
                self._config_revision = new_revision
                self._discovery.model_config_changed()
                published = True
                result["revision"] = new_revision
        finally:
            if not published:
                _close_client(new_client)
        if old_owned:
            _close_client(old_client)
        return result

    def set_thinking(self, value: str) -> str:
        if value not in ("on", "off"):
            raise ValueError("thinking 仅支持 on/off")
        with self._channels.control(), self._lock:
            self.cfg.llm.thinking = value
            self.cfg.llm.disable_thinking = False
            self.store.meta_set("thinking", value)
        return value

    def thinking_view(self) -> str:
        with self._lock:
            return self.cfg.llm.thinking

    def metrics_view(self) -> dict:
        from . import metrics

        with self._lock:
            return metrics.snapshot(self.store)

    def metrics_toggle(self, module: str, on: bool) -> bool:
        from . import metrics

        with self._lock:
            return metrics.set_enabled(self.store, module, on)

    def _watchers_due(self) -> list[str]:
        from .steps import drive
        return drive(self._watchers_steps(), self._lock)

    def _watchers_steps(self):
        events: list[str] = []
        for watcher in (persona.evolve_if_due_steps, thought.deliberate_if_due_steps, skills.distill_if_due_steps):
            try:
                msg = yield from watcher(self.store, self.client, self.cfg)
            except Exception as e:  # Never return provider exception bodies or credentials.
                msg = f"{watcher.__name__} 暂失败：{type(e).__name__}"
            if msg:
                events.append(msg)
        return events

    def close(self, *, extract: bool = True) -> str | None:
        """通常先强制提取再关资源；启动失败可用 extract=False 直接释放。"""
        self.begin_shutdown()
        self._channels.drain()  # HTTP 202 workers outlive their submitting connections.
        self._evaluations.shutdown()  # Join outside the Store lock; worker commits need it.
        self._discovery.shutdown()
        self._workers.shutdown()
        with self._lock:
            warning = None
            try:
                if extract:
                    from . import extraction

                    n = extraction.extract_pending(self.store, self.client, self.cfg, force=True)
                    if n:
                        print(f"（退出前提取了 {n} 条新记忆）")
            except Exception as e:
                # Provider exceptions may contain credentials. The saved turns remain for retry.
                print(f"（退出前记忆提取失败：{type(e).__name__}）")
                warning = "服务已关闭，但退出前记忆提取失败；原始对话保存在本机，下次启动可重试"
            finally:
                try:
                    self.store.close()
                finally:
                    if self._client_owned:
                        _close_client(self.client)
            return warning
