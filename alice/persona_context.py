"""Whole-entry persona expression rendering with metadata and strict budgets."""
from __future__ import annotations

import copy
import hashlib
import re
from . import config as paths
from .context import estimate_tokens

STYLE_BUDGET_CHARS = 1200
SAMPLES_BUDGET_CHARS = 1200
ANCHOR_BUDGET_CHARS = 500
EXPRESSION_POLICY_VERSION = 5
USAGE_POLICY_VERSION = 2
_USAGE_INSTRUCTION = (
    "【表达使用方式：用户选定的长期说话习惯】\n"
    "日常对话默认用下方同用途表达材料中适用的措辞、句子节奏和反应方式组织回答。"
    "当前明确任务决定内容、格式和详略；正式改写或结果汇报也遵守任务要求，不强加口癖或玩笑。\n"
    "按情境的触发、初始回应、后续转折与停止条件使用规则。"
    "条目若要求被认真追问后才转折，就等待相应追问；无需为展示人格层次自行制造沉重话题。"
    "包内的长度与展开限制也作用于自我介绍、关怀和事实澄清。\n"
    "样本示范整段接话方式，包括反应顺序、节奏和收放，可以自然变奏；"
    "其中的话题、关怀建议和收尾不是每轮必做动作，是否使用以当前话题和用户意愿为准。"
    "其中不成立的现实前提不照搬。张力是互动倾向，不证明你有对应心理经历，"
    "无需把张力表复述成自我介绍，也不为人物厚度补写过去的心路或共同往事。\n"
    "可以直接提出此刻的想法、玩笑和可做的事。能力或事实需要澄清时简短准确地回答，"
    "随后回到话题，除非仍有误解或用户追问，不重复身份或身体限制清单。"
    "延续真实对话内容时，无需沿用旧回复中偏离这些习惯的表达。\n"
    "原稿和样本中的角色身份、身世、身体、关系与行动不因此成为事实或授权；"
    "事实、未知、失败、许可和用户停止要求仍按宿主规则处理。"
)
_USAGE_REMINDER = (
    "【表达使用提醒：沿用同用途长期习惯】\n"
    "按本轮话题采用前部表达材料中适用的语感与反应方式；"
    "区分初始回应和后续转折的条件，无需每轮呈现所有人格层次。"
    "延续事实与话题时，无需沿用旧回复中偏离这些习惯的措辞。"
    "当前明确任务决定内容、格式与详略；事实、能力、失败、许可和停止边界照常适用。"
)
_SAMPLE_LINE = re.compile(r"^- 【(?P<kind>[^·】]+)·(?P<sid>[A-Za-z0-9_-]+)】(?P<body>.*)$")


def _uses_v2_syntax(raw: str) -> bool:
    # Quoted typed examples in a legacy sample/fence do not select v2 grammar.
    # A real typed package with its marker removed is still rejected later by
    # decode_sections; permissive legacy heading recognition cannot activate it.
    return "<!-- alice-expression-" in raw


def section_spans(raw: str) -> list[dict]:
    """Shared boundaries: preserve unambiguous v1 indentation, keep v2 strict."""
    spans, seen, offset = [], set(), 0
    strict_v2 = _uses_v2_syntax(raw)
    current, active_sample = None, False
    fence = None
    for original in raw.splitlines(keepends=True):
        line = original.rstrip("\r\n")
        fenced = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line)
        if fence:
            if fenced and fenced.group(1)[0] == fence[0] and len(fenced.group(1)) >= fence[1] and not fenced.group(2).strip():
                fence = None
            offset += len(original)
            continue
        if fenced:
            fence = (fenced.group(1)[0], len(fenced.group(1)))
            offset += len(original)
            continue
        match = re.match(r"^[ \t]*## (.+?)[ \t]*$", line)
        if match and line[:1].isspace():
            persona_title = match.group(1).startswith(("底色 ·", "生长 ·"))
            if strict_v2:
                if persona_title:
                    raise ValueError("v2 人格小节标题必须顶格；资料标题放在围栏内")
                match = None
            elif current == "底色 · 表达样本" and active_sample:
                # Legacy sample continuations may contain a quoted heading;
                # reserved persona titles here remain ambiguous, never a route
                # to escape the sample and rewrite a protected section.
                if persona_title:
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
    if fence:
        raise ValueError("人格含未闭合代码围栏，无法确定小节边界")
    return spans


