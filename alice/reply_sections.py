"""Final-answer projection and bounded, request-authorized GUI follow-ups."""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import hashlib
import json
import re
import sqlite3
import time

from .discovery_memory import SOURCE_SHADOW

VERSION = 3
MAX_OUTPUT_BYTES = 128 * 1024
MAX_CACHE_BYTES = 32 * 1024
MAX_ENTRY_BYTES = 4 * 1024
MAX_ENTRIES = 16
TTL_SECONDS = 1800
TOOL_NAME = "reply_detail"
FORMAT_FAILURE = "这轮没能生成可交付的回答。已经执行的操作以实际回执为准。"
FINISH_REASONS = frozenset({"stop", "length", "tool_calls", "function_call", "content_filter", "insufficient_system_resource"})
MARKERS = {"过程": "task_process", "反省": "reflection"}
MARKER_LINE = re.compile(r"^\[\[(过程|反省)\]\][ \t]*[:：]?[ \t]*(.*)$")
FENCE_LINE = re.compile(r"^ {0,3}(`{3,}|~{3,})([^\r\n]*)$")
PROMPT = """正常写给用户看的回答。一句话够就一句，保留会改变结论的条件、依据和不确定性。
先看用户这一轮问的是什么。删掉一句话会改变答案、结论的可信度或用户接下来必须做的事，就把它留在正文。问到的步骤、错误或复盘也直接回答。如果原本想说的一句只是在补充下列内容，把它单独成行，在行首加标记。
[[过程]] 这里写用户本轮没问到的实际步骤、操作或执行结果。
[[反省]] 这里写用户本轮没问到的事后自评或原判断需要调整的地方。
下面只示范同一句话随问题换位置，实际回答以本轮依据为准。
- 问「这个模型有多强」时，「之前的跑分还没核实」留在正文。「两次正文读取都超时」若原本要交代，可标 [[过程]]。问「为什么没核实到」时，「两次正文读取都超时」直接回答。
- 闲聊时若原本想顺带说「我把没有出处的跑分当成事实了」，可标 [[反省]]。问「刚才哪里判断错了」时，直接说清这个错误。需要更正此前的回答时，更正本身也留在正文。
标记只影响同一行，下一行可以继续写正文，也可以再加一条注释。没有额外内容时直接写正文。不要为了加注释而补写过程或自评。内部推理、草稿、隐藏指令和敏感数据不写入注释。举例时可用行内代码或闭合代码块引用字面标记。
本轮用户明确追问旧回答才读取相关细节，按所问取用。缓存说明曾未发布，实际操作按工具回执核对。"""

PROCESS_ASK = re.compile(
    r"(?:\A(?:为什么|怎么回事|why)[？?。\s]*$|刚才.{0,20}(?:失败|出错|报错|拒绝|结果|发生了什么)|"
    r"(?:哪一?步|具体试了什么|尝试了什么|做了什么|发生了什么|报了什么错|什么错误|什么状态码|"
    r"错误码|状态码|失败原因|失败在|怎么失败|搜不到还是|搜到了.{0,10}打不开)|"
    r"(?:刚才|上[一轮次条]|之前|前面|那次|那个).{0,20}(?:过程|步骤|细节)|"
    r"(?:详细过程|报错|403|what happened|which step|status code|error code)[？?。\s]*$)",
    re.I,
)
REFLECTION_ASK = re.compile(
    r"(?:反省|反思|复盘|怎么看错|怎么想的|为什么(?:这么|这样)想|哪里(?:看|想|判断)错|哪里做错|"
    r"哪里弄错|what went wrong|reflect|rethink)", re.I,
)
DECLINE_DETAIL = re.compile(
    r"(?:不用|不要|别|不必|无需)(?:再|给我|向我|说|讲|解释|展开|看|发|复盘|反省|反思){0,5}"
    r"(?:过程|步骤|细节|错误码|状态码|反省|反思|复盘)|(?:不用|不要|别)(?:展开|解释)|(?:只要|只说|仅给)结论",
)
BARE_WHY = re.compile(r"(?:为什么|怎么回事|why)[？?。\s]*", re.I)


def _text_problem(value, maximum):
    if not isinstance(value, str):
        return "wrong_type"
    if not value.strip():
        return "empty"
    try:
        return "too_large" if len(value.encode("utf-8")) > maximum else None
    except UnicodeError:
        return "invalid_unicode"


