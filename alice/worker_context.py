"""GUI-only task dependencies, distinct from web evidence and personal memory.

Every operation is a short Store-owner step. This module never executes an
adapter, interprets a report as evidence, or grants permission to a task.
"""
from __future__ import annotations

import json
import sqlite3
import time

WORKER_SHADOW = "（这一轮使用了协作任务资料；正文仅在 GUI 内、任务资料仍获准时可见。）"
SCHEMA = """
CREATE TABLE IF NOT EXISTS worker_turns (
 turn_id INTEGER PRIMARY KEY, scope TEXT NOT NULL, content TEXT NOT NULL,
 refs_json TEXT NOT NULL
);
"""


def merge_worker_refs(*groups):
    """Preserve all immutable versions, including conflicting/obsolete ones."""
    merged = []
    for group in groups:
        if not isinstance(group, (list, tuple)):
            raise ValueError("任务依赖格式无效")
        for ref in group:
            if not isinstance(ref, dict) or set(ref) != {
                "task_id", "message_id", "version", "epoch", "policy_revision"
            }:
                raise ValueError("任务依赖字段无效")
            if any(not isinstance(ref[key], str) or not 1 <= len(ref[key]) <= 200
                   for key in ("task_id", "message_id", "epoch")):
                raise ValueError("任务依赖身份无效")
            if any(type(ref[key]) is not int or ref[key] < 1
                   for key in ("version", "policy_revision")):
                raise ValueError("任务依赖版本无效")
            if ref not in merged:
                merged.append(dict(ref))
    if len(merged) > 128:
        raise ValueError("本轮任务依赖已达上限，请撤回旧任务资料后再读")
    return merged


