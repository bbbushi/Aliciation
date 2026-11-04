"""Evidence-bound section patches for ordinary personality growth.

Only independent ordinary event memories qualify in this first implementation.
The embedded host marker keeps body and evidence in one atomic Markdown write.
Legacy sections remain readable, explicitly without a verified evidence claim.
"""
from __future__ import annotations

import hashlib
import json
import re

from .observation_memory import version as memory_version

EDITABLE = ("生长 · 自我叙事", "生长 · 与用户的关系")
EXPRESSION = ("底色 · 气质与风格", "底色 · 情境反应", "底色 · 表达样本")
MARKER = "<!-- alice-growth-v1:"
_MARKER_LINE = re.compile(r"^<!-- alice-growth-v1:(.*?) -->\n?", re.S)
MAX_SECTION_CHARS = 6000
MAX_EVIDENCE = 16


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def bodies(md: str) -> dict[str, str]:
    from .persona_context import section_spans
    return {span["title"]: md[span["body_start"]:span["end"]].strip()
            for span in section_spans(md)}


def replace_bodies(md: str, changes: dict[str, str], *, allowed=EDITABLE) -> str:
    """Use the same section boundaries as input rendering; keep other bytes."""
    from .persona_context import section_spans, validate_section_body
    for title, body in changes.items():
        if title not in allowed:
            raise ValueError("普通演化无权修改此小节")
        validate_section_body(title, body)
        span = next((item for item in section_spans(md) if item["title"] == title), None)
        if span is not None:
            md = md[:span["body_start"]] + "\n" + body + "\n\n" + md[span["end"]:]
        else:
            md = md.rstrip("\n") + f"\n\n## {title}\n\n{body}\n"
    return md


def evidence_rows(store) -> dict[int, object]:
    # No source packets, task material, inherited stances or parent-linked prose
    # may become channel-independent personality through an ordinary event ID.
    epoch = store.meta_get("session_epoch")
    return {row["id"]: {**dict(row), "_persona_epoch": epoch}
            for row in store.context_memory_rows(scope="gui")
            if row["kind"] == "event" and not row["pressure"]}


def evidence_version(row) -> str:
    return digest(json.dumps([row["_persona_epoch"], memory_version(row)]))


def _unpack(body: str):
    if MARKER not in body:
        return body, None
    match = _MARKER_LINE.match(body)
    if match is None or MARKER in body[match.end():]:
        raise ValueError("生长依据标记损坏")
    payload = json.loads(match[1])
    text = body[match.end():].strip()
    if (not isinstance(payload, dict) or payload.get("v") != 1
            or payload.get("content_sha256") != digest(text)):
        raise ValueError("生长正文与依据版本不符")
    refs = payload.get("memories")
    if (not isinstance(refs, list) or not 1 <= len(refs) <= MAX_EVIDENCE
            or any(not isinstance(ref, dict) or type(ref.get("id")) is not int
                   or not isinstance(ref.get("version"), str) for ref in refs)
            or len({ref["id"] for ref in refs}) != len(refs)):
        raise ValueError("生长依据结构无效")
    return text, refs


def refs_valid(store, refs: list[dict]) -> bool:
    if not refs:
        return True
    available = evidence_rows(store)
    return all(ref["id"] in available
               and evidence_version(available[ref["id"]]) == ref["version"] for ref in refs)


def project(sections: dict[str, str], available: dict) -> tuple[dict, dict]:
    """Filter derived sections against live evidence. Never mutate disk."""
    result, states, used = dict(sections), {}, {}
    for title in EDITABLE:
        body = sections.get(title, "")
        try:
            text, refs = _unpack(body)
        except (ValueError, TypeError):
            result[title] = "（此节依据记录无法核对，暂不作为当前认知使用）"
            states[title] = "invalid_record"
            continue
        if refs is None:
            states[title] = "legacy_unverified" if body else "absent"
            continue
        valid = all(ref["id"] in available
                    and evidence_version(available[ref["id"]]) == ref["version"] for ref in refs)
        if valid:
            result[title] = text
            states[title] = "valid"
            used.update({ref["id"]: ref for ref in refs})
        else:
            result[title] = "（此节所依赖的记忆已变更、失效或不可用，待有新依据后修订）"
            states[title] = "unavailable_evidence"
    return result, {"sections": states, "refs": list(used.values())}


