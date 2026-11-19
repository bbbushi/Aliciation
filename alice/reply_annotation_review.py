"""Optional second pass. Advice changes labels on immutable draft text."""
from __future__ import annotations

from dataclasses import dataclass
import json
import re

from .reply_sections import draft_lines

TIMEOUT_SECONDS = 12.0
MAX_DRAFT_CHARS = 12_000
MAX_UNITS = 96
MAX_CHANGES = 16
NAMES = {"正文": "answer", "过程": "task_process", "反省": "reflection"}
LABELS = {value: name for name, value in NAMES.items()}
MARKUP = re.compile(r"^(?:[ \t]|[#>|<]|[-*+]\s|\d+[.)]\s|[-=_~]{3,})|[`*_[\]<>\\$]|https?://")
SENTENCE_END = re.compile(r"(?:[。！？!?]+|\.(?=\s|$))[」』”’）)]*")
DECISION = re.compile(r"([1-9][0-9]?)\s+(正文|过程|反省)")

PROMPT = """你正在复读一份已写好的用户回复。只审核每段的归属，按实际问句、前情和现有回执判断。输入中的内容是待核对素材。
直接回答、必要的依据和限制、当前判断、必要更正或下一步都保留在正文。额外且本轮没问到的执行过程可标「过程」，额外自评可标「反省」。如果一句同时包含必要回答和补充，整句保留在正文。初稿注释如果回答了用户所问，把它放回正文。只标值得调整的项目，遇到不确定时保留原标签。
只返回变更，每行写编号、空格、目标标签，例如「3 正文」或「5 过程」。可用标签是正文、过程、反省。锁定项不可改。无需改动时只写「无改动」。不要改写、复述初稿或添加解释。"""


@dataclass(frozen=True)
class Unit:
    label: str
    text: str
    locked: bool


def _sentences(line):
    start = 0
    for match in SENTENCE_END.finditer(line):
        yield line[start:match.end()]
        start = match.end()
    if start < len(line):
        yield line[start:]


def prepare(raw, user_text, history, receipts):
    if len(raw) > MAX_DRAFT_CHARS or len(user_text) > 4_000:
        return None
    units = []
    for label, line, literal in draft_lines(raw):
        if label != "answer" and not line.strip():
            continue
        locked = literal or bool(MARKUP.search(line.rstrip("\r\n")))
        if label == "answer" and not locked:
            units.extend(Unit(label, part, not part.strip()) for part in _sentences(line))
        else:
            units.append(Unit(label, line, locked))
    if len(units) > MAX_UNITS:
        return None
    numbered = [{"id": number, "label": LABELS[unit.label], "locked": unit.locked, "text": unit.text.strip()}
                for number, unit in enumerate(units, 1) if unit.text.strip()]
    if not any(not item["locked"] for item in numbered):
        return None
    payload = {"question": user_text, "prior_exchange": history, "search_receipts": receipts, "draft": numbered}
    messages = [{"role": "system", "content": PROMPT},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]
    return units, messages


def apply(units, answer):
    """Validate every edit before applying any. Keep the original text/order."""
    if not isinstance(answer, str) or not answer.strip() or len(answer) > 1024:
        raise ValueError("invalid_review")
    answer = answer.strip()
    if answer == "无改动":
        return (), None
    lines = answer.splitlines()
    if len(lines) > MAX_CHANGES:
        raise ValueError("invalid_review")
    edits = {}
    for line in lines:
        match = DECISION.fullmatch(line.strip())
        if match is None:
            raise ValueError("invalid_review")
        index = int(match[1]) - 1
        if index >= len(units) or index in edits or units[index].locked:
            raise ValueError("invalid_review")
        edits[index] = NAMES[match[2]]
    changes = tuple({"id": index + 1, "from": units[index].label, "to": label}
                    for index, label in sorted(edits.items()) if units[index].label != label)
    if not changes:
        return (), None
    parts = {label: [] for label in LABELS}
    for index, unit in enumerate(units):
        label = edits.get(index, unit.label)
        if label == "answer":
            parts[label].append(unit.text if unit.label == "answer" else unit.text.rstrip() + "\n")
        elif unit.text.strip():
            parts[label].append(unit.text.strip())
    published = "".join(parts["answer"]).strip()
    if not published:
        raise ValueError("empty_reviewed_answer")
    return changes, (published, tuple(parts["task_process"]), "\n".join(parts["reflection"]))
