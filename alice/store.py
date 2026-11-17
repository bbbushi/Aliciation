"""SQLite 存储层：对话轮次、三层记忆、技能、遗忘墓碑、元信息。"""
from __future__ import annotations

import hashlib
import threading
from collections import deque
import re
import sqlite3
import time
from pathlib import Path

from .discovery_memory import SCHEMA as SOURCE_SCHEMA, SOURCE_SHADOW, SourceStoreMixin
from .observation_memory import ObservationStoreMixin
from .learning import LearningStoreMixin
from .maintenance import SCHEMA as MAINTENANCE_SCHEMA, MaintenanceStoreMixin
from .worker_context import SCHEMA as WORKER_CONTEXT_SCHEMA, WORKER_SHADOW, WorkerStoreMixin
from .thought_workspace import SCHEMA as THOUGHT_SCHEMA
from .autonomy_v2.schema import SCHEMA as AUTONOMY_V2_SCHEMA

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS turns (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
  content TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_turns_ts ON turns (ts);
CREATE TABLE IF NOT EXISTS memories (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at REAL NOT NULL,
  layer TEXT NOT NULL DEFAULT 'active' CHECK (layer IN ('active', 'warehouse', 'longterm')),
  title TEXT NOT NULL,
  content TEXT NOT NULL,
  keywords TEXT NOT NULL DEFAULT '',
  salience INTEGER NOT NULL DEFAULT 0,
  mention_count INTEGER NOT NULL DEFAULT 0,
  use_count INTEGER NOT NULL DEFAULT 0,
  last_used_at REAL,
  promoted_at REAL,
  kind TEXT NOT NULL DEFAULT 'event' CHECK (kind IN ('event', 'stance')),
  pressure INTEGER NOT NULL DEFAULT 0 CHECK (pressure IN (0, 1)),
  source_url TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_memories_layer ON memories (layer);
CREATE TABLE IF NOT EXISTS memory_tombstones (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  memory_id INTEGER NOT NULL,
  title TEXT NOT NULL,
  expired_at REAL NOT NULL,
  reason TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS skills (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at REAL NOT NULL,
  title TEXT NOT NULL,
  content TEXT NOT NULL,
  source_titles TEXT NOT NULL DEFAULT '',
  use_count INTEGER NOT NULL DEFAULT 0,
  last_used_at REAL
);
CREATE TABLE IF NOT EXISTS turn_metrics (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  module TEXT NOT NULL,
  value REAL NOT NULL,
  detail TEXT NOT NULL DEFAULT '',
  ts REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_turn_metrics_module ON turn_metrics (module, ts);
CREATE TABLE IF NOT EXISTS context_recaps (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  start_turn_id INTEGER NOT NULL,
  end_turn_id INTEGER NOT NULL,
  recap_md TEXT NOT NULL,
  source_turn_ids TEXT NOT NULL DEFAULT '[]',
  boundary_turn_id INTEGER NOT NULL,
  chars INTEGER NOT NULL DEFAULT 0,
  created_at REAL NOT NULL,
  UNIQUE (start_turn_id, end_turn_id)
);
-- Legacy context_recaps is retained as audit evidence, never injected by v2.
CREATE TABLE IF NOT EXISTS context_recap_cards (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  start_turn_id INTEGER NOT NULL, end_turn_id INTEGER NOT NULL,
  recap_md TEXT NOT NULL, source_turn_ids TEXT NOT NULL,
  boundary_turn_id INTEGER NOT NULL, chars INTEGER NOT NULL,
  created_at REAL NOT NULL, generation_key TEXT NOT NULL,
  UNIQUE (start_turn_id, end_turn_id, generation_key)
);
CREATE TABLE IF NOT EXISTS memory_extraction_batches (
  id INTEGER PRIMARY KEY AUTOINCREMENT, created_at REAL NOT NULL,
  session_epoch TEXT NOT NULL, source_json TEXT NOT NULL,
  candidates_json TEXT NOT NULL, review_json TEXT NOT NULL,
  status TEXT NOT NULL, memory_ids TEXT NOT NULL
);
"""

_PUNCT = re.compile(r"[\s，。！？、,.!?~…—\-\"'「」《》()（）:：;；·]")


def bigrams(s: str) -> set[str]:
    s = _PUNCT.sub("", s.lower())
    if len(s) < 2:
        return {s} if s else set()
    return {s[i : i + 2] for i in range(len(s) - 1)}


def text_score(query: str, text: str) -> float:
    """朴素检索打分：字符二元组重合度 + 子串加成。对中文够用且零依赖。"""
    qb, tb = bigrams(query), bigrams(text)
    if not qb:
        return 0.0
    score = len(qb & tb) / len(qb)
    if query.strip() and query.strip() in text:
        score += 0.5
    return score


def memory_score(m: sqlite3.Row, now: float) -> float:
    """记忆权重 = 提及×2 + 使用 + 近期使用加成。容量清理与排序共用。"""
    from .memory_policy import usage_score
    return usage_score(m, now)


class Store(MaintenanceStoreMixin, LearningStoreMixin, WorkerStoreMixin, ObservationStoreMixin, SourceStoreMixin):
    def __init__(self, db_path: Path):
        self._llm_event_lock = threading.Lock()
        self._llm_events = deque(maxlen=2000)
        self._llm_events_dropped = 0
        db_path.parent.mkdir(parents=True, exist_ok=True)
        # GUI 服务是多线程处理请求的；跨线程访问由上层（service 的单写者锁）串行化保证
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        try:
            self._install(db_path)
        except BaseException:
            self.conn.close()
            raise

    def _install(self, db_path: Path) -> None:
        from . import context_observation

        self.conn.row_factory = sqlite3.Row
        if self.conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='memories'").fetchone():
            columns = {row[1] for row in self.conn.execute("PRAGMA table_info(memories)")}
            if "lifecycle" not in columns:
                backup = db_path.with_name(db_path.name + ".before-dialogue-" + str(time.time_ns()) + ".bak")
                with sqlite3.connect(backup) as destination:
                    self.conn.backup(destination)
        self.conn.executescript(SCHEMA)
        context_observation.install(self)  # Migration reads turn_metrics; connection/schema must exist.
        self._migrate()
        self.conn.executescript(SOURCE_SCHEMA)
        self.init_observation_memory()
        self.init_learning()
        self.conn.executescript(WORKER_CONTEXT_SCHEMA)
        self.conn.executescript(MAINTENANCE_SCHEMA)
        self.conn.executescript(THOUGHT_SCHEMA)
        self.conn.executescript(AUTONOMY_V2_SCHEMA)
        self.purge_source_packets()

    def _migrate(self) -> None:
        """老库升级：v0.2 思想层补 kind 列，P2 卫生补 pressure 列（压力窗口标记）。"""
        cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(memories)")}
        changed = False
        if "kind" not in cols:
            self.conn.execute(
                "ALTER TABLE memories ADD COLUMN kind TEXT NOT NULL DEFAULT 'event'"
            )
            changed = True
        if "pressure" not in cols:
            self.conn.execute(
                "ALTER TABLE memories ADD COLUMN pressure INTEGER NOT NULL DEFAULT 0"
            )
            changed = True
        for name, declaration in (
            ("origin", "TEXT NOT NULL DEFAULT 'conversation'"),
            ("scope", "TEXT NOT NULL DEFAULT '[]'"),
            ("expires_at", "REAL"),
            ("source_url", "TEXT NOT NULL DEFAULT ''"),
            ("lifecycle", "TEXT NOT NULL DEFAULT 'memory'"),
            ("provenance_status", "TEXT NOT NULL DEFAULT 'valid'"),
            ("value_tier", "TEXT NOT NULL DEFAULT 'unknown'"),
            ("value_note", "TEXT NOT NULL DEFAULT ''"),
            ("reflection_hint", "TEXT NOT NULL DEFAULT 'ordinary'"),
            ("value_policy", "INTEGER NOT NULL DEFAULT 0"),
        ):
            if name not in cols:
                self.conn.execute(f"ALTER TABLE memories ADD COLUMN {name} {declaration}")
                changed = True
        if changed:
            self.conn.commit()

    def close(self) -> None:
        from .metrics import flush_calls
        try:
            flush_calls(self)
        finally:
            self.conn.close()

    # ---------- meta ----------
    def meta_get(self, key: str, default: str | None = None) -> str | None:
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def meta_set(self, key: str, value) -> None:
        owned = not self.conn.in_transaction
        self.conn.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) "
            "ON CONFLICT (key) DO UPDATE SET value=excluded.value",
            (key, str(value)),
        )
        if owned:
            self.conn.commit()

    def meta_get_time(self, key: str) -> float:
        v = self.meta_get(key)
        return float(v) if v else 0.0

    # ---------- 对话轮次 ----------
    def turn_display(self, turn_id: int, *, scope="gui"):
        from .thought_workspace import valid_basis
        row = self.conn.execute("SELECT c.basis FROM proactive_turns p JOIN thought_contacts c ON c.id=p.contact_id WHERE p.turn_id=?", (turn_id,)).fetchone()
        if row is not None:
            import json
            if scope != "gui" or not valid_basis(self, json.loads(row[0]), scope=scope):
                return SOURCE_SHADOW
        return super().turn_display(turn_id, scope=scope)

    def model_history_turns(self, limit):
        rows = super().model_history_turns(limit)
        return [{**dict(row), "proactive": self.conn.execute("SELECT 1 FROM proactive_turns WHERE turn_id=?", (row["id"],)).fetchone() is not None}
                for row in rows if self.turn_display(row["id"], scope="gui") != SOURCE_SHADOW
                or self.turn_source_ids(row["id"])]

    def ordinary_turns(self, rows):
        return [{**dict(row), "proactive": self.conn.execute("SELECT 1 FROM proactive_turns WHERE turn_id=?", (row["id"],)).fetchone() is not None}
                for row in super().ordinary_turns(rows)
                if self.turn_display(row["id"], scope="gui") != SOURCE_SHADOW]

    def add_turn(self, role: str, content: str, ts: float | None = None, *, request_id=None,
                 source_packet_ids=(), source_scope="gui", worker_refs=()) -> int:
        # A short transaction binds the actual row ID to its request before any
        # network call or callback. No model call is inside this transaction.
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO turns (ts, role, content) VALUES (?, ?, ?)",
                (ts if ts is not None else time.time(), role,
                 WORKER_SHADOW if worker_refs else SOURCE_SHADOW if source_packet_ids else content),
            )
            turn_id = cur.lastrowid
            if source_packet_ids:
                self._source_turn_write(turn_id, content, source_packet_ids, source_scope)
            if worker_refs:
                self._worker_turn_write(turn_id, content, worker_refs, source_scope)
            if request_id is not None:
                column = "user_turn_id" if role == "user" else "assistant_turn_id"
                status = "user_committed" if role == "user" else "generated"
                updated = self.conn.execute(
                    f"UPDATE channel_requests SET {column}=?,status=?,updated_at=? "
                    f"WHERE request_id=? AND {column} IS NULL",
                    (turn_id, status, time.time(), request_id),
                )
                if updated.rowcount != 1:
                    raise ValueError("请求轮次不能重复入库")
                if role == "assistant":
                    # The request receipt binds the actual saved reply, before
                    # source isolation substitutes a shadow in ordinary turns.
                    self.conn.execute(
                        "UPDATE channel_requests SET reply_hash=? WHERE request_id=? AND reply_hash IS NULL",
                        (hashlib.sha256(content.encode("utf-8")).hexdigest(), request_id),
                    )
                self.conn.execute("INSERT INTO channel_turns(turn_id,request_id,channel_id) "
                                  "SELECT ?,request_id,channel_id FROM channel_requests WHERE request_id=?",
                                  (turn_id, request_id))
        return turn_id

    def recent_turns(self, n: int) -> list[sqlite3.Row]:
        rows = self.conn.execute(
            "SELECT * FROM turns ORDER BY id DESC LIMIT ?", (n,)
        ).fetchall()
        return list(reversed(rows))

    def turns_since(self, turn_id: int, limit: int = 80) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM turns WHERE id > ? ORDER BY id LIMIT ?", (turn_id, limit)
        ).fetchall()

    @staticmethod
    def count_user_turns(rows) -> int:
        return sum(1 for r in rows if r["role"] == "user")

    # ---------- 记忆 ----------
    def insert_memory(
        self,
        title: str,
        content: str,
        keywords: str = "",
        salience: int = 0,
        created_at: float | None = None,
        layer: str = "active",
        kind: str = "event",
        pressure: int = 0,
        source_url: str = "",
    ) -> int:
        with self.source_transaction():
            cur = self.conn.execute(
                "INSERT INTO memories (created_at, layer, title, content, keywords, salience, kind, pressure, source_url,provenance_status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (created_at if created_at is not None else time.time(), layer, title.strip(),
                 content.strip(), keywords, int(salience), kind, 1 if pressure else 0, source_url.strip(),
                 "legacy_unverified" if source_url.strip() else "valid"),
            )
        return cur.lastrowid

    def save_web_seen_memory(self, title: str, content: str, source_url: str,
                             keywords: str = "", salience: int = 0) -> str:
        """Legacy URL-only entry point cannot establish that a page was read."""
        return "仅有URL不能证明已读。请先读取真实材料，并通过本轮来源记录保存见闻。"

    def memories_in_layer(self, layer: str, kind: str | None = None, *,
                          consumer: str = "maintenance") -> list[sqlite3.Row]:
        # Maintenance must retain the original forgetting path for every origin.
        # Prompt callers explicitly request conversation-only material.
        clauses, args = ["layer=?"], [layer]
        if kind:
            clauses.append("kind=?")
            args.append(kind)
        if consumer == "conversation" or kind == "stance":
            clauses.append("origin='conversation'")
            clauses.append("provenance_status='valid'")
        elif consumer != "maintenance":
            raise ValueError("记忆消费者无效")
        rows = self.conn.execute(
            "SELECT * FROM memories WHERE " + " AND ".join(clauses)
            + " ORDER BY created_at DESC,id DESC", args,
        ).fetchall()
        return [r for r in rows if self._parents_available(r["id"], "gui")] if consumer == "conversation" or kind == "stance" else rows

    def count_in_layer(self, layer: str) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) AS c FROM memories WHERE layer=?", (layer,)
        ).fetchone()["c"]

    def context_memory_rows(self, *, scope: str) -> list[sqlite3.Row]:
        """C1 ordinary, independent memories only. Explicit audience, no GUI default.

        Source/parent-linked records keep their existing source-aware read path;
        no catalogue title is exposed before this qualification.
        """
        from .discovery_memory import SOURCE_SCOPES
        if scope not in SOURCE_SCOPES:
            return []
        return self.conn.execute(
            "SELECT m.* FROM memories m WHERE m.origin='conversation' "
            "AND m.provenance_status='valid' AND m.source_url='' "
            "AND (m.expires_at IS NULL OR m.expires_at>?) "
            "AND NOT EXISTS(SELECT 1 FROM memory_dependencies d WHERE d.memory_id=m.id) "
            "AND NOT EXISTS(SELECT 1 FROM memory_evidence_links e WHERE e.memory_id=m.id) "
            "AND NOT EXISTS(SELECT 1 FROM source_memory_dependencies s WHERE s.memory_id=m.id) "
            "ORDER BY m.created_at DESC,m.id DESC", (time.time(),)).fetchall()

    def count_pressure(self) -> int:
        """压力窗口记忆存量（活跃的）：给状态面板与"结构性沦陷"检查用。"""
        return self.conn.execute(
            "SELECT COUNT(*) AS c FROM memories WHERE pressure=1"
        ).fetchone()["c"]

    def context_history_rounds(self, *, scope: str, before_id: int, limit: int = 200,
                               user_id: int | None = None) -> list:
        """C3: same-epoch/channel complete ordinary request pairs, never shadows.

        Untagged legacy/CLI messages are deliberately outside this first adapter.
        Filter attachments and notifications before LIMIT, even after expiry.
        """
        if scope not in ("gui", "qq-owner"):
            return []
        if not self.conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='channel_requests'").fetchone():
            return []
        report_filter = ""
        if self.conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='worker_reports'").fetchone():
            report_filter = " AND NOT EXISTS(SELECT 1 FROM worker_reports w WHERE w.turn_id IN(u.id,a.id))"
        sql = ("SELECT q.request_id,u.id AS user_id,u.ts AS user_ts,u.content AS user_content,"
               "a.id AS assistant_id,a.ts AS assistant_ts,a.content AS assistant_content "
               "FROM channel_requests q JOIN turns u ON u.id=q.user_turn_id "
               "JOIN turns a ON a.id=q.assistant_turn_id "
               "WHERE q.session_epoch=? AND q.channel_id=? "
               "AND q.status IN('completed','completed_with_warning') "
               "AND u.role='user' AND a.role='assistant' AND a.id>u.id AND a.id<? "
               "AND NOT EXISTS(SELECT 1 FROM source_turns s WHERE s.turn_id IN(u.id,a.id)) "
               "AND NOT EXISTS(SELECT 1 FROM worker_turns w WHERE w.turn_id IN(u.id,a.id)) "
               + report_filter)
        args = [self.meta_get("session_epoch"), scope, before_id]
        if user_id is not None:
            sql += " AND u.id=?"
            args.append(user_id)
        sql += " ORDER BY u.id DESC LIMIT ?"
        args.append(min(max(limit, 1), 200))
        return self.conn.execute(sql, args).fetchall()

    def alive_memories(self, *, include_external: bool = False) -> list[sqlite3.Row]:
        # Thought/persona/skills and extraction all use this default. External
        # material cannot silently become personality input after a restart.
        where = " WHERE provenance_status='valid'" + ("" if include_external else " AND origin='conversation'")
        rows = self.conn.execute("SELECT * FROM memories" + where + " ORDER BY created_at DESC").fetchall()
        return [r for r in rows if self._parents_available(r["id"], "gui")]

    def count_memories_since(self, ts: float, kind: str | None = None) -> int:
        """数据触发器的刻度：某时间点之后新产生的记忆条数。可只数事件类。"""
        if kind:
            return self.conn.execute(
                "SELECT COUNT(*) AS c FROM memories WHERE created_at > ? AND kind=? AND origin='conversation' AND provenance_status='valid'",
                (ts, kind),
            ).fetchone()["c"]
        return self.conn.execute(
            "SELECT COUNT(*) AS c FROM memories WHERE created_at > ? AND origin='conversation' AND provenance_status='valid'", (ts,)
        ).fetchone()["c"]

    def get_memory(self, memory_id: int) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM memories WHERE id=?", (memory_id,)).fetchone()

    def update_memory_content(self, memory_id: int, content: str) -> None:
        """原位更新记忆正文（v0.4 先验状态迁移用：先验→验证不改身份，只改状态行）。"""
        with self.source_transaction():
            row = self.get_memory(memory_id)
            if row is None or row["origin"] != "conversation" or row["content"] == content.strip():
                return
            children = self.dependent_memories([memory_id]) - {memory_id}
            for child in children:
                self.forget_memory_tree(child, "必要依据已修订，旧观点待重新形成")
            self.conn.execute("UPDATE memories SET content=? WHERE id=?", (content.strip(), memory_id))

    def bump_mention(self, memory_id: int) -> None:
        self.conn.execute(
            "UPDATE memories SET mention_count=mention_count+1 WHERE id=?", (memory_id,)
        )
        self.conn.commit()

    def bump_use(self, memory_id: int) -> None:
        self.conn.execute(
            "UPDATE memories SET use_count=use_count+1, last_used_at=? WHERE id=?",
            (time.time(), memory_id),
        )
        self.conn.commit()

    def set_layer(self, memory_id: int, layer: str) -> None:
        self.conn.execute("UPDATE memories SET layer=? WHERE id=?", (layer, memory_id))
        if layer == "longterm":
            self.conn.execute(
                "UPDATE memories SET promoted_at=? WHERE id=?", (time.time(), memory_id)
            )
        self.conn.commit()

    def expire_memory(self, memory_id: int, reason: str) -> None:
        """真实遗忘：内容删除，仅留墓碑供人工审计（墓碑永不进入上下文）。"""
        self.forget_memory_tree(memory_id, reason)

    def find_by_title(self, title: str) -> sqlite3.Row | None:
        title = title.strip()
        if not title:
            return None
        row = self.conn.execute(
            "SELECT * FROM memories WHERE title=? AND origin='conversation' AND provenance_status='valid' ORDER BY id DESC LIMIT 1", (title,)
        ).fetchone()
        if row:
            return row
        return self.conn.execute(
            "SELECT * FROM memories WHERE title LIKE ? AND origin='conversation' AND provenance_status='valid' ORDER BY id DESC LIMIT 1",
            (f"%{title}%",),
        ).fetchone()

    def search_memories(
        self, query: str, layers: tuple[str, ...], top_n: int | None = 5, min_score: float = 0.12,
        *, source_scope: str | None = None, include_external_active: bool = False,
        allowed_source_packet_ids: set[str] | None = None,
        origins: tuple[str, ...] | None = None,
    ) -> list[tuple[float, sqlite3.Row]]:
        scored = []
        for m in self.alive_memories(include_external=source_scope is not None):
            if origins is not None and m["origin"] not in origins:
                continue
            if m["layer"] not in layers and not (
                include_external_active and m["origin"] != "conversation" and m["layer"] == "active"
            ):
                continue
            if m["origin"] != "conversation":
                if m["lifecycle"] != "memory" and (m["expires_at"] is None or m["expires_at"] <= time.time()):
                    continue
                packets = self.external_memory_sources(m["id"], scope=source_scope)
                if not packets or (allowed_source_packet_ids is not None
                                   and any(p["id"] not in allowed_source_packet_ids for p in packets)):
                    continue
            s = text_score(query, f'{m["title"]} {m["keywords"]} {m["content"]}')
            if s >= min_score:
                scored.append((s, m))
        scored.sort(key=lambda t: (-t[0], -t[1]["use_count"]))
        return scored if top_n is None else scored[:top_n]

    # ---------- 技能 ----------
    def insert_skill(self, title: str, content: str, source_titles: str = "",
                     created_at: float | None = None) -> int:
        cur = self.conn.execute(
            "INSERT INTO skills (created_at, title, content, source_titles) VALUES (?, ?, ?, ?)",
            (created_at if created_at is not None else time.time(), title.strip(),
             content.strip(), source_titles),
        )
        self.conn.commit()
        return cur.lastrowid

    def list_skills(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM skills ORDER BY created_at DESC").fetchall()

    def count_skills(self) -> int:
        return self.conn.execute("SELECT COUNT(*) AS c FROM skills").fetchone()["c"]

    def skill_titles(self) -> list[str]:
        return [r["title"] for r in self.list_skills()]

    def evict_skill(self, skill_id: int) -> None:
        self.conn.execute("DELETE FROM skills WHERE id=?", (skill_id,))
        self.conn.commit()