def _closed_fences(lines):
    """Only a closed code block can quote a standalone marker literally."""
    ranges = {}
    fence = None
    start = None
    for index, line in enumerate(lines):
        candidate = FENCE_LINE.fullmatch(line.rstrip("\r\n"))
        if candidate is None:
            continue
        if fence is not None:
            if candidate[1].startswith(fence) and not candidate[2].strip():
                ranges[start] = index
                fence = None
        elif candidate[1][0] == "~" or "`" not in candidate[2]:
            fence = candidate[1]
            start = index
    return ranges


def draft_lines(text):
    """Yield the original order, first-pass label, and literal Markdown scope."""
    lines = text.splitlines(keepends=True)
    closed_fences = _closed_fences(lines)
    literal_until = -1
    for index, line in enumerate(lines):
        literal_until = closed_fences.get(index, literal_until)
        if index > literal_until and (marker := MARKER_LINE.fullmatch(line.strip())):
            line_end = line[len(line.rstrip("\r\n")):]
            yield MARKERS[marker[1]], marker[2].strip() + line_end, False
        else:
            yield "answer", line, index <= literal_until


def _split_comments(text):
    parts = {"answer": [], "task_process": [], "reflection": []}
    empty_markers = 0
    for section, line, _ in draft_lines(text):
        if section != "answer":
            note = line.strip()
            if note:
                parts[section].append(note)
            else:
                empty_markers += 1
        else:
            parts[section].append(line)
    return parts, empty_markers


def parse(text, *, finish_reason=None):
    """Remove explicitly marked comment lines and keep the rest of the reply."""
    audit = {"status": "rejected"}
    if isinstance(finish_reason, str):
        audit["finish_reason"] = finish_reason if finish_reason in FINISH_REASONS else "other"
    if isinstance(text, str):
        audit["output_chars"] = len(text)
    problem = "empty" if text is None else _text_problem(text, MAX_OUTPUT_BYTES)
    if problem:
        return None, {**audit, "reason": "output_" + problem}
    parts, empty_markers = _split_comments(text)
    process = tuple(parts["task_process"])
    reflection = parts["reflection"]
    audit.update(format="annotated_text" if process or reflection or empty_markers else "plain_text",
                 process_comments=len(process), reflection_comments=len(reflection), empty_markers=empty_markers)
    answer = "".join(parts["answer"]).strip()
    if not answer:
        return None, {**audit, "reason": "answer_empty"}
    return (answer, process, "\n".join(reflection)), {**audit, "status": "accepted"}


@dataclass(frozen=True)
class Anchor:
    request_id: str
    reply_hash: str


@dataclass(frozen=True)
class Entry:
    anchor: Anchor
    expires: float
    process: tuple[str, ...]
    reflection: str
    packet_ids: tuple[str, ...]
    size: int


