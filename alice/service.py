"""会话服务层：GUI 与未来渠道共用的会话入口（单写者）。"""
from __future__ import annotations

import shutil
import threading
from datetime import datetime
from pathlib import Path

from . import config as appconfig
from . import consolidation, loop, metrics, persona, skills, thought
from .config import Config, load
from .llm import make_client
from .store import Store

ARCHIVE_ROOT = appconfig.DATA_DIR / "archive"  # 初始化时把旧会话整体搬到这里


def _iso(ts) -> str | None:
    """库里的 float 时间戳 → 本地可读时间（GUI 展示用）。"""
    try:
        return datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return None


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

    def seed(self, tags: list[str], reset: bool = False) -> dict:
        """reset=True 表示「初始化」：先归档旧会话（对话/记忆/人格），再装新人格。"""
        with self._lock:
            if reset:
                # 先让模型把底色写出来，成功了才清旧会话：失败时她的对话和记忆都还在
                md = persona.generate_seed_md(self.client, self.cfg, tags)
                self._archive_locked()
                persona.install_persona(
                    self.store, self.cfg, md, source=f"标签：{'、'.join(tags)}")
            else:
                persona.seed_from_tags(self.store, self.client, self.cfg, tags)
        return self.state()

    def install_persona(self, md_text: str, source: str = "用户导入", reset: bool = False) -> dict:
        with self._lock:
            if reset:
                self._archive_locked()
            persona.install_persona(self.store, self.cfg, md_text, source=source)
        return self.state()

    def reset_session(self) -> dict:
        """丢掉当前会话，回到空白的「第一次见面」。旧数据归档在 data/archive/<时间戳>/。"""
        with self._lock:
            self._archive_locked()
            return self.state()

    def _archive_locked(self) -> Path | None:
        """把这场会话（对话轮次/记忆/人格）整体搬进归档目录，然后换上一张空库。"""
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
            return None
        (dest / "note.txt").write_text(
            f"初始化归档：{datetime.now():%Y-%m-%d %H:%M}\n"
            f"丢弃了 {turns} 轮对话、"
            f"{mem['active'] + mem['warehouse'] + mem['longterm']} 条记忆、{mem['skills']} 个技能。\n",
            encoding="utf-8",
        )
        self.store = Store(appconfig.DB_PATH)  # 新库：meta/记忆/对话全空，人格待重新安装
        return dest

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
        for watcher in (persona.evolve_if_due, thought.deliberate_if_due, skills.distill_if_due):
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
