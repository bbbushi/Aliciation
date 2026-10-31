"""Retained citations and source-aware ordinary forgetting.

All methods run on the host under its Store lock. No network/model operations.
The source packet is a short-lived reading; retained evidence is the small
authorized citation kept with an admitted memory, not a copy of the page.
"""
from __future__ import annotations

import hashlib
import json
import re
import time

from .discovery_memory import _dump, _ids, _text, SOURCE_SCOPES, DAY_ZONE
from datetime import datetime

SCHEMA = """
CREATE TABLE IF NOT EXISTS retained_evidence (
 id TEXT PRIMARY KEY, packet_id TEXT NOT NULL, source_id TEXT NOT NULL,
 source_version TEXT NOT NULL, created_at REAL NOT NULL, expires_at REAL,
 scope_json TEXT NOT NULL, payload_json TEXT NOT NULL, revoked INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS retained_evidence_packet ON retained_evidence(packet_id);
CREATE TABLE IF NOT EXISTS memory_evidence_links (
 memory_id INTEGER NOT NULL, evidence_id TEXT NOT NULL, PRIMARY KEY(memory_id,evidence_id)
);
CREATE TABLE IF NOT EXISTS memory_dependencies (
 memory_id INTEGER NOT NULL, parent_memory_id INTEGER NOT NULL, parent_version TEXT NOT NULL,
 PRIMARY KEY(memory_id,parent_memory_id)
);
CREATE TABLE IF NOT EXISTS observation_records (identity TEXT PRIMARY KEY, memory_id INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS discussion_records (
 turn_id INTEGER PRIMARY KEY, scope TEXT NOT NULL, user_quote TEXT NOT NULL,
 explicit INTEGER NOT NULL DEFAULT 0, memory_id INTEGER, created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS discussion_memory_links (
 turn_id INTEGER NOT NULL, memory_id INTEGER NOT NULL, PRIMARY KEY(turn_id,memory_id)
);
"""


def version(memory):
    return hashlib.sha256(_dump([memory[k] for k in ("id", "title", "content", "created_at")]).encode()).hexdigest()