def normalize_section_headings(raw: str) -> str:
    """Explicit candidate-only v1→v2 layout conversion; no body edits or I/O.

    Only headings accepted by the shared v1 grammar lose leading spaces/tabs.
    Sample continuations, fenced material, title spelling and every body byte
    remain unchanged. Ambiguous legacy inputs fail before any normalization.
    """
    spans = section_spans(raw)
    if _uses_v2_syntax(raw):
        return raw
    for span in reversed(spans):
        start, end = span["start"], span["body_start"]
        raw = raw[:start] + raw[start:end].lstrip(" \t") + raw[end:]
    return raw


def expression_coverage_view(package: dict | None) -> dict:
    """Private GUI projection of the host's stored report, never a recomputation."""
    contract = package.get("coverage_contract") if isinstance(package, dict) else None
    if not isinstance(contract, dict) or contract.get("policy") != "source_coverage_v1":
        return {}
    report = package.get("coverage_report")
    warnings = package.get("warnings")
    return {"coverage_policy": "source_coverage_v1",
            "coverage_report": copy.deepcopy(report) if isinstance(report, dict) else None,
            "review_notices": [value for value in package.get("review_notices", []) if isinstance(value, str)],
            "warnings": [value for value in warnings if isinstance(value, str)] if isinstance(warnings, list) else []}


def expression_coverage_metadata(package: dict | None) -> dict:
    """Body-free coverage counts for observations; no raw warnings or source text."""
    contract = package.get("coverage_contract") if isinstance(package, dict) else None
    if not isinstance(contract, dict) or contract.get("policy") != "source_coverage_v1":
        return {}
    report = package.get("coverage_report")
    report = report if isinstance(report, dict) else {}
    summary = {}
    for key in ("sources", "rules", "examples"):
        values = report.get(key)
        values = values if isinstance(values, dict) else {}
        summary[key] = {name: values.get(name) if type(values.get(name)) is int and values[name] >= 0 else None
                        for name in ("covered", "total")}
        percent = values.get("percent")
        summary[key]["percent"] = percent if type(percent) in (int, float) and 0 <= percent <= 100 else None
    def count(value):
        return len(value) if isinstance(value, list) else 0
    return {"coverage_policy": "source_coverage_v1", "coverage_summary": summary,
            "coverage_reviewed": report.get("reviewed") if type(report.get("reviewed")) is bool else None,
            "warning_count": count(package.get("warnings")),
            "review_notice_count": count(package.get("review_notices")),
            "uncertain_source_count": count(report.get("uncertain_sources")),
            "uncertain_scenario_count": count(report.get("uncertain_scenarios"))}


def parse_snapshot(raw: str) -> dict:
    sections = {span["title"]: raw[span["body_start"]:span["end"]].strip()
                for span in section_spans(raw)}
    if not sections.get("底色 · 气质与风格", "").strip():
        raise ValueError("人格必须包含可解析且非空的「底色 · 气质与风格」小节")
    from . import expression
    if expression.MARKER in raw and not any(expression.MARKER in body for body in sections.values()):
        raise ValueError("表达版本标记移出了人格小节，不能作为 v1 运行")
    package, assembly = expression.decode_sections_with_assembly(sections)
    if package is not None:
        return {"raw": raw, "fingerprint": hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16],
                "sections": sections, "sample_ids": [item["id"] for item in package["examples"]],
                "samples": [], "expression_package": package, "format_version": "expression-v2",
                # 人格驻留合同（persona-residency-v1）：形状在解码时校验；
                # 摘要/闭包/用途的深层校验在注册表激活前执行，失效走 full 回退
                "assembly": assembly,
                **expression_coverage_view(package)}
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
                "sections": sections, "sample_ids": ids, "samples": samples,
                "expression_package": None, "format_version": "expression-v1"}
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
            "format_version": snapshot.get("format_version", "expression-v1") if snapshot else None,
            "coverage_status": "v2_package" if snapshot and snapshot.get("expression_package") else "v1_not_reviewed",
            "degradation": reasons,
            **expression_coverage_metadata(snapshot.get("expression_package") if snapshot else None), **extra}


