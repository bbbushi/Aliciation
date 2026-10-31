"""SQLite 存储层：对话轮次、三层记忆、技能、遗忘墓碑、元信息。"""
from __future__ import annotations

import hashlib
import re
import sqlite3
import time
from pathlib import Path

from .discovery_memory import SCHEMA as SOURCE_SCHEMA, SOURCE_SHADOW, SourceStoreMixin

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
    anchor = m["last_used_at"] or m["created_at"]
    recency = max(0.0, 3.0 - (now - anchor) / 86400)
    return m["mention_count"] * 2 + m["use_count"] + recency


class Store(SourceStoreMixin):
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        # GUI 服务是多线程处理请求的；跨线程访问由上层（service 的单写者锁）串行化保证
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.conn.executescript(SOURCE_SCHEMA)
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
        ):
            if name not in cols:
                self.conn.execute(f"ALTER TABLE memories ADD COLUMN {name} {declaration}")
                changed = True
        if changed:
            self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # ---------- meta ----------
    def meta_get(self, key: str, default: str | None = None) -> str | None:
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def meta_set(self, key: str, value) -> None:
        self.conn.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) "
            "ON CONFLICT (key) DO UPDATE SET value=excluded.value",
            (key, str(value)),
        )
        self.conn.commit()

    def meta_get_time(self, key: str) -> float:
        v = self.meta_get(key)
        return float(v) if v else 0.0

    # ---------- 对话轮次 ----------
    def add_turn(self, role: str, content: str, ts: float | None = None, *, request_id=None,
                 source_packet_ids=(), source_scope="gui") -> int:
        # A short transaction binds the actual row ID to its request before any
        # network call or callback. No model call is inside this transaction.
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO turns (ts, role, content) VALUES (?, ?, ?)",
                (ts if ts is not None else time.time(), role,
                 SOURCE_SHADOW if source_packet_ids else content),
            )
            turn_id = cur.lastrowid
            if source_packet_ids:
                self._source_turn_write(turn_id, content, source_packet_ids, source_scope)
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
        cur = self.conn.execute(
            "INSERT INTO memories (created_at, layer, title, content, keywords, salience, kind, pressure, source_url) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (created_at if created_at is not None else time.time(), layer, title.strip(),
             content.strip(), keywords, int(salience), kind, 1 if pressure else 0, source_url.strip()),
        )
        self.conn.commit()
        return cur.lastrowid

    def save_web_seen_memory(self, title: str, content: str, source_url: str,
                             keywords: str = "", salience: int = 0) -> str:
        """见闻单元（v0.4 P2）：网上读到的信息，经她复述后转正为对话起源的记忆。

        与普通记忆同表同管线（七日线/容量竞争/思辨视野），出处由 source_url 列
        与 content 内的来源明示承担（「{来源}说：…」措辞即认识论防线）。
        去重：同 source_url 且同题的见闻不重复落库。返回给模型的确认语。
        """
        url = source_url.strip()
        if not url:
            return "见闻需要来源链接（source_url），没有出处的不落库。"
        dup = self.conn.execute(
            "SELECT id FROM memories WHERE source_url=? AND title=? LIMIT 1",
            (url, title.strip()[:24]),
        ).fetchone()
        if dup:
            return "这条来源已经记过了，不重复落库。"
        self.insert_memory(
            title=title[:24], content=content, keywords=(keywords + " 见闻").strip(),
            salience=salience, layer="active", kind="event", source_url=url,
        )
        return "已作为见闻记住（带来源链接，与普通记忆同管线，会随时间自然过期）。"

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
        elif consumer != "maintenance":
            raise ValueError("记忆消费者无效")
        return self.conn.execute(
            "SELECT * FROM memories WHERE " + " AND ".join(clauses)
            + " ORDER BY created_at DESC,id DESC", args,
        ).fetchall()

    def count_in_layer(self, layer: str) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) AS c FROM memories WHERE layer=?", (layer,)
        ).fetchone()["c"]

    def count_pressure(self) -> int:
        """压力窗口记忆存量（活跃的）：给状态面板与"结构性沦陷"检查用。"""
        return self.conn.execute(
            "SELECT COUNT(*) AS c FROM memories WHERE pressure=1"
        ).fetchone()["c"]

    def alive_memories(self, *, include_external: bool = False) -> list[sqlite3.Row]:
        # Thought/persona/skills and extraction all use this default. External
        # material cannot silently become personality input after a restart.
        where = "" if include_external else " WHERE origin='conversation'"
        return self.conn.execute("SELECT * FROM memories" + where + " ORDER BY created_at DESC").fetchall()

    def count_memories_since(self, ts: float, kind: str | None = None) -> int:
        """数据触发器的刻度：某时间点之后新产生的记忆条数。可只数事件类。"""
        if kind:
            return self.conn.execute(
                "SELECT COUNT(*) AS c FROM memories WHERE created_at > ? AND kind=? AND origin='conversation'",
                (ts, kind),
            ).fetchone()["c"]
        return self.conn.execute(
            "SELECT COUNT(*) AS c FROM memories WHERE created_at > ? AND origin='conversation'", (ts,)
        ).fetchone()["c"]

    def get_memory(self, memory_id: int) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM memories WHERE id=?", (memory_id,)).fetchone()

    def update_memory_content(self, memory_id: int, content: str) -> None:
        """原位更新记忆正文（v0.4 先验状态迁移用：先验→验证不改身份，只改状态行）。"""
        self.conn.execute(
            "UPDATE memories SET content=? WHERE id=? AND origin='conversation'", (content.strip(), memory_id)
        )
        self.conn.commit()

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
        m = self.get_memory(memory_id)
        if not m:
            return
        self.conn.execute(
            "INSERT INTO memory_tombstones (memory_id, title, expired_at, reason) VALUES (?, ?, ?, ?)",
            (memory_id, m["title"] if m["origin"] == "conversation" else "外部见闻已遗忘",
             time.time(), reason if m["origin"] == "conversation" else "外部记忆按既有分层规则遗忘"),
        )
        self.conn.execute("DELETE FROM memories WHERE id=?", (memory_id,))
        self.conn.execute("DELETE FROM source_memory_dependencies WHERE memory_id=?", (memory_id,))
        self.conn.commit()

    def find_by_title(self, title: str) -> sqlite3.Row | None:
        title = title.strip()
        if not title:
            return None
        row = self.conn.execute(
            "SELECT * FROM memories WHERE title=? AND origin='conversation' ORDER BY id DESC LIMIT 1", (title,)
        ).fetchone()
        if row:
            return row
        return self.conn.execute(
            "SELECT * FROM memories WHERE title LIKE ? AND origin='conversation' ORDER BY id DESC LIMIT 1",
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
                if m["expires_at"] is None or m["expires_at"] <= time.time():
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
