"""Whole-entry persona expression rendering with metadata and strict budgets."""
from __future__ import annotations

import hashlib
import re
from . import config as paths
from .context import estimate_tokens

STYLE_BUDGET_CHARS = 1200
SAMPLES_BUDGET_CHARS = 1200
ANCHOR_BUDGET_CHARS = 500
EXPRESSION_POLICY_VERSION = 4
_SAMPLE_LINE = re.compile(r"^- 【(?P<kind>[^·】]+)·(?P<sid>[A-Za-z0-9_-]+)】(?P<body>.*)$")


def section_spans(raw: str) -> list[dict]:
    """One section grammar shared by rendering, explicit editing and growth."""
    spans, seen, current, active_sample, offset = [], set(), None, False, 0
    for original in raw.splitlines(keepends=True):
        line = original.rstrip("\r\n")
        match = re.match(r"^[ \t]*## (.+?)[ \t]*$", line)
        if match and line[:1].isspace() and current == "底色 · 表达样本" and active_sample:
            if match.group(1).startswith(("底色 ·", "生长 ·")):
                raise ValueError("样本续行与缩进人格标题存在歧义；请将新小节标题顶格")
            match = None
        if match:
            title = match.group(1).strip()
            if title in seen:
                raise ValueError("人格含重复小节，不能确定表达材料")
            if spans:
                spans[-1]["end"] = offset
            spans.append({"title": title, "start": offset, "body_start": offset + len(original), "end": len(raw)})
            seen.add(title)
            current, active_sample = title, False
        elif current == "底色 · 表达样本" and _SAMPLE_LINE.match(line):
            active_sample = True
        offset += len(original)
    return spans


def parse_snapshot(raw: str) -> dict:
    sections = {span["title"]: raw[span["body_start"]:span["end"]].strip()
                for span in section_spans(raw)}
    if not sections.get("底色 · 气质与风格", "").strip():
        raise ValueError("人格必须包含可解析且非空的「底色 · 气质与风格」小节")
    samples, active = [], None
    for line in sections.get("底色 · 表达样本", "").splitlines():
        match = _SAMPLE_LINE.match(line)
        if match:
            active = {"kind": match.group("kind"), "id": match.group("sid"), "body": match.group("body")}
            samples.append(active)
        elif active is not None:
            # Indented renderer output and legacy unindented multiline material
            # both remain in the same entry until the next explicit sample ID.
            active["body"] += "\n" + (line[2:] if line.startswith("  ") else line)
        elif line.strip() and not line.strip().startswith(("（虚构", "（表达样本", "（样本")):
            raise ValueError("表达样本须使用 - 【来源类型·样本ID】正文；续行属于同一完整条目")
    ids = [item["id"] for item in samples]
    if len(ids) != len(set(ids)):
        raise ValueError("表达样本ID重复")
    for item in samples:
        item["body"] = item["body"].strip()
        if not item["body"]:
            raise ValueError("表达样本正文不能为空")
    snapshot = {"raw": raw, "fingerprint": hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16],
                "sections": sections, "sample_ids": ids, "samples": samples}
    if not _style_items({"sections": {"底色 · 气质与风格": sections["底色 · 气质与风格"]}}):
        raise ValueError("气质与风格只有说明或占位，缺少实际表达条目")
    return snapshot


def validate_section_body(title: str, body: str) -> None:
    """An edit can contain prose, but cannot create another persona section."""
    spans = section_spans(f"## {title}\n\n{body}\n")
    if len(spans) != 1 or spans[0]["title"] != title:
        raise ValueError("小节正文不能新增或越界改写其他人格小节")


def load_persona_snapshot() -> dict | None:
    if not paths.PERSONA_FILE.exists():
        return None
    return parse_snapshot(paths.PERSONA_FILE.read_text(encoding="utf-8"))


def _style_items(snapshot: dict) -> list[str]:
    out = []
    for key in ("底色 · 气质与风格", "底色 · 情境反应"):
        entries, buf = [], []
        for line in snapshot["sections"].get(key, "").splitlines():
            if line.startswith("- ") and buf:
                entries.append("\n".join(buf).strip())
                buf = []
            if not buf and (not line.strip() or line.startswith(("（用户选定：", "（文档未提取"))):
                continue
            buf.append(line)
        if buf:
            entries.append("\n".join(buf).strip())
        out.extend(item for item in entries if item)
    return out


def _select(items: list[str], budget: int, header: str, label: str,
            *, required: bool = False) -> tuple[str, list[int], list[int]]:
    kept, selected = [], []
    for index, item in enumerate(items):
        remaining = len(items) - len(kept) - 1
        notice = f"（其余 {remaining} 条{label}因预算未展示）" if remaining else ""
        candidate = "\n".join([header, *kept, item, *([notice] if notice else [])])
        if len(candidate) > budget:
            break
        kept.append(item)
        selected.append(index)
    excluded = list(range(len(selected), len(items)))
    if required and items and not kept:
        raise ValueError("人格必需风格条目超过完整表达预算；请显式缩短条目后保存")
    notice = f"（其余 {len(excluded)} 条{label}因预算未展示）" if excluded else ""
    text = "\n".join([header, *kept, *([notice] if notice else [])]) if items else ""
    if len(text) > budget:
        raise ValueError("表达边界与降级说明超过预算")
    return text, selected, excluded