class WorkerStoreMixin:
    def worker_refs_valid(self, refs, *, scope="gui", now=None):
        if scope != "gui":
            return False
        try:
            refs = merge_worker_refs(refs)
            if not refs:
                return False
            now = time.time() if now is None else now
            epoch = self.meta_get("session_epoch")
            for ref in refs:
                # 授权按任务所属工人取对应政策表（bridge_instance 是工人身份的账面事实）。
                task = self.conn.execute(
                    "SELECT bridge_instance,epoch,policy_revision,expires_at,revoked,scope "
                    "FROM worker_tasks WHERE task_id=?", (ref["task_id"],)).fetchone()
                if task is None:
                    return False
                if task["bridge_instance"] == "local:claude-agent":
                    policy = self.conn.execute(
                        "SELECT revision,enabled FROM claude_policy WHERE id=1").fetchone()
                else:
                    policy = self.conn.execute(
                        "SELECT revision,enabled FROM worker_policy WHERE id=1").fetchone()
                if policy is None or not policy["enabled"]:
                    return False
                if ref["epoch"] != epoch or ref["policy_revision"] != policy["revision"]:
                    return False
                task = self.conn.execute("SELECT epoch,scope,policy_revision,expires_at,revoked "
                                         "FROM worker_tasks WHERE task_id=?", (ref["task_id"],)).fetchone()
                message = self.conn.execute("SELECT task_id,version,state FROM worker_messages WHERE message_id=?",
                                            (ref["message_id"],)).fetchone()
                if (task is None or message is None or task["revoked"] or task["scope"] != "gui"
                        or task["epoch"] != epoch or task["policy_revision"] != policy["revision"]
                        or task["expires_at"] is None or task["expires_at"] <= now
                        or message["task_id"] != ref["task_id"] or message["version"] != ref["version"]
                        or message["state"] != "completed"):
                    return False
            return True
        except (ValueError, TypeError, sqlite3.OperationalError):
            # An unavailable or older task ledger cannot authorize stored prose.
            return False

    def turn_worker_refs(self, turn_id):
        row = self.conn.execute("SELECT refs_json FROM worker_turns WHERE turn_id=?", (turn_id,)).fetchone()
        return json.loads(row[0]) if row else []

    def _worker_sources_valid(self, turn_id, *, scope, now=None):
        now = time.time() if now is None else now
        attached = self.conn.execute("SELECT * FROM source_turns WHERE turn_id=?", (turn_id,)).fetchone()
        return (attached is None or attached["scope"] == scope and bool(attached["content"])
                and attached["expires_at"] > now
                and bool(self.source_packets_for(json.loads(attached["source_ids_json"]), scope=scope, now=now)))

    def worker_turn_status(self, turn_id, *, scope="gui"):
        row = self.conn.execute("SELECT * FROM worker_turns WHERE turn_id=?", (turn_id,)).fetchone()
        if row is None:
            return None
        now = time.time()
        refs = json.loads(row["refs_json"])
        available = (scope == row["scope"] == "gui" and bool(row["content"])
                     and self.worker_refs_valid(refs, scope=scope, now=now)
                     and self._worker_sources_valid(turn_id, scope=scope, now=now))
        expires_at = None
        if available:
            # The browser must expire a cached task reply even when offline.
            # A null deadline used to be interpreted as zero and hide valid text.
            deadlines = [self.conn.execute("SELECT expires_at FROM worker_tasks WHERE task_id=?",
                                           (task_id,)).fetchone()[0]
                         for task_id in {ref["task_id"] for ref in refs}]
            attached = self.conn.execute("SELECT expires_at FROM source_turns WHERE turn_id=?",
                                         (turn_id,)).fetchone()
            if attached is not None:
                deadlines.append(attached["expires_at"])
                packets = self.source_packets_for(self.turn_source_ids(turn_id), scope=scope, now=now)
                deadlines.extend(packet["expires_at"] for packet in packets)
            expires_at = min(deadlines)
        return {"worker_bound": True, "worker_available": bool(available),
                "worker_expires_at": expires_at,
                "worker_refs": refs if scope == "gui" else []}

    def turn_display(self, turn_id, *, scope="gui"):
        row = self.conn.execute("SELECT * FROM worker_turns WHERE turn_id=?", (turn_id,)).fetchone()
        if row is None:
            return super().turn_display(turn_id, scope=scope)
        return row["content"] if self.worker_turn_status(turn_id, scope=scope)["worker_available"] else WORKER_SHADOW

    def _worker_turn_write(self, turn_id, content, refs, scope):
        """Inside the caller's transaction; no raw prose in ordinary turns."""
        refs = merge_worker_refs(refs)
        if scope != "gui" or not refs:
            raise ValueError("任务正文只允许 GUI 有界读取")
        if not self.worker_refs_valid(refs, scope=scope):
            content = ""
        if len(content.encode("utf-8")) > 128000:
            content = ""  # Preserve the boundary even when the body is too large.
        self.conn.execute("INSERT INTO worker_turns(turn_id,scope,content,refs_json) VALUES(?,?,?,?) "
                          "ON CONFLICT(turn_id) DO UPDATE SET content=excluded.content,refs_json=excluded.refs_json",
                          (turn_id, scope, content, json.dumps(refs, ensure_ascii=False)))
        self.conn.execute("UPDATE turns SET content=? WHERE id=?", (WORKER_SHADOW, turn_id))
        # Existing web evidence keeps its dependency identities, never a second
        # copy that could bypass task revocation through a source-only consumer.
        self.conn.execute("UPDATE source_turns SET content=? WHERE turn_id=? AND content!=''",
                          (WORKER_SHADOW, turn_id))

    def protect_worker_turn(self, turn_id, refs, *, scope="gui"):
        row = self.conn.execute("SELECT content FROM turns WHERE id=?", (turn_id,)).fetchone()
        if row is None:
            raise ValueError("任务关联轮次不存在")
        old = self.conn.execute("SELECT * FROM worker_turns WHERE turn_id=?", (turn_id,)).fetchone()
        source = self.conn.execute("SELECT content FROM source_turns WHERE turn_id=?", (turn_id,)).fetchone()
        body = old["content"] if old else source["content"] if source else row["content"]
        refs = merge_worker_refs(json.loads(old["refs_json"]) if old else [], refs)
        with self.source_transaction():
            self._worker_turn_write(turn_id, body, refs, scope)

    def ordinary_turns(self, rows):
        return super().ordinary_turns([row for row in rows if not self.conn.execute(
            "SELECT 1 FROM worker_turns WHERE turn_id=?", (row["id"],)).fetchone()])

    def capture_discussion_user(self, turn_id, text, *, scope="gui", mixed=False):
        if not self.turn_worker_refs(turn_id):
            return super().capture_discussion_user(turn_id, text, scope=scope, mixed=mixed)
        # The original explicit-user path may have already saved an independent
        # assertion before a tool read introduced task material. Preserve that
        # authored record, but never derive a new personal quote from task-bound
        # history or a later search attachment's restored display text.
        with self.source_transaction():
            self.conn.execute("INSERT OR IGNORE INTO discussion_records VALUES(?,?,?,2,NULL,?)",
                              (turn_id, scope, "", time.time()))
        return None

    def model_history_turns(self, limit):
        """Display-only notices do not silently import task prose into a chat.

        Filter before LIMIT so notifications do not crowd out actual dialogue.
        The bare Store/CLI also works before a worker ledger has been installed.
        """
        if not self.conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='worker_reports'").fetchone():
            return self.recent_turns(limit)
        rows = self.conn.execute(
            "SELECT t.* FROM turns t WHERE NOT EXISTS "
            "(SELECT 1 FROM worker_reports r WHERE r.turn_id=t.id) ORDER BY t.id DESC LIMIT ?",
            (limit,)).fetchall()
        return list(reversed(rows))

    def worker_history_refs(self, limit, *, scope="gui"):
        if scope != "gui":
            return []
        refs = []
        for turn in self.model_history_turns(limit):
            status = self.worker_turn_status(turn["id"], scope=scope)
            if status and status["worker_available"]:
                refs = merge_worker_refs(refs, status["worker_refs"])
        return refs

    def worker_history_sources(self, limit, *, scope="gui"):
        """A task-derived turn may also depend on actual web evidence."""
        ids = []
        if scope == "gui":
            for turn in self.model_history_turns(limit):
                status = self.worker_turn_status(turn["id"], scope=scope)
                if status and status["worker_available"]:
                    ids.extend(self.turn_source_ids(turn["id"]))
        return list(dict.fromkeys(ids))

    def revoke_worker_context(self, task_id=None):
        """Permanently erase derivatives; re-enabling cannot resurrect them."""
        with self.source_transaction():
            for row in self.conn.execute("SELECT turn_id,refs_json FROM worker_turns WHERE content!=''").fetchall():
                if task_id is None or any(ref["task_id"] == task_id for ref in json.loads(row["refs_json"])):
                    self.conn.execute("UPDATE worker_turns SET content='' WHERE turn_id=?", (row["turn_id"],))
                    self.conn.execute("UPDATE source_turns SET content='' WHERE turn_id=?", (row["turn_id"],))

    def clear_source_material_for_archive(self):
        self.revoke_worker_context()
        return super().clear_source_material_for_archive()
