"""Interest-led content discovery: one supervised worker, parent-only database writes.

This module never reads a user configuration file. ChatService supplies its current
model settings only when an explicitly enabled activity needs an isolated call.
All persistent access is under ChatService._lock; no network or process wait is.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import math
import os
import re
import subprocess
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from .runtime_paths import RESOURCE_ROOT, user_root
from .runtime_process import addon_available, discovery_command

from .channels import ChannelError
from .store import text_score


DAY_ZONE = timezone(timedelta(hours=8))
PLATFORMS = {"web": "全网", "wikipedia": "维基百科", "bilibili": "B站", "zhihu": "知乎", "tieba": "贴吧"}
_BILI_STAGES = frozenset({"device", "wbi_keys", "search", "video_info"})
_SEARCH_PROVIDERS = frozenset({"native", "bing_rss", "brave", "searxng", "browser"})
_SEARCH_COUNTS = ("received", "accepted", "returned", "rejected", "duplicates", "unsupported")
DEFAULTS = {
    "enabled": False, "allow_paid": False, "window_start": "18:00", "window_end": "23:00",
    "background_enabled": True, "chat_search_enabled": False, "remember_observations": False,
    "chat_search_calls": None, "chat_search_results": 3, "tool_round_limit": 4,
    "chat_search_timeout_seconds": 300, "search_quota_testing": False,
    "observation_ttl_hours": None,
    "daily_queries": 60, "daily_models": 120, "allow_chat_interests": False,
    "share_in_qq": False, "save_memories": False, "provider_kind": "native",
    "provider_endpoint": "", "brave_key": "", "provider_engines": "duckduckgo",
    "bilibili_access": "native", "zhihu_access": "third_party", "tieba_access": "native", "network_mode": "system", "daily_network": 1000, "daily_response_mib": 1024,
    "tieba_read_replies": False, "tieba_read_nested": False, "tieba_reply_limit": 200, "tieba_text_limit": 30000,
    "daily_shares": 20, "daily_memory_writes": 10, "pool_capacity": 100,
    "source_pool_bytes": 200 * 1024 * 1024, "chat_material_limit": 2,
    "chat_dependency_limit": 8, "external_recall_limit": 10,
    "activity_interval_minutes": 30, "share_interval_minutes": 15, "tick_minutes": 5,
    "stock_target": 10, "candidate_ttl_hours": 72, "source_ttl_hours": 72,
    "memory_ttl_hours": 168, "seen_ttl_days": 7, "block_ttl_days": 30,
    "less_share_days": 7, "discussion_minutes": 30,
    "interest_capacity": None, "active_interest_capacity": None,
    "search_results": 20, "read_candidates": 10, "planning_interests": 10,
    "activity_timeout_seconds": 300, "search_timeout_seconds": 30,
    "read_timeout_seconds": 30, "model_timeout_seconds": 120,
    "search_response_bytes": 1024 * 1024, "read_response_bytes": 4 * 1024 * 1024,
    "model_response_bytes": 1024 * 1024,
    "browser_resource_bytes": 8 * 1024 * 1024,
    "platforms": {name: {"enabled": False, "allow_third_party": False} for name in PLATFORMS},
}
NULLABLE_LIMITS = frozenset({
    "chat_search_calls", "chat_search_results", "tool_round_limit",
    "observation_ttl_hours",
    "daily_queries", "daily_models", "daily_network", "daily_shares", "daily_memory_writes",
    "pool_capacity", "source_pool_bytes", "chat_material_limit", "chat_dependency_limit",
    "external_recall_limit", "stock_target", "interest_capacity", "active_interest_capacity",
    "read_candidates", "planning_interests",
})
NONNEGATIVE_TIMES = frozenset({"activity_interval_minutes", "share_interval_minutes"})
POSITIVE_SETTINGS = frozenset({
    "chat_search_timeout_seconds",
    "tick_minutes", "candidate_ttl_hours", "source_ttl_hours", "memory_ttl_hours",
    "seen_ttl_days", "block_ttl_days", "less_share_days", "discussion_minutes",
    "activity_timeout_seconds", "search_timeout_seconds", "read_timeout_seconds", "model_timeout_seconds",
})
POSITIVE_INTEGERS = frozenset({"search_results", "search_response_bytes", "read_response_bytes", "model_response_bytes", "browser_resource_bytes", "tieba_reply_limit", "tieba_text_limit"})
SCHEMA = """
CREATE TABLE IF NOT EXISTS discovery_config (
 id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS discovery_interests (
 id TEXT PRIMARY KEY, topic TEXT NOT NULL, summary TEXT NOT NULL, enabled INTEGER NOT NULL,
 external_allowed INTEGER NOT NULL, origin TEXT NOT NULL, version INTEGER NOT NULL,
 created_at REAL NOT NULL, last_selected REAL NOT NULL DEFAULT 0,
 share_after REAL NOT NULL DEFAULT 0, weight REAL NOT NULL DEFAULT 1,
 preferred_forums TEXT NOT NULL DEFAULT '[]');
CREATE TABLE IF NOT EXISTS discovery_runs (
 id TEXT PRIMARY KEY, epoch TEXT NOT NULL, revision INTEGER NOT NULL, day TEXT NOT NULL,
 state TEXT NOT NULL, phase TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '',
 interest_id TEXT, started_at REAL NOT NULL, finished_at REAL,
 queries INTEGER NOT NULL DEFAULT 0, cards INTEGER NOT NULL DEFAULT 0,
 diagnostic TEXT NOT NULL DEFAULT '{}', search_summary TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS activity_budget (
 day TEXT PRIMARY KEY, queries INTEGER NOT NULL DEFAULT 0, network INTEGER NOT NULL DEFAULT 0,
 models INTEGER NOT NULL DEFAULT 0, body_bytes INTEGER NOT NULL DEFAULT 0,
 shares INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS discovery_attempts (
 id TEXT PRIMARY KEY, run_id TEXT NOT NULL, day TEXT NOT NULL, op TEXT NOT NULL,
 reserved_bytes INTEGER NOT NULL, actual_bytes INTEGER, state TEXT NOT NULL,
 reason TEXT NOT NULL DEFAULT '', input_tokens INTEGER, output_tokens INTEGER, created_at REAL NOT NULL,
 diagnostic TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS discovery_cards (
 id TEXT PRIMARY KEY, packet_id TEXT NOT NULL, platform TEXT NOT NULL, interest_id TEXT,
 interest_version INTEGER, title TEXT NOT NULL, url TEXT NOT NULL,
 content_hash TEXT NOT NULL, coverage TEXT NOT NULL, summary TEXT NOT NULL,
 author_view TEXT NOT NULL, interpretation TEXT NOT NULL, uncertainty TEXT NOT NULL,
 question TEXT NOT NULL, created_at REAL NOT NULL, expires_at REAL NOT NULL,
 used_at REAL, offered_at REAL, disabled INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS discovery_cards_interest ON discovery_cards(interest_id,created_at);
CREATE TABLE IF NOT EXISTS discovery_seen (
 identity TEXT PRIMARY KEY, expires_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS discovery_blocks (
 identity TEXT PRIMARY KEY, platform TEXT NOT NULL, expires_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS discovery_tieba_policy (
 id INTEGER PRIMARY KEY CHECK(id=1), version TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS discovery_tieba_sources (
 packet_id TEXT PRIMARY KEY, forum_name TEXT NOT NULL, reply_count INTEGER NOT NULL,
 replies_requested INTEGER NOT NULL, policy_version TEXT NOT NULL);
"""


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _diagnostic(value):
    """Keep only fixed source metadata, safe counts or bounded API status."""
    if isinstance(value, str):
        if len(value) > 1024:
            return {}
        try:
            value = json.loads(value)
        except (ValueError, RecursionError):
            return {}
    if not isinstance(value, dict):
        return {}
    if value.get("kind") == "search_results":
        platform, provider = value.get("platform"), value.get("provider")
        if (not isinstance(platform, str) or platform not in PLATFORMS
                or not isinstance(provider, str) or provider not in _SEARCH_PROVIDERS
                or value.get("stage") != "search"
                or any(type(value.get(key)) is not int or not 0 <= value[key] <= 9007199254740991
                       for key in _SEARCH_COUNTS)):
            return {}
        if (value["received"] != sum(value[key] for key in ("accepted", "rejected", "duplicates", "unsupported"))
                or value["returned"] > value["accepted"]):
            return {}
        return {"kind": "search_results", "platform": platform, "provider": provider,
                "stage": "search", **{key: value[key] for key in _SEARCH_COUNTS}}
    if "kind" in value:
        return {}
    platform = value.get("platform", "bilibili")
    stages = _BILI_STAGES if platform == "bilibili" else {"search", "read", "forum_check"} if platform == "tieba" else ()
    if not isinstance(value.get("stage"), str) or value["stage"] not in stages:
        return {}
    result = {"stage": value["stage"]}
    if platform == "tieba":
        result["platform"] = platform
    if "api_code" in value:
        code = value["api_code"]
        result["api_code"] = code if type(code) is int and -(2**31) <= code < 2**31 else None
    if "verification_required" in value:
        flag = value["verification_required"]
        result["verification_required"] = flag if type(flag) is bool else None
    return result


def _search_summary(value):
    result = _diagnostic(value)
    return result if result.get("kind") == "search_results" else {}


def _bili_response_diagnostic(stage, data, maximum):
    """Observe an already received body; never persist body, voucher or message."""
    result = _diagnostic({"stage": stage})
    if not result:
        return result
    result.update(api_code=None, verification_required=None)
    try:
        encoded = data.get("body_base64")
        if not isinstance(encoded, str) or len(encoded) > ((maximum + 2) // 3) * 4:
            return result
        raw = base64.b64decode(encoded, validate=True)
        if len(raw) > maximum:
            return result

        def unique_object(pairs):
            value = {}
            for key, item in pairs:
                if key in value:
                    raise ValueError()
                value[key] = item
            return value

        envelope = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_object,
                              parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        if not isinstance(envelope, dict):
            return result
        code = envelope.get("code")
        result["api_code"] = code if type(code) is int and -(2**31) <= code < 2**31 else None
        signals = [container["v_voucher"] for container in (envelope, envelope.get("data"))
                   if isinstance(container, dict) and "v_voucher" in container]
        if all(value is None or isinstance(value, str) for value in signals):
            result["verification_required"] = any(isinstance(value, str) and bool(value) for value in signals)
    except (ValueError, UnicodeError, TypeError, RecursionError):
        pass
    return result


def _day():
    return datetime.now(DAY_ZONE).strftime("%Y-%m-%d")


def _identity(url):
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def _exceeds(used, amount, limit):
    return amount > 0 and limit is not None and used + amount > limit


def _limit_rows(rows, limit):
    return rows if limit is None else rows[:limit]


def _response_budget(settings):
    value = settings["daily_response_mib"]
    return None if value is None else int(value * 1024 * 1024)


def _third_party(platform, settings):
    return (platform != "wikipedia"
            and not (platform == "bilibili" and settings["bilibili_access"] in ("native", "browser"))
            and not (platform == "zhihu" and settings["zhihu_access"] == "browser")
            and not (platform == "tieba" and settings["tieba_access"] == "native"))


def _tieba_options(settings):
    return {"preferred_forums": list(settings.get("_tieba_preferred_forums", [])), "read_replies": settings["tieba_read_replies"],
            "read_nested": settings["tieba_read_nested"], "reply_limit": settings["tieba_reply_limit"],
            "search_sort": settings.get("_tieba_search_sort", "recent"),
            "published_after": settings.get("_tieba_published_after")}


def _tieba_scope_changed(previous, settings):
    return (previous["tieba_read_replies"] != settings["tieba_read_replies"]
            or previous["tieba_read_nested"] != settings["tieba_read_nested"]
            or previous["tieba_text_limit"] != settings["tieba_text_limit"]
            or previous["tieba_reply_limit"] != settings["tieba_reply_limit"])


def _preferred_forums(row):
    from .discovery_tieba import normalize_forums
    try:
        value = row["preferred_forums"]
        return normalize_forums(json.loads(value) if isinstance(value, str) else value)
    except (ValueError, TypeError, KeyError, IndexError):
        return []


def _forum_check_result(data, requested):
    from .discovery_tieba import normalize_forums
    if not isinstance(data, dict) or data.get("requested") != requested:
        raise _Stopped("worker_protocol")
    name, identity = data.get("forum_name"), data.get("forum_id")
    count, state = data.get("thread_count"), data.get("status")
    try:
        canonical = normalize_forums([name])
    except ValueError:
        raise _Stopped("worker_protocol") from None
    if (canonical != [name] or not isinstance(identity, str) or not re.fullmatch(r"[1-9][0-9]{0,31}", identity)
            or type(count) is not int or not 0 <= count <= 9007199254740991
            or not isinstance(state, str) or state not in {"ready", "empty", "mismatch"}
            or (name != requested) != (state == "mismatch")
            or state == "ready" and count == 0 or state == "empty" and count != 0):
        raise _Stopped("worker_protocol")
    return {"requested": requested, "forum_name": name, "forum_id": identity,
            "thread_count": count, "status": state, "checked_at": time.time()}


def _seen_key(platform, value, settings, *, content=False):
    if platform != "tieba" or settings["_tieba_policy_version"] == "legacy":
        return value if content else _identity(value)
    # A newly approved reading scope may revisit a URL; unrelated settings must
    # not silently reset dedup. Explicit source blocks remain outside this key.
    return "tieba:" + settings["_tieba_policy_version"] + (":content:" if content else ":url:") + (value if content else _identity(value))


def _validate_tieba_evidence(doc, settings):
    """Recheck the reader's bounded scope before interpretation and publication."""
    from .discovery_tieba import normalize_forums, timestamp
    forum, count = doc.get("tieba_forum"), doc.get("tieba_reply_count")
    nested = doc.get("tieba_nested_reply_count")
    published, basis = doc.get("tieba_published_at"), doc.get("tieba_published_at_basis")
    if (basis not in ("unknown", "first_floor_time", "thread_create_time")
            or (published is None) != (basis == "unknown")
            or published is not None and (type(published) is not int or timestamp(published) != published)):
        raise _Stopped("worker_protocol")
    if not isinstance(forum, str):
        raise _Stopped("worker_protocol")
    if forum:
        try:
            if normalize_forums([forum]) != [forum]:
                raise ValueError()
        except ValueError:
            raise _Stopped("worker_protocol") from None
    if (type(doc.get("tieba_page")) is not int or doc["tieba_page"] != 1
            or type(doc.get("tieba_replies_requested")) is not bool
            or doc["tieba_replies_requested"] != settings["tieba_read_replies"]
            or type(count) is not int or not 0 <= count <= settings["tieba_reply_limit"]
            or count > 0 and not settings["tieba_read_replies"]
            or type(nested) is not int or not 0 <= nested <= count
            or type(doc.get("tieba_nested_requested")) is not bool
            or doc["tieba_nested_requested"] != (settings["tieba_read_replies"] and settings["tieba_read_nested"])
            or nested > 0 and not doc["tieba_nested_requested"]
            or type(doc.get("tieba_pages_read")) is not int or not 1 <= doc["tieba_pages_read"] <= 9007199254740991
            or not settings["tieba_read_replies"] and doc["tieba_pages_read"] != 1
            or type(doc.get("tieba_nested_partial")) is not bool
            or not isinstance(doc.get("tieba_read_stop"), str)
            or doc["tieba_read_stop"] not in {"main_only", "end", "reply_limit", "text_limit", "pagination_unconfirmed", "repeated_page"}
            or not isinstance(doc.get("text"), str) or not 80 <= len(doc["text"]) <= settings["tieba_text_limit"]
            or doc.get("coverage") != ("forum_thread_excerpt" if count else "forum_main_post_excerpt")):
        raise _Stopped("worker_protocol")


def _string(value, maximum, *, empty=False):
    if (not isinstance(value, str) or len(value) > maximum
            or any(ord(c) < 32 and c not in "\n\t" or 0xD800 <= ord(c) <= 0xDFFF for c in value)):
        raise ChannelError(400, "invalid_request", "文字格式或长度无效")
    value = " ".join(value.split())
    if not value and not empty:
        raise ChannelError(400, "invalid_request", "请填写必要内容")
    return value


def _bool(value):
    if type(value) is not bool:
        raise ChannelError(400, "invalid_request", "开关必须为布尔值")
    return value


def _clock(value):
    if not isinstance(value, str) or not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", value):
        raise ChannelError(400, "invalid_request", "活动时间请使用24小时制时分")
    return value


class DiscoveryCoordinator:
    """The only discovery writer. Public access is through ChatService.discovery."""

    def __init__(self, service):
        self.service = service
        self._guard = threading.RLock()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._cancel = threading.Event()
        self._worker = None
        self._scheduler = None
        self._task = None
        self._tieba_forum_check = None
        self._pending = None
        self._note = None
        self._generation = 0
        self._unavailable = False
        self._orphans = []
        self._sharing_failed = False
        self._browser_results = {}
        self._chat_session = None
        self.bind_store()

    def bind_store(self):
        # Startup/reset only; caller holds the service lock on reset.
        self._generation += 1
        self._pending = self._note = None
        self._tieba_forum_check = None
        self._sharing_failed = False
        self._browser_results = {}
        store = self.service.store
        conn = store.conn
        conn.executescript(SCHEMA)
        store.migrate_interest_evidence()
        from .dialogue_search import migrate
        migrate(self)
        with conn:
            if conn.execute("SELECT 1 FROM discovery_tieba_policy WHERE id=1").fetchone() is None:
                conn.execute("INSERT INTO discovery_tieba_policy VALUES(1,'legacy')")
                # Before this migration the Tieba reader could only publish the
                # main post. Keep that proven reading scope; its forum was not
                # recorded and must not be invented for the source display.
                conn.execute("INSERT OR IGNORE INTO discovery_tieba_sources "
                             "SELECT id,'',0,0,'legacy' FROM source_packets WHERE source_id LIKE 'tieba:%'")
            for table in ("discovery_runs", "discovery_attempts"):
                if "diagnostic" not in {column[1] for column in conn.execute(f"PRAGMA table_info({table})")}:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN diagnostic TEXT NOT NULL DEFAULT '{{}}'")
            if "search_summary" not in {column[1] for column in conn.execute("PRAGMA table_info(discovery_runs)")}:
                conn.execute("ALTER TABLE discovery_runs ADD COLUMN search_summary TEXT NOT NULL DEFAULT '{}'")
            if "preferred_forums" not in {column[1] for column in conn.execute("PRAGMA table_info(discovery_interests)")}:
                conn.execute("ALTER TABLE discovery_interests ADD COLUMN preferred_forums TEXT NOT NULL DEFAULT '[]'")
            conn.execute("INSERT OR IGNORE INTO discovery_config VALUES(1,1,?)", (_json(DEFAULTS),))
            row = conn.execute("SELECT value FROM discovery_config WHERE id=1").fetchone()
            previous = json.loads(row["value"])
            merged = {**copy.deepcopy(DEFAULTS), **previous}
            merged.pop("provider_key", None)  # Legacy values have no verifiable provider binding.
            merged.pop("tieba_forums", None)  # Replaced by per-interest, non-exclusive preferences.
            previous_platforms = previous.get("platforms", {})
            if not isinstance(previous_platforms, dict):
                previous_platforms = {}
            merged["platforms"] = {}
            for name, policy in DEFAULTS["platforms"].items():
                saved = previous_platforms.get(name, {})
                saved = saved if isinstance(saved, dict) else {}
                merged["platforms"][name] = {
                    key: saved[key] if type(saved.get(key)) is bool else default
                    for key, default in policy.items()
                }
            if merged != previous:
                conn.execute("UPDATE discovery_config SET value=?,revision=revision+1 WHERE id=1", (_json(merged),))
            # Infer legacy setup once. Later platform and access changes can
            # alter the configuration without completing the wizard.
            if store.meta_get("discovery_settings_saved") is None:
                store.meta_set("discovery_settings_saved", time.time() if merged != DEFAULTS else 0)
            conn.execute("UPDATE discovery_runs SET state='interrupted',reason='process_interrupted',"
                         "finished_at=? WHERE state IN ('queued','running','draining')", (time.time(),))
            conn.execute("UPDATE discovery_attempts SET state='unknown',reason='process_interrupted' "
                         "WHERE state='reserved'")
        self.dataset_id = self.service._channels.epoch
        self._purge_locked()

    def start(self):
        self._scheduler = threading.Thread(target=self._schedule, name="alice-discovery-scheduler", daemon=True)
        self._scheduler.start()

    def foreground_started(self):
        # Called under the channel condition. Never take another lock or wait here.
        self._cancel.set()

    def _chat_search_allowed(self, scope, settings):
        return (settings["enabled"] and settings["chat_search_enabled"]
                and scope in {"gui", "qq-owner"}
                and (scope != "qq-owner" or (settings["share_in_qq"] and self.service._channels.enabled())))

    def prepare_chat_locked(self, request, token, on_progress=None):
        from .dialogue_search import SearchSession, search_declined
        _, settings = self._config_locked()
        if (not self._chat_search_allowed(request["channel_id"], settings)
                or search_declined(request["text"])):
            return None
        session = SearchSession(self, request, token, on_progress)
        with self._guard:
            self._chat_session = session
        return session

    def finish_chat(self, request_id):
        with self._guard:
            if self._chat_session and self._chat_session.request_id == request_id:
                self._chat_session.finished = True
                self._chat_session.results.clear()
                if self._chat_session.progress["phase"] == "idle":
                    self._chat_session.progress.update(phase="completed", status="not_used", message="本轮未联网查找")
                    self._chat_session.progress["sequence"] += 1

    def chat_control(self, payload, *, cancel=False):
        if (not isinstance(payload, dict) or set(payload) != {"request_id", "session_epoch"}
                or not isinstance(payload["request_id"], str) or not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", payload["request_id"])
                or payload["session_epoch"] != self.dataset_id):
            raise ChannelError(409, "session_changed", "请刷新后查看本轮检索")
        with self._guard:
            session = self._chat_session
            if session is not None and session.request_id == payload["request_id"]:
                if cancel and not session.finished:
                    session.cancel.set()
                progress = {**copy.deepcopy(session.progress), "stop_requested": session.cancel.is_set()}
            else:
                session = None
        if session is not None:
            if cancel:
                return {**progress, "items": []}
            with self.service._lock:
                if payload["session_epoch"] != self.dataset_id:
                    raise ChannelError(409, "session_changed", "会话已更换")
                return session.hydrate_locked(progress, evidence=False)
        with self.service._lock:
            if payload["session_epoch"] != self.dataset_id:
                raise ChannelError(409, "session_changed", "会话已更换")
            row = self.service.store.conn.execute(
                "SELECT result_json,state,reason FROM discovery_runs WHERE parent_request_id=? AND epoch=? ORDER BY started_at DESC LIMIT 1",
                (payload["request_id"], payload["session_epoch"])).fetchone()
            if row is None:
                raise ChannelError(404, "not_found", "未找到这条检索记录，请先同步聊天历史")
            result = json.loads(row["result_json"])
            return {**payload, **result, "items": [], "phase": "completed", "status": result.get("status", "interrupted"),
                    "message": result.get("message", "原检索已中断，没有自动重新搜索")}

    def _foreground_allowed(self, job):
        channels = self.service._channels
        if job.get("kind") == "chat_search":
            scope = job.get("scope")
            return (scope in {"gui", "qq-owner"} and channels.slot is job.get("owner_token") and channels.slot is not None
                    and channels.active_channel == scope and channels.active_request == job.get("parent_request_id"))
        return channels.slot is None

    def begin_shutdown(self):
        self._stop.set()
        self._cancel.set()
        with self._guard:
            if self._chat_session:
                self._chat_session.cancel.set()
        self._wake.set()

    def before_reset(self):
        self._generation += 1
        self._cancel.set()
        self._pending = self._note = None
        with self._guard:
            self._tieba_forum_check = None
        # An archived database has no live TTL collector. Remove bounded external
        # payloads before it leaves the active store, retaining ordinary history.
        with self.service.store.source_transaction():
            self.service.store.clear_source_material_for_archive()
            self.service.store.conn.execute("DELETE FROM discovery_tieba_sources")

    def shutdown(self):
        self.begin_shutdown()
        for thread in (self._scheduler, self._worker):
            if thread is not None and thread is not threading.current_thread():
                thread.join()
        for orphan in self._orphans:
            if orphan.poll() is None:
                orphan.kill()
                orphan.wait(timeout=5)

    def _unreaped(self, process):
        self._unavailable = True
        if process is not None and process not in self._orphans:
            self._orphans.append(process)

    def _config_locked(self):
        row = self.service.store.conn.execute("SELECT revision,value FROM discovery_config WHERE id=1").fetchone()
        value = json.loads(row["value"])
        return int(row["revision"]), value

    def _write_config_locked(self, revision, value):
        with self.service.store.source_transaction():
            changed = self.service.store.conn.execute(
                "UPDATE discovery_config SET revision=revision+1,value=? WHERE id=1 AND revision=?",
                (_json(value), revision)).rowcount
            if changed != 1:
                raise ChannelError(409, "revision_conflict", "设置已更新，请重新载入")

    def _tieba_policy_version_locked(self):
        return self.service.store.conn.execute("SELECT version FROM discovery_tieba_policy WHERE id=1").fetchone()[0]

    def _restrict_tieba_sources_locked(self, settings):
        """Revoke out-of-scope evidence, including retired cards' dependencies."""
        conn = self.service.store.conn
        packets = []
        for row in conn.execute(
                "SELECT p.id,p.payload_json,m.forum_name,m.reply_count,m.replies_requested FROM source_packets p "
                "LEFT JOIN discovery_tieba_sources m ON m.packet_id=p.id "
                "WHERE p.source_id LIKE 'tieba:%' AND p.revoked=0").fetchall():
            # A missing record cannot establish the previously read scope.
            count = row["reply_count"]
            policy = json.loads(row["payload_json"]).get("reading_policy") or {}
            nested = policy.get("tieba_nested_reply_count", 0)
            valid = (type(count) is int and count >= 0 and row["replies_requested"] in (0, 1)
                     and (count == 0 or row["replies_requested"] == 1)
                     and type(nested) is int and 0 <= nested <= count
                     and (nested == 0 or settings["tieba_read_nested"])
                     and (count == 0 or settings["tieba_read_replies"] and count <= settings["tieba_reply_limit"]))
            if not valid:
                packets.append(row["id"])
        if packets:
            self.service.store.revoke_source_packets(packets)
            conn.executemany("DELETE FROM discovery_cards WHERE packet_id=?", [(pid,) for pid in packets])
            conn.executemany("DELETE FROM discovery_tieba_sources WHERE packet_id=?", [(pid,) for pid in packets])
        return packets

    def _bump_revision_locked(self):
        if self._chat_session:
            self._chat_session.cancel.set()
        self.service.store.conn.execute("UPDATE discovery_config SET revision=revision+1 WHERE id=1")

    def _budget_locked(self, day=None):
        row = self.service.store.conn.execute("SELECT * FROM activity_budget WHERE day=?", (day or _day(),)).fetchone()
        return dict(row) if row else {"day": day or _day(), "queries": 0, "network": 0, "models": 0,
                                     "body_bytes": 0, "shares": 0}

    def _available(self, platform, settings):
        if platform in ("bilibili", "zhihu") and settings[platform + "_access"] == "browser":
            from .browser_transport import availability
            return availability()
        if platform == "bilibili" and settings["bilibili_access"] == "native" and not addon_available("bilibili"):
            return False, "B站只读组件缺失，请保留仓库中的addons/bilibili目录"
        if not _third_party(platform, settings):
            return True, ""
        if settings["provider_kind"] == "native" and platform != "web":
            return False, "需配置并允许第三方搜索通道"
        if settings["provider_kind"] == "brave" and not settings["brave_key"]:
            return False, "请填写并保存 Brave 搜索密钥"
        if settings["provider_kind"] == "searxng" and not settings["provider_endpoint"]:
            return False, "请配置固定搜索服务地址"
        return True, ""

    def _purge_locked(self):
        conn, now = self.service.store.conn, time.time()
        self.service.store.purge_source_packets(now)
        expired = [r[0] for r in conn.execute("SELECT packet_id FROM discovery_cards WHERE expires_at<=?", (now,))]
        # Candidate retirement does not revoke a still-authorized formal memory.
        with conn:
            conn.execute("DELETE FROM discovery_cards WHERE expires_at<=?", (now,))
            conn.execute("DELETE FROM discovery_seen WHERE expires_at<=?", (now,))
            conn.execute("DELETE FROM discovery_blocks WHERE expires_at<=?", (now,))
            conn.execute("DELETE FROM discovery_attempts WHERE created_at<? AND state!='reserved'", (now - 7 * 86400,))
            conn.execute("DELETE FROM discovery_attempts WHERE id IN (SELECT id FROM discovery_attempts "
                         "WHERE state!='reserved' ORDER BY created_at DESC LIMIT -1 OFFSET 1000)")
            conn.execute("DELETE FROM discovery_tieba_sources WHERE packet_id IN "
                         "(SELECT id FROM source_packets WHERE revoked=1)")
            conn.execute("DELETE FROM discovery_runs WHERE started_at<? AND state NOT IN ('queued','running','draining')",
                         (now - 7 * 86400,))
        if self._pending and self._pending["expires_at"] <= now:
            self._pending = None
        if self._note and (self._note["expires_at"] <= now or self._note["packet_id"] in expired):
            self._note = None

    def snapshot_locked(self):
        revision, settings = self._config_locked()
        self.service.store.decay_interests(self.service.cfg.memory.interest_decay_days)
        now = time.time()
        public = {k: copy.deepcopy(v) for k, v in settings.items()
                  if k not in ("brave_key", "provider_key", "platforms")}
        public.update(revision=revision, brave_key_present=bool(settings["brave_key"]),
                      provider_key_present=settings["provider_kind"] == "brave" and bool(settings["brave_key"]),
                      settings_archived=self.service.store.meta_get_time("discovery_settings_saved") > 0)
        learning_preview = self.service.store.learning_preview(self.service.cfg)
        platforms = []
        for identity, label in PLATFORMS.items():
            available, reason = self._available(identity, settings)
            details = {
                "web": ("全网搜索结果中的公开网页；读取HTML正文或纯文本，不执行脚本、不使用登录态", ["网页正文节选", "纯文本节选"]),
                "wikipedia": ("公开百科条目", ["条目正文节选"]),
                "bilibili": ("公开视频信息与文字简介，不含视频理解", ["视频信息", "视频简介"]),
                "zhihu": ("与链接身份对应的公开问题、回答或文章正文", ["问题描述", "指定回答", "文章正文"]),
                "tieba": ("主帖及获准的分页回复文字；楼中楼需另行开启，仅取得接口附带节选；不读图片或视频" if settings["tieba_read_replies"]
                          else "帖子主楼的公开文字，不采集回复或楼中楼",
                          ["主帖文字节选", "分页回复文字节选"] + (["楼中楼文字节选"] if settings["tieba_read_nested"] else []) if settings["tieba_read_replies"] else ["主帖文字节选"]),
            }[identity]
            method = ({"wikipedia": "平台公开接口", "bilibili": "B站站内只读接口", "tieba": "贴吧公开搜索入口"}.get(identity, "")
                      if not _third_party(identity, settings) else settings["provider_kind"])
            if identity == "web" and settings["provider_kind"] == "native":
                method = "bing_rss"
            if identity in ("bilibili", "zhihu") and settings[identity + "_access"] == "browser":
                method = "独立本机浏览器"
                if identity == "zhihu":
                    details = ("独立会话搜索并读取指定回答；文章、问题描述尚未开放", ["指定回答正文节选"])
            platforms.append({"id": identity, "name": label, "available": available, "reason": reason,
                              **settings["platforms"][identity],
                              "description": details[0], "discovery_method": method,
                              "readable_types": details[1],
                              "requires_third_party": _third_party(identity, settings),
                              "third_party_notice": "" if not _third_party(identity, settings) else "搜索词将交给所选第三方服务"})
            if identity in ("bilibili", "zhihu"):
                from .browser_transport import availability
                installed, message = availability()
                with self._guard:
                    latest = self._browser_results.get(identity)
                platforms[-1]["browser"] = {"installed": installed, "message": message,
                    "last_result": latest, "last_message": _reason(latest) if latest else "尚未确认当前浏览器会话的访问状态"}
            if identity == "tieba":
                platforms[-1]["scope_notice"] = "按各兴趣的优先吧排序当前关键词结果页；没有合适材料时可读其它吧，不遍历吧内列表"
                platforms[-1]["scope_notice"] += (f"；按楼层分页，回复合计最多 {settings['tieba_reply_limit']} 条；" + ("包含接口附带的楼中楼节选" if settings["tieba_read_nested"] else "不读楼中楼")
                                                   if settings["tieba_read_replies"] else "；仅主帖文字，不读取回复")
        interests = []
        for row in self.service.store.conn.execute("SELECT * FROM discovery_interests ORDER BY created_at DESC"):
            item = dict(row)
            if 'gui' not in json.loads(item.get('scope_json', '["gui"]')):
                continue
            item['basis_user_turn_ids'] = json.loads(item['basis_user_turn_ids'])
            item['evidence_quotes'] = [r[0] for r in self.service.store.conn.execute(
                "SELECT quote FROM interest_evidence WHERE interest_id=? AND scope='gui' ORDER BY created_at DESC LIMIT 3", (item['id'],))]
            item["enabled"], item["external_allowed"] = bool(item["enabled"]), bool(item["external_allowed"])
            item["preferred_forums"] = _preferred_forums(item)
            interests.append(item)
        cards = []
        for row in self.service.store.conn.execute(
                "SELECT c.*,i.topic AS interest_topic,i.summary AS interest_summary,i.enabled AS interest_enabled,"
                "i.version AS live_version,i.external_allowed FROM discovery_cards c LEFT JOIN discovery_interests i ON i.id=c.interest_id "
                "WHERE c.expires_at>? ORDER BY c.created_at DESC", (now,)):
            item = dict(row)
            if self.service.store.get_source_packet(item["packet_id"], scope="gui") is None:
                continue  # Expired/revoked payloads must not leak through card copies.
            unavailable = ("", "")
            if item.get("trigger_kind") == "chat":
                item["interest_topic"] = "本轮查找"
            elif item["live_version"] is None:
                unavailable = ("interest_removed", "关联兴趣已移除，这条资料已暂停讨论")
            elif not item["interest_enabled"]:
                unavailable = ("interest_disabled", "关联兴趣已停用，这条资料已暂停讨论")
            elif not item["external_allowed"]:
                unavailable = ("interest_permission_required", "关联兴趣未获准用于外部搜索，这条资料已暂停讨论")
            elif item["interest_version"] != item["live_version"]:
                unavailable = ("interest_changed", "关联兴趣已修改，这条资料基于旧兴趣生成，已暂停讨论")
            if not unavailable[0] and not settings["platforms"][item["platform"]]["enabled"]:
                unavailable = ("platform_disabled", "来源平台已停用，这条资料已暂停讨论")
            elif not unavailable[0] and item["disabled"]:
                unavailable = ("card_disabled", "这条资料已停用，不能继续用于讨论")
            valid = not unavailable[0]
            item.update(can_discuss=valid, unavailable_reason=unavailable[0], discuss_unavailable_reason=unavailable[1],
                        reason="关联兴趣：" + (item["interest_topic"] or "已移除"),
                        share_state="已暂停" if not valid else "已提供给对话（是否采用未推断）" if item["used_at"] else "待讨论")
            item["memory_links"] = self.service.store.observation_links(item["packet_id"])
            for key in ("interest_summary", "interest_enabled", "live_version"):
                item.pop(key, None)
            cards.append(item)
        budget = self._budget_locked()
        with self._guard:
            task = copy.deepcopy(self._task)
            forum_check = copy.deepcopy(self._tieba_forum_check)
        if task is None:
            row = self.service.store.conn.execute("SELECT * FROM discovery_runs ORDER BY started_at DESC LIMIT 1").fetchone()
            task = dict(row) if row else None
        if task is not None:
            task["reason_message"] = _reason(task.get("reason", ""))
            task["diagnostic"] = _diagnostic(task.get("diagnostic"))
            task["search_summary"] = _search_summary(task.get("search_summary"))
        from .autonomy_policy import search_settings, search_quota_active
        effective = search_settings(settings)
        return {"status": "ok", "dataset_id": self.dataset_id, "settings": public, "learning_preview": learning_preview,
                "search_quota_testing": search_quota_active(settings), "platforms": platforms,
                "autonomous_scheduler": "lifecycle-v1",
                "tieba_forum_check": forum_check,
                "sharing_warning": "资料共享记录异常，已暂停本次会话的资料注入；普通聊天可继续，重新载入服务后再检查。" if self._sharing_failed else "",
                "interests": interests, "task": task, "cards": cards,
                "discuss_card_id": self._pending["card_id"] if self._pending and self._pending["expires_at"] > now else None,
                "discuss_expires_at": self._pending["expires_at"] if self._pending and self._pending["expires_at"] > now else None,
                "budget": {"day": budget["day"], "queries_used": budget["queries"], "queries_limit": effective["daily_queries"],
                           "models_used": budget["models"], "models_limit": effective["daily_models"],
                           "network_used": budget["network"], "network_limit": effective["daily_network"],
                           "bytes_used": budget["body_bytes"], "bytes_limit": _response_budget(effective),
                           "shares_used": budget["shares"], "shares_limit": settings["daily_shares"],
                           "memory_writes_used": self._memory_writes_locked(), "memory_writes_limit": settings["daily_memory_writes"]},
                "storage": {"used_bytes": self.service.store.source_usage_bytes(), "limit_bytes": effective["source_pool_bytes"]},
                "note": copy.deepcopy(self._note)}

    def _memory_writes_locked(self):
        row = self.service.store.conn.execute("SELECT count FROM source_memory_writes WHERE day=?", (_day(),)).fetchone()
        return row[0] if row else 0

    def access(self, action="status", payload=None):
        payload = payload or {}
        if action != "status" and self._stop.is_set():
            raise ChannelError(503, "draining", "内容发现正在关闭")
        if not self.service._lock.acquire(blocking=False):
            raise ChannelError(503, "busy", "Alice正在处理对话，请稍后查看内容发现")
        try:
            self._purge_locked()
            if action == "status":
                return self.snapshot_locked()
            revision, settings = self._config_locked()
            if payload.get("dataset_id") != self.dataset_id:
                raise ChannelError(409, "session_changed", "会话已更新，请重新载入")
            if type(payload.get("revision")) is not int or payload["revision"] != revision:
                raise ChannelError(409, "revision_conflict", "设置已更新，请重新载入后再操作")
            allowed = {
                "settings": {"settings", "configured"}, "platform": {"id", "enabled", "allow_third_party"},
                "interest": {"id", "topic", "summary", "enabled", "external_allowed", "preferred_forums"},
                "interest_delete": {"id"}, "run": {"interest_id"}, "cancel": {"task_id"},
                "feedback": {"card_id", "value"}, "delete": {"card_id"}, "clear": {"platform_id"},
                "discuss": {"card_id"}, "remember_card": {"card_id"}, "undiscuss": set(), "tieba_forum_check": {"forums"}, "browser_open": {"platform"},
                "remember_user": {"turn_id"},
            }
            if action not in allowed or set(payload) - allowed[action] - {"action", "dataset_id", "revision"}:
                raise ChannelError(400, "invalid_request", "内容发现操作或字段无效")
            if action == "settings":
                # 只有设置向导的最后一步会带 configured；单独拨动开关、平台卡片上的
                # 即时改动不带，免得用户还没走过首启引导就被判定为"已配置"。
                configured = payload.get("configured")
                if "configured" in payload and configured is not True:
                    raise ChannelError(400, "invalid_request", "设置完成标记无效")
                self._save_settings_locked(revision, settings, payload.get("settings"), configured=configured is True)
            elif action == "platform":
                identity = payload.get("id")
                if identity not in PLATFORMS:
                    raise ChannelError(400, "invalid_platform", "平台无效")
                enabled = _bool(payload.get("enabled"))
                third = _bool(payload.get("allow_third_party", False))
                if enabled and (not self._available(identity, settings)[0] or _third_party(identity, settings) and not third):
                    raise ChannelError(409, "platform_unavailable", "请先配置并允许该平台的发现通道")
                self._cancel.set()
                if self._chat_session:
                    self._chat_session.cancel.set()
                settings["platforms"][identity] = {"enabled": enabled, "allow_third_party": third}
                self._write_config_locked(revision, settings)
                if not enabled:
                    self._clear_pending_locked(platform=identity)
            elif action == "interest":
                self._save_interest_locked(payload)
            elif action == "interest_delete":
                self._remove_interest_locked(_string(payload.get("id"), 32))
            elif action == "run":
                self._launch_manual_locked(settings, revision, interest_id=payload.get("interest_id"))
            elif action == "tieba_forum_check":
                self._launch_tieba_check_locked(settings, revision, payload.get("forums"))
            elif action == "browser_open":
                from .browser_transport import BrowserError, open_browser
                if self._worker and self._worker.is_alive():
                    raise ChannelError(409, "busy", "请先停止当前寻找，再打开独立浏览器")
                try:
                    open_browser(user_root(), payload.get("platform"))
                except (BrowserError, OSError):
                    raise ChannelError(409, "browser_missing", "无法打开该平台的独立 Edge 窗口") from None
            elif action == "cancel":
                with self._guard:
                    if self._task and payload.get("task_id") not in (None, self._task["id"]):
                        raise ChannelError(409, "task_changed", "活动已变化，请刷新")
                self._cancel.set()
            elif action == "feedback":
                self._feedback_locked(payload)
            elif action in ("delete", "clear"):
                self._delete_locked(payload, action)
            elif action == "discuss":
                card = self._valid_card_locked(payload.get("card_id"), "gui", settings)
                if card is None:
                    raise ChannelError(409, "material_unavailable", "资料已过期或已暂停")
                self._pending = {"card_id": card["id"], "expires_at": min(time.time() + settings["discussion_minutes"] * 60, card["expires_at"])}
            elif action == "remember_card":
                card = self._valid_card_locked(payload.get("card_id"), "gui", settings)
                if card is None:
                    raise ChannelError(409, "material_unavailable", "资料已失效")
                packet = self.service.store.get_source_packet(card["packet_id"], scope="gui")
                try:
                    result = self.service.store.save_learning(
                        {'keep':True,'title':card['title'][:24],
                         'content':'读到《'+card['title'][:80]+'》中的节选：'+packet['evidence'],
                         'value_tier':'normal','reason':'用户在资料卡明确选择保存',
                         'uncertainty':packet.get('uncertainty') or '节选与来源说法尚未独立核实'},
                        {'scope':'gui','packet_ids':[packet['id']],'request_id':card['id']},purpose='user_save')
                    mid = result.get('memory_id')
                except ValueError:
                    mid = None
                if mid is None:
                    raise ChannelError(409, "memory_not_allowed", "请开启择要保存见闻，并确认来源与写入额度仍可用")
            elif action == "remember_user":
                memory_id = self.service.store.remember_user_turn(payload.get("turn_id"), scope="gui")
                return {**self.snapshot_locked(), "remembered_memory_id": memory_id}
            elif action == "undiscuss":
                self._pending = None
            if action not in ("tieba_forum_check", "browser_open"):
                self._wake.set()
            return self.snapshot_locked()
        finally:
            self.service._lock.release()

    def _save_settings_locked(self, revision, previous, changes, *, configured=False):
        permitted = set(DEFAULTS) - {"platforms", "brave_key"} | {"provider_key", "clear_provider_key"}
        if not isinstance(changes, dict) or set(changes) - permitted:
            raise ChannelError(400, "invalid_request", "设置字段无效")
        settings = copy.deepcopy(previous)
        for key, value in changes.items():
            if key in {"provider_key", "clear_provider_key"}:
                continue  # These fields apply to the selected provider after validation.
            if key in {"search_quota_testing", "enabled", "background_enabled", "chat_search_enabled", "remember_observations", "allow_paid", "allow_chat_interests", "share_in_qq", "save_memories", "tieba_read_replies", "tieba_read_nested"}:
                settings[key] = _bool(value)
            elif key in ("window_start", "window_end"):
                settings[key] = _clock(value)
            elif key in NULLABLE_LIMITS:
                if value is not None and (type(value) is not int or not 0 <= value <= 9007199254740991):
                    raise ChannelError(400, "invalid_request", "额度请填非负整数，或选择不限")
                settings[key] = value
            elif key == "daily_response_mib":
                if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 9007199254740991 / 1048576):
                    raise ChannelError(400, "invalid_request", "每日响应量请填非负MiB数，或选择不限")
                settings[key] = value
            elif key in POSITIVE_SETTINGS | NONNEGATIVE_TIMES | POSITIVE_INTEGERS:
                if (type(value) not in (int, float) or not math.isfinite(value) or value < 0
                        or key not in NONNEGATIVE_TIMES and value == 0
                        or key in POSITIVE_INTEGERS and type(value) is not int
                        or key == "tieba_text_limit" and value < 80
                        or value > 9007199254740991):
                    raise ChannelError(400, "invalid_request", "此配置需要有效的正数；活动和分享间隔可为0")
                settings[key] = value
            elif key in ("bilibili_access", "zhihu_access", "tieba_access"):
                options = {"bilibili_access": ("native", "third_party", "browser"),
                           "zhihu_access": ("third_party", "browser"), "tieba_access": ("native", "third_party")}
                if value not in options[key]:
                    raise ChannelError(400, "invalid_request", "平台访问方式无效")
                settings[key] = value
            elif key == "network_mode":
                if value not in ("system", "direct"):
                    raise ChannelError(400, "invalid_request", "网络连接方式无效")
                settings[key] = value
            elif key == "provider_kind":
                if value not in ("native", "bing_rss", "brave", "searxng"):
                    raise ChannelError(400, "invalid_request", "搜索通道无效")
                settings[key] = value
            elif key == "provider_endpoint":
                value = _string(value, 1024, empty=True).rstrip("/")
                if value:
                    try:
                        parsed = urlsplit(value)
                        valid = (parsed.scheme == "https" or parsed.scheme == "http" and
                                 parsed.hostname in ("localhost", "127.0.0.1", "::1"))
                        valid = (valid and parsed.hostname and not parsed.username and not parsed.password
                                 and not parsed.query and not parsed.fragment and parsed.path in ("", "/search")
                                 and parsed.port != 0 and not any(c.isspace() for c in value) and "\\" not in value)
                        if parsed.hostname not in ("localhost", "127.0.0.1", "::1") and parsed.port not in (None, 443):
                            valid = False
                    except ValueError:
                        valid = False
                    if not valid:
                        raise ChannelError(400, "invalid_request", "搜索服务地址须为HTTPS或明确的本机回环HTTP搜索端点")
                    if not parsed.path:
                        value += "/search"
                settings[key] = value
            elif key == "provider_engines":
                value = _string(value, 8192)
                engines = [name.strip() for name in value.split(",")]
                if not engines or any(not re.fullmatch(r"[a-zA-Z0-9_-]{1,40}", name) for name in engines):
                    raise ChannelError(400, "invalid_request", "请指定有效引擎名称，用英文逗号分隔")
                settings[key] = ",".join(dict.fromkeys(engines))
        key = _string(changes.get("provider_key", ""), 512, empty=True)
        clear_key = _bool(changes.get("clear_provider_key", False))
        if any(c.isspace() for c in key):
            raise ChannelError(400, "invalid_request", "搜索密钥不能包含空白")
        if (key or clear_key) and settings["provider_kind"] != "brave":
            raise ChannelError(400, "invalid_request", "请先选择 Brave 搜索服务再修改密钥")
        if key and clear_key:
            raise ChannelError(400, "invalid_request", "填写新密钥和移除密钥只能选择一项")
        if key or clear_key:
            settings["brave_key"] = key
        # Thoughts settings own autonomous model permission. Discovery allow_paid
        # and daily_models still govern the explicit discovery worker.
        self._cancel.set()
        if self._chat_session:
            self._chat_session.cancel.set()
        self._pending = self._note = None
        revoked_packets = []
        scope_changed = _tieba_scope_changed(previous, settings)
        qq_restricted = previous["share_in_qq"] and not settings["share_in_qq"]
        with self.service.store.source_transaction():
            self._write_config_locked(revision, settings)
            if configured:
                self.service.store.meta_set("discovery_settings_saved", time.time())
            if scope_changed:
                self.service.store.conn.execute("UPDATE discovery_tieba_policy SET version=? WHERE id=1", (uuid.uuid4().hex,))
                revoked_packets = self._restrict_tieba_sources_locked(settings)
                self.service.store.restrict_observation_tieba(settings)
                # The new namespace also permits revisiting a policy that was
                # explicitly left and later re-selected. Blocks are unaffected.
                self.service.store.conn.execute("DELETE FROM discovery_seen WHERE identity LIKE 'tieba:%'")
            if qq_restricted:
                self.service.store.restrict_source_scope("qq-owner")
            if previous["save_memories"] and not settings["save_memories"]:
                self.service.store.restrict_source_memory()
        if qq_restricted or revoked_packets:
            self.service._channels.invalidate_source_cache()

    def _clear_pending_locked(self, *, platform=None, interest_id=None, packet_ids=()):
        def matches(card_id):
            row = self.service.store.conn.execute("SELECT * FROM discovery_cards WHERE id=?", (card_id,)).fetchone()
            return row is None or (platform and row["platform"] == platform) or (interest_id and row["interest_id"] == interest_id) or row["packet_id"] in packet_ids
        if self._pending and matches(self._pending["card_id"]):
            self._pending = None
        if self._note and matches(self._note["card_id"]):
            self._note = None

    def _save_interest_locked(self, payload):
        conn = self.service.store.conn
        revision, settings = self._config_locked()
        topic = _string(payload.get("topic"), 80)
        summary = _string(payload.get("summary", topic), 240)
        enabled, allowed = _bool(payload.get("enabled", True)), _bool(payload.get("external_allowed", False))
        identity = payload.get("id")
        old = None
        if identity is not None:
            identity = _string(identity, 32)
            old = conn.execute("SELECT * FROM discovery_interests WHERE id=?", (identity,)).fetchone()
            if old is None:
                raise ChannelError(404, "not_found", "该兴趣已不存在")
        elif _exceeds(conn.execute("SELECT COUNT(*) FROM discovery_interests").fetchone()[0], 1, settings["interest_capacity"]):
            raise ChannelError(409, "interest_capacity", "已达到你设置的兴趣容量，可在设置中调整")
        from .discovery_tieba import normalize_forums
        previous_forums = _preferred_forums(old) if old is not None else []
        try:
            preferred = normalize_forums(payload.get("preferred_forums", previous_forums))
        except ValueError:
            raise ChannelError(400, "invalid_request", "优先吧须为有效吧名列表") from None
        self._require_forum_checks_locked(preferred, previous_forums, revision)
        if (old is not None and topic == old["topic"] and summary == old["summary"]
                and enabled == bool(old["enabled"]) and allowed == bool(old["external_allowed"])):
            if preferred != previous_forums:
                # A soft ranking preference changes future work, not the
                # permission or interest version of already collected material.
                self._cancel.set()
                with conn:
                    conn.execute("UPDATE discovery_interests SET preferred_forums=? WHERE id=?", (_json(preferred), identity))
                    self._bump_revision_locked()
            return
        if enabled and allowed and _exceeds(conn.execute(
                "SELECT COUNT(*) FROM discovery_interests WHERE enabled=1 AND external_allowed=1 AND id!=?", (identity or "",)).fetchone()[0], 1, settings["active_interest_capacity"]):
            raise ChannelError(409, "interest_capacity", "已达到你设置的启用兴趣容量")
        identity = identity or uuid.uuid4().hex
        self._cancel.set()
        with conn:
            conn.execute("INSERT INTO discovery_interests(id,topic,summary,enabled,external_allowed,origin,version,created_at,preferred_forums) "
                         "VALUES(?,?,?,?,?,'manual',1,?,?) ON CONFLICT(id) DO UPDATE SET topic=excluded.topic,summary=excluded.summary,"
                         "enabled=excluded.enabled,external_allowed=excluded.external_allowed,preferred_forums=excluded.preferred_forums,origin='manual',"
                         "version=discovery_interests.version+1",
                         (identity, topic, summary, int(enabled), int(allowed), time.time(), _json(preferred)))
            conn.execute("UPDATE discovery_cards SET disabled=1 WHERE interest_id=?", (identity,))
            self._bump_revision_locked()
        self._clear_pending_locked(interest_id=identity)

    def _require_forum_checks_locked(self, preferred, previous, revision):
        added = set(preferred) - set(previous)
        if not added:
            return
        with self._guard:
            check = self._tieba_forum_check
            ready = ({row["requested"] for row in check["results"]
                      if row["status"] == "ready" and row["requested"] == row["forum_name"]}
                     if check and check["state"] == "completed" and check["revision"] == revision else set())
        if not added.issubset(ready):
            raise ChannelError(409, "tieba_forum_check_required", "新增优先吧须先检测并确认有公开帖子；请检测当前吧名后再保存兴趣")

    def _remove_interest_locked(self, identity):
        self._cancel.set()
        with self.service.store.conn:
            self.service.store.conn.execute("UPDATE discovery_cards SET disabled=1 WHERE interest_id=?", (identity,))
            self.service.store.conn.execute("DELETE FROM discovery_interests WHERE id=?", (identity,))
            self.service.store.conn.execute('DELETE FROM interest_evidence WHERE interest_id=?', (identity,))
            self._bump_revision_locked()
        self._clear_pending_locked(interest_id=identity)

    def _feedback_locked(self, payload):
        _, settings = self._config_locked()
        value = payload.get("value")
        if value not in ("more", "less", "stop_interest", "stop_source"):
            raise ChannelError(400, "invalid_request", "反馈选项无效")
        row = self.service.store.conn.execute("SELECT * FROM discovery_cards WHERE id=?", (payload.get("card_id"),)).fetchone()
        if row is None:
            raise ChannelError(404, "not_found", "资料已不存在")
        if row["interest_id"] is None and value in {"more", "less", "stop_interest"}:
            raise ChannelError(400, "interest_required", "本轮查找没有绑定长期兴趣")
        conn = self.service.store.conn
        self._cancel.set()
        with conn:
            if value == "more":
                conn.execute("UPDATE discovery_interests SET weight=MIN(2,weight+0.25),share_after=0 WHERE id=?", (row["interest_id"],))
            elif value == "less":
                conn.execute("UPDATE discovery_interests SET share_after=? WHERE id=?", (time.time() + settings["less_share_days"] * 86400, row["interest_id"]))
            elif value == "stop_interest":
                conn.execute("UPDATE discovery_interests SET enabled=0,version=version+1 WHERE id=?", (row["interest_id"],))
                conn.execute("UPDATE discovery_cards SET disabled=1 WHERE interest_id=?", (row["interest_id"],))
            else:
                conn.execute("INSERT OR REPLACE INTO discovery_blocks VALUES(?,?,?)", (_identity(row["url"]), row["platform"], time.time() + settings["block_ttl_days"] * 86400))
                conn.execute("UPDATE discovery_cards SET disabled=1 WHERE url=?", (row["url"],))
            self._bump_revision_locked()
        if value in ("less", "stop_interest"):
            self._clear_pending_locked(interest_id=row["interest_id"])
        elif value == "stop_source":
            self._clear_pending_locked(packet_ids=[row["packet_id"]])

    def _delete_locked(self, payload, action):
        conn = self.service.store.conn
        if action == "clear":
            platform = payload.get("platform_id")
            if platform not in PLATFORMS:
                raise ChannelError(400, "invalid_platform", "平台无效")
            # Includes packets whose 48-hour candidate already retired.
            packets = [r[0] for r in conn.execute("SELECT id FROM source_packets WHERE source_id LIKE ?", (platform + ":%",))]
            rows = conn.execute("SELECT id FROM discovery_cards WHERE platform=?", (platform,)).fetchall()
        else:
            row = conn.execute("SELECT id,packet_id FROM discovery_cards WHERE id=?", (payload.get("card_id"),)).fetchone()
            if row is None:
                raise ChannelError(404, "not_found", "资料已不存在")
            packets, rows = [row["packet_id"]], [row]
        self._cancel.set()
        if self._chat_session:
            self._chat_session.cancel.set()
        self.service.store.revoke_source_packets(packets)
        with conn:
            conn.executemany("DELETE FROM discovery_cards WHERE id=?", [(r["id"],) for r in rows])
            conn.executemany("DELETE FROM discovery_tieba_sources WHERE packet_id=?", [(pid,) for pid in packets])
            self._bump_revision_locked()
        self._clear_pending_locked(packet_ids=packets)
        self.service._channels.invalidate_source_cache()

    def _valid_card_locked(self, identity, scope, settings):
        if not isinstance(identity, str):
            return None
        row = self.service.store.conn.execute(
            "SELECT c.*,i.enabled AS ie,i.external_allowed AS ia,i.version AS iv,i.topic,i.summary AS interest_summary,"
            "i.share_after FROM discovery_cards c LEFT JOIN discovery_interests i ON i.id=c.interest_id WHERE c.id=?", (identity,)).fetchone()
        if (row is None or row["disabled"] or row["expires_at"] <= time.time()
                or row["trigger_kind"] != "chat" and (not row["ie"] or not row["ia"] or row["interest_version"] != row["iv"])
                or not settings["platforms"][row["platform"]]["enabled"]
                or scope == "qq-owner" and not settings["share_in_qq"]
                or self.service.store.get_source_packet(row["packet_id"], scope=scope) is None):
            return None
        item = dict(row)
        if item["trigger_kind"] == "chat":
            item.update(topic="本轮查找", interest_summary="", share_after=0)
        return item

    def select_for_chat_locked(self, request):
        """No extra model call. Explicit selection or conservative topic overlap."""
        if self._sharing_failed:
            return [], False
        self._purge_locked()
        _, settings = self._config_locked()
        scope, text = request["channel_id"], request["text"]
        explicit = any(phrase in text for phrase in ("最近看了什么", "找到什么", "最近有什么发现", "有什么新资料",
                                                     "前几天读到", "回顾资料", "之前看过的", "之前读过的"))
        if scope not in ("gui", "qq-owner"):
            return [], False
        if scope == "gui" and self._pending and self._pending["expires_at"] > time.time():
            card = self._valid_card_locked(self._pending["card_id"], scope, settings)
            self._pending = None
            if card and settings["chat_material_limit"] != 0 and settings["chat_dependency_limit"] != 0:
                return [card["packet_id"]], True
        if (not settings["enabled"] or self._unavailable or _exceeds(self._budget_locked()["shares"], 1, settings["daily_shares"])) and not explicit:
            return [], False
        recent = self.service.store.conn.execute("SELECT MAX(offered_at) FROM discovery_cards").fetchone()[0]
        if recent and time.time() - recent < settings["share_interval_minutes"] * 60 and not explicit:
            return [], False
        candidates = []
        for row in self.service.store.conn.execute("SELECT id FROM discovery_cards WHERE used_at IS NULL AND disabled=0 ORDER BY created_at DESC"):
            card = self._valid_card_locked(row[0], scope, settings)
            if not card or card["share_after"] > time.time() or card["trigger_kind"] == "chat" and not explicit:
                continue
            score = text_score(card["topic"], text)
            if explicit or score >= 0.55:
                candidates.append((score, card))
        if not candidates:
            return [], explicit
        candidates.sort(key=lambda pair: (pair[0], pair[1]["created_at"]), reverse=True)
        candidates = _limit_rows(_limit_rows(candidates, settings["chat_material_limit"]), settings["chat_dependency_limit"])
        return [card["packet_id"] for _, card in candidates], explicit

    def chat_accounting_failed(self):
        """Fail closed for future sharing without discarding a saved chat reply."""
        self._sharing_failed = True
        self._cancel.set()
        self._pending = self._note = None

    def record_chat_locked(self, request, usage, *, explicit=False):
        """Record facts, not inferred adoption. Offered material retires conservatively."""
        continued = set(usage.get("continued_ids", []))
        offered = set(usage.get("offered_ids", []))
        # Continuing an existing conversation is not another background share;
        # don't charge it again or keep extending the card's sharing cooldown.
        packets = [pid for pid in usage.get("packet_ids", []) if pid not in continued or pid in offered]
        conn = self.service.store.conn
        if usage.get("reply_saved") and packets:
            now = time.time()
            with conn:
                for packet in packets:
                    conn.execute("UPDATE discovery_cards SET offered_at=?,used_at=? WHERE packet_id=?", (now, now, packet))
                if not explicit:
                    conn.execute("INSERT INTO activity_budget(day,shares) VALUES(?,1) "
                                 "ON CONFLICT(day) DO UPDATE SET shares=shares+1", (_day(),))
            if self._note and self._note["packet_id"] in packets:
                self._note = None
        _, settings = self._config_locked()
        # Interest inference is handled by the current model's observe_interest
        # tool with exact user-turn evidence, rather than a second regex path.

    def _schedule(self):
        next_tick = time.monotonic()
        while not self._stop.is_set():
            changed = self._wake.wait(max(0.1, min(60, next_tick - time.monotonic())))
            self._wake.clear()
            if self._stop.is_set():
                break
            if not changed and time.monotonic() < next_tick:
                continue
            next_tick = time.monotonic() + 60  # Error backoff, not the user's activity cadence.
            if not self.service._lock.acquire(blocking=False):
                next_tick = time.monotonic() + 1
                continue
            try:
                self._purge_locked()
                # A permanent desktop session must not wait for the next restart
                # to perform hourly memory maintenance. No model/network here.
                with self.service._channels.condition:
                    if (self.service._channels.slot is None and not self.service._channels.closing
                            and self.service._channels.activity()['status']=='ready'):
                        from . import consolidation
                        consolidation.run_if_due(self.service.store,self.service.cfg)
                _, settings = self._config_locked()
                next_tick = time.monotonic() + settings["tick_minutes"] * 60
                # Autonomous selection belongs to the three-mode coordinator.
                # Keep this timer for source expiry / non-model housekeeping.
            except Exception:
                # Settings/conditions are visible through status; never log provider data.
                pass
            finally:
                self.service._lock.release()

    def _background_run(self, target, args):
        with self.service._background.claim("discovery") as acquired:
            if acquired:
                return target(*args)
            job = args[0]
            self._phase(job, "finished", state="cancelled", reason="background_busy")
            with self.service._lock:
                if job["epoch"] == self.dataset_id:
                    with self.service.store.source_transaction():
                        self.service.store.conn.execute("UPDATE discovery_runs SET state='cancelled',phase='finished',"
                            "reason='background_busy',finished_at=? WHERE id=?", (time.time(), job["id"]))

    def _launch_manual_locked(self, settings, revision, *, interest_id=None):
        from .autonomy_policy import search_settings
        settings = search_settings(settings)
        if self._stop.is_set() or self._unavailable:
            raise ChannelError(409, "draining", "后台工作者尚未回收或正在关闭")
        if not settings["enabled"]:
            raise ChannelError(409, "disabled", "请先启用内容发现")
        if settings["pool_capacity"] == 0 or settings["source_pool_bytes"] == 0 or settings["read_candidates"] == 0:
            raise ChannelError(409, "material_disabled", "资料容量或候选读取数为0，请先在设置中调整")
        with self._guard:
            if self._worker is not None and self._worker.is_alive():
                raise ChannelError(409, "busy", "已有内容发现任务")
        activity = self.service._channels.activity()
        if activity["status"] != "ready":
            raise ChannelError(409, "foreground_busy", "请在Alice已初始化且空闲时开始")
        now = time.time()
        platforms = [identity for identity, policy in settings["platforms"].items()
                     if policy["enabled"] and self._available(identity, settings)[0]
                     and (not _third_party(identity, settings) or policy["allow_third_party"])]
        if not platforms:
            raise ChannelError(409, "no_platform", "请先开放至少一个可用平台")
        self.service.store.decay_interests(self.service.cfg.memory.interest_decay_days)
        interests = [dict(r) for r in self.service.store.conn.execute(
            "SELECT * FROM discovery_interests WHERE enabled=1 AND external_allowed=1 "
            "AND scope_json LIKE '%gui%' ORDER BY last_selected,created_at")]
        interests = _limit_rows(interests, settings["active_interest_capacity"])
        if interest_id is not None:
            interests = [row for row in interests if row["id"] == interest_id]
        if not interests:
            raise ChannelError(409, "no_interest", "请先添加并允许至少一项搜索兴趣")
        budget = self._budget_locked()
        if _exceeds(budget["queries"], 1, settings["daily_queries"]) or _exceeds(budget["network"], 1, settings["daily_network"]):
            raise ChannelError(409, "quota_exceeded", "今天的发现额度已用完")
        # User feedback is bounded; age wins after fourteen days, never bypassing permissions.
        interests.sort(key=lambda i: (now - i["last_selected"] >= 14 * 86400,
                                     (now - i["last_selected"]) * i["weight"]), reverse=True)
        model = {"base_url": self.service.cfg.llm.base_url, "api_key": self.service.cfg.llm.resolved_key(),
                 "model": self.service.cfg.llm.cheap_model, "thinking": False, "allow_loopback": True}
        task = {"id": uuid.uuid4().hex, "epoch": self.dataset_id, "revision": revision, "day": _day(),
                "manual": True,
                "state": "queued", "phase": "queued", "reason": "", "started_at": now,
                "generation": self._generation, "interest_id": interests[0]["id"], "cards": 0}
        with self.service.store.conn:
            self.service.store.conn.execute("INSERT INTO discovery_runs(id,epoch,revision,day,state,phase,interest_id,started_at) "
                                            "VALUES(?,?,?,?,?,?,?,?)", tuple(task[k] for k in
                                            ("id", "epoch", "revision", "day", "state", "phase", "interest_id", "started_at")))
        cancel = threading.Event()
        worker_settings = copy.deepcopy(settings)
        worker_settings["_tieba_policy_version"] = self._tieba_policy_version_locked()
        worker = threading.Thread(target=self._background_run, args=(self._run, (task, worker_settings, interests, platforms, model, cancel)),
                                  name="alice-discovery", daemon=False)
        # Publish under the same admission condition used by foreground reserve.
        with self.service._channels.condition:
            if self.service._channels.slot is not None or self.service._channels.closing:
                with self.service.store.conn:
                    self.service.store.conn.execute("UPDATE discovery_runs SET state='cancelled',reason='foreground_busy',finished_at=? WHERE id=?", (time.time(), task["id"]))
                raise ChannelError(409, "foreground_busy", "对话刚刚开始，请稍后再试")
            self._cancel = cancel
            with self._guard:
                self._task, self._worker = dict(task), worker
            worker.start()
        return True

    def _launch_tieba_check_locked(self, settings, revision, forums):
        from .autonomy_policy import search_settings
        settings = search_settings(settings)
        from .discovery_tieba import normalize_forums
        try:
            forums = normalize_forums(forums)
        except ValueError:
            raise ChannelError(400, "invalid_request", "请填写有效的优先吧名列表") from None
        if not forums:
            raise ChannelError(400, "invalid_request", "请先填写要检测的吧名")
        if self._stop.is_set() or self._unavailable:
            raise ChannelError(409, "draining", "后台工作者尚未回收或正在关闭")
        with self._guard:
            if self._worker is not None and self._worker.is_alive():
                raise ChannelError(409, "busy", "已有后台寻找或吧名检测，请等待完成或先停止")
        if self.service._channels.activity()["status"] != "ready":
            raise ChannelError(409, "foreground_busy", "请在Alice已初始化且聊天空闲时检测")
        budget = self._budget_locked()
        if (_exceeds(budget["network"], 1, settings["daily_network"])
                or _exceeds(budget["body_bytes"], settings["read_response_bytes"], _response_budget(settings))):
            raise ChannelError(409, "quota_exceeded", "今日网络或响应量额度不足，暂不能检测")
        task = {"id": uuid.uuid4().hex, "kind": "tieba_forum_check", "epoch": self.dataset_id,
                "revision": revision, "day": _day(), "state": "queued", "phase": "tieba_forum_check",
                "reason": "", "started_at": time.time(), "generation": self._generation,
                "interest_id": None, "cards": 0}
        with self.service.store.conn:
            self.service.store.conn.execute("INSERT INTO discovery_runs(id,epoch,revision,day,state,phase,interest_id,started_at) "
                                            "VALUES(?,?,?,?,?,?,?,?)", tuple(task[key] for key in
                                            ("id", "epoch", "revision", "day", "state", "phase", "interest_id", "started_at")))
        cancel = threading.Event()
        worker = threading.Thread(target=self._run_tieba_check,
                                  args=(task, copy.deepcopy(settings), forums, cancel),
                                  name="alice-tieba-forum-check", daemon=False)
        with self.service._channels.condition:
            if self.service._channels.slot is not None or self.service._channels.closing:
                with self.service.store.conn:
                    self.service.store.conn.execute("UPDATE discovery_runs SET state='cancelled',reason='foreground_busy',finished_at=? WHERE id=?",
                                                    (time.time(), task["id"]))
                raise ChannelError(409, "foreground_busy", "对话刚刚开始，请稍后再检测")
            self._cancel = cancel
            with self._guard:
                self._tieba_forum_check = {
                    "task_id": task["id"], "state": "queued", "forums": forums, "revision": revision,
                    "results": [{"requested": name, "forum_name": "", "forum_id": "", "thread_count": 0,
                                 "status": "unverified", "checked_at": None, "code": "not_checked"} for name in forums],
                }
                self._task, self._worker = dict(task), worker
            worker.start()

    def _run_tieba_check(self, job, settings, forums, cancel):
        from .discovery_network import NetworkRouteError, resolve_system_proxy
        deadline = time.monotonic() + settings["activity_timeout_seconds"]
        state, code = "completed", "tieba_forum_check_finished"

        def publish(index, result):
            with self._guard:
                check = self._tieba_forum_check
                if check and check["task_id"] == job["id"]:
                    check["results"][index] = result

        def unverified(name, reason):
            return {"requested": name, "forum_name": "", "forum_id": "", "thread_count": 0,
                    "status": "unverified", "checked_at": None if reason == "checking" else time.time(), "code": reason}

        try:
            try:
                settings["_network_proxy"] = resolve_system_proxy() if settings["network_mode"] == "system" else ""
            except NetworkRouteError as exc:
                raise _Stopped(exc.code) from None
            for index, name in enumerate(forums):
                self._store_step(job, cancel, lambda: publish(index, unverified(name, "checking")))
                try:
                    data = self._execute(job, cancel, settings, "tieba_forum_check", {"forum_name": name}, deadline)
                    result = _forum_check_result(data, name)
                except _Stopped as exc:
                    reason = exc.code
                    self._store_step(job, cancel, lambda: publish(index, unverified(name, reason)), settle=True)
                    if (self._unavailable or reason.startswith("proxy_") or reason in {
                            "cancelled", "foreground_busy", "session_changed", "quota_exceeded", "timeout",
                            "tieba_api_error", "verification_required", "http_401", "http_403", "http_429",
                            "network", "tls", "dns", "dns_resolution", "dns_non_public", "dns_fake_ip", "dns_invalid"}):
                        raise
                    # A distinct explicitly requested forum is not a retry of
                    # the failed one. Missing identity/shape stays unverified.
                    continue
                self._store_step(job, cancel, lambda: publish(index, result))
        except _Stopped as exc:
            code = exc.code
            state = "cancelled" if code in ("cancelled", "foreground_busy", "session_changed") else "failed"
        except Exception:
            state, code = "failed", "processing_failed"
        finally:
            if self._unavailable:
                state, code = "draining", "worker_not_reaped"

            def finish():
                nonlocal state, code
                if state == "completed" and not self._valid_job_locked(job, cancel):
                    state, code = "cancelled", "cancelled"
                with self._guard:
                    check = self._tieba_forum_check
                    if check and check["task_id"] == job["id"]:
                        check["state"], check["code"] = state, code
                        for result in check["results"]:
                            if result.get("code") in {"not_checked", "checking"}:
                                result["code"] = code if state != "completed" else "not_checked"
                self._phase(job, "tieba_forum_check", state=state, reason=code)
                with self.service.store.conn:
                    self.service.store.conn.execute("UPDATE discovery_runs SET state=?,phase='tieba_forum_check',reason=?,finished_at=?,diagnostic=? WHERE id=?",
                                                    (state, code, time.time(), _json(_diagnostic(job.get("diagnostic"))), job["id"]))
            try:
                self._store_step(job, cancel, finish, settle=True)
            except _Stopped as exc:
                self._phase(job, "tieba_forum_check", state="cancelled", reason=exc.code)
            except Exception:
                self._phase(job, "tieba_forum_check", state="failed", reason="processing_failed")

    def model_config_changed(self):
        self._generation += 1
        self._cancel.set()

    def _valid_job_locked(self, job, cancel):
        if (cancel.is_set() or self._stop.is_set() or self._generation != job["generation"]
                or self.dataset_id != job["epoch"] or _day() != job["day"]):
            return False
        revision, settings = self._config_locked()
        with self._guard:
            check_job = (job.get("kind") == "tieba_forum_check" and self._tieba_forum_check
                         and self._tieba_forum_check["task_id"] == job["id"])
        enabled = settings["enabled"] or bool(check_job)
        if job.get("kind") not in {"chat_search", "exploration_search"} and not check_job and not job.get("manual"):
            from .activity import awake
            enabled = enabled and awake(self.service.store, testing_policy=False)
        if job.get("kind") == "chat_search":
            with self.service._channels.condition:
                enabled = self._chat_search_allowed(job.get("scope"), settings) and self._foreground_allowed(job)
        elif job.get("kind") == "exploration_search":
            from .thought_workspace import settings as autonomy_settings
            from .autonomy_policy import TEST_CONTROLS_ONLY
            enabled = ((TEST_CONTROLS_ONLY or settings["enabled"]) and job.get("scope") == "gui"
                       and autonomy_settings(self.service.store)["allow_external"]
                       and self.service._channels.activity().get("status") == "ready")
        return revision == job["revision"] and enabled

    def _phase(self, job, phase, *, state="running", reason=""):
        with self._guard:
            if self._task and self._task["id"] == job["id"]:
                self._task.update(state=state, phase=phase, reason=reason,
                                  reason_message=_reason(reason), cards=job.get("cards", 0),
                                  diagnostic=_diagnostic(job.get("diagnostic")),
                                  search_summary=_search_summary(job.get("search_summary")))
            if (job.get("kind") == "tieba_forum_check" and self._tieba_forum_check
                    and self._tieba_forum_check["task_id"] == job["id"]):
                self._tieba_forum_check["state"] = state
                if reason:
                    self._tieba_forum_check["code"] = reason

    def _store_step(self, job, cancel, callback, *, settle=False):
        # A foreground writer may be long-lived; it never waits for this worker.
        while not self.service._lock.acquire(timeout=0.1):
            if not settle and (cancel.is_set() or self._stop.is_set()):
                raise _Stopped("cancelled")
            if self._generation != job["generation"] or self.dataset_id != job["epoch"]:
                raise _Stopped("session_changed")
        try:
            if self._generation != job["generation"] or self.dataset_id != job["epoch"]:
                raise _Stopped("session_changed")
            if not settle and not self._valid_job_locked(job, cancel):
                raise _Stopped("cancelled")
            return callback()
        finally:
            self.service._lock.release()

    def _reserve_step(self, job, cancel, settings, op, maximum, *, query_attempt=False):
        diagnostic = _json(_diagnostic(job.get("diagnostic")))
        def reserve():
            budget = self._budget_locked(job["day"])
            model = int(op in ("plan", "interpret", "reflect"))
            query = int(op == "search" or query_attempt)
            from .autonomy_policy import search_quota_active
            testing = search_quota_active(settings)
            if (not testing and (_exceeds(budget["network"], 1, settings["daily_network"])
                    or _exceeds(budget["models"], model, settings["daily_models"])
                    or _exceeds(budget["queries"], query, settings["daily_queries"])
                    or _exceeds(budget["body_bytes"], maximum, _response_budget(settings)))
                    or model and not settings["allow_paid"]):
                raise _Stopped("quota_exceeded")
            if model:
                from .background_runtime import reserve_model
                reserve_model(self.service.store)
            identity = uuid.uuid4().hex
            conn = self.service.store.conn
            with conn:
                conn.execute("INSERT INTO activity_budget(day,queries,network,models,body_bytes) VALUES(?,?,1,?,?) "
                             "ON CONFLICT(day) DO UPDATE SET queries=queries+excluded.queries,network=network+1,"
                             "models=models+excluded.models,body_bytes=body_bytes+excluded.body_bytes",
                             (job["day"], query, model, maximum))
                conn.execute("INSERT INTO discovery_attempts(id,run_id,day,op,reserved_bytes,state,created_at,diagnostic) "
                             "VALUES(?,?,?,?,?,'reserved',?,?)", (identity, job["id"], job["day"], op, maximum, time.time(), diagnostic))
                conn.execute("UPDATE discovery_runs SET state='running',phase=?,queries=queries+?,diagnostic=? WHERE id=?",
                             (op, query, diagnostic, job["id"]))
            return identity
        return self._store_step(job, cancel, reserve)

    def _settle_step(self, job, cancel, attempt, maximum, result, code, diagnostic):
        diagnostic = _json(_diagnostic(diagnostic))
        usage = result.get("usage", {}) if isinstance(result, dict) else {}
        actual = usage.get("body_bytes")
        if type(actual) is not int or not 0 <= actual <= maximum:
            actual = None
        tokens = [usage.get(k) if type(usage.get(k)) is int and 0 <= usage[k] <= 10**8 else None
                  for k in ("input_tokens", "output_tokens")]
        def settle():
            conn = self.service.store.conn
            with conn:
                row = conn.execute("SELECT state FROM discovery_attempts WHERE id=?", (attempt,)).fetchone()
                if row is None or row["state"] != "reserved":
                    return
                conn.execute("UPDATE discovery_attempts SET state=?,actual_bytes=?,reason=?,input_tokens=?,output_tokens=?,diagnostic=? WHERE id=?",
                             ("settled" if actual is not None else "unknown", actual, code, *tokens, diagnostic, attempt))
                conn.execute("UPDATE discovery_runs SET diagnostic=?,search_summary=? WHERE id=?",
                             (diagnostic, _json(_search_summary(job.get("search_summary"))), job["id"]))
                if actual is not None:
                    conn.execute("UPDATE activity_budget SET body_bytes=body_bytes-? WHERE day=?", (maximum - actual, job["day"]))
        try:
            self._store_step(job, cancel, settle, settle=True)
        except _Stopped:
            pass  # Old store/epoch keeps the reservation; never write into a new session.

    def _execute(self, job, cancel, settings, op, data, deadline):
        if (job.get("kind") == "tieba_forum_check") != (op == "tieba_forum_check"):
            raise _Stopped("worker_protocol")
        if self._unavailable:
            raise _Stopped("worker_not_reaped")
        diagnostic = _diagnostic({"stage": data["request"]["purpose"]}) if op == "bili_http" else {}
        tieba_stage = (op in ("tieba_forum_check", "tieba_page") or op in ("search", "read")
                       and (data.get("platform") if op == "search" else data.get("candidate", {}).get("platform")) == "tieba")
        tieba_stage_name = "forum_check" if op == "tieba_forum_check" else "read" if op == "tieba_page" else op
        if tieba_stage:
            diagnostic = _diagnostic({"platform": "tieba", "stage": tieba_stage_name})
            if op != "tieba_forum_check":
                data = {**data, "tieba_options": _tieba_options(settings)}
        job["diagnostic"] = diagnostic
        phase = "model" if op in ("plan", "interpret", "reflect") else "read" if (op in ("read", "tieba_forum_check", "tieba_page") or op == "bili_http" and data["request"]["purpose"] == "video_info") else "search"
        maximum = settings[phase + "_response_bytes"]
        output_limit = max(192 * 1024, maximum * 2 + 65536)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise _Stopped("timeout")
        timeout = min(remaining, settings[phase + "_timeout_seconds"])
        operation_deadline = min(deadline, time.monotonic() + timeout)
        if op in ("search", "read", "bili_http", "tieba_forum_check", "tieba_page"):
            data = {**data, "network_proxy": settings.get("_network_proxy", "")}
        input_limit = 192 * 1024
        if op == "interpret":
            data = {**data, "tieba_text_limit": settings["tieba_text_limit"]}
            input_limit = max(input_limit, settings["tieba_text_limit"] * 6 * len(data.get("evidence", [])) + 65536)
        encoded = _json({"version": 1, "op": op, "require_admission": True,
                         "timeout_seconds": timeout, "response_bytes": maximum, **data}).encode("utf-8")
        if len(encoded) > input_limit:
            raise _Stopped("input_limit")
        attempt = self._reserve_step(job, cancel, settings, op, maximum,
                                     query_attempt=op == "bili_http" and data["request"]["purpose"] == "search")
        self._phase(job, op)
        process = io_thread = None
        io_ready, io_done = threading.Event(), threading.Event()
        output = {}
        result, code = None, "unknown"
        try:
            # No credentials in argv/environment/logs. Spawn an idle stdin reader;
            # authorize starting the operation under foreground admission's lock.
            env = {key: value for key, value in os.environ.items()
                   if key.upper() in {"SYSTEMROOT", "WINDIR", "PATH", "PATHEXT", "TEMP", "TMP", "LANG", "LC_ALL"}}
            env["PYTHONIOENCODING"] = "utf-8"
            process = subprocess.Popen(discovery_command(input_limit),
                                       cwd=str(RESOURCE_ROOT),
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                       env=env, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            def communicate():
                try:
                    # On Windows communicate(input=..., timeout=...) can block in
                    # synchronous stdin.write before applying its timeout. Keep all
                    # large pipe I/O off the supervising thread.
                    process.stdin.write(encoded + b"\n")
                    process.stdin.flush()
                    ready = json.loads(process.stdout.readline(4097).decode("utf-8"))
                    if ready != {"version": 1, "ready": True, "op": op}:
                        output["error"] = True
                        return
                    io_ready.set()
                    output["stdout"] = process.stdout.read(output_limit + 1)
                except Exception:
                    output["error"] = True
                finally:
                    io_done.set()
            io_thread = threading.Thread(target=communicate, name="alice-discovery-io", daemon=True)
            io_thread.start()
            admitted = False
            while True:
                if (cancel.is_set() or self._stop.is_set() or self._generation != job["generation"]
                        or _day() != job["day"]):
                    raise _Stopped("cancelled")
                left = operation_deadline - time.monotonic()
                if left <= 0:
                    raise _Stopped("timeout")
                if io_ready.is_set() and not admitted:
                    # Worker has drained all payload and is waiting for these 3
                    # bytes. Foreground reserve and side-effect admission are ordered
                    # by one condition; no network/model wait happens under it.
                    with self.service._channels.condition:
                        if (cancel.is_set() or self._stop.is_set() or not self._foreground_allowed(job)
                                or self.service._channels.closing or self._generation != job["generation"]
                                or _day() != job["day"]):
                            raise _Stopped("foreground_busy")
                        process.stdin.write(b"GO\n")
                        process.stdin.flush()
                        admitted = True
                if io_done.wait(min(0.05, left)):
                    break
            if output.get("error") or not admitted:
                raise _Stopped("worker_protocol")
            process.wait(timeout=max(0.01, min(1, operation_deadline - time.monotonic())))
            stdout = output.get("stdout", b"")
            if process.returncode != 0 or len(stdout) > output_limit:
                raise _Stopped("worker_failed")
            result = json.loads(stdout.decode("utf-8"), parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            if (not isinstance(result, dict) or result.get("version") != 1 or result.get("op") != op
                    or type(result.get("ok")) is not bool):
                raise _Stopped("worker_protocol")
            if not result["ok"]:
                if tieba_stage:
                    reported = _diagnostic(result.get("diagnostic"))
                    if reported.get("platform") == "tieba" and reported.get("stage") == tieba_stage_name:
                        diagnostic = reported
                        job["diagnostic"] = diagnostic
                error = result.get("error")
                raw_code = error.get("code", "source_failed") if isinstance(error, dict) else "worker_protocol"
                code = raw_code if isinstance(raw_code, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,59}", raw_code) else "source_failed"
                raise _Stopped(code)
            if not isinstance(result.get("data"), dict):
                raise _Stopped("worker_protocol")
            if op == "bili_http":
                diagnostic = _bili_response_diagnostic(data["request"]["purpose"], result["data"], maximum)
                job["diagnostic"] = diagnostic
                self._phase(job, op)
            elif op == "search":
                reported = result["data"].get("search_diagnostic")
                summary = _search_summary({**reported, "kind": "search_results"}) if isinstance(reported, dict) else {}
                candidates = result["data"].get("candidates")
                if (summary.get("platform") != data.get("platform")
                        or summary.get("provider") != data.get("provider", {}).get("kind", "native")
                        or not isinstance(candidates, list) or summary.get("returned") != len(candidates)):
                    summary = {}
                # Missing/invalid optional diagnostics never invent an empty
                # search. Later read/model stages retain this separate summary.
                job["search_summary"] = summary
                if summary:
                    diagnostic = summary
                    job["diagnostic"] = diagnostic
                self._phase(job, op)
            code = "ok"
            return result["data"]
        except _Stopped as exc:
            code = exc.code
            raise
        except (OSError, ValueError, TypeError, subprocess.TimeoutExpired):
            code = "worker_failed"
            raise _Stopped(code) from None
        finally:
            if process is not None and process.poll() is None:
                self._phase(job, op, state="draining", reason=code)
                try:
                    process.kill()
                    process.wait(timeout=5)
                except (OSError, subprocess.TimeoutExpired):
                    self._unreaped(process)
                    code = "worker_not_reaped"
            if io_thread is not None:
                io_thread.join(timeout=5)
                if io_thread.is_alive():
                    self._unreaped(process)
                    code = "worker_not_reaped"
            if process is not None and (io_thread is None or not io_thread.is_alive()):
                for stream in (process.stdin, process.stdout):
                    try:
                        stream.close()
                    except OSError:
                        pass
            self._settle_step(job, cancel, attempt, maximum, result, code, diagnostic)

    def _execute_bilibili(self, job, cancel, settings, operation, payload, deadline):
        from .bilibili_transport import TransportError, run_adapter

        def check():
            if self._unavailable:
                raise _Stopped("worker_not_reaped")
            if (cancel.is_set() or self._stop.is_set() or self._generation != job["generation"]
                    or self.dataset_id != job["epoch"] or _day() != job["day"]):
                raise _Stopped("cancelled")

        def request(message, context):
            check()
            return self._execute(job, cancel, settings, "bili_http", {"request": message, "context": context}, deadline)

        try:
            job["diagnostic"] = {}
            self._phase(job, "bili_" + operation)
            return run_adapter(operation, payload, response_bytes=settings[("search" if operation == "search" else "read") + "_response_bytes"],
                               deadline=deadline, perform_http=request, check_cancelled=check, unreaped=self._unreaped)
        except TransportError as exc:
            raise _Stopped(exc.code) from None

    def _execute_browser(self, job, cancel, settings, operation, platform, payload, deadline):
        from .browser_transport import BrowserError, run_browser
        maximum = settings["browser_resource_bytes"]
        end = min(deadline, time.monotonic() + settings[operation + "_timeout_seconds"])
        first = True
        def check():
            if self._unavailable:
                raise _Stopped("worker_not_reaped")
            if (cancel.is_set() or self._stop.is_set() or self._generation != job["generation"]
                    or self.dataset_id != job["epoch"] or _day() != job["day"]):
                raise _Stopped("cancelled")
            with self.service._channels.condition:
                if not self._foreground_allowed(job) or self.service._channels.closing:
                    raise _Stopped("foreground_busy")
        def admit(frame):
            nonlocal first
            check()
            token = self._reserve_step(job, cancel, settings, "browser_resource", maximum,
                                       query_attempt=operation == "search" and first)
            first = False
            return token
        def authorize(action):
            with self.service._channels.condition:
                if (cancel.is_set() or self._stop.is_set() or not self._foreground_allowed(job)
                        or self.service._channels.closing or self._generation != job["generation"]
                        or _day() != job["day"]):
                    raise _Stopped("foreground_busy")
                action()
        def settle(token, amount):
            self._settle_step(job, cancel, token, maximum, {"usage": {"body_bytes": amount}},
                              "ok" if amount is not None else "browser_transfer_unknown", {})
        try:
            job["diagnostic"] = {}
            self._phase(job, "browser_" + operation)
            result = run_browser({"op": operation, "platform": platform, "limit": settings["search_results"],
                                  "response_bytes": maximum, "network_proxy": settings.get("_network_proxy", ""), **payload},
                                 user_dir=user_root(), deadline=end,
                                 admit=admit, settle=settle, check=check, unreaped=self._unreaped, authorize=authorize)
            if operation == "search":
                job["search_summary"] = _search_summary(result.get("diagnostic"))
            with self._guard:
                self._browser_results[platform] = "search_no_candidates" if operation == "search" and not result.get("candidates") else "browser_ready"
            return result
        except BrowserError as exc:
            with self._guard:
                self._browser_results[platform] = exc.code
            raise _Stopped(exc.code) from None

    def _run(self, job, settings, interests, platforms, model, cancel):
        from .discovery_sources import normalize_candidate, validate_query
        from .discovery_network import NetworkRouteError, resolve_system_proxy
        deadline = time.monotonic() + settings["activity_timeout_seconds"]
        code, state = "no_new_material", "completed"
        try:
            try:
                settings["_network_proxy"] = resolve_system_proxy() if settings["network_mode"] == "system" else ""
            except NetworkRouteError as exc:
                raise _Stopped(exc.code) from None
            plan = {"platform": platforms[0], "interest_id": interests[0]["id"], "query": interests[0]["summary"]}
            budget = self._store_step(job, cancel, lambda: self._budget_locked(job["day"]))
            plan["platform"] = platforms[budget["queries"] % len(platforms)]
            use_model = bool(settings["allow_paid"] and model["api_key"] and model["model"] and model["base_url"])
            # Reserve room for reading/interpretation; no model call just to decide
            # whether an empty or already-sufficient pool deserves another model.
            planning = _limit_rows(interests, settings["planning_interests"])
            if use_model and planning and not _exceeds(budget["models"], 2, settings["daily_models"]):
                plan = self._execute(job, cancel, settings, "plan", {
                    "model": model, "interests": [{"id": row["id"], "text": row["summary"]} for row in planning],
                    "platforms": platforms}, deadline)
            if plan.get("platform") not in platforms or plan.get("interest_id") not in {r["id"] for r in interests}:
                raise _Stopped("model_scope")
            interest = next(r for r in interests if r["id"] == plan["interest_id"])
            settings["_tieba_preferred_forums"] = _preferred_forums(interest)
            query = validate_query(plan["query"])
            job["interest_id"] = interest["id"]
            def select_interest():
                with self.service.store.conn:
                    self.service.store.conn.execute("UPDATE discovery_interests SET last_selected=? WHERE id=? AND version=?",
                                                    (time.time(), interest["id"], interest["version"]))
                    self.service.store.conn.execute("UPDATE discovery_runs SET interest_id=? WHERE id=?", (interest["id"], job["id"]))
            self._store_step(job, cancel, select_interest)
            # P1：前后台统一搜索链（候选规范化/去重/排序在共享方法内）
            candidates = self.search_documents(job, cancel, settings, plan["platform"], query, deadline, interest=interest)
            if not candidates:
                code = "search_no_candidates"
            evidence = []
            for candidate in _limit_rows(candidates, settings["read_candidates"]):
                def seen():
                    conn = self.service.store.conn
                    return (conn.execute("SELECT 1 FROM discovery_seen WHERE identity=? AND expires_at>?", (_seen_key(plan["platform"], candidate["url"], settings), time.time())).fetchone()
                            or conn.execute("SELECT 1 FROM discovery_blocks WHERE identity=? AND expires_at>?", (_identity(candidate["url"]), time.time())).fetchone())
                if self._store_step(job, cancel, seen):
                    continue
                try:
                    # P1：前后台统一读取链；B站浏览器搜索的读取保留固定公开视图端点语义
                    data = self.read_document(job, cancel, settings, plan["platform"], candidate, deadline,
                                              bilibili_public_view=True)
                except _Stopped as exc:
                    if (self._unavailable or exc.code.startswith("proxy_") or exc.code in (
                            "cancelled", "foreground_busy", "session_changed", "quota_exceeded",
                            "platform_rejected", "tieba_api_error", "verification_required", "http_401", "http_403", "http_429",
                            "browser_login_required", "browser_profile_busy", "browser_missing", "browser_scope", "browser_response_limit",
                            "network", "tls", "dns", "dns_resolution", "dns_non_public", "dns_fake_ip", "dns_invalid")):
                        raise
                    if exc.code == "timeout":
                        if time.monotonic() >= deadline:
                            raise
                        code = "page_timeout"
                        break  # Preserve already-read evidence; do not retry the slow page.
                    code = exc.code
                    continue  # A different already-selected page is not a retry.
                normalized = normalize_candidate(data, plan["platform"])
                text_limit = settings["tieba_text_limit"] if plan["platform"] == "tieba" else 12000
                if normalized["url"] != candidate["url"] or not isinstance(data.get("text"), str) or not 80 <= len(data["text"]) <= text_limit:
                    raise _Stopped("worker_protocol")
                if plan["platform"] == "tieba":
                    _validate_tieba_evidence(data, settings)
                data["content_hash"] = hashlib.sha256(data["text"].encode("utf-8")).hexdigest()
                hash_seen = self._store_step(job, cancel, lambda: self.service.store.conn.execute(
                    "SELECT 1 FROM discovery_seen WHERE identity=? AND expires_at>?", (_seen_key(plan["platform"], data["content_hash"], settings, content=True), time.time())).fetchone())
                if not hash_seen:
                    evidence.append(data)
                    # A card has one immutable source dependency. Isolate the
                    # reader input to that source as well; a prompt alone cannot
                    # prevent a model from mixing facts across several pages.
                    break
            if evidence:
                budget = self._store_step(job, cancel, lambda: self._budget_locked(job["day"]))
                items, excerpt_only = None, False
                if use_model and not _exceeds(budget["models"], 1, settings["daily_models"]):
                    try:
                        from .learning import detail_targets
                        interpretation = self._execute(job, cancel, settings, "interpret", {"model": model, "evidence": evidence,
                            'detail_targets':detail_targets(self.service.cfg)}, deadline)
                        items = interpretation.get("items", [])
                    except _Stopped as exc:
                        if (exc.code in ("cancelled", "foreground_busy", "session_changed", "quota_exceeded")
                                or self._unavailable or time.monotonic() >= deadline):
                            raise
                        excerpt_only = True
                if items is None:
                    items = [{"evidence_index": i, "quote": doc["text"][:240], "summary": doc["text"][:240],
                              "author_view": "", "interpretation": "尚未进行独立模型判断，可在相关聊天中结合来源讨论。",
                              "uncertainty": "仅保留已读取文本的节选，未核实作者结论。", "question": "你怎么看这份材料？"}
                             for i, doc in enumerate(evidence)]
                if not isinstance(items, list) or len(items) > 3:
                    raise _Stopped("worker_protocol")
                job["cards"] = self._store_step(job, cancel, lambda: self._publish_locked(job, settings, interest, evidence, items))
                code = (("memory_failed" if job.get("memory_failed") else "excerpt_only" if excerpt_only else "ok")
                        if job["cards"] else "no_selected_material" if not items else "no_new_material")
            elif code not in ("no_new_material", "search_no_candidates"):
                state = "failed"
        except _Stopped as exc:
            code = exc.code
            state = "cancelled" if code in ("cancelled", "foreground_busy", "session_changed") else "failed"
        except Exception:
            code, state = "processing_failed", "failed"
        finally:
            if self._unavailable:
                code, state = "worker_not_reaped", "draining"
            self._phase(job, "finished", state=state, reason=code)
            def finish():
                with self.service.store.conn:
                    self.service.store.conn.execute("UPDATE discovery_runs SET state=?,phase='finished',reason=?,cards=?,finished_at=?,diagnostic=?,search_summary=? WHERE id=?",
                                                    (state, code, job.get("cards", 0), time.time(), _json(_diagnostic(job.get("diagnostic"))),
                                                     _json(_search_summary(job.get("search_summary"))), job["id"]))
            try:
                self._store_step(job, cancel, finish, settle=True)
            except Exception:
                pass

    def _publish_locked(self, job, settings, interest, evidence, items):
        conn = self.service.store.conn
        if settings["pool_capacity"] == 0:
            return 0
        if any(doc.get("platform") == "tieba" for doc in evidence):
            revision, current = self._config_locked()
            if (revision != job["revision"] or not current["platforms"]["tieba"]["enabled"]
                    or _tieba_scope_changed(settings, current)
                    or settings["_tieba_policy_version"] != self._tieba_policy_version_locked()):
                raise _Stopped("source_revoked")
        live = conn.execute("SELECT version,enabled,external_allowed FROM discovery_interests WHERE id=?", (interest["id"],)).fetchone()
        if not live or live["version"] != interest["version"] or not live["enabled"] or not live["external_allowed"]:
            raise _Stopped("interest_changed")
        accepted, seen_indices = 0, set()
        now = time.time()
        for item in items:
            if not isinstance(item, dict) or type(item.get("evidence_index")) is not int:
                raise _Stopped("model_schema")
            index = item["evidence_index"]
            if not 0 <= index < len(evidence) or index in seen_indices:
                raise _Stopped("model_source")
            seen_indices.add(index)
            doc = evidence[index]
            if doc["platform"] == "tieba":
                _validate_tieba_evidence(doc, settings)
            quote = item.get("quote")
            if not isinstance(quote, str) or not 20 <= len(quote) <= 300 or quote not in doc["text"]:
                raise _Stopped("model_quote")
            if conn.execute("SELECT 1 FROM discovery_seen WHERE identity=? AND expires_at>?", (_seen_key(doc["platform"], doc["content_hash"], settings, content=True), now)).fetchone():
                continue
            texts = {key: _utf8(item.get(key, ""), maximum) for key, maximum in (
                ("summary", 1200), ("author_view", 1800), ("interpretation", 2800), ("uncertainty", 1400), ("question", 600))}
            # Only a model's explicit decision can admit a read document as memory.
            eligible = bool(settings["remember_observations"]) and type(item.get("keep")) is bool
            packet_id, card_id = uuid.uuid4().hex, uuid.uuid4().hex
            title, url = _utf8(doc["title"], 1000), doc["url"]
            coverage = _coverage(doc.get("coverage"), doc)
            version = doc["content_hash"] + ":" + str(int(now))
            if doc["platform"] == "tieba":
                version = doc["content_hash"] + ":" + settings["_tieba_policy_version"] + ":" + str(int(now))
            from .learning import material_packet
            from .discovery_tieba import reading_policy
            source = material_packet(packet_id=packet_id, source_id=doc['platform']+':'+_identity(url),
                version_id=version,title=title,url=url,text=doc['text'],quote=quote,
                created_at=now,expires_at=now+settings['source_ttl_hours']*3600,
                scope=['gui','qq-owner'] if settings['share_in_qq'] else ['gui'],coverage=coverage,
                reading_policy=reading_policy(doc, settings['tieba_text_limit']),
                author_view=texts['author_view'],interpretation=texts['interpretation'],
                uncertainty='阅读范围：'+coverage+'。'+texts['uncertainty'],keywords=interest['summary'])
            source["uncertainty"] = _utf8(source["uncertainty"], 1500)
            # Savepoints keep packet/card/dedup publication atomic even though
            # the source store is also used independently by ordinary chat.
            with self.service.store.source_transaction():
                while _exceeds(conn.execute("SELECT COUNT(*) FROM discovery_cards").fetchone()[0], 1, settings["pool_capacity"]):
                    # Candidate retirement does not revoke a memory/turn source.
                    conn.execute("DELETE FROM discovery_cards WHERE id=(SELECT id FROM discovery_cards ORDER BY created_at LIMIT 1)")
                self.service.store.save_source_packet(source)
                if doc["platform"] == "tieba":
                    conn.execute("INSERT INTO discovery_tieba_sources VALUES(?,?,?,?,?)",
                                 (packet_id, doc["tieba_forum"], doc["tieba_reply_count"],
                                  int(doc["tieba_replies_requested"]), settings["_tieba_policy_version"]))
                self.service.store._check_source_space(sum(len(v.encode("utf-8")) for v in (title, url, *texts.values())))
                conn.execute("INSERT INTO discovery_cards(id,packet_id,platform,interest_id,interest_version,title,url,content_hash,"
                             "coverage,summary,author_view,interpretation,uncertainty,question,created_at,expires_at) "
                             "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                             (card_id, packet_id, doc["platform"], interest["id"], interest["version"], title, url,
                              doc["content_hash"], coverage, texts["summary"], texts["author_view"],
                              texts["interpretation"], texts["uncertainty"], texts["question"], now,
                              min(now + settings["candidate_ttl_hours"] * 3600, source["expires_at"])))
                conn.executemany("INSERT OR REPLACE INTO discovery_seen VALUES(?,?)",
                                 [(key, now + settings["seen_ttl_days"] * 86400) for key in (
                                     _seen_key(doc["platform"], url, settings),
                                     _seen_key(doc["platform"], doc["content_hash"], settings, content=True))])
            accepted += 1
            job["cards"] = accepted
            if eligible:
                try:
                    self.service.store.save_learning({
                        'keep': item['keep'], 'title': title[:24], 'content': texts['summary'] + '\n暂时解读：' + texts['interpretation'],
                        'uncertainty': texts['uncertainty'], 'value_tier': item['value_tier'],
                        'reason': item['value_reason'], 'keywords': interest['summary']},
                        {'scope':'gui','packet_ids':[packet_id],'request_id':job['id']})
                except Exception:
                    job["memory_failed"] = True  # A valid temporary card remains useful.
            if texts["question"] and self._note is None:
                self._note = {"card_id": card_id, "packet_id": packet_id, "text": texts["question"][:160],
                              "created_at": now, "expires_at": min(now + settings["candidate_ttl_hours"] * 3600, source["expires_at"])}
        return accepted

    # ---------- P1 共享材料链：前后台统一调用（N07/N08） ----------
    # 前台（SearchSession）与后台（_run 轮）此前各自实现候选排序、读取分发与发布，
    # 两处逻辑平行漂移。以下三个方法提炼共享步骤；两条入口只保留各自的目标、
    # 选材方式与调度差异。发布仍走原有 _publish_locked / SearchSession._publish_locked
    # 两条（前者含模型卡字段与观察入账，后者是快路径节选卡），由统一包装按入口选择。

    def rank_candidates(self, platform, query, raw_candidates, *, settings, preferred_forums=None, selection=None):
        """候选去重、日期筛选及软优先吧排序；搜索摘要始终不作为正文。"""
        from .discovery_sources import normalize_candidate
        if not isinstance(raw_candidates, list) or len(raw_candidates) > settings["search_results"]:
            raise _Stopped("worker_protocol")
        candidates, urls = [], set()
        for raw in raw_candidates:
            candidate = normalize_candidate(raw, platform)
            if candidate["url"] not in urls:
                candidates.append(candidate)
                urls.add(candidate["url"])
        preferred = set(preferred_forums or []) if platform == "tieba" else set()
        if platform == "tieba":
            after = settings.get("_tieba_published_after")
            counts = {"before_date": 0, "date_unknown": 0}
            if after is not None:
                selected = []
                for candidate in candidates:
                    stamp = candidate.get("published_at")
                    if stamp is None:
                        counts["date_unknown"] += 1
                    elif stamp < after:
                        counts["before_date"] += 1
                    else:
                        selected.append(candidate)
                candidates = selected
            if selection is not None:
                selection.update(counts)
            if settings.get("_tieba_search_sort", "recent") == "recent":
                # Source publication time leads relevance, rather than letting
                # a word-dense old story displace current discussions again.
                candidates.sort(key=lambda c: (c.get("forum_name") in preferred,
                    c.get("published_at") or 0,
                    text_score(query, c["title"] + " " + c.get("snippet", ""))), reverse=True)
                return candidates
        candidates.sort(key=lambda c: (c.get("forum_name") in preferred,
                                       text_score(query, c["title"] + " " + c.get("snippet", ""))), reverse=True)
        return candidates

    def read_document(self, job, cancel, settings, platform, candidate, deadline, *, bilibili_public_view=False):
        """读取一个候选并做协议校验；贴吧每页复用同一个宿主监督与预算入口。

        bilibili_public_view：B站浏览器模式下，读取用固定公开视图端点（无需播放）
        而非通用浏览器会话——保留后台轮原有的这条语义。
        """
        browser = platform in ("bilibili", "zhihu") and settings[platform + "_access"] == "browser"
        native_bili = platform == "bilibili" and settings["bilibili_access"] == "native"
        if platform == "tieba":
            from . import discovery_tieba
            from .discovery_sources import DiscoveryError, finish_tieba_read
            try:
                parsed = discovery_tieba.read(candidate, tieba_options=_tieba_options(settings),
                    text_limit=settings["tieba_text_limit"], fetch_page=lambda page, size: self._execute(
                        job, cancel, settings, "tieba_page", {"candidate": candidate, "page": page, "page_size": size}, deadline))
                return finish_tieba_read(candidate, parsed)
            except DiscoveryError as exc:
                if exc.diagnostic:
                    job["diagnostic"] = _diagnostic(exc.diagnostic)
                raise _Stopped(exc.code) from None
        if browser and platform == "bilibili" and bilibili_public_view:
            return self._execute_bilibili(job, cancel, settings, "read", {"candidate": candidate}, deadline)
        if browser:
            return self._execute_browser(job, cancel, settings, "read", platform, {"url": candidate["url"]}, deadline)
        if native_bili:
            return self._execute_bilibili(job, cancel, settings, "read", {"candidate": candidate}, deadline)
        return self._execute(job, cancel, settings, "read", {"candidate": candidate}, deadline)

    def search_documents(self, job, cancel, settings, platform, query, deadline, *, limit=None, interest=None):
        """搜索一个平台并返回排序后的候选——前台后台同一次序、同一传输分发。"""
        kind = settings["provider_kind"] if _third_party(platform, settings) else "native"
        provider = {"kind": "bing_rss" if platform == "web" and kind == "native" else kind}
        if kind == "brave":
            provider["api_key"] = settings["brave_key"]
        elif kind == "searxng":
            provider.update(endpoint=settings["provider_endpoint"], allow_loopback=True,
                            engines=[name.strip() for name in settings["provider_engines"].split(",")])
        browser = platform in ("bilibili", "zhihu") and settings[platform + "_access"] == "browser"
        native_bili = platform == "bilibili" and settings["bilibili_access"] == "native"
        payload = {"query": query, "limit": limit or settings["search_results"]}
        if browser:
            response = self._execute_browser(job, cancel, settings, "search", platform, {"query": query}, deadline)
        elif native_bili:
            response = self._execute_bilibili(job, cancel, settings, "search", payload, deadline)
        else:
            response = self._execute(job, cancel, settings, "search",
                                     {"platform": platform, "query": query, "provider": provider, **payload}, deadline)
        preferred = _preferred_forums(interest) if interest is not None else settings.get("_tieba_preferred_forums", [])
        job["selection_summary"] = {}
        return self.rank_candidates(platform, query, response.get("candidates"),
                                    settings=settings, preferred_forums=preferred, selection=job["selection_summary"])


class _Stopped(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _utf8(value, maximum):
    if not isinstance(value, str):
        raise _Stopped("model_schema")
    return value.encode("utf-8")[:maximum].decode("utf-8", "ignore")


def _coverage(value, doc=None):
    if doc and doc.get("platform") == "tieba":
        place = "贴吧「" + doc["tieba_forum"] + "」" if doc["tieba_forum"] else "贴吧（吧归属未取得）"
        count = doc["tieba_reply_count"]
        nested = doc["tieba_nested_reply_count"]
        scope = place + f"主帖及 {count - nested} 条回帖、{nested} 条楼中楼文字节选；读取 {doc['tieba_pages_read']} 页"
        reason = {"main_only": "仅主帖", "end": "已到接口末页", "reply_limit": "达到回复额度",
                  "text_limit": "达到文字额度，末条可能截断", "pagination_unconfirmed": "后续页码未能确认",
                  "repeated_page": "后续页无新楼层，已停止"}[doc["tieba_read_stop"]]
        return scope + "；" + reason + ("；楼中楼仅为接口附带节选，可能遗漏" if doc["tieba_nested_requested"] else "；未读楼中楼") + "；不含图片或视频，不代表完整讨论"
    return {"web_article_excerpt": "公开网页文章正文节选", "plain_text_excerpt": "公开纯文本节选",
            "page_text_excerpt": "公开正文节选", "static_page_excerpt": "静态页面文字节选",
            "zhihu_answer_excerpt": "知乎指定回答正文节选", "zhihu_article_excerpt": "知乎指定文章正文节选",
            "zhihu_question_excerpt": "知乎问题描述节选，不含回答", "forum_main_post_excerpt": "贴吧主帖文字节选，不含回复",
            "forum_thread_excerpt": "贴吧主帖及获准回复文字节选，不含图片或视频",
            "video_description_only": "仅视频简介，未观看视频", "page_description_only": "仅页面简介"}.get(value, "有限文本节选")


def _reason(code):
    return {"ok": "本次资料已整理", "no_new_material": "本轮没有新增资料",
            "chat_search_disabled": "请在发现设置中开启内容访问和对话内搜索",
            "chat_search_limit": "本轮已达到你设置的搜索次数",
            "no_platform": "请在发现偏好中开放全网搜索，或明确选择已开放的平台",
            "no_readable_content": "搜索找到了候选，但没有读到可用正文",
            "source_failed": "本次来源读取未完成，未用搜索摘要替代正文",
            "material_disabled": "当前资料容量或本轮资料数量设为0，未发起读取",
            "search_no_candidates": "搜索已返回，但没有可读取的页面或帖子候选，尚未进入正文阅读",
            "search_no_recent_candidates": "本次返回的候选没有满足发布日期要求的材料；可缩短关键词重新查找，不能据此断言平台没有近期讨论",
            "source_date_unknown": "主帖发布日期未能确认，未作为指定日期后的材料",
            "source_too_old": "主帖早于本次指定日期，未继续读取回帖",
            "no_selected_material": "已读取材料，本轮未选入资料卡", "cancelled": "已停止本次活动",
            "bili_missing": "B站只读组件缺失", "bili_scope": "B站请求超出本轮只读范围",
            "bili_protocol": "B站组件或接口响应格式不符合约定", "bili_failed": "B站访问未完成",
            "platform_rejected": "B站接口拒绝了本次匿名请求，本次已停止",
            "verification_required": "平台要求额外验证，本次已停止，未尝试完成验证或重试",
            "invalid_device": "未能解析B站返回的匿名设备标识",
            "invalid_signature_keys": "B站未提供可用的搜索签名参数",
            "content_unavailable": "该视频没有可读取的公开文字简介",
            "source_mismatch": "返回的正文与所选来源不一致",
            "tieba_forum_check_finished": "吧名检测已完成，请查看每个吧的结果；检测不会保存兴趣或发起内容寻找",
            "tieba_forum_check_required": "新增优先吧需要先完成检测并确认有公开帖子",
            "tieba_forum_unconfirmed": "平台未返回可确认的吧信息，本次保持未验证，不能据此判断吧不存在",
            "adapter_failed": "B站只读组件未能完成本次操作",
            "browser_ready": "本次独立浏览器访问已取得可用内容",
            "browser_missing": "独立浏览器组件尚未安装或 Edge 不可用",
            "browser_login_required": "请打开该平台的独立浏览器，亲自登录后关闭窗口再寻找",
            "browser_profile_busy": "独立浏览器正在使用，请关闭它的窗口后再寻找",
            "browser_start_failed": "独立浏览器未能启动，请检查 Edge 和组件安装",
            "browser_content_unavailable": "浏览器未取得可确认的目标正文或搜索结果，未以摘要补足",
            "browser_navigation_failed": "独立浏览器未能打开目标页面，本次未重试",
            "browser_response_limit": "浏览器资源超过单次响应大小，请在任务设置中调整",
            "browser_scope": "浏览器页面转向了当前只读范围之外，本次已停止",
            "browser_supervision": "无法监督浏览器进程，已停止启动",
            "browser_protocol": "浏览器组件返回了无效协议，本次已停止",
            "browser_failed": "独立浏览器读取未完成，请检查平台登录或验证状态",
            "response_too_large": "响应超过你设置的单次大小",
            "invalid_response": "平台返回的数据格式不符合预期",
            "login_required": "该B站内容需要登录，当前只读通道未使用账号",
            "risk_control": "B站暂时拒绝本次访问，请稍后人工查看平台状态",
            "input_limit": "本阶段超过执行载荷大小，请减少单次规划兴趣或材料输入",
            "excerpt_only": "已保留来源节选；本次模型判断未完成", "memory_failed": "资料已保留，正式见闻未能写入",
            "foreground_busy": "已让出时间给当前对话", "session_changed": "会话或配置已变化",
            "interest_changed": "兴趣已更新，旧结果未采用", "quota_exceeded": "本日额度不足",
            "timeout": "本次活动超时", "worker_not_reaped": "工作者尚未回收，后台已停用",
            "processing_failed": "本次整理未完成", "blocked_address": "来源地址不在允许范围",
            "dns": "域名解析失败，请检查网络连接或系统代理",
            "dns_resolution": "域名解析失败，请检查网络连接或系统代理",
            "dns_invalid": "域名解析未返回受支持的地址",
            "dns_non_public": "DNS含非公网或保留地址，直连已停止；使用代理时请选择跟随本机系统HTTP代理",
            "dns_fake_ip": "DNS返回了可能由代理生成的Fake-IP地址，请选择跟随本机系统HTTP代理",
            "network": "网络连接失败；若浏览器依赖代理，请检查内容发现的网络连接设置",
            "tls": "平台或接口的TLS证书校验失败，未忽略证书错误",
            "proxy_config": "系统代理须为无账号的本机HTTP代理；远程代理、SOCKS或PAC脚本暂不支持",
            "proxy_unavailable": "无法连接本机HTTP代理，请确认代理程序已启动",
            "proxy_connect": "无法连接本机HTTP代理，请确认代理程序已启动",
            "proxy_auth": "本机HTTP代理要求账号验证，当前通道不传代理凭据",
            "proxy_rejected": "本机HTTP代理拒绝了到平台的连接",
            "proxy_protocol": "本机HTTP代理未返回有效的CONNECT响应",
            "proxy_scope": "本次目标不在本机代理通道的固定平台范围内",
            "wiki_maxlag": "维基百科接口当前繁忙，请稍后再试",
            "wiki_api_error": "维基百科接口返回错误，本次未当作空结果",
            "wiki_response": "维基百科未返回预期的结果或正文结构",
            "tieba_api_error": "贴吧拒绝了本次匿名请求，本次已停止",
            "tieba_response": "贴吧未返回可确认的搜索、楼层或分页结构",
            "tieba_no_text": "贴吧主帖及获准回复没有可读取文字，未理解图片或视频",
            "tieba_short_text": "贴吧获准范围内的文字不足，未生成资料；不以搜索摘要或媒体补足",
            "zhihu_unreadable": "未取得该知乎问题、回答或文章的公开正文；可能需要登录或验证",
            "zhihu_response": "知乎未返回预期的正文结构",
            "search_response": "搜索服务未返回可识别的有效结果，本次未当作空结果",
            "redirect_blocked": "来源要求跳转，本次未自动跟随；可能是登录、验证或错误页面",
            "provider_error": "搜索服务返回错误，本次未当作空结果",
            "unreadable": "未取得可用的公开正文",
            "body_limit": "响应超过你设置的单次大小，可在内容发现设置中调整",
            "platform_scope": "地址不属于开放平台", "model_quote": "引文未能对应原文",
            "model_response": "模型未返回完整结果", "model_schema": "模型资料格式不符合约定",
            "http_401": "搜索或模型凭据无效", "http_403": "来源拒绝访问", "http_429": "来源暂时限流",
            "http_404": "该来源已不存在或无法读取",
            "worker_failed": "工作进程未能完成", "page_timeout": "本页读取超时，未重复请求",
            "empty_content": "未取得可读正文"}.get(code, "来源或模型未能完成本次请求" if code else "")
