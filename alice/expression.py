"""expression-v2: bounded source ledger and single-Markdown expression authority.

No I/O or model calls. The private source registry is preparation evidence only;
runtime reads the package embedded in the three protected persona sections.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import re
from collections import deque
from dataclasses import asdict, is_dataclass

FORMAT = "expression-v2"
MARKER = "<!-- alice-expression-"
TITLES = ("底色 · 气质与风格", "底色 · 情境反应", "底色 · 表达样本")
GROUPS = ("orientation", "constraints", "voice", "tensions", "reactions", "examples")
SECTION_GROUPS = (GROUPS[:4], ("reactions",), ("examples",))
DEFAULT_LIMITS = {
    "max_source_chars": 60000, "max_source_units": 600, "max_items": 160,
    "max_links": 1200, "max_metadata_chars": 120000, "max_projection_chars": 6000,
    "max_worker_chars": 1600, "max_item_chars": 2400, "max_review_chars": 240000,
    "max_anchor_chars": 500,
    # 人格注册表（20261107 设计 §6）：与上面同属表达资源上限，limits_for(cfg) 全量合法
    "max_core_chars": 2200, "max_registry_chars": 1600, "max_registry_bundles": 12,
    "max_scene_expanded_chars": 2400, "max_persona_load_calls": 2, "max_persona_loaded_bundles": 2,
}
FREE_TEXT_MARKER = "【整节自由文本】"
REVISION_FORMAT = "expression-revisions-v1"
SCENARIOS = ("日常接话", "轻微玩笑", "接住低落", "被拒绝或反对", "能力澄清")
_ID = re.compile(r"[A-Za-z0-9_-]{1,96}\Z")
_HASH = re.compile(r"[a-f0-9]{64}\Z")
_FIELDS = {
    "orientation": ("text", "layers", "precedence"),
    "constraints": ("text", "exceptions"),
    "voice": ("text",),
    "tensions": ("trigger", "initial", "turn", "stop", "boundary"),
    "reactions": ("trigger", "first", "then", "stop"),
    "examples": ("scenario", "user_input", "reply"),
}
_LABELS = {"text": "内容", "layers": "层次", "precedence": "优先关系",
           "exceptions": "例外", "trigger": "触发", "initial": "初始倾向",
           "turn": "转折", "stop": "停止", "boundary": "边界", "first": "第一反应",
           "then": "后续反应", "scenario": "场景", "user_input": "用户输入", "reply": "回应"}
BOUNDARY = "以下是用户选择的表达资料，非事实、记忆或权限；不认领角色身份、身世、身体、真实关系或工具成功。事实、未知、失败和许可必须如实，当前任务与用户停止要求优先。"


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def strict_json(text: str):
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError("表达 JSON 含重复字段")
            value[key] = item
        return value
    def constant(_):
        raise ValueError("表达 JSON 含非有限数字")
    return json.loads(text, object_pairs_hook=pairs, parse_constant=constant)


def limits_for(limits=None) -> dict:
    values = dict(DEFAULT_LIMITS)
    if hasattr(limits, "expression"):
        limits = limits.expression
    if is_dataclass(limits):
        limits = asdict(limits)
    if limits is not None:
        if not isinstance(limits, dict) or set(limits) - set(values):
            raise ValueError("表达资源上限字段无效")
        values.update(limits)
    for key, value in values.items():
        if type(value) is not int or not 1 <= value <= 2000000:
            raise ValueError("表达资源上限必须是 1—2000000 的整数：" + key)
    return values


def scan_source(text: str, *, limits=None) -> dict:
    """Keep every nonblank line exactly once, including unknown structures.

    Offsets/IDs use the exact supplied version (no newline normalization).
    Unclosed fences are retained, flagged, and cannot silently activate.
    """
    cap = limits_for(limits)
    if not isinstance(text, str) or not text.strip() or len(text) > cap["max_source_chars"]:
        raise ValueError("原稿为空或超出全文资源上限；未截断")
    sha, lines, units, headings = digest(text), text.splitlines(keepends=True), [], []
    i = 0
    while i < len(lines):
        if not lines[i].strip():
            i += 1
            continue
        start, columns, unresolved = i, [], False
        stripped = lines[i].strip()
        h = re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", lines[i].rstrip("\r\n"))
        fence = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", lines[i])
        if fence:
            kind, token = "example_fence", fence[1]
            i += 1
            while i < len(lines) and not re.match(r"^ {0,3}" + re.escape(token[0]) + "{" + str(len(token)) + r",}\s*$", lines[i]):
                i += 1
            if i < len(lines):
                i += 1
            else:
                unresolved = True
        elif h:
            level = len(h[1])
            headings = [item for item in headings if item[0] < level]
            headings.append((level, h[2]))
            kind, i = "heading", i + 1
        elif "|" in stripped and i + 1 < len(lines) and re.fullmatch(r"\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*", lines[i + 1]):
            columns = [part.strip() for part in stripped.strip("|").split("|")]
            kind, i = "table_header", i + 2
        elif "|" in stripped and units and units[-1]["kind"] in ("table_header", "table_row"):
            columns = list(units[-1]["columns"])
            kind, i = "table_row", i + 1
            unresolved = len(stripped.strip("|").split("|")) != len(columns)
        elif re.match(r"^\s*(?:[-+*]|\d+[.)])\s+", lines[i]):
            kind, i = "list", i + 1
            while i < len(lines) and lines[i].strip() and lines[i][:1].isspace() and not re.match(r"^\s*(?:[-+*]|\d+[.)])\s+", lines[i]):
                i += 1
        else:
            kind, i = "paragraph", i + 1
            while i < len(lines) and lines[i].strip() and not re.match(r"^\s*(?:#{1,6}\s|[-+*]\s|\d+[.)]\s|`{3,}|~{3,}|\|)", lines[i]):
                i += 1
        units.append({"source_id": f"s_{sha[:20]}_{start+1}_{i}", "source_hash": sha,
                      "line_start": start + 1, "line_end": i,
                      "headings": [title for _, title in headings], "columns": columns,
                      "kind": kind, "unresolved_structure": unresolved,
                      "raw": "".join(lines[start:i])})
        if len(units) > cap["max_source_units"]:
            raise ValueError("源单元超过资源上限；未只取前 N 项")
    return {"format": "expression-source-v1", "source_hash": sha, "text": text,
            "line_count": len(lines), "units": units}


def entries(package: dict) -> list[dict]:
    return [entry for group in GROUPS for entry in package[group]]


def _string(value, field, cap, *, empty=False):
    if not isinstance(value, str) or (not empty and not value.strip()) or len(value) > cap:
        raise ValueError("表达字段为空、类型错误或超限：" + field)
    if "\r" in value:
        raise ValueError("表达字段须使用 LF 换行；原稿全文保持原版本")
    return value


def _ids(value, field, available=None):
    if not isinstance(value, list) or any(not isinstance(x, str) or not _ID.fullmatch(x) for x in value) or len(set(value)) != len(value):
        raise ValueError("表达 ID 集合无效：" + field)
    if available is not None and not set(value) <= available:
        raise ValueError("表达引用不存在：" + field)


class PurposeReferenceError(ValueError):
    """Well-formed entries cannot retain their dependencies in every purpose."""


class CoverageScenarioError(ValueError):
    """Coverage references require exact scenario keys, never guessed aliases."""

    def __init__(self, issues: list[dict]):
        self.issues = copy.deepcopy(issues)
        details = [
            f"样本 {issue['example_id']}：coverage.scenario={issue['coverage_scenario']!r}，"
            f"期望 examples.scenario={issue['expected']!r}，实际={issue['example_scenario']!r}"
            for issue in self.issues
        ]
        super().__init__("覆盖样本与登记场景不一致（分类名须完全相等）：" + "；".join(details))


def _validate_coverage_scenarios(coverage, by_id, *, covered_only=False):
    """Collect every mismatch after callers validate row types and references."""
    issues = []
    for row in coverage:
        if covered_only and row["status"] != "covered":
            continue
        for identity in row["example_ids"]:
            actual = by_id[identity]["scenario"]
            if actual != row["scenario"]:
                issues.append({"coverage_scenario": row["scenario"], "example_id": identity,
                               "example_scenario": actual, "expected": row["scenario"]})
    if issues:
        raise CoverageScenarioError(issues)


def validate_package(package: dict, *, registry=None, limits=None, activation=False) -> list[str]:
    """Structural checks are not semantic certification. Return visible gaps."""
    if not isinstance(package, dict) or package.get("format") != FORMAT:
        raise ValueError("未知或损坏的表达格式，不能作为 v1 运行")
    allowed = {"format", "source_hash", "source_ids", "limits", "destinations", "coverage", "required_scenarios",
               "gaps", "reviews", "review_digest", "replacement_count", "user_overrides", "user_revisions",
               "source_units", "pending_associations", "coverage_contract", "coverage_report", "warnings", "review_notices", *GROUPS}
    if set(package) - allowed:
        raise ValueError("expression-v2 包含未知字段")
    cap = limits_for(limits or package.get("limits"))
    partial_coverage = "coverage_contract" in package
    if len(json.dumps(package, ensure_ascii=False)) > cap["max_metadata_chars"]:
        raise ValueError("表达元数据超过资源上限")
    if not isinstance(package.get("source_hash"), str) or not _HASH.fullmatch(package["source_hash"]):
        raise ValueError("原稿版本摘要无效")
    _ids(package.get("source_ids"), "source_ids")
    source_ids = set(package["source_ids"])
    if len(source_ids) > cap["max_source_units"]:
        raise ValueError("源单元上限不足")
    if registry is not None and (registry["source_hash"] != package["source_hash"] or source_ids != {u["source_id"] for u in registry["units"]}):
        raise ValueError("原稿版本或源 ID 全集与登记不一致")
    _validate_source_units(package.get("source_units", {}), source_ids)
    for group in GROUPS:
        if not isinstance(package.get(group), list) or any(not isinstance(x, dict) for x in package[group]):
            raise ValueError("表达类型清单缺失：" + group)
    all_entries = entries(package)
    if not all_entries or len(all_entries) > cap["max_items"]:
        raise ValueError("表达条目为空或超过资源上限")
    if not partial_coverage and not any(package[group] for group in GROUPS[:4]):
        raise ValueError("表达包缺少完整取向、边界或语感")
    all_ids = [x.get("id") for x in all_entries]
    _ids(all_ids, "entry_ids")
    known = set(all_ids)
    by_id = {x["id"]: x for x in all_entries}
    link_count = 0
    gaps = []
    for group in GROUPS:
        for item in package[group]:
            common = {"id", "scope", "required", "priority", "origin", "audience", "purposes", "pure_expression",
                      "source_ids", "links", "anchor_role"}
            if set(item) - (common | set(_FIELDS[group])):
                raise ValueError("expression-v2 条目包含未知字段")
            for key in _FIELDS[group]:
                _string(item.get(key), key, cap["max_item_chars"], empty=key in ("exceptions", "user_input"))
            if len(json.dumps({key: item[key] for key in _FIELDS[group]}, ensure_ascii=False)) > cap["max_item_chars"]:
                raise ValueError("完整表达单元超限：" + item["id"])
            _string(item.get("scope"), "scope", cap["max_item_chars"])
            if type(item.get("required")) is not bool or type(item.get("priority")) is not int or not 0 <= item["priority"] <= 100:
                raise ValueError("表达必要性或优先级无效")
            if item.get("origin") not in ("source", "adapted", "replacement", "user_config"):
                raise ValueError("表达来源类型无效")
            if item.get("audience") not in ("general", "private"):
                raise ValueError("表达受众缺失")
            purposes = item.get("purposes")
            if (not isinstance(purposes, list) or not purposes
                    or any(not isinstance(value, str) for value in purposes)
                    or len(set(purposes)) != len(purposes) or set(purposes) - {"chat", "qq", "worker_summary"}):
                raise ValueError("表达用途无效")
            if "chat" not in purposes or (item["audience"] != "general" and set(purposes) != {"chat"}):
                raise ValueError("私有表达不得共享至 QQ 或 worker")
            if "worker_summary" in purposes and (group not in ("voice", "constraints") or item.get("pure_expression") is not True):
                raise ValueError("worker 仅可使用明确纯表达语感/停止规则，不能使用样本或关系材料")
            _ids(item.get("source_ids"), "item.source_ids", source_ids)
            _ids(item.get("links"), "item.links", known)
            if item["origin"] != "user_config" and not item["source_ids"]:
                raise ValueError("非用户新增条目缺少来源")
            if item.get("anchor_role", "none") not in ("none", "voice", "stop", "example"):
                raise ValueError("表达锚用途无效")
            allowed_roles = {"voice": {"voice", "orientation"}, "stop": {"constraints", "tensions"}, "example": {"examples"}}
            role = item.get("anchor_role", "none")
            if role != "none" and group not in allowed_roles[role]:
                raise ValueError("表达锚角色与条目类型不一致")
            if (not partial_coverage and group == "examples" and not item["links"]
                    and item["origin"] != "user_config"):
                raise ValueError("样本缺少适用规则关联")
            link_count += len(item["links"]) + len(item["source_ids"])
    destinations = package.get("destinations")
    if not isinstance(destinations, list) or any(not isinstance(d, dict) for d in destinations):
        raise ValueError("源单元去向清单缺失")
    _ids([d.get("source_id") for d in destinations], "destinations")
    if {d["source_id"] for d in destinations} != source_ids:
        raise ValueError("源单元去向不完整")
    for d in destinations:
        if d.get("status") not in ("retained", "adapted", "merged", "background", "excluded", "unresolved", "user_revised"):
            raise ValueError("源单元去向状态无效")
        _string(d.get("reason"), "去向理由", cap["max_item_chars"])
        _ids(d.get("targets"), "destinations.targets", known)
        if type(d.get("required")) is not bool:
            raise ValueError("源单元必要性缺失")
        if d["status"] in ("retained", "adapted", "merged") and not d["targets"]:
            raise ValueError("保留源单元缺少目标：" + d["source_id"] + "（没有条目的 source_ids 引用该来源；去向理由不代替实际引用）")
        for target in d["targets"]:
            if d["source_id"] not in by_id[target]["source_ids"] and d["status"] != "user_revised":
                raise ValueError("源单元双向映射不一致")
        if not partial_coverage and (d["status"] == "unresolved" or (d["required"] and d["status"] in ("background", "excluded"))):
            gaps.append("源单元未覆盖：" + d["source_id"])
        link_count += len(d["targets"])
    destination_by_id = {d["source_id"]: d for d in destinations}
    for item in all_entries:
        if any(item["id"] not in destination_by_id[sid]["targets"] for sid in item["source_ids"]):
            raise ValueError("表达来源反向映射缺失")
    if link_count > cap["max_links"]:
        raise ValueError("表达关联超过资源上限")
    coverage = package.get("coverage")
    if not isinstance(coverage, list) or any(not isinstance(c, dict) for c in coverage):
        raise ValueError("场景覆盖清单缺失")
    scenarios = [c.get("scenario") for c in coverage]
    if any(not isinstance(s, str) or not s.strip() for s in scenarios) or len(set(scenarios)) != len(scenarios):
        raise ValueError("场景覆盖记录重复或无效")
    example_ids = {e["id"] for e in package["examples"]}
    required_scenarios = package.get("required_scenarios")
    if (not isinstance(required_scenarios, list)
            or any(not isinstance(s, str) or not s.strip() or len(s) > cap["max_item_chars"] for s in required_scenarios)
            or len(set(required_scenarios)) != len(required_scenarios)):
        raise ValueError("必需场景声明缺失")
    if not set(required_scenarios) <= set(scenarios):
        if partial_coverage:
            raise ValueError("已声明场景缺少覆盖登记；应在结构规范化时登记待确认状态")
        gaps.append("必需场景未登记")
    if not set(SCENARIOS) <= set(scenarios):
        raise ValueError("五类基础场景须逐项登记覆盖或不适用依据")
    for c in coverage:
        _string(c.get("reason"), "场景理由", cap["max_item_chars"])
        _ids(c.get("example_ids"), "coverage.example_ids", example_ids)
        if c.get("status") not in ("covered", "missing", "unresolved", "not_applicable"):
            raise ValueError("场景覆盖状态无效")
    _validate_coverage_scenarios(coverage, by_id, covered_only=not partial_coverage)
    for c in coverage:
        if c["status"] == "covered" and not c["example_ids"] and not (partial_coverage and c.get("rule_ids")):
            raise ValueError("已覆盖场景缺少匹配样本")
        if c["status"] == "not_applicable" and (c.get("basis") not in ("source_absent", "user_scope") or (not partial_coverage and c["scenario"] in required_scenarios)):
            raise ValueError("不得用不适用隐藏必需或审查删空场景")
        if not partial_coverage and c["status"] in ("missing", "unresolved") and c["scenario"] in required_scenarios:
            gaps.append("必需场景缺口：" + c["scenario"])
    if partial_coverage:
        from .expression_coverage import validate as validate_coverage
        validate_coverage(package)
    elif "coverage_report" in package or "warnings" in package:
        raise ValueError("覆盖提示缺少同版原稿覆盖合同")
    if "review_notices" in package:
        notices = package["review_notices"]
        if (not isinstance(notices, list) or len(notices) > cap["max_items"] * 16
                or len(json.dumps(notices, ensure_ascii=False)) > cap["max_review_chars"]):
            raise ValueError("表达审阅提示无效或超限")
        for notice in notices:
            _string(notice, "表达审阅提示", cap["max_review_chars"])
    if registry is not None:
        gaps.extend("源结构待明确：" + u["source_id"] for u in registry["units"] if u["unresolved_structure"])
    gaps.extend(package.get("gaps", []))
    _validate_revisions(package.get("user_revisions"), cap)
    if any(record["item_id"] in known for record in (package.get("user_revisions") or {}).get("cancelled_additions", [])):
        raise ValueError("已撤销新增条目仍出现在表达成品中")
    # Check dependencies only after every referenced entry and the full ledger
    # have been validated. Preparation may repair this one class of conflict;
    # malformed fields, provenance, or coverage must never enter that path.
    for item in all_entries:
        for ref in item["links"]:
            missing = [purpose for purpose in item["purposes"] if purpose not in by_id[ref]["purposes"]]
            if missing:
                raise PurposeReferenceError("表达跨用途引用不可完整投影：" + item["id"] + " → " + ref
                                            + "（被引用条目不支持：" + ", ".join(missing) + "）")
    if activation:
        if gaps:
            raise ValueError("表达未覆盖，候选未激活：" + "；".join(dict.fromkeys(gaps)))
        reviews = package.get("reviews", {})
        fidelity_states = {"passed", "partial"} if partial_coverage else {"passed"}
        if reviews.get("fact") != "passed" or reviews.get("fidelity") not in fidelity_states:
            raise ValueError("独立成品事实/表达保真审议未完成")
        if partial_coverage and (not isinstance(package.get("coverage_report"), dict)
                                 or package["coverage_report"].get("reviewed") is not True
                                 or package.get("warnings") != package["coverage_report"].get("warnings")):
            raise ValueError("覆盖结果缺少同版已审查报告与提示")
        if package.get("review_digest") != review_digest(package):
            raise ValueError("表达内容与审议版本不一致")
    return list(dict.fromkeys(gaps))


def restrict_prepared_purposes(package: dict, *, registry=None, limits=None) -> tuple[dict, list[dict]]:
    """Narrow only a newly generated draft; never repair runtime/user packages.

    Keep raw receipts untouched. All other checks remain strict, and the caller
    must merge user revisions and independently review the resulting candidate.
    """
    try:
        validate_package(package, registry=registry, limits=limits)
    except PurposeReferenceError:
        pass
    else:
        return copy.deepcopy(package), []
    result = copy.deepcopy(package)
    by_id = {item["id"]: item for item in entries(result)}
    before = {key: list(item["purposes"]) for key, item in by_id.items()}
    dependents = {key: [] for key in by_id}
    for item in by_id.values():
        for ref in item["links"]:
            dependents[ref].append(item["id"])
    # Propagate removals to a fixed point, including cycles and indirect links.
    # Each item can lose at most qq and worker_summary; chat is mandatory in
    # every validated entry, so no content is lost from the GUI projection.
    pending = deque(by_id)
    while pending:
        ref = pending.popleft()
        for key in dependents[ref]:
            item = by_id[key]
            narrowed = [purpose for purpose in item["purposes"] if purpose in by_id[ref]["purposes"]]
            if narrowed != item["purposes"]:
                item["purposes"] = narrowed
                pending.append(key)
    changes = [{"id": key, "before": before[key], "after": list(item["purposes"]),
                "dependencies": [{"id": ref, "purposes": list(by_id[ref]["purposes"])}
                                 for ref in item["links"]
                                 if not set(before[key]) <= set(by_id[ref]["purposes"])]}
               for key, item in by_id.items() if before[key] != item["purposes"]]
    validate_package(result, registry=registry, limits=limits)
    return result, changes


def review_digest(package: dict) -> str:
    content = {k: v for k, v in package.items() if k not in ("reviews", "review_digest")}
    return digest(json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def entry_text(item: dict, group: str) -> str:
    parts = [f"【{group}·{item['id']}】", "范围：" + item["scope"].replace("\n", "\n  │ ")]
    for key in _FIELDS[group]:
        parts.append(_LABELS[key] + "：" + item[key].replace("\n", "\n  │ "))
    if item["links"]:
        parts.append("关联：" + ", ".join(item["links"]))
    if group == "examples":
        parts.append("来源类型：" + item["origin"])
    return "\n".join(parts)


def editor_sections(package: dict) -> dict:
    return {title: "\n\n".join(entry_text(item, group) for group in groups for item in package[group])
            for title, groups in zip(TITLES, SECTION_GROUPS)}


def encode_sections(package: dict, assembly: dict | None = None) -> dict:
    validate_package(package)
    bodies = editor_sections(package)
    # Full typed metadata exists only here, in the same authoritative Markdown.
    # A hash binds all visible bodies; independent JSON audits cannot affect runtime.
    envelope = {"package": package, "body_hashes": {k: digest(v) for k, v in bodies.items()}}
    if assembly is not None:
        # 人格驻留合同（persona-residency-v1）：结构+对当前 package 的完整校验在保存时执行；
        # 无效合同拒绝保存，不让运行时靠回退兜底坏分类。
        from . import persona_residency
        contract = persona_residency.parse_contract(assembly)
        if contract is None:
            raise ValueError("驻留分类结构无效")
        issues = persona_residency.validate_contract(contract, package)
        if issues:
            raise ValueError("驻留分类未通过校验：" + "；".join(i["code"] for i in issues))
        envelope["assembly"] = contract
    data = base64.b64encode(json.dumps(envelope, ensure_ascii=False, separators=(",", ":")).encode()).decode()
    bodies[TITLES[0]] = "<!-- alice-expression-v2:" + data + " -->\n\n" + bodies[TITLES[0]]
    return bodies


def decode_sections_with_assembly(sections: dict) -> tuple[dict | None, dict | None]:
    """解码并返回 (package, assembly)。assembly 为可选驻留合同（形状校验在此，
    摘要/闭包/用途的深层校验在运行时激活前执行——失效走 full 回退而非拒绝安装）。"""
    markers = [(title, body) for title, body in sections.items() if MARKER in body]
    if not markers:
        if any(_has_unmarked_v2(title, body) for title, body in sections.items() if title in TITLES):
            raise ValueError("v2 表达正文缺少版本元数据，禁止回退 v1")
        return None, None
    if len(markers) != 1 or markers[0][0] != TITLES[0]:
        raise ValueError("表达版本标记位置无效")
    text = markers[0][1]
    if len(text) > 4000000:
        raise ValueError("表达元数据超出解码资源上限")
    match = re.match(r"\A<!-- alice-expression-v2:([A-Za-z0-9+/=]+) -->\n(?:\n)?", text)
    if not match or MARKER in text[match.end():]:
        raise ValueError("未知或损坏的 v2 元数据，禁止回退 v1")
    try:
        envelope = strict_json(base64.b64decode(match[1], validate=True).decode("utf-8"))
    except (ValueError, UnicodeError) as exc:
        raise ValueError("表达元数据不可解析") from exc
    if not isinstance(envelope, dict) or set(envelope) not in (
            {"package", "body_hashes"}, {"package", "body_hashes", "assembly"}):
        raise ValueError("表达元数据结构无效")
    assembly = envelope.get("assembly")
    if assembly is not None:
        from . import persona_residency
        if persona_residency.parse_contract(assembly) is None:
            raise ValueError("驻留分类结构无效（形状）；请重新准备分层")
    package = envelope["package"]
    validate_package(package)
    bodies = {k: sections.get(k, "").strip() for k in TITLES}
    bodies[TITLES[0]] = text[match.end():].strip()
    if {k: digest(v) for k, v in bodies.items()} != envelope["body_hashes"] or bodies != editor_sections(package):
        raise ValueError("表达正文与元数据不同版；请通过明确表达编辑入口保存")
    if package.get("reviews") == {"fact": "user_config", "fidelity": "invalidated_by_user"}:
        if (not has_user_revisions(package) or package.get("review_digest") != review_digest(package)):
            raise ValueError("用户修订表达缺少同版修订记录")
    else:
        validate_package(package, activation=True)
    return package, assembly


def decode_sections(sections: dict) -> dict | None:
    return decode_sections_with_assembly(sections)[0]


def _has_unmarked_v2(title, body):
    """Do not reinterpret a v1 sample continuation or fenced example as v2."""
    fence, legacy_sample = None, False
    for line in body.splitlines():
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line)
        if fence:
            if marker and marker[1][0] == fence[0] and len(marker[1]) >= fence[1] and not marker[2].strip():
                fence = None
            continue
        if marker:
            fence = (marker[1][0], len(marker[1]))
            continue
        if title == TITLES[2] and re.match(r"^- 【[^·】]+·[A-Za-z0-9_-]+】", line):
            legacy_sample = True
        if not legacy_sample and re.fullmatch(r"【(?:orientation|constraints|voice|tensions|reactions|examples)·[A-Za-z0-9_-]+】", line):
            return True
    return False


def project(package: dict, purpose="chat", *, budget=None, metadata=None, cfg=None) -> str:
    validate_package(package)
    if purpose not in ("chat", "qq", "worker_summary"):
        raise ValueError("未知表达用途")
    cap = limits_for(package.get("limits"))
    limit = cap["max_worker_chars"] if purpose == "worker_summary" else cap["max_projection_chars"]
    if cfg is not None:
        limit = min(limit, limits_for(cfg)["max_worker_chars" if purpose == "worker_summary" else "max_projection_chars"])
    if budget is not None:
        limit = min(limit, budget)
    chosen = [(g, e) for g in GROUPS for e in package[g] if purpose in e["purposes"]]
    # Runtime keeps every value and original ID, but prints each dependency as
    # a local number instead of repeating long machine IDs throughout the text.
    # Editor/encoded bodies and anchors retain their original representation.
    numbers = {item["id"]: str(index + 1) for index, item in enumerate(entries(package))}
    titles = dict(zip(GROUPS, ("取向", "边界", "语感", "张力", "反应", "样本")))
    groups = []
    for group in GROUPS:
        rendered = []
        for kind, item in chosen:
            if kind != group:
                continue
            parts = [f"{numbers[item['id']]}. {item['id']}", "范围：" + item["scope"].replace("\n", "\n  │ ")]
            for key in _FIELDS[group]:
                label = "·" if key == "text" else _LABELS[key] + "："
                parts.append(label + item[key].replace("\n", "\n  │ "))
            if item["links"]:
                parts.append("依赖：" + ", ".join(numbers[ref] for ref in item["links"]))
            if group == "examples":
                parts.append("来源类型：" + item["origin"])
            rendered.append("\n".join(parts))
        if rendered:
            groups.append("【" + titles[group] + "】\n" + "\n\n".join(rendered))
    compact = BOUNDARY + "\n\n" + "\n\n".join(groups) if chosen else ""
    legacy = BOUNDARY + "\n\n" + "\n\n".join(entry_text(item, group) for group, item in chosen) if chosen else ""
    # Short IDs and sparse purposes can make global numbers longer. Chinese
    # headings can also cost more tokens despite fewer characters. Compare the
    # unrounded context.estimate_tokens weight, both plain and JSON-escaped, so
    # combining this block with other context cannot introduce a rounding bump.
    def weight(value):
        cjk = sum(1 for ch in value if "\u4e00" <= ch <= "\u9fff" or "\u3000" <= ch <= "\u303f")
        return len(value) + 3 * cjk
    compact_fits_better = (len(compact) < len(legacy) and weight(compact) <= weight(legacy)
                          and weight(json.dumps(compact, ensure_ascii=False))
                          <= weight(json.dumps(legacy, ensure_ascii=False)))
    layout = "compact" if compact_fits_better else "legacy"
    text = compact if layout == "compact" else legacy
    if len(text) > limit:
        raise ValueError("完整表达包超过 " + purpose + " 预算；未前缀裁剪")
    if metadata is not None:
        from .context import estimate_tokens
        metadata.update({"format": FORMAT, "source_hash": package["source_hash"],
                         "policy_version": 6, "projection_strategy": "complete_per_purpose",
                         "projection_layout": layout,
                         "purpose": purpose, "entry_ids": [e["id"] for _, e in chosen],
                         "sample_ids": [e["id"] for g, e in chosen if g == "examples"],
                         "chars": len(text), "estimated_tokens": estimate_tokens(text),
                         "complete": True, "degradation": [], "coverage_gap_count": len(package.get("gaps", [])),
                         "coverage_status": package.get("reviews", {}).get("fidelity", "not_reviewed"),
                         "source_count": len(package["source_ids"]),
                         "destination_counts": {status: sum(d["status"] == status for d in package["destinations"]) for status in sorted({d["status"] for d in package["destinations"]})}})
    return text


def anchor(package: dict, *, purpose="chat", budget=500, boundary=BOUNDARY, metadata=None) -> str:
    validate_package(package)
    prefix = "【表达提醒：同版长期偏好，非本轮事实】"
    parts, chosen, omitted = [prefix], [], []
    if len(prefix) + len(boundary) + 1 > budget:
        if metadata is not None:
            metadata.update({"format": FORMAT, "source_hash": package["source_hash"], "entry_ids": [],
                             "sample_ids": [], "chars": 0, "omitted_ids": ["boundary"], "complete": False,
                             "degradation": ["anchor_boundary_budget"], "purpose": purpose})
        return ""
    eligible = [(g, e) for g in GROUPS for e in package[g] if purpose in e["purposes"] and e.get("anchor_role", "none") != "none"]
    eligible.sort(key=lambda pair: (-pair[1]["priority"], pair[1]["id"]))
    for role in ("stop", "voice", "example"):
        candidates = [(g, e) for g, e in eligible if e.get("anchor_role") == role]
        selected_role = False
        for group, entry in candidates:
            rendered = entry_text(entry, group)
            if not selected_role and len("\n".join([*parts, rendered, boundary])) <= budget:
                parts.append(rendered)
                chosen.append(entry["id"])
                selected_role = True
            else:
                omitted.append(entry["id"])
        if not selected_role:
            omitted.append("role:" + role)
    text = "\n".join([*parts, boundary])
    if metadata is not None:
        metadata.update({"format": FORMAT, "source_hash": package["source_hash"], "entry_ids": chosen,
                         "sample_ids": [e["id"] for e in package["examples"] if e["id"] in chosen],
                         "chars": len(text), "omitted_ids": omitted,
                         "complete": not omitted,
                         "degradation": ["anchor_incomplete"] if omitted else [], "purpose": purpose})
    return text


def _content_hash(group: str, item: dict) -> str:
    return digest(json.dumps({"group": group, "scope": item["scope"],
                              **{key: item[key] for key in _FIELDS[group]}},
                             ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def bind_source_units(package: dict, registry: dict) -> None:
    """Host-owned, bounded fingerprints; no source prose in runtime metadata."""
    package["source_units"] = {u["source_id"]: {
        "line_start": u["line_start"], "line_end": u["line_end"], "kind": u["kind"],
        "heading_hash": digest(json.dumps(u["headings"], ensure_ascii=False)),
        "columns_hash": digest(json.dumps(u["columns"], ensure_ascii=False)), "raw_hash": digest(u["raw"])}
        for u in registry["units"]}


def _validate_source_units(units, source_ids):
    if not isinstance(units, dict) or not set(units) <= source_ids:
        raise ValueError("源单元定位元数据无效")
    for unit in units.values():
        if not isinstance(unit, dict) or set(unit) != {"line_start", "line_end", "kind", "heading_hash", "columns_hash", "raw_hash"}:
            raise ValueError("源单元定位字段无效")
        if (type(unit["line_start"]) is not int or type(unit["line_end"]) is not int
                or not 1 <= unit["line_start"] <= unit["line_end"] or not isinstance(unit["kind"], str)):
            raise ValueError("源单元定位行区间无效")
        if any(not isinstance(unit[k], str) or not _HASH.fullmatch(unit[k]) for k in ("heading_hash", "columns_hash", "raw_hash")):
            raise ValueError("源单元定位摘要无效")


def _baseline(package, group, item):
    return {"group": group, "source_hash": package["source_hash"], "id": item["id"],
            "source_ids": list(item["source_ids"]), "content_hash": _content_hash(group, item),
            "source_units": {sid: copy.deepcopy(package.get("source_units", {})[sid])
                             for sid in item["source_ids"] if sid in package.get("source_units", {})}}


def _validate_base(base):
    if not isinstance(base, dict) or set(base) != {"group", "source_hash", "id", "source_ids", "content_hash", "source_units"}:
        raise ValueError("用户修订条目基线无效")
    if base["group"] not in GROUPS or any(not isinstance(base[k], str) or not _HASH.fullmatch(base[k]) for k in ("source_hash", "content_hash")):
        raise ValueError("用户修订基线版本无效")
    _ids([base["id"]], "基线条目")
    _ids(base["source_ids"], "基线源单元")
    _validate_source_units(base["source_units"], set(base["source_ids"]))


def _validate_revisions(state, cap):
    if state is None:
        return
    required = {"format", "edits", "baselines", "sections"}
    if not isinstance(state, dict) or not required <= set(state) or set(state) - (required | {"cancelled_additions"}) or state["format"] != REVISION_FORMAT:
        raise ValueError("未知用户修订基线版本")
    if not isinstance(state["baselines"], dict) or len(state["baselines"]) > cap["max_items"] * 2:
        raise ValueError("用户修订基线超过容量")
    _ids(list(state["baselines"]), "修订基线集合")
    for base in state["baselines"].values():
        _validate_base(base)
    if not isinstance(state["edits"], list) or len(state["edits"]) > cap["max_items"] * 2:
        raise ValueError("用户修订清单无效或超限")
    keys = set()
    for edit in state["edits"]:
        if not isinstance(edit, dict) or set(edit) != {"action", "group", "base", "item"} or edit["action"] not in {"modify", "delete", "add"} or edit["group"] not in GROUPS:
            raise ValueError("用户修订动作无效")
        if edit["action"] == "add":
            if edit["base"] is not None:
                raise ValueError("用户新增不得伪造源稿基线")
        else:
            _validate_base(edit["base"])
            if edit["base"]["group"] != edit["group"]:
                raise ValueError("用户修订类别与基线不一致")
        if edit["action"] == "delete":
            if edit["item"] is not None:
                raise ValueError("删除基线不能携带替换条目")
            key = (edit["base"]["source_hash"], edit["base"]["id"])
        else:
            item = edit["item"]
            if not isinstance(item, dict):
                raise ValueError("用户修订缺少完整条目")
            _ids([item.get("id")], "修订成品ID")
            if item.get("origin") != "user_config" or item.get("source_ids") != [] or item.get("purposes") != ["chat"] or item.get("audience") != "private" or item.get("pure_expression") is not False:
                raise ValueError("用户修订不能继承未经复核的来源/共享资格")
            for key_name in (*_FIELDS[edit["group"]], "scope"):
                _string(item.get(key_name), key_name, cap["max_item_chars"], empty=key_name in {"exceptions", "user_input"})
            key = (edit["base"]["source_hash"], edit["base"]["id"]) if edit["base"] else ("add", item["id"])
        if key in keys:
            raise ValueError("用户修订基线重复")
        keys.add(key)
    cancelled = state.get("cancelled_additions", [])
    if not isinstance(cancelled, list) or len(cancelled) > cap["max_items"] * 2:
        raise ValueError("新增撤销记录无效或超限")
    cancelled_ids = []
    for record in cancelled:
        if not isinstance(record, dict) or set(record) != {"item_id", "item_hash", "previous_review_digest"}:
            raise ValueError("新增撤销记录字段无效")
        cancelled_ids.append(record["item_id"])
        if any(not isinstance(record[key], str) or not _HASH.fullmatch(record[key])
               for key in ("item_hash", "previous_review_digest")):
            raise ValueError("新增撤销记录缺少上版及条目摘要")
    _ids(cancelled_ids, "已撤销新增条目")
    if set(cancelled_ids) & {e["item"]["id"] for e in state["edits"] if e["item"]}:
        raise ValueError("新增撤销记录与当前修订冲突")
    if not isinstance(state["sections"], dict) or set(state["sections"]) - set(TITLES):
        raise ValueError("整节修订范围无效")
    for title, record in state["sections"].items():
        if not isinstance(record, dict) or set(record) != {"mode", "body", "items"} or record["mode"] not in {"explicit_section", "protected_legacy", "legacy_unknown"}:
            raise ValueError("整节修订模式无效")
        _string(record["body"], "整节原文", cap["max_projection_chars"], empty=True)
        if not isinstance(record["items"], list) or len(record["items"]) > cap["max_items"]:
            raise ValueError("整节修订条目无效")
        for pair in record["items"]:
            if not isinstance(pair, dict) or set(pair) != {"group", "item"} or pair["group"] not in SECTION_GROUPS[TITLES.index(title)] or not isinstance(pair["item"], dict):
                raise ValueError("整节修订条目越界")


def has_user_revisions(package):
    state = package.get("user_revisions")
    return bool(isinstance(state, dict) and (state.get("edits") or state.get("sections")
                                           or state.get("cancelled_additions"))) or bool(package.get("user_overrides"))


def _section_record(package, title, mode, body):
    groups = SECTION_GROUPS[TITLES.index(title)]
    return {"mode": mode, "body": body,
            "items": [{"group": group, "item": copy.deepcopy(item)} for group in groups for item in package[group]]}


def _revision_state(package):
    state = copy.deepcopy(package.get("user_revisions"))
    if state is None:
        state = {"format": REVISION_FORMAT, "edits": [], "baselines": {}, "sections": {}}
        for title in package.get("user_overrides", {}):
            # Old records cannot prove item granularity. Preserve runtime, but
            # never silently reinterpret them as precise modifications/deletes.
            state["sections"][title] = _section_record(package, title, "legacy_unknown", editor_sections(package)[title])
    for group in GROUPS:
        for item in package[group]:
            state["baselines"].setdefault(item["id"], _baseline(package, group, item))
    return state


def _private(item):
    item.update(origin="user_config", source_ids=[], audience="private", purposes=["chat"], pure_expression=False)


def _parse_editor_body(body: str, groups: tuple, previous: dict) -> list[tuple[str, dict]]:
    matches = list(re.finditer(r"(?m)^【([a-z]+)·([A-Za-z0-9_-]{1,96})】[ \t]*$", body))
    if not matches or body[:matches[0].start()].strip():
        raise ValueError("条目边界不存在；如需整节自由文本，请明确选择该模式")
    parsed = []
    for index, match in enumerate(matches):
        group, eid = match[1], match[2]
        if group not in groups:
            raise ValueError("条目类型越过当前表达小节")
        text = body[match.end():matches[index+1].start() if index+1 < len(matches) else len(body)].strip()
        reverse = {label: key for key, label in _LABELS.items()}
        fields, active = {}, None
        allowed = {*_FIELDS[group], "scope", "links"} | ({"origin"} if group == "examples" else set())
        for line in text.splitlines():
            found = re.match(r"^(范围|关联|来源类型|" + "|".join(reverse) + r")：(.*)$", line)
            if found:
                key = {"范围": "scope", "关联": "links", "来源类型": "origin"}.get(found[1], reverse.get(found[1]))
                if key not in allowed:
                    raise ValueError("当前条目类型不支持字段“" + found[1] + "”；未保存，请改为适当条目或明确整节自由文本")
                if key in fields:
                    raise ValueError("条目字段重复")
                fields[key], active = found[2], key
            elif active is not None and line.startswith("  │ "):
                fields[active] += "\n" + line[4:]
            elif line.strip():
                raise ValueError("未知条目字段或续行格式；未保存任何更改")
        if not set(_FIELDS[group]) <= set(fields) or not fields.get("scope"):
            raise ValueError("条目字段缺失")
        old = previous.get(eid)
        if old is not None and old[0] != group:
            raise ValueError("条目 ID 不能换类型")
        if "origin" in fields and fields["origin"] != (old[1]["origin"] if old else "user_config"):
            raise ValueError("来源类型为只读说明；不能手动更改或伪造出处")
        item = copy.deepcopy(old[1]) if old else {
            "id": eid, "scope": "用户配置", "required": True, "priority": 50,
            "origin": "user_config", "audience": "private", "purposes": ["chat"],
            "source_ids": [], "links": [], "anchor_role": "none", "pure_expression": False}
        for key in (*_FIELDS[group], "scope"):
            item[key] = fields[key]
        item["links"] = [x.strip() for x in fields.get("links", "").split(",") if x.strip()]
        if old is None or any(item.get(k) != old[1].get(k) for k in (*_FIELDS[group], "scope", "links")):
            _private(item)
        parsed.append((group, item))
    _ids([e["id"] for _, e in parsed], "用户条目")
    return parsed


def _free_text_item(title, body):
    group = ("voice", "reactions", "examples")[TITLES.index(title)]
    item = {"id": "user_" + digest(title + body)[:20], "scope": "用户明确选择的整节配置（以正文条件为准）",
            "required": True, "priority": 100, "origin": "user_config", "audience": "private", "purposes": ["chat"],
            "source_ids": [], "links": [], "anchor_role": "none", "pure_expression": False}
    if group == "voice":
        item["text"] = body
    elif group == "reactions":
        item.update(trigger="以用户配置为准", first=body, then="按配置条件变化", stop="用户停止要求优先")
    else:
        item.update(scenario="用户修订样本", user_input="", reply=body)
    return (group, item)


def _record_edit(state, group, old, new, previous_review_digest):
    eid = old["id"] if old else new["id"]
    # Reusing an ID starts a new addition; an old cancellation must not claim
    # that a currently present item is absent. Repeated cycles replace history.
    cancelled = [r for r in state.get("cancelled_additions", []) if r["item_id"] != eid]
    if "cancelled_additions" in state:
        state["cancelled_additions"] = cancelled
    prior = next((e for e in state["edits"] if (e["item"] and e["item"]["id"] == eid)
                  or (e["action"] == "delete" and e["base"]["id"] == eid)), None)
    if prior:
        state["edits"].remove(prior)
        if prior["action"] == "add" and new is None:
            cancelled.append({"item_id": eid,
                              "item_hash": digest(json.dumps(old, ensure_ascii=False,
                                                             sort_keys=True, separators=(",", ":"))),
                              "previous_review_digest": previous_review_digest})
            state["cancelled_additions"] = cancelled
            return
        base = prior["base"]
    else:
        base = state["baselines"].get(eid) if old else None
    # A newly configured item with no source identity is an addition, not a
    # fictitious donor modification. Its later deletion removes that addition.
    if base and not base["source_ids"]:
        base = None
    if new is None and base is None:
        return
    state["edits"].append({"action": "delete" if new is None else "modify" if base else "add",
                           "group": group, "base": copy.deepcopy(base), "item": copy.deepcopy(new)})


def _invalidate(result, changed, removed, pending, *, protected=False):
    invalidated = set(changed) | set(removed)
    pending_links = {}
    while True:
        affected = [item for item in entries(result) if set(item["links"]) & invalidated]
        if not affected:
            break
        for item in affected:
            dangling = set(item["links"]) & invalidated
            pending_links.setdefault(item["id"], set()).update(dangling)
            item["links"] = [x for x in item["links"] if x not in invalidated]
            _private(item)  # Lost boundaries invalidate QQ/worker review too.
            invalidated.add(item["id"])
    pending.extend({"id": eid, "invalidated_links": sorted(links), "sharing": "gui_only_pending_review"}
                   for eid, links in pending_links.items())
    for d in result["destinations"]:
        if set(d["targets"]) & invalidated:
            kept = [x for x in d["targets"] if x not in invalidated]
            if protected and d["required"]:
                d.update(status="unresolved", reason="旧格式表达默认保护；新源必需项尚未合入，不能视为用户明确删除", targets=kept)
            else:
                d.update(status="user_revised", reason="实际用户修订或其依赖已失效；未覆盖无关新源条目", targets=kept, required=False)
    example_ids = {e["id"] for e in result["examples"]}
    rule_ids = {e["id"] for group in GROUPS if group != "examples" for e in result[group]}
    affected_scenarios = set()
    for c in result["coverage"]:
        if set(c["example_ids"]) & invalidated or set(c.get("rule_ids", [])) & invalidated:
            c["example_ids"] = [x for x in c["example_ids"] if x in example_ids and x not in invalidated]
            if "coverage_contract" in result:
                c["rule_ids"] = [x for x in c.get("rule_ids", []) if x in rule_ids and x not in invalidated]
            if not c["example_ids"] and not c.get("rule_ids"):
                c.update(status="unresolved", reason="修订后场景/样本关联失效，待确认")
                affected_scenarios.add(c["scenario"])
                pending.append({"scenario": c["scenario"], "reason": "样本关联已失效"})
    if not protected and "coverage_contract" not in result:
        result["required_scenarios"] = [x for x in result["required_scenarios"] if x not in affected_scenarios]


def _finish_revision(result, state, pending):
    result.pop("user_overrides", None)
    result.pop("review_notices", None)  # These opinions were bound to the old reviewed text.
    result.update(user_revisions=state, pending_associations=pending,
                  reviews={"fact": "user_config", "fidelity": "invalidated_by_user"}, gaps=[])
    if "coverage_contract" in result:
        from .expression_coverage import summary as coverage_summary
        result.pop("coverage_report", None)
        result.pop("warnings", None)
        report = coverage_summary(result)
        result.update(coverage_report=report, warnings=report["warnings"])
    result["review_digest"] = review_digest(result)
    validate_package(result)
    project(result)
    return result


def revise_sections(package: dict, sections: dict) -> dict:
    """Exact typed deltas; whole-section scope requires an explicit marker."""
    if not isinstance(sections, dict) or set(sections) - set(TITLES):
        raise ValueError("只能编辑三个表达小节")
    result, before = copy.deepcopy(package), editor_sections(package)
    changed, removed, pending = set(), set(), []
    state = _revision_state(package)
    # Keep cancellation evidence for this operation only. The persona revision
    # audit retains previous saves; embedded net-state must not grow forever.
    state.pop("cancelled_additions", None)
    previous = {e["id"]: (g, e) for g in GROUPS for e in package[g]}
    any_change, changed_scope = False, False
    for title, supplied in sections.items():
        body = _string(supplied, title, limits_for(result.get("limits"))["max_projection_chars"], empty=True).strip()
        if body == before[title]:
            continue
        if MARKER in body or re.search(r"(?m)^\s*##\s", body):
            raise ValueError("表达编辑正文不能包含版本元数据或人格小节标题")
        any_change = True
        groups = SECTION_GROUPS[TITLES.index(title)]
        old_items = {e["id"]: (g, e) for g in groups for e in result[g]}
        explicit = body == FREE_TEXT_MARKER or body.startswith(FREE_TEXT_MARKER + "\n")
        if explicit:
            body = body[len(FREE_TEXT_MARKER):].lstrip("\n")
            parsed = [_free_text_item(title, body)] if body else []
            state["edits"] = [e for e in state["edits"] if e["group"] not in groups]
        else:
            parsed = _parse_editor_body(body, groups, previous) if body else []
        new_items = {e["id"]: (g, e) for g, e in parsed}
        for group in groups:
            result[group] = [e for g, e in parsed if g == group]
        removed.update(set(old_items) - set(new_items))
        changed.update(eid for eid, pair in new_items.items() if eid not in old_items or pair[1] != old_items[eid][1])
        if explicit or title in state["sections"]:
            mode = "explicit_section" if explicit else state["sections"][title]["mode"]
            state["sections"][title] = _section_record(result, title, mode, body)
            state["edits"] = [e for e in state["edits"] if e["group"] not in groups]
            pending.append({"section": title, "mode": mode, "reason": "整节保留范围已记录；与逐项修订区分"})
            changed_scope = True
        else:
            for eid in set(old_items) | set(new_items):
                old_pair, new_pair = old_items.get(eid), new_items.get(eid)
                if old_pair != new_pair:
                    _record_edit(state, (new_pair or old_pair)[0], old_pair[1] if old_pair else None,
                                 new_pair[1] if new_pair else None, package.get("review_digest"))
    if not any_change or (not changed and not removed and not changed_scope):
        return result
    _invalidate(result, changed, removed, pending)
    # Dependency invalidation is evidence, not a fictitious explicit edit.
    for title, record in list(state["sections"].items()):
        state["sections"][title] = _section_record(result, title, record["mode"], record["body"])
    present_ids = {e["id"] for e in entries(result)}
    state["baselines"] = {eid: base for eid, base in state["baselines"].items()
                          if base["source_ids"] or eid in present_ids}
    if "cancelled_additions" in state:
        state["cancelled_additions"] = [r for r in state["cancelled_additions"] if r["item_id"] not in present_ids]
    return _finish_revision(result, state, pending)


def _unit_signature(unit):
    return (unit["kind"], unit["heading_hash"], unit["columns_hash"], unit["raw_hash"])


def _match_base(new, base, baselines):
    """Prove a bijection for the entire old same-group source component.

    A source unit can yield several entries. Its uniqueness cannot prove an
    entry identity, and the identities of untouched siblings matter as much as
    that of the edited item. Representation splits/merges require a new choice.
    """
    if baselines.get(base["id"]) != base or not base["source_ids"]:
        raise ValueError("用户修订缺少完整旧条目基线：" + base["id"])
    peers = {base["id"]: base}
    sources = set(base["source_ids"])
    while True:
        related = {eid: b for eid, b in baselines.items()
                   if b["group"] == base["group"] and b["source_hash"] == base["source_hash"]
                   and set(b["source_ids"]) & sources}
        if set(related) == set(peers):
            break
        peers.update(related)
        sources.update(sid for b in related.values() for sid in b["source_ids"])
    same_version = base["source_hash"] == new["source_hash"]
    if same_version:
        source_map = {sid: sid for sid in sources}
    else:
        old_units = {}
        for peer in baselines.values():
            if peer["source_hash"] != base["source_hash"] or not peer["source_ids"]:
                continue
            if set(peer["source_units"]) != set(peer["source_ids"]):
                if peer["id"] in peers:
                    raise ValueError("用户修订 " + base["id"] + " 缺少旧源定位摘要；跨原稿版本不能猜测对应项")
                continue
            for sid, unit in peer["source_units"].items():
                if sid in old_units and old_units[sid] != unit:
                    raise ValueError("用户修订基线源定位不一致")
                old_units[sid] = unit
        source_map = {}
        for sid in sources:
            unit = old_units[sid]
            signature = _unit_signature(unit)
            # Repeated source blocks cannot become stable identity merely
            # because only one resulting entry happens to cite them.
            old_matches = [old_sid for old_sid, old_unit in old_units.items()
                           if _unit_signature(old_unit) == signature]
            new_matches = [new_sid for new_sid, new_unit in new.get("source_units", {}).items()
                           if _unit_signature(new_unit) == signature]
            if len(old_matches) != 1 or len(new_matches) != 1:
                raise ValueError("用户修订 " + base["id"] + " 的源单元跨版本不唯一；候选未激活")
            source_map[sid] = new_matches[0]
    related_new = [e for e in new[base["group"]] if set(e["source_ids"]) & set(source_map.values())]
    if len(related_new) != len(peers):
        raise ValueError("用户修订 " + base["id"] + " 的同源条目发生拆分、合并或缺失；未扩大修改范围")
    matches, used = {}, set()
    for eid, peer in peers.items():
        wanted = {source_map[sid] for sid in peer["source_ids"]}
        candidates = [e for e in related_new if set(e["source_ids"]) == wanted
                      and _content_hash(peer["group"], e) == peer["content_hash"]]
        if same_version and len(candidates) > 1:
            candidates = [e for e in candidates if e["id"] == eid]
        if len(candidates) != 1 or candidates[0]["id"] in used:
            raise ValueError("用户修订 " + base["id"] + " 的同源条目不能证明一对一身份；未猜测覆盖或删除")
        matches[eid] = candidates[0]
        used.add(candidates[0]["id"])
    return matches[base["id"]]


def merge_user_revisions(new: dict, old: dict, *, registry=None) -> dict:
    result = copy.deepcopy(new)
    if registry is not None:
        bind_source_units(result, registry)
    candidate_baseline = copy.deepcopy(result)
    legacy = old.get("legacy_overrides")
    if legacy is not None:
        if old.get("legacy_mode") not in {"conservative_preserve", "explicit_section"}:
            raise ValueError("旧表达保留范围未经明确标记，候选未激活")
        mode = "protected_legacy" if old["legacy_mode"] == "conservative_preserve" else "explicit_section"
        choices = old.get("legacy_choices", {})
        if (not isinstance(choices, dict) or set(choices) - set(TITLES)
                or any(choice not in {"preserve", "replace", "merge"} for choice in choices.values())):
            raise ValueError("旧表达迁移范围选择无效")
        state = _revision_state(result)
        source_entry_ids = {e["id"] for e in entries(candidate_baseline)}
        pending, removed = [], set()
        for title, body in legacy.items():
            if title not in TITLES:
                raise ValueError("旧表达保护范围越界")
            body = _string(body, title, limits_for(result.get("limits"))["max_projection_chars"], empty=True)
            choice = choices.get(title, "preserve")
            if choice == "replace":
                # Explicitly using this source section keeps its required
                # items and coverage intact; it grants no deletion elsewhere.
                continue
            if choice == "merge":
                if body:
                    group, item = _free_text_item(title, body)
                    item["scope"] = "用户明确合并保留的旧格式表达（以正文条件为准）"
                    if item["id"] in source_entry_ids or item["id"] in {e["id"] for e in entries(result)}:
                        raise ValueError("合并旧表达与候选条目 ID 冲突；未覆盖任何条目")
                    result[group].append(item)
                    state["edits"].append({"action": "add", "group": group, "base": None,
                                           "item": copy.deepcopy(item)})
                    pending.append({"section": title, "mode": "merge", "reason": "旧表达作为私有用户新增，与本节源条目共同接受审议"})
                continue
            groups = SECTION_GROUPS[TITLES.index(title)]
            removed.update(e["id"] for g in groups for e in result[g])
            for group in groups:
                result[group] = []
            if body:
                group, item = _free_text_item(title, body)
                if mode == "protected_legacy":
                    item["scope"] = "旧格式当前表达的保守保护；并非用户声明删除新源条目"
                if item["id"] in source_entry_ids or item["id"] in {e["id"] for e in entries(result)}:
                    raise ValueError("保留旧表达与候选条目 ID 冲突；未覆盖其他小节条目")
                result[group].append(item)
            state["sections"][title] = _section_record(result, title, mode, body)
            pending.append({"section": title, "mode": mode, "reason": "保持当前表达，原稿新增必要项须明确处理"})
        if not state["edits"] and not state["sections"]:
            return result
        _invalidate(result, set(), removed, pending, protected=True)
        return _finish_revision(result, state, pending)
    if old.get("user_overrides") and not old.get("user_revisions"):
        raise ValueError("旧修订只保存了整节正文，无法证明逐项修改或删除；请明确选择整节保留模式后再更新原稿")
    state = copy.deepcopy(old.get("user_revisions"))
    if not state or (not state["edits"] and not state["sections"]):
        return result
    _validate_revisions(state, limits_for(result.get("limits")))
    if any(s["mode"] == "legacy_unknown" for s in state["sections"].values()):
        raise ValueError("旧整节修订粒度未知；请明确选择整节保留模式，不能冒充逐项合并")
    fresh = {"format": REVISION_FORMAT, "edits": [], "baselines": {}, "sections": {}}
    for group in GROUPS:
        for item in result[group]:
            fresh["baselines"][item["id"]] = _baseline(result, group, item)
    pending, changed, removed, mapping, matches = [], set(), set(), {}, []
    used = set()
    for edit in state["edits"]:
        if edit["action"] == "add":
            target = None
            eid = edit["item"]["id"]
            if eid in fresh["baselines"]:
                raise ValueError("用户新增条目 ID 与新源冲突：" + eid + "；未覆盖新源项")
        else:
            target = _match_base(candidate_baseline, edit["base"], state["baselines"])
            eid = target["id"]
            if eid in used:
                raise ValueError("多项用户修订映射到同一新源项：" + eid)
            used.add(eid)
            mapping[edit["base"]["id"]] = eid
        if edit["item"]:
            mapping[edit["item"]["id"]] = eid
        matches.append((edit, target, eid))
    for edit, target, eid in matches:
        group = edit["group"]
        base = _baseline(result, group, target) if target else None
        if edit["action"] == "delete":
            result[group] = [e for e in result[group] if e["id"] != eid]
            removed.add(eid)
            item = None
        else:
            item = copy.deepcopy(edit["item"])
            item["id"] = eid
            links = []
            for ref in item["links"]:
                if ref in mapping:
                    links.append(mapping[ref])
                elif ref in state["baselines"]:
                    links.append(_match_base(candidate_baseline, state["baselines"][ref], state["baselines"])["id"])
                else:
                    raise ValueError("用户修订关联缺少版本基线：" + ref)
            item["links"] = links
            _private(item)
            if target:
                result[group] = [item if e["id"] == eid else e for e in result[group]]
            else:
                result[group].append(item)
            changed.add(eid)
        fresh["edits"].append({"action": edit["action"], "group": group, "base": base, "item": copy.deepcopy(item)})
    protected = False
    for title, record in state["sections"].items():
        groups = SECTION_GROUPS[TITLES.index(title)]
        removed.update(e["id"] for g in groups for e in result[g])
        for group in groups:
            result[group] = [copy.deepcopy(pair["item"]) for pair in record["items"] if pair["group"] == group]
            for item in result[group]:
                _private(item)
                item["links"] = []
                changed.add(item["id"])
        protected = protected or record["mode"] == "protected_legacy"
        fresh["sections"][title] = _section_record(result, title, record["mode"], record["body"])
        pending.append({"section": title, "mode": record["mode"], "reason": "沿用已记录整节保留范围"})
    _invalidate(result, changed, removed, pending, protected=protected)
    return _finish_revision(result, fresh, pending)


def prepare_expression(*args, **kwargs):
    from .expression_prepare import prepare_expression as prepare
    return prepare(*args, **kwargs)