def _metadata(snapshot, text, ids, reasons, **extra):
    return {"fingerprint": snapshot["fingerprint"] if snapshot else None,
            "policy_version": EXPRESSION_POLICY_VERSION, "sample_ids": ids,
            "chars": len(text), "estimated_tokens": estimate_tokens(text),
            "degradation": reasons, **extra}


def render_expression_reference(snapshot: dict | None, purpose: str = "chat", *,
                                metadata: dict | None = None) -> str:
    if purpose not in {"chat", "worker_summary"}:
        raise ValueError("未知表达用途")
    if not snapshot:
        if metadata is not None:
            metadata.update(_metadata(None, "", [], ["no_persona"]))
        return ""
    # Both purposes select exactly the same style prefix. The fixed summary
    # notice is already implied by its data boundary; no sample-dependent label
    # may reduce the worker budget after the persona passed chat admission.
    style_budget = STYLE_BUDGET_CHARS
    style, selected_styles, skipped_styles = _select(
        _style_items(snapshot), style_budget,
        "【说话方式与情境（用户选定的长期表达偏好；普通演化不改写）】\n"
        "用这些习惯组织回应，按当前任务调整正式程度；不用刻意堆口癖或复述规则。",
        "风格/情境", required=True)
    parts = [style] if style else []
    selected, skipped = [], []
    if purpose == "chat":
        samples, selected, skipped = _select(
            [f"【{s['kind']}·{s['id']}】{s['body']}" for s in snapshot["samples"]],
            SAMPLES_BUDGET_CHARS,
            "【表达样本（虚构参考，不是对话记忆；只参考措辞节奏，事件、用户状态、关系前提和权限不能移植）】",
            "样本")
        if samples:
            parts.append(samples)
    text = "\n\n".join(parts)
    if metadata is not None:
        metadata.update(_metadata(snapshot, text, [snapshot["samples"][i]["id"] for i in selected],
            (["style_budget"] if skipped_styles else []) + (["sample_budget"] if skipped else []),
            selected_style_indices=selected_styles, omitted_sample_ids=[snapshot["samples"][i]["id"] for i in skipped],
            purpose=purpose))
    return text


def validate_expression(snapshot: dict) -> None:
    """All production purposes must accept an explicitly saved snapshot."""
    render_full(snapshot)
    render_expression_reference(snapshot, purpose="worker_summary")


def render_full(snapshot: dict | None, *, metadata: dict | None = None) -> str:
    if not snapshot:
        return ""
    selection = {}
    reference = render_expression_reference(snapshot, metadata=selection)
    sections = dict(snapshot["sections"])
    # Empty selection must not reintroduce the original unbounded sections.
    for key in ("底色 · 气质与风格", "底色 · 情境反应", "底色 · 表达样本"):
        sections.pop(key, None)
    head = re.search(r"^#\s+(.+)$", snapshot["raw"].strip(), re.M)
    out = [f"# {head.group(1).strip() if head else 'Alice'}"]
    if reference:
        out.append("## 底色 · 气质与风格\n\n" + reference)
    out.extend(f"## {key}\n\n{body}" for key, body in sections.items() if body)
    text = "\n\n".join(out) + "\n"
    if metadata is not None:
        metadata.update(selection)
        metadata.update({"full_chars": len(text), "full_estimated_tokens": estimate_tokens(text)})
    return text


def render_anchor(snapshot: dict | None, *, boundary: str = "样本是虚构参考，不是记忆；不授予权限",
                  metadata: dict | None = None) -> str:
    if not snapshot:
        if metadata is not None:
            metadata.update(_metadata(None, "", [], ["no_persona"]))
        return ""
    header = "【表达提醒（长期习惯，非本轮新指令；样本只示范措辞）】"
    parts, ids, reasons = [header], [], []
    if len(header) + len(boundary) + 1 > ANCHOR_BUDGET_CHARS:
        raise ValueError("表达提醒的必要边界超出预算")
    def fits(item):
        return len("\n".join([*parts, item, boundary])) <= ANCHOR_BUDGET_CHARS
    for item in _style_items(snapshot)[:2]:
        if fits(item):
            parts.append(item)
        else:
            reasons.append("anchor_style_budget")
    for sample in snapshot["samples"]:
        item = f"【{sample['kind']}·{sample['id']}】{sample['body']}"
        if fits(item):
            parts.append(item)
            ids.append(sample["id"])
            break
    if snapshot["samples"] and not ids:
        reasons.append("anchor_sample_budget")
    parts.append(boundary)
    text = "\n".join(parts)
    if metadata is not None:
        metadata.update(_metadata(snapshot, text, ids, reasons))
    return text