class ObservationStoreMixin:
    def init_observation_memory(self):
        self.conn.executescript(SCHEMA)
        if self.meta_get("observation_schema") is None:
            with self.conn:
                self.conn.execute("UPDATE memories SET lifecycle='source' WHERE origin!='conversation'")
                self.conn.execute("UPDATE memories SET provenance_status='legacy_unverified' "
                                  "WHERE source_url!='' AND origin='conversation'")
                # Old title-only stances cannot prove an independent basis.
                legacy = [r[0] for r in self.conn.execute("SELECT title FROM memories WHERE provenance_status='legacy_unverified'")]
                for row in self.conn.execute("SELECT id,content FROM memories WHERE kind='stance'").fetchall():
                    if any(title and title in row["content"] for title in legacy):
                        self.conn.execute("UPDATE memories SET provenance_status='legacy_unverified' WHERE id=?", (row["id"],))
                self.conn.execute("INSERT INTO meta(key,value) VALUES('observation_schema','1')")

    def _observation_settings(self):
        if not self.conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='discovery_config'").fetchone():
            return {}
        row = self.conn.execute("SELECT value FROM discovery_config WHERE id=1").fetchone()
        return json.loads(row[0]) if row else {}

    def get_retained_source(self, identity, *, scope="gui", now=None):
        now = time.time() if now is None else now
        row = self.conn.execute("SELECT * FROM retained_evidence WHERE id=?", (identity,)).fetchone()
        if (row is None or row["revoked"] or scope not in json.loads(row["scope_json"])
                or row["expires_at"] is not None and row["expires_at"] <= now):
            return None
        if not self.conn.execute("SELECT 1 FROM memory_evidence_links l JOIN memories m ON m.id=l.memory_id "
                                 "WHERE l.evidence_id=? AND m.provenance_status='valid'", (identity,)).fetchone():
            return None
        payload = json.loads(row["payload_json"])
        return {**payload, "id": identity, "scope": json.loads(row["scope_json"]), "retained": True,
                "observation_expires_at": row["expires_at"],
                # Finite transport representation, not a fixed retention policy.
                # Actual lifetime is the nullable deadline and live dependency.
                "expires_at": row["expires_at"] if row["expires_at"] is not None else 253402300799.0}

    def observation_sources(self, memory_id, *, scope="gui"):
        row = self.conn.execute("SELECT * FROM memories WHERE id=?", (memory_id,)).fetchone()
        if (row is None or row["lifecycle"] != "memory" or row["provenance_status"] != "valid"
                or row["origin"] == "conversation" or scope not in json.loads(row["scope"])
                or row["expires_at"] is not None and row["expires_at"] <= time.time()):
            return []
        ids = [r[0] for r in self.conn.execute("SELECT evidence_id FROM memory_evidence_links WHERE memory_id=?", (memory_id,))]
        packets = [self.get_retained_source(pid, scope=scope) for pid in ids]
        return packets if packets and all(packets) and self._parents_available(memory_id, scope) else []

    def _parents_available(self, memory_id, scope, seen=None):
        seen = set() if seen is None else set(seen)
        if memory_id in seen:
            return False
        seen.add(memory_id)
        for link in self.conn.execute("SELECT parent_memory_id,parent_version FROM memory_dependencies WHERE memory_id=?", (memory_id,)):
            parent = self.conn.execute("SELECT * FROM memories WHERE id=?", (link[0],)).fetchone()
            if parent is None or parent["provenance_status"] != "valid" or version(parent) != link[1]:
                return False
            if parent["origin"] != "conversation" and scope not in json.loads(parent["scope"]):
                return False
            if parent["expires_at"] is not None and parent["expires_at"] <= time.time():
                return False
            if not self._parents_available(parent["id"], scope, seen):
                return False
        return True

    def memory_rows(self, *, purpose="chat", scope="gui", kind=None):
        """Consumers must opt in to source-derived material and its audience."""
        rows = self.conn.execute("SELECT * FROM memories WHERE provenance_status='valid' ORDER BY created_at DESC,id DESC").fetchall()
        result = []
        for row in rows:
            if kind is not None and row["kind"] != kind:
                continue
            if row["origin"] == "conversation":
                if self._parents_available(row["id"], scope):
                    result.append(row)
            elif purpose in {"chat", "thought", "observer"} and self.observation_sources(row["id"], scope=scope):
                result.append(row)
        return result

    def save_observation(self, packet_ids, *, title, content, keywords="", scope="gui", user_turn_id=None):
        settings = self._observation_settings()
        if not settings.get("remember_observations") or scope not in SOURCE_SCOPES:
            return None
        retention = settings.get("observation_ttl_hours")
        if retention == 0:
            return None
        packets = self.source_packets_for(_ids(packet_ids), scope=scope)
        if not packets or any(not p.get("observation_eligible") for p in packets):
            return None
        title, content = _text(title, 1200, required=True)[:24], _text(content, 8000, required=True)
        keywords = _text(keywords, 1000)
        content = "见闻记录（来源说法或讨论中的暂时判断，未独立核实）：\n" + content
        now = time.time()
        # A new fetch timestamp or a paraphrase is not a new memory. Keep the
        # content-version identity after forgetting, without keeping its prose.
        versions = sorted((p["source_id"], p["version"].split(":", 1)[0]) for p in packets)
        identity = hashlib.sha256(_dump([versions, title, scope]).encode()).hexdigest()
        old = self.conn.execute("SELECT memory_id FROM observation_records WHERE identity=?", (identity,)).fetchone()
        if old:
            return old[0] if self.observation_sources(old[0], scope=scope) else None
        created_at = min(p["created_at"] for p in packets)
        for source_id, content_version in versions:
            for retained in self.conn.execute("SELECT source_version,created_at,revoked FROM retained_evidence WHERE source_id=?", (source_id,)):
                if retained["source_version"].split(":", 1)[0] != content_version:
                    continue
                if retained["revoked"]:
                    return None  # Renaming a forgotten/cleared observation cannot revive it.
                created_at = min(created_at, retained["created_at"])
        day = datetime.fromtimestamp(now, DAY_ZONE).strftime("%Y-%m-%d")
        used = self.conn.execute("SELECT count FROM source_memory_writes WHERE day=?", (day,)).fetchone()
        cap = self.source_limits()["daily_memory_writes"]
        if cap is not None and (used[0] if used else 0) >= cap:
            return None
        refs, additional, expiry_values = [], 0, []
        if retention is not None:
            expiry_values.append(now + retention * 3600)
        for packet in packets:
            eid = packet["id"] if packet.get("retained") else "evidence:" + hashlib.sha256(packet["id"].encode()).hexdigest()
            refs.append((eid, packet))
            retained = self.conn.execute("SELECT revoked,expires_at FROM retained_evidence WHERE id=?", (eid,)).fetchone()
            if retained and (retained["revoked"] or retained["expires_at"] is not None and retained["expires_at"] <= now):
                return None
            if retained and retained["expires_at"] is not None:
                expiry_values.append(retained["expires_at"])
            if packet.get("observation_expires_at") is not None:
                expiry_values.append(packet["observation_expires_at"])
            if retained is None:
                additional += len(_dump(packet).encode("utf-8"))
        additional += sum(len(v.encode("utf-8")) for v in (title, content, keywords))
        with self.source_transaction():
            self._check_source_space(additional)
            for eid, packet in refs:
                if not packet.get("retained"):
                    payload = {key: packet.get(key) for key in ("source_id", "version", "created_at", "title", "url", "evidence",
                        "evidence_kind", "evidence_hash", "author_view", "model_interpretation", "uncertainty", "coverage", "reading_policy")}
                    payload.update(observation_eligible=True, memory_eligible=False)
                    self.conn.execute("INSERT OR IGNORE INTO retained_evidence VALUES(?,?,?,?,?,?,?,?,0)",
                        (eid, packet["id"], packet["source_id"], packet["version"], packet["created_at"], packet.get("observation_expires_at"),
                         _dump(packet["scope"]), _dump(payload)))
            cur = self.conn.execute("INSERT INTO memories(created_at,layer,title,content,keywords,salience,kind,pressure,origin,scope,expires_at,source_url,lifecycle,provenance_status) "
                "VALUES(?,'active',?,?,?,0,'event',0,'external_derived',?,?,?,'memory','valid')",
                (created_at, title, content, (keywords + " 见闻").strip(), _dump([scope]), min(expiry_values) if expiry_values else None, packets[0]["url"]))
            mid = cur.lastrowid
            self.conn.executemany("INSERT INTO memory_evidence_links VALUES(?,?)", [(mid, eid) for eid, _ in refs])
            self.conn.execute("INSERT INTO observation_records VALUES(?,?)", (identity, mid))
            self.conn.execute("INSERT INTO source_memory_writes VALUES(?,1) ON CONFLICT(day) DO UPDATE SET count=count+1", (day,))
            if user_turn_id is not None:
                self.conn.execute("INSERT OR IGNORE INTO discussion_memory_links VALUES(?,?)", (user_turn_id, mid))
        return mid

    def reserve_observation_write(self):
        """Shared daily admission for source-derived thought as well as events."""
        if not self._observation_settings().get("remember_observations"):
            return False
        if self._observation_settings().get("observation_ttl_hours") == 0:
            return False
        day = datetime.now(DAY_ZONE).strftime("%Y-%m-%d")
        row = self.conn.execute("SELECT count FROM source_memory_writes WHERE day=?", (day,)).fetchone()
        cap = self.source_limits()["daily_memory_writes"]
        if cap is not None and (row[0] if row else 0) >= cap:
            return False
        self.conn.execute("INSERT INTO source_memory_writes VALUES(?,1) ON CONFLICT(day) DO UPDATE SET count=count+1", (day,))
        return True

    def bind_derived_memory(self, memory_id, parents, *, scope="gui"):
        """Host-selected full input dependencies; model source titles cannot reduce them."""
        parents = list({row["id"]: row for row in parents if row["id"] != memory_id}.values())
        external = [row for row in parents if row["origin"] != "conversation"]
        with self.source_transaction():
            self.conn.executemany("INSERT OR REPLACE INTO memory_dependencies VALUES(?,?,?)",
                                  [(memory_id, p["id"], version(p)) for p in parents])
            if external:
                if not self.reserve_observation_write():
                    raise ValueError("当前正式见闻写入开关或额度不允许派生新观点")
                evidence = {p["id"] for row in external for p in self.observation_sources(row["id"], scope=scope)}
                if not evidence:
                    raise ValueError("观点缺少可用来源")
                expiries = [p["expires_at"] for p in parents if p["expires_at"] is not None]
                retention = self._observation_settings().get("observation_ttl_hours")
                if retention is not None:
                    expiries.append(time.time() + retention * 3600)
                self.conn.execute("UPDATE memories SET origin='external_derived',lifecycle='memory',scope=?,expires_at=? WHERE id=?",
                                  (_dump([scope]), min(expiries) if expiries else None, memory_id))
                self.conn.executemany("INSERT OR IGNORE INTO memory_evidence_links VALUES(?,?)", [(memory_id, eid) for eid in evidence])

    def dependent_memories(self, identities):
        found, pending = set(identities), list(identities)
        while pending:
            parent = pending.pop()
            for row in self.conn.execute("SELECT memory_id FROM memory_dependencies WHERE parent_memory_id=?", (parent,)):
                if row[0] not in found:
                    found.add(row[0])
                    pending.append(row[0])
        return found

    def forget_memory_tree(self, memory_id, reason):
        ids = self.dependent_memories([memory_id])
        with self.source_transaction():
            for mid in ids:
                row = self.conn.execute("SELECT * FROM memories WHERE id=?", (mid,)).fetchone()
                if row is None:
                    continue
                ordinary = row["origin"] == "conversation" and mid == memory_id
                self.conn.execute("INSERT INTO memory_tombstones(memory_id,title,expired_at,reason) VALUES(?,?,?,?)",
                    (mid, row["title"] if ordinary else "来源派生记忆已失效", time.time(), reason if ordinary else "来源或必要依据已不可用"))
                self.conn.execute("DELETE FROM memories WHERE id=?", (mid,))
                self.conn.execute("DELETE FROM memory_evidence_links WHERE memory_id=?", (mid,))
                self.conn.execute("DELETE FROM source_memory_dependencies WHERE memory_id=?", (mid,))
                self.conn.execute("DELETE FROM memory_dependencies WHERE memory_id=? OR parent_memory_id=?", (mid, mid))
                self.conn.execute("DELETE FROM discussion_memory_links WHERE memory_id=?", (mid,))
                self.conn.execute("UPDATE discussion_records SET memory_id=NULL WHERE memory_id=?", (mid,))
            self.gc_retained_evidence()

    def gc_retained_evidence(self):
        self.conn.execute("DELETE FROM memory_evidence_links WHERE memory_id NOT IN (SELECT id FROM memories)")
        self.conn.execute("UPDATE retained_evidence SET revoked=1,payload_json='{}' WHERE id NOT IN "
                          "(SELECT evidence_id FROM memory_evidence_links) AND revoked=0")
        revoked = {r[0] for r in self.conn.execute("SELECT id FROM retained_evidence WHERE revoked=1")}
        for turn in self.conn.execute("SELECT turn_id,source_ids_json FROM source_turns WHERE content!=''").fetchall():
            if revoked.intersection(json.loads(turn["source_ids_json"])):
                self.conn.execute("UPDATE source_turns SET content='' WHERE turn_id=?", (turn["turn_id"],))

    def revoke_retained_evidence(self, packet_ids=(), *, source_id=None):
        ids = set(packet_ids)
        rows = [r for r in self.conn.execute("SELECT id,packet_id,source_id FROM retained_evidence WHERE revoked=0")
                if r["id"] in ids or r["packet_id"] in ids or source_id is not None and r["source_id"] == source_id]
        with self.source_transaction():
            for row in rows:
                mids = [r[0] for r in self.conn.execute("SELECT memory_id FROM memory_evidence_links WHERE evidence_id=?", (row["id"],))]
                for mid in mids:
                    self.forget_memory_tree(mid, "来源已清除")
                self.conn.execute("UPDATE retained_evidence SET revoked=1,payload_json='{}' WHERE id=?", (row["id"],))
                for turn in self.conn.execute("SELECT turn_id,source_ids_json FROM source_turns WHERE content!=''").fetchall():
                    if row["id"] in json.loads(turn["source_ids_json"]):
                        self.conn.execute("UPDATE source_turns SET content='' WHERE turn_id=?", (turn["turn_id"],))

    def restrict_observation_tieba(self, settings):
        ids = []
        for row in self.conn.execute("SELECT id,payload_json FROM retained_evidence WHERE revoked=0 AND source_id LIKE 'tieba:%'"):
            policy = json.loads(row["payload_json"]).get("reading_policy") or {}
            count = policy.get("tieba_reply_count", 0)
            if count and (not settings["tieba_read_replies"] or count > settings["tieba_reply_limit"]):
                ids.append(row["id"])
        self.revoke_retained_evidence(ids)

    def observation_links(self, packet_id):
        return [dict(row) for row in self.conn.execute("SELECT DISTINCT m.id,m.title,m.kind,m.provenance_status FROM memories m "
            "JOIN memory_evidence_links l ON l.memory_id=m.id JOIN retained_evidence e ON e.id=l.evidence_id "
            "WHERE e.packet_id=? AND m.provenance_status='valid'", (packet_id,)) if self.observation_sources(row["id"], scope="gui")]

    def observation_detail(self, memory_id, *, scope="gui"):
        row = self.get_memory(memory_id)
        if row is None:
            return None
        if row["origin"] != "conversation" and not self.observation_sources(memory_id, scope=scope):
            return None
        return {"memory": dict(row), "sources": self.observation_sources(memory_id, scope=scope),
                "discussions": [dict(r) for r in self.conn.execute(
                    "SELECT d.turn_id,d.user_quote,d.created_at FROM discussion_records d WHERE d.scope=? AND "
                    "(d.memory_id=? OR d.turn_id IN (SELECT turn_id FROM discussion_memory_links WHERE memory_id=?))",
                    (scope, memory_id, memory_id))]}

    @staticmethod
    def _independent_user_quote(text):
        # This path preserves the owner's own complete assertion, not an LLM
        # classification of mixed web prose. Ambiguous quotations stay candidates.
        text = re.sub(r"^(?:请)?(?:记住|记一下)[，,:：\s]*", "", text.strip())
        if (not re.match(r"^我(?:更?喜欢|不喜欢|希望|认为|觉得|打算|决定|想要|偏好)", text)
                or any(x in text for x in ('http:', 'https:', '“', '”', '「', '」', '\"', '\n'))
                or re.search(r"(他|她|作者|文章|帖子|网友).{0,3}(说|认为|表示)|(?:这|那)(?:个|样|条|篇|些|种|位|段|部)|同上|上述|上面|前面|也这么", text)):
            return None
        return text

    def capture_discussion_user(self, turn_id, text, *, scope="gui", mixed=False):
        quote = self._independent_user_quote(text)
        if quote is None and not mixed:
            return None
        explicit = bool(re.match(r"^(?:请)?(?:记住|记一下)", text.strip()))
        with self.conn:
            self.conn.execute("INSERT OR IGNORE INTO discussion_records VALUES(?,?,?,?,NULL,?)",
                              (turn_id, scope, quote or "", 2 if not quote and re.search(r"我(?:认为|觉得|喜欢|不喜欢|希望)|记住", text) else int(explicit), time.time()))
        return self.remember_user_turn(turn_id, scope=scope) if explicit and quote else None

    def discussion_status(self, turn_id, *, scope="gui"):
        row = self.conn.execute("SELECT memory_id,user_quote,explicit FROM discussion_records WHERE turn_id=? AND scope=?", (turn_id, scope)).fetchone()
        if row is None or not row["user_quote"] and not row["explicit"]:
            return None
        return {"status": "saved" if row["memory_id"] else "candidate" if row["user_quote"] else "clarify",
                "memory_id": row["memory_id"]}

    def remember_user_turn(self, turn_id, *, scope="gui"):
        if type(turn_id) is not int:
            raise ValueError("用户消息标识无效")
        row = self.conn.execute("SELECT d.* FROM discussion_records d JOIN turns t ON t.id=d.turn_id WHERE d.turn_id=? AND d.scope=? AND t.role='user'",
                                (turn_id, scope)).fetchone()
        if row is None or not row["user_quote"]:
            raise ValueError("请用独立的第一人称表述说明要记住的看法，引用或指代不明的内容暂不写入个人记忆")
        if row["memory_id"] and self.get_memory(row["memory_id"]):
            return row["memory_id"]
        quote = row["user_quote"]
        mid = self.insert_memory(title=("用户表达：" + quote)[:24], content="用户明确表达：“" + quote + "”", keywords="用户 偏好 讨论", salience=1)
        with self.conn:
            self.conn.execute("UPDATE discussion_records SET memory_id=?,explicit=1 WHERE turn_id=?", (mid, turn_id))
        return mid