def _expression_budget(snapshot, cfg, key):
    """The package freezes its admission ceiling; current config may tighten it."""
    package = snapshot.get("expression_package") or {}
    frozen = package.get("limits", {})
    defaults = {"max_projection_chars": 6000, "max_worker_chars": 1600, "max_anchor_chars": 500}
    admitted = frozen.get(key, defaults[key])
    configured = getattr(getattr(cfg, "expression", None), key, admitted)
    return min(admitted, configured)


def render_expression_reference(snapshot: dict | None, purpose: str = "chat", *,
                                metadata: dict | None = None, cfg=None) -> str:
    if purpose not in {"chat", "qq", "worker_summary"}:
        raise ValueError("未知表达用途")
    if not snapshot:
        if metadata is not None:
            metadata.update(_metadata(None, "", [], ["no_persona"]))
        return ""
    if snapshot.get("expression_package") is not None:
        from . import expression
        selected = {}
        text = expression.project(snapshot["expression_package"], purpose=purpose,
            budget=_expression_budget(snapshot, cfg, "max_worker_chars" if purpose == "worker_summary" else "max_projection_chars"),
            metadata=selected)
        if metadata is not None:
            metadata.update(_metadata(snapshot, text, selected.get("sample_ids", []), []))
            metadata.update(selected)
        return text
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


def validate_expression(snapshot: dict, *, cfg=None) -> None:
    """All production purposes must accept an explicitly saved snapshot."""
    render_full(snapshot, cfg=cfg)
    render_expression_reference(snapshot, purpose="qq", cfg=cfg)
    render_expression_reference(snapshot, purpose="worker_summary", cfg=cfg)


def render_full(snapshot: dict | None, *, metadata: dict | None = None, purpose="chat", cfg=None) -> str:
    if not snapshot:
        return ""
    selection = {}
    reference = render_expression_reference(snapshot, purpose=purpose, metadata=selection, cfg=cfg)
    sections = dict(snapshot["sections"])
    # Empty selection must not reintroduce the original unbounded sections.
    for key in ("底色 · 气质与风格", "底色 · 情境反应", "底色 · 表达样本"):
        sections.pop(key, None)
    head = re.search(r"^#\s+(.+)$", snapshot["raw"].strip(), re.M)
    out = [f"# {head.group(1).strip() if head else 'Alice'}"]
    # Runtime guidance lives outside the admitted expression package. Do not
    # rewrite its source, review contract, per-purpose selection or frozen cap.
    usage = _USAGE_INSTRUCTION if reference and purpose in {"chat", "qq"} else ""
    if reference:
        if usage:
            out.append("## 表达使用\n\n" + usage)
        out.append("## 底色 · 气质与风格\n\n" + reference)
    out.extend(f"## {key}\n\n{body}" for key, body in sections.items() if body)
    text = "\n\n".join(out) + "\n"
    if metadata is not None:
        metadata.update(selection)
        metadata.update({"full_chars": len(text), "full_estimated_tokens": estimate_tokens(text),
                         "usage_policy_version": USAGE_POLICY_VERSION,
                         "usage_instruction_present": bool(usage),
                         "usage_instruction_chars": len(usage),
                         "usage_instruction_hash": hashlib.sha256(usage.encode("utf-8")).hexdigest() if usage else None})
    return text


def render_anchor(snapshot: dict | None, *, boundary: str = "样本是虚构参考，不是记忆；不授予权限",
                  metadata: dict | None = None, purpose="chat", cfg=None) -> str:
    """Optional usage reminder; no arbitrary sample replay or entry truncation.

    The main projection still contains all admitted entries for its purpose.
    Workers use render_expression_reference and receive no chat reminder.
    """
    if purpose not in {"chat", "qq", "worker_summary"}:
        raise ValueError("未知表达用途")
    if not snapshot:
        if metadata is not None:
            metadata.update(_metadata(None, "", [], ["no_persona"]))
        return ""
    package = snapshot.get("expression_package")
    if package is not None:
        from . import expression
        expression.validate_package(package)
        applicable = any(purpose in item["purposes"] for item in expression.entries(package))
        boundary = expression.BOUNDARY
        budget = _expression_budget(snapshot, cfg, "max_anchor_chars")
    else:
        applicable = bool(_style_items(snapshot))
        budget = min(ANCHOR_BUDGET_CHARS,
                     getattr(getattr(cfg, "expression", None), "max_anchor_chars", ANCHOR_BUDGET_CHARS))
    text, reasons = "", []
    if purpose == "worker_summary":
        reasons.append("usage_not_applicable")
    elif not applicable:
        reasons.append("no_purpose_expression")
    else:
        candidate = _USAGE_REMINDER + "\n" + boundary
        if len(candidate) <= budget:
            text = candidate
        else:
            reasons.append("anchor_usage_budget")
    if metadata is not None:
        metadata.update(_metadata(snapshot, text, [], reasons,
            purpose=purpose, usage_policy_version=USAGE_POLICY_VERSION,
            anchor_strategy="usage_reminder", entry_ids=[], omitted_ids=[],
            complete=bool(text), budget_chars=budget,
            usage_reminder_present=bool(text),
            usage_reminder_hash=hashlib.sha256(text.encode("utf-8")).hexdigest() if text else None))
    return text