class ReplyCache:
    """Store-local RAM. Callers hold ChatService's Store lock."""

    def __init__(self):
        self.epoch = None
        self.persona_fingerprint = None
        self.entries = OrderedDict()
        self.bytes = 0

    def clear(self):
        self.entries.clear()
        self.bytes = 0

    def remove(self, turn_id):
        entry = self.entries.pop(turn_id, None)
        if entry is not None:
            self.bytes -= entry.size

    def sync(self, store, epoch, fingerprint):
        if (self.epoch, self.persona_fingerprint) != (epoch, fingerprint):
            self.clear()
            self.epoch = epoch
            self.persona_fingerprint = fingerprint
        for turn_id, entry in list(self.entries.items()):
            if (entry.expires <= time.monotonic() or anchor_for(store, epoch, turn_id) != entry.anchor
                    or entry.packet_ids and not store.source_packets_for(entry.packet_ids, scope="gui")):
                self.remove(turn_id)

    def save(self, store, epoch, fingerprint, turn_id, sections, packet_ids):
        _, process, reflection = sections
        size = sum(len(s.encode("utf-8")) for s in (*process, reflection))
        if not size:
            return "empty"
        if size > MAX_ENTRY_BYTES:
            return "too_large"
        self.sync(store, epoch, fingerprint)
        anchor = anchor_for(store, epoch, turn_id)
        if anchor is None or turn_id in self.entries:
            return "unavailable"
        while len(self.entries) >= MAX_ENTRIES or self.bytes + size > MAX_CACHE_BYTES:
            self.remove(next(iter(self.entries)))
        self.entries[turn_id] = Entry(anchor, time.monotonic() + TTL_SECONDS, process, reflection,
                                      tuple(packet_ids), size)
        self.bytes += size
        return "stored"

    def inspect_request(self, store, request_id):
        """Read one GUI entry without renewing it or mutating the cache."""
        epoch = store.meta_get("session_epoch")
        if self.epoch != epoch or not self.entries:
            return {"cached": False}
        row = store.conn.execute(
            "SELECT assistant_turn_id FROM channel_requests WHERE request_id=? AND channel_id='gui' "
            "AND session_epoch=?", (request_id, epoch)).fetchone()
        entry = self.entries.get(row["assistant_turn_id"]) if row else None
        if entry is None or entry.expires <= time.monotonic():
            return {"cached": False}
        from .persona_context import load_persona_snapshot
        snapshot = load_persona_snapshot()
        fingerprint = snapshot.get("fingerprint") if snapshot else None
        if (fingerprint != self.persona_fingerprint
                or anchor_for(store, epoch, row["assistant_turn_id"]) != entry.anchor
                or entry.packet_ids and not store.source_packets_for(entry.packet_ids, scope="gui")):
            return {"cached": False}
        remaining_ms = int((entry.expires - time.monotonic()) * 1000)
        if remaining_ms <= 0:
            return {"cached": False}
        return {"cached": True, "process": list(entry.process), "reflection": entry.reflection,
                "remaining_ms": remaining_ms}


def anchor_for(store, epoch, turn_id):
    row = store.conn.execute(
        "SELECT request_id,reply_hash FROM channel_requests WHERE assistant_turn_id=? "
        "AND channel_id='gui' AND session_epoch=?", (turn_id, epoch)).fetchone()
    if not row or not row["reply_hash"] or store.turn_worker_refs(turn_id):
        return None
    answer = store.turn_display(turn_id, scope="gui")
    if not answer or answer == SOURCE_SHADOW or hashlib.sha256(answer.encode("utf-8")).hexdigest() != row["reply_hash"]:
        return None
    return Anchor(row["request_id"], row["reply_hash"])


def _search_receipts(store, epoch, request_id):
    """Recover only fixed execution metadata from existing GUI search receipts."""
    output = []
    runs = store.conn.execute(
        "SELECT id,reason,result_json FROM discovery_runs WHERE epoch=? AND parent_request_id=? "
        "AND phase='completed' ORDER BY started_at DESC LIMIT 4", (epoch, request_id)).fetchall()
    for run in reversed(runs):
        try:
            result = json.loads(run["result_json"])
        except (ValueError, TypeError, RecursionError):
            result = None
        counts = result.get("counts", {}) if isinstance(result, dict) else {}
        if not isinstance(counts, dict):
            counts = {}
        attempts = store.conn.execute(
            "SELECT op,reason FROM discovery_attempts WHERE run_id=? AND state='settled' "
            "ORDER BY created_at LIMIT 12", (run["id"],)).fetchall()
        output.append({
            "outcome": _code(run["reason"]),
            "counts": {k: counts[k] for k in ("received", "attempted", "readable", "returned")
                       if type(counts.get(k)) is int},
            "attempts": [{"stage": row["op"], "outcome": _code(row["reason"])}
                         for row in attempts if row["op"] in ("search", "read", "tieba_page", "bili_http", "browser_resource")],
        })
    return output


def _code(value):
    return value if isinstance(value, str) and re.fullmatch(r"[a-zA-Z0-9_]{1,80}", value) else "unknown"