def proposal_input(current: str, available: dict, since: float) -> tuple[dict, dict]:
    sections = bodies(current)
    projected, status = project(sections, available)
    required = {ref["id"] for ref in status["refs"]}
    fresh = [row for row in available.values() if row["created_at"] > since]
    fresh.sort(key=lambda row: (row["created_at"], row["id"]))
    selected, used = {}, 0
    # Carry forward valid old dependencies before filling the remaining budget.
    rows = [available[mid] for mid in sorted(required)] + fresh
    for row in rows:
        if row["id"] in selected:
            continue
        size = len(row["title"]) + len(row["content"]) + 200
        if used + size > 24000 or len(selected) >= 100:
            raise ValueError("生长依据超出完整输入预算，暂不演化")
        selected[row["id"]] = row
        used += size
    if not required.issubset(selected):
        raise ValueError("既有生长依据超出完整输入预算，暂不演化")
    payload = {
        "sections": [{"title": title, "base_hash": digest(sections.get(title, "")),
                      "content": projected.get(title, ""),
                      "evidence_status": status["sections"].get(title, "absent")}
                     for title in EDITABLE],
        "memories": [{"id": row["id"], "title": row["title"], "content": row["content"]}
                     for row in selected.values()],
    }
    return payload, selected


def apply_proposal(current: str, data, supplied: dict, available: dict) -> tuple[str, list[dict]]:
    if not isinstance(data, dict) or set(data) != {"patches"}:
        raise ValueError("演化须返回分节提案，不接受整份人格重写")
    patches = data["patches"]
    if not isinstance(patches, list) or len(patches) > len(EDITABLE):
        raise ValueError("演化小节提案数量无效")
    sections, changes, audit = bodies(current), {}, []
    for patch in patches:
        if not isinstance(patch, dict) or set(patch) != {"title", "base_hash", "content", "evidence_ids", "reason"}:
            raise ValueError("演化小节提案结构无效")
        title = patch["title"]
        if not isinstance(title, str) or title not in EDITABLE or title in changes:
            raise ValueError("演化提案重复或越过小节权限")
        if patch["base_hash"] != digest(sections.get(title, "")):
            raise ValueError("演化提案基于旧小节，暂不提交")
        text, reason, ids = patch["content"], patch["reason"], patch["evidence_ids"]
        if (not isinstance(text, str) or not text.strip() or len(text) > MAX_SECTION_CHARS
                or re.search(r"^[ \t]*#{1,6}\s", text, re.M) or "<!--" in text
                or not isinstance(reason, str) or not reason.strip() or len(reason) > 600
                or not isinstance(ids, list) or not 1 <= len(ids) <= MAX_EVIDENCE
                or any(type(mid) is not int for mid in ids) or len(set(ids)) != len(ids)):
            raise ValueError("演化正文、理由或依据格式无效")
        if any(mid not in supplied or mid not in available
               or evidence_version(supplied[mid]) != evidence_version(available[mid]) for mid in ids):
            raise ValueError("演化依据未提供、已变更或已失效")
        text = text.strip()
        refs = [{"id": mid, "version": evidence_version(available[mid])} for mid in ids]
        record = {"v": 1, "content_sha256": digest(text), "memories": refs}
        changes[title] = MARKER + json.dumps(record, ensure_ascii=False, separators=(",", ":")) + " -->\n" + text
        audit.append({"title": title, "base_hash": patch["base_hash"],
                      "content_sha256": digest(text), "evidence": refs, "reason": reason.strip()})
    return replace_bodies(current, changes), audit