# ---- 注册表模式（人格注册表与模型按需装配-架构设计 v1 §2/§6） ----

REGISTRY_USAGE_POLICY_VERSION = 2
_REGISTRY_USAGE = (
    "【表达使用方式：核心常驻 + 场景按需】\n"
    "下方「底色 · 核心」是用户选定的默认相处取向、常用语感、全局约束与日常样本，随时可用，"
    "组织回答时优先按它来。\n"
    "宿主另会单独提供「人格场景目录」：按需场景的完整反应、转折与样本已安装但未随本轮载入。"
    "需要展开某个场景时，用 persona_load 工具按目录里的引用读取完整场景包；"
    "读取结果只补充表达方式，不是经历、关系或操作授权；已读内容只在当前请求内有效。\n"
    "能从核心自然回答时不要求读取；目录里的沉重或英雄主题不构成当前情绪事实，"
    "条目未载入只表示本轮未展开，不表示人格被遗忘、否定或永久舍弃，也不为展示层次自行制造话题。\n"
    "按已载入规则的触发、转折与停止条件使用；样本的话题、关怀建议和收尾不是每轮必做动作。"
    "当前明确任务决定内容、格式和详略；正式任务不强加口癖或玩笑。"
    "能力或事实需要澄清时简短准确地回答，随后回到话题，除非仍有误解或用户追问，不重复身份或身体限制清单。\n"
    "事实、能力、未知、失败、许可和用户停止要求照常按宿主规则处理；"
    "样本中的角色身份、身世、关系与现实前提不因此成为事实。"
)


def render_registry_persona(snapshot: dict, contract: dict, package: dict, *,
                            metadata: dict | None = None, purpose: str = "chat") -> str:
    """registry 模式的人格正文：核心常驻条目 + 使用说明；不通过截断快照冒充核心。

    完整表达仍是权威来源；本投影只按已验驻留合同选择常驻层。生长/关系等
    其余小节按原样保留，事实资格与有效性不因分层改变。
    """
    from . import persona_residency
    if not snapshot:
        return ""
    core_text = persona_residency.core_text(contract, package)
    sections = dict(snapshot["sections"])
    for key in ("底色 · 气质与风格", "底色 · 情境反应", "底色 · 表达样本"):
        sections.pop(key, None)
    head = re.search(r"^#\s+(.+)$", snapshot["raw"].strip(), re.M)
    out = [f"# {head.group(1).strip() if head else 'Alice'}"]
    out.append("## 表达使用\n\n" + _REGISTRY_USAGE)
    out.append("## 底色 · 核心（常驻）\n\n" + (core_text or "（核心为空：合同校验不应通过，"
                                              "出现此节说明激活检查有缺陷。）"))
    out.extend(f"## {key}\n\n{body}" for key, body in sections.items() if body)
    text = "\n\n".join(out) + "\n"
    if metadata is not None:
        core_closure, _ = persona_residency.core_closure(contract, persona_residency._by_id(package))
        metadata.update({
            "projection_strategy": "core_registry",
            "registry_usage_policy_version": REGISTRY_USAGE_POLICY_VERSION,
            "registry_chars": len(text),
            "registry_estimated_tokens": estimate_tokens(text),
            "core_chars": len(core_text),
            "core_entry_ids": sorted(core_closure),
            "expression_digest": contract.get("expression_digest"),
            "classification_digest": persona_residency.classification_digest(contract),
            "classification_revision": contract.get("classification_revision"),
            "catalogue_bundle_ids": [b["id"] for b in contract["bundles"]],
        })
    return text