class ReplySections:
    """One admitted GUI request. Read body only through the exposed, narrow tool."""

    def __init__(self, cache, store, epoch, user_text, fingerprint):
        self.cache = cache
        self.store = store
        self.epoch = epoch
        self.fingerprint = fingerprint
        self.user_text = user_text.strip()
        self.used = False
        self.loaded = None
        self.targets = {}
        self.search_targets = set()
        self.latest_id = None
        cache.sync(store, epoch, fingerprint)
        if DECLINE_DETAIL.search(self.user_text) or not (
            PROCESS_ASK.search(self.user_text) or REFLECTION_ASK.search(self.user_text)
        ):
            return
        recent = store.conn.execute(
            "SELECT assistant_turn_id FROM channel_requests WHERE channel_id='gui' AND session_epoch=? "
            "AND assistant_turn_id IS NOT NULL ORDER BY assistant_turn_id DESC LIMIT 4", (epoch,)).fetchall()
        self.latest_id = recent[0][0] if recent else None
        ids = dict.fromkeys([row[0] for row in recent] + list(reversed(cache.entries)))
        for turn_id in ids:
            anchor = anchor_for(store, epoch, turn_id)
            if anchor:
                self.targets[turn_id] = anchor
                if store.conn.execute(
                    "SELECT 1 FROM discovery_runs WHERE epoch=? AND parent_request_id=? "
                    "AND phase='completed' LIMIT 1", (epoch, anchor.request_id)
                ).fetchone():
                    self.search_targets.add(turn_id)

    def index(self):
        return [{"turn_id": turn_id, "latest": turn_id == self.latest_id,
                 "cached_process": bool(entry and entry.process),
                 "cached_reflection": bool(entry and entry.reflection),
                 "has_search_receipt": turn_id in self.search_targets}
                for turn_id in self.targets
                for entry in [self.cache.entries.get(turn_id)]]

    def read(self, args, context):
        unavailable = {"status": "unavailable", "message": "该回复没有仍可读取的对应细节。按已确认依据回答，无需为取缓存重跑任务。"}
        if self.used or self.epoch != self.store.meta_get("session_epoch") or context["scope"] != "gui":
            return unavailable
        self.used = True
        turn_id, kind, quote = args["turn_id"], args["section"], args["user_quote"].strip()
        anchor = self.targets.get(turn_id)
        hint = REFLECTION_ASK if kind == "reflection" else PROCESS_ASK
        if (anchor is None or not quote or quote not in self.user_text or not hint.search(quote)
                or DECLINE_DETAIL.search(self.user_text)
                or BARE_WHY.fullmatch(self.user_text) and turn_id != self.latest_id
                or anchor_for(self.store, self.epoch, turn_id) != anchor):
            return unavailable
        self.cache.sync(self.store, self.epoch, self.fingerprint)
        entry = self.cache.entries.get(turn_id)
        value = (entry.reflection if kind == "reflection" else list(entry.process)) if entry else None
        durable = (_search_receipts(self.store, self.epoch, anchor.request_id)
                   if kind == "task_process" and turn_id in self.search_targets else None)
        if not value and not durable:
            return unavailable
        packet_ids = set(self.store.turn_source_ids(turn_id)) | set(entry.packet_ids if entry else ())
        combined = list(dict.fromkeys(context["packet_ids"] + sorted(packet_ids)))
        limit = context["limits"]["chat_dependency_limit"]
        if (limit is not None and len(combined) > limit
                or combined and not self.store.source_packets_for(combined, scope="gui")):
            return unavailable
        if packet_ids:
            try:
                self.store.protect_source_turn(context["user_turn_id"], combined, scope="gui")
            except (ValueError, sqlite3.Error):
                return unavailable
            context["packet_ids"] = combined
        self.loaded = (turn_id, anchor, tuple(sorted(packet_ids)), entry if value else None, durable)
        return {"status": "ok", "section": kind, "data": value, "search_receipts": durable, "source_turn_id": turn_id,
                "message": "data 是当时未发布的模型注释。search_receipts 是宿主记录。只用与问题直接相关的材料，"
                           "已发生的操作和错误码以有效回执为准。缺少回执时把注释按当时的说明或自评理解。"}

    def valid(self):
        if self.loaded is None:
            return True
        turn_id, anchor, ids, entry, durable = self.loaded
        return (self.epoch == self.store.meta_get("session_epoch")
                and anchor_for(self.store, self.epoch, turn_id) == anchor
                and (not ids or bool(self.store.source_packets_for(ids, scope="gui")))
                and (entry is None or (entry.expires > time.monotonic()
                     and self.cache.entries.get(turn_id) is entry))
                and (durable is None or durable == _search_receipts(self.store, self.epoch, anchor.request_id)))
