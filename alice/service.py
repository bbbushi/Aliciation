"""会话服务层：GUI 与未来渠道共用的会话入口（单写者）。"""
from __future__ import annotations

import threading

from . import config as appconfig
from . import consolidation, loop, metrics, persona, skills
from .config import Config, load
from .llm import make_client
from .store import Store


class ChatService:
    """拥有 Store 与 client 的生命周期；所有写操作经 _lock 串行（单一永久会话语义）。"""

    def __init__(self, cfg: Config | None = None, db_path=None, client=None):
        self.cfg = cfg or load()
        self.store = Store(db_path or appconfig.DB_PATH)
        self.client = client if client is not None else make_client(self.cfg)
        self._lock = threading.RLock()  # 单一永久会话语义：所有读写串行
        saved_thinking = self.store.meta_get("thinking")
        if saved_thinking in ("on", "off"):  # GUI 里改过的思考深度优先于 config 文件
            self.cfg.llm.thinking = saved_thinking

    # ---------- 状态查询 ----------

    def _memory_stats(self) -> dict:
        s = self.store
        tomb = s.conn.execute("SELECT COUNT(*) AS c FROM memory_tombstones").fetchone()["c"]
        return {
            "active": s.count_in_layer("active"),
            "warehouse": s.count_in_layer("warehouse"),
            "longterm": s.count_in_layer("longterm"),
            "forgotten": tomb,
            "skills": s.count_skills(),
        }

    def state(self) -> dict:
        with self._lock:
            return {
                "name": self.cfg.persona.name,
                "needs_seed": persona.persona_md() is None,
                "tags": list(persona.TAGS),
                "continuity": loop._continuity(self.store),
                "memory": self._memory_stats(),
            }

    def history(self, limit: int = 60) -> list[dict]:
        with self._lock:
            return [
                {"role": r["role"], "content": r["content"], "ts": r["ts"]}
                for r in self.store.recent_turns(limit)
            ]

    # ---------- 写操作（全部持锁） ----------

    def prepare(self) -> list[str]:
        """启动时的到期维护，对应 CLI 启动段；返回应展示给用户的事件。"""
        events: list[str] = []
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

    def seed(self, tags: list[str]) -> dict:
        with self._lock:
            persona.seed_from_tags(self.store, self.client, self.cfg, tags)
        return self.state()

    def send(self, text: str, on_delta=None, on_tool=None, on_reasoning=None) -> dict:
        """处理一轮对话；on_delta/on_tool/on_reasoning 为流式回调（见 loop.chat_turn）。"""
        with self._lock:
            usage: dict = {}
            tools_used: list[str] = []

            def _tool(name: str) -> None:
                tools_used.append(name)
                if on_tool:
                    on_tool(name)

            reply = loop.chat_turn(
                self.store, self.client, self.cfg, text,
                on_delta=on_delta, on_tool=_tool, on_reasoning=on_reasoning,
                usage_out=usage,
            )
            events = self._watchers_due()
            self._collect_metrics(text, reply, tools_used, usage)
            return {
                "reply": reply,
                "events": events,
                "memory": self._memory_stats(),
                "metrics": metrics.snapshot(self.store),
            }

    def _collect_metrics(self, user: str, reply: str, tools_used: list[str], usage: dict) -> None:
        """按启停状态采集/评审指标；任何失败都不影响对话。"""
        try:
            on = metrics.enabled(self.store)
            if "tokens" in on and (usage.get("prompt") or usage.get("completion")):
                metrics.record(
                    self.store, "tokens",
                    usage.get("prompt", 0) + usage.get("completion", 0),
                    detail=f"入{usage.get('prompt', 0)}/出{usage.get('completion', 0)}",
                )
            judge_names = [m for m in ("drift", "honesty", "human") if m in on]
            if judge_names and reply:
                scores = metrics.judge_turn(self.store, self.client, self.cfg,
                                            user, reply, tools_used)
                if scores:
                    for name in judge_names:
                        if name in scores:
                            metrics.record(self.store, name, scores[name] * 10,
                                           detail=scores.get("note", ""))
        except Exception as e:
            print(f"（指标采集暂失败：{e}）")

    # ---------- 设置 ----------

    def set_thinking(self, value: str) -> str:
        if value not in ("on", "off"):
            raise ValueError("thinking 仅支持 on/off")
        with self._lock:
            self.cfg.llm.thinking = value
            self.store.meta_set("thinking", value)
        return value

    def metrics_view(self) -> dict:
        from . import metrics

        with self._lock:
            return metrics.snapshot(self.store)

    def metrics_toggle(self, module: str, on: bool) -> bool:
        from . import metrics

        with self._lock:
            return metrics.set_enabled(self.store, module, on)

    def _watchers_due(self) -> list[str]:
        events: list[str] = []
        for watcher in (persona.evolve_if_due, skills.distill_if_due):
            try:
                msg = watcher(self.store, self.client, self.cfg)
            except Exception as e:  # 后台任务失败不影响对话
                msg = f"{watcher.__name__} 暂失败：{e}"
            if msg:
                events.append(msg)
        return events

    def close(self) -> None:
        try:
            from . import extraction

            n = extraction.extract_pending(self.store, self.client, self.cfg, force=True)
            if n:
                print(f"（退出前提取了 {n} 条新记忆）")
        except Exception as e:
            print(f"（退出前记忆提取失败：{e}）")
        finally:
            self.store.close()
