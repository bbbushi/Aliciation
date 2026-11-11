"""Verbatim persona material, navigation, and shared admission/projection rules.

No file, database, network or model access. Classification never rewrites source.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import re

from .expression_adoption import ADOPTION_GUIDANCE
from . import persona_interpretation as interpretation

FORMAT = "persona-material-v1"
POLICY_VERSION = 2
MARKER_PREFIX = "<!-- alice-expression-material-v1:"
_MARKER_RE = re.compile(r"\A<!-- alice-expression-material-v1:([A-Za-z0-9+/=]+) -->\n(?:\n)?")
MATERIAL_DIGEST_VERSION = "pmat-v1"
KINDS = ("表达习惯", "反应模式", "示例", "观点候选", "角色背景", "混合", "待分类")
PLACEMENTS = ("常驻", "按需", "仅存档", "待确认")
EXPRESSION_KINDS = frozenset(("表达习惯", "反应模式", "示例", "混合"))
MAX_SOURCE_CHARS = 60000
MAX_BLOCK_CHARS = 4000
MAX_CUE_CHARS = 72
MAX_LABEL_CHARS = 24
_DEFAULT_LIMITS = {"max_core_chars": 2200, "max_registry_chars": 1600,
                   "max_registry_bundles": 12, "max_scene_expanded_chars": 2400,
                   "max_persona_load_calls": 2, "max_persona_loaded_bundles": 2,
                   "max_projection_chars": 6000, "max_source_chars": 60000,
                   "max_source_units": 600}
MATERIAL_KEYS = {"format", "source_text", "source_sha256", "revision", "blocks",
                 "classifications", "core_block_ids", "bundles", "derivatives", "limits"}
BLOCK_KEYS = {"block_id", "start", "end", "heading_path", "context_ids"}
CLASSIFICATION_KEYS = {"kind", "placement", "cues", "status"}
BUNDLE_KEYS = {"bundle_id", "label", "cues", "block_ids", "enabled"}
DERIVATIVE_KEYS = {"derivative_id", "block_id", "base_sha256", "patch", "purpose", "status", "version"}
_ID = re.compile(r"[A-Za-z0-9_-]{1,96}\Z")
_HASH = re.compile(r"[a-f0-9]{64}\Z")

USAGE = (ADOPTION_GUIDANCE + "以下是保留原文的语言参考；结合示例的必要语境学习表达。")
REGISTRY_USAGE = (USAGE + "核心提供日常语感；场景目录用于按需选择，命中相关线索时用persona_load读取原文及必要上下文。")


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def material_digest(material):
    return MATERIAL_DIGEST_VERSION + ":sha256:" + _sha256(_canonical(material))


def configured_limits(cfg=None):
    expression = getattr(cfg, "expression", cfg)
    if isinstance(expression, dict):
        return {k: expression.get(k, v) for k, v in _DEFAULT_LIMITS.items()}
    return {k: getattr(expression, k, v) for k, v in _DEFAULT_LIMITS.items()}


def effective_limits(material, configured=None):
    """Frozen admission limits can only be tightened at runtime."""
    frozen = material.get("limits", _DEFAULT_LIMITS)
    current = configured_limits(configured)
    return {k: min(frozen.get(k, v), current[k]) for k, v in _DEFAULT_LIMITS.items()}


_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_FENCE = re.compile(r"^ {0,3}([\x60~]{3,})(.*)$")
_LIST_ITEM = re.compile(r"^(?:[-*+]|\d+[.)])\s+")
_TABLE_ROW = re.compile(r"^\|.*\|\s*$")
_TABLE_DELIM = re.compile(r"^\|[\s:|-]+\|\s*$")
_QUOTE = re.compile(r"^>\s?")
_DIALOGUE = re.compile(r"^\s*(?:用户|角色|助手|Alice|AI|User|Assistant|{{user}})\s*[:：]", re.I)
_USER_DIALOGUE = re.compile(r"^\s*(?:用户|User|{{user}})\s*[:：]", re.I)


def dialogue_groups(material):
    """Recognizable prompt/answer pairs, including old separately indexed quotes."""
    groups = {}
    blocks, text = material["blocks"], material["source_text"]
    for left, right in zip(blocks, blocks[1:]):
        prompt, answer = text[left["start"]:left["end"]].strip(), text[right["start"]:right["end"]].strip()
        if (_USER_DIALOGUE.match(prompt) and answer and not _USER_DIALOGUE.match(answer)
                and (_DIALOGUE.match(answer) or answer.startswith(('"', '“', '「', '『')))
                and not text[left["end"]:right["start"]].strip()
                and left["heading_path"] == right["heading_path"]):
            pair = [left["block_id"], right["block_id"]]
            for bid in pair:
                groups[bid] = pair
    return groups


def assembly_material(material):
    """Pure compatibility view: placement is authoritative; old selected roots survive."""
    result = copy.deepcopy(material)
    for bid in result["core_block_ids"]:
        if usable(result, bid):
            result["classifications"][bid]["placement"] = "常驻"
    result["core_block_ids"] = [b["block_id"] for b in result["blocks"]
                                if usable(result, b["block_id"])
                                and result["classifications"][b["block_id"]]["placement"] == "常驻"]
    return result


def chunk_source(text):
    """Keep complete fences, adjacent quoted/dialogue turns and separate list items.

    Only table headers create automatic shared dependencies. Neighboring list
    items are not evidence that every item depends on every other item.
    """
    if not isinstance(text, str) or not text.strip() or len(text) > MAX_SOURCE_CHARS:
        raise ValueError("原文为空或超过60000字符，请调整范围")
    blocks, path, lines = [], [], text.splitlines(keepends=True)
    pos, para, fence, table_header, grouped, list_item = 0, None, None, None, None, None

    def add(start, end, contexts=()):
        if start is not None and text[start:end].strip():
            blocks.append({"block_id": f"b{len(blocks) + 1:04d}", "start": start, "end": end,
                           "heading_path": "/".join(path), "context_ids": list(contexts)})

    def flush(end):
        nonlocal para, grouped, list_item
        if para is not None:
            add(para, end)
        elif grouped is not None:
            add(grouped[1], end)
        elif list_item is not None:
            add(list_item, end)
        para = grouped = list_item = None

    for line in lines:
        at, pos = pos, pos + len(line)
        stripped = line.strip()
        marker = _FENCE.match(line)
        if fence is not None:
            if (marker and set(marker[1]) == {fence[0]} and len(marker[1]) >= fence[1]
                    and not marker[2].strip()):
                add(fence[2], pos)
                fence = None
            continue
        if marker and len(set(marker[1])) == 1:
            flush(at)
            table_header = None
            fence = (marker[1][0], len(marker[1]), at)
            continue
        heading = _HEADING.match(line)
        if heading:
            flush(at)
            path = path[:len(heading[1]) - 1] + [heading[2].strip()]
            table_header = None
            continue
        if _TABLE_DELIM.match(line) and table_header is not None:
            continue
        if _TABLE_ROW.match(line):
            flush(at)
            add(at, pos, () if table_header is None else (table_header,))
            if table_header is None:
                table_header = blocks[-1]["block_id"]
            continue
        table_header = None
        if grouped is not None and grouped[0] == "dialogue" and stripped.startswith(('"', '“', '「', '『')):
            continue  # Unlabelled quoted answers belong to the preceding prompt.
        if (grouped is not None and grouped[0] == "dialogue" and _USER_DIALOGUE.match(line)
                and any(l.strip().startswith(('"', '“', '「', '『'))
                        for l in text[grouped[1]:at].splitlines())):
            flush(at)  # Start a separate example, rather than merging all examples.
        group_kind = "quote" if _QUOTE.match(line) else "dialogue" if _DIALOGUE.match(line) else None
        if group_kind:
            if grouped is None or grouped[0] != group_kind:
                flush(at)
                grouped = (group_kind, at)
            continue
        # Blank separators between explicit dialogue turns remain in that unit.
        if grouped is not None and grouped[0] == "dialogue" and not stripped:
            continue
        if _LIST_ITEM.match(line):
            flush(at)
            list_item = at
            continue
        if list_item is not None and line.startswith((" ", "\t")) and stripped:
            continue
        if not stripped:
            flush(at)
            continue
        if grouped is not None or list_item is not None:
            flush(at)
        if para is None:
            para = at
    if fence is not None:
        raise ValueError("原文围栏未闭合；请先确认原稿边界，未调用模型")
    flush(len(text))
    return blocks


def source_material(text, *, revision=1, limits=None):
    cap = configured_limits(limits)
    blocks = chunk_source(text)
    if not blocks or len(text) > cap["max_source_chars"] or len(blocks) > min(600, cap["max_source_units"]):
        raise ValueError("原文或原子块数量超过当前配置，请缩小本次范围")
    return {"format": FORMAT, "source_text": text, "source_sha256": _sha256(text),
            "revision": revision, "blocks": blocks, "limits": cap,
            "classifications": {b["block_id"]: pending_row() for b in blocks},
            "core_block_ids": [], "bundles": [], "derivatives": []}


def pending_row():
    return {"kind": "待分类", "placement": "待确认", "cues": [], "status": "pending"}


def _ids(value, known=None, *, empty=True):
    return (isinstance(value, list) and (empty or bool(value))
            and all(isinstance(x, str) and _ID.fullmatch(x) for x in value)
            and len(value) == len(set(value)) and (known is None or set(value) <= set(known)))


def _cues(value):
    return (isinstance(value, list) and len(value) <= 3
            and all(isinstance(c, str) and c.strip() and len(c) <= MAX_CUE_CHARS for c in value))


def valid_classification(row):
    return (isinstance(row, dict) and set(row) == CLASSIFICATION_KEYS
            and row["kind"] in KINDS and row["placement"] in PLACEMENTS
            and row["status"] in ("accepted", "isolated", "pending", "paused") and _cues(row["cues"])
            and not (row["status"] == "accepted" and row["placement"] in ("常驻", "按需")
                     and row["kind"] not in EXPRESSION_KINDS))


def validate_material(material):
    """Structural integrity only; local admission issues belong to projection_plan."""
    if (not isinstance(material, dict) or set(material) not in (MATERIAL_KEYS, MATERIAL_KEYS | {"interpretation"})
            or material.get("format") != FORMAT):
        return [{"code": "material_shape_invalid"}]
    text, limits = material["source_text"], material["limits"]
    if (not isinstance(text, str) or not text.strip() or len(text) > MAX_SOURCE_CHARS
            or _sha256(text) != material["source_sha256"]):
        return [{"code": "source_digest_mismatch"}]
    if (not isinstance(limits, dict) or set(limits) != set(_DEFAULT_LIMITS)
            or any(type(v) is not int or not 1 <= v <= 240000 for v in limits.values())
            or len(text) > limits["max_source_chars"]):
        return [{"code": "limits_invalid"}]
    if type(material["revision"]) is not int or material["revision"] < 1:
        return [{"code": "revision_invalid"}]
    blocks = material["blocks"]
    if not isinstance(blocks, list) or not blocks or len(blocks) > min(600, limits["max_source_units"]):
        return [{"code": "blocks_empty_or_over_limit"}]
    known, end, issues = {}, 0, []
    for block in blocks:
        if not isinstance(block, dict) or set(block) != BLOCK_KEYS:
            return [{"code": "block_shape_invalid"}]
        bid, start, stop = block["block_id"], block["start"], block["end"]
        if (not isinstance(bid, str) or not _ID.fullmatch(bid) or bid in known
                or type(start) is not int or type(stop) is not int or not end <= start < stop <= len(text)
                or not isinstance(block["heading_path"], str) or len(block["heading_path"]) > 400
                or not _ids(block["context_ids"])):
            return [{"code": "block_range_invalid"}]
        known[bid], end = block, stop
    for b in blocks:
        if not _ids(b["context_ids"], known) or b["block_id"] in b["context_ids"]:
            issues.append({"code": "block_context_invalid", "block": b["block_id"]})
    rows = material["classifications"]
    if not isinstance(rows, dict) or set(rows) != set(known):
        return issues + [{"code": "classification_targets_invalid"}]
    issues.extend({"code": "classification_row_invalid", "block": bid}
                  for bid, row in rows.items() if not valid_classification(row))
    if not _ids(material["core_block_ids"], known):
        issues.append({"code": "core_shape_invalid"})
    bundles, seen = material["bundles"], set()
    if not isinstance(bundles, list) or len(bundles) > 600:
        return issues + [{"code": "bundles_shape_invalid"}]
    for b in bundles:
        if (not isinstance(b, dict) or set(b) != BUNDLE_KEYS
                or not isinstance(b["bundle_id"], str) or not _ID.fullmatch(b["bundle_id"])
                or b["bundle_id"] in seen or not isinstance(b["label"], str)
                or not 1 <= len(b["label"].strip()) <= MAX_LABEL_CHARS
                or not _cues(b["cues"]) or not _ids(b["block_ids"], known, empty=False)
                or type(b["enabled"]) is not bool):
            return issues + [{"code": "bundle_shape_invalid"}]
        seen.add(b["bundle_id"])
    derivatives = material["derivatives"]
    if not isinstance(derivatives, list) or len(derivatives) > 600:
        issues.append({"code": "derivatives_invalid"})
    else:
        seen = set()
        for row in derivatives:
            if (not isinstance(row, dict) or set(row) != DERIVATIVE_KEYS
                    or not isinstance(row["derivative_id"], str) or not _ID.fullmatch(row["derivative_id"])
                    or row["derivative_id"] in seen or not isinstance(row["block_id"], str)
                    or row["block_id"] not in known or not isinstance(row["base_sha256"], str)
                    or not _HASH.fullmatch(row["base_sha256"]) or not isinstance(row["patch"], dict)
                    or set(row["patch"]) != {"quote", "replacement"}
                    or any(not isinstance(x, str) for x in row["patch"].values())
                    or not isinstance(row["purpose"], str) or row["purpose"] != "chat"
                    or row["status"] not in ("proposed", "accepted", "rejected", "stale")
                    or type(row["version"]) is not int or row["version"] < 1):
                return issues + [{"code": "derivative_row_invalid"}]
            seen.add(row["derivative_id"])
    return issues + interpretation.validate(material)


def require_material(material):
    issues = validate_material(material)
    if issues:
        raise ValueError("原文材料结构无效：" + "；".join(i["code"] for i in issues[:8]))


def usable(material, bid):
    row = material["classifications"].get(bid)
    return bool(valid_classification(row) and row["status"] == "accepted"
                and row["placement"] in ("常驻", "按需") and row["kind"] in EXPRESSION_KINDS
                and interpretation.effective_text(material, bid).strip())


def closure(material, roots):
    """Recursive, source-ordered closure; every member must be explicitly usable."""
    known = {b["block_id"]: b for b in material["blocks"]}
    groups = dialogue_groups(material)
    pending, seen, visiting = list(roots), set(), set()
    def visit(bid):
        if bid in visiting:
            raise ValueError("原文上下文依赖形成循环：" + bid)
        if bid in seen:
            return
        if bid not in known or not usable(material, bid):
            raise ValueError("原文块尚未确认可用或已暂停/存档：" + str(bid))
        if len(interpretation.effective_text(material, bid)) > MAX_BLOCK_CHARS:
            raise ValueError("原文块过长，需要人工拆分：" + bid)
        visiting.add(bid)
        for cid in known[bid]["context_ids"]:
            visit(cid)
        visiting.remove(bid)
        seen.add(bid)
        for sibling in groups.get(bid, []):
            if sibling not in visiting and sibling not in seen:
                visit(sibling)
    for bid in pending:
        visit(bid)
    return [b["block_id"] for b in material["blocks"] if b["block_id"] in seen]


def core_closure(material):
    canonical = assembly_material(material)
    return closure(canonical, canonical["core_block_ids"])


def block_text(material, block_id):
    b = next((b for b in material["blocks"] if b["block_id"] == block_id), None)
    if b is None:
        raise ValueError("未知原文块")
    return material["source_text"][b["start"]:b["end"]]


def ordered_text(material, ids):
    selected = set(ids)
    return "\n\n".join((f"[{b['heading_path']}]\n" if b["heading_path"] and "interpretation" not in material else "")
                       + interpretation.effective_text(material, b["block_id"]) for b in material["blocks"]
                       if b["block_id"] in selected)


def core_text(material):
    return ordered_text(material, core_closure(material))


def core_reference(material):
    return "## 底色 · 核心（表达参考）\n\n" + REGISTRY_USAGE + "\n\n" + core_text(material)


def bundle_closure(material, bundle):
    try:
        return closure(material, bundle["block_ids"])
    except ValueError:
        return None


def bundle_text(material, bundle, *, already_present=None):
    ids = bundle_closure(material, bundle)
    if ids is None:
        raise ValueError("场景正文及共同上下文尚未确认可用")
    return ordered_text(material, set(ids) - set(already_present or ()))


def catalogue_block(rows, nonce):
    refs, lines = {}, ["人格场景索引（尚未展开，表达参考不是经历或权限）："]
    for i, row in enumerate(rows, 1):
        ref = f"persona:{nonce}:{i}"
        refs[ref] = row["bundle_id"]
        lines.append([ref, row["label"], "；".join(row["cues"])])
    return lines[0] + "\n" + json.dumps(lines[1:], ensure_ascii=False, separators=(",", ":")), refs


def load_item(material, bundle, ref, present):
    ids = closure(material, bundle["block_ids"])
    return {"ref": ref, "status": "loaded", "label": bundle["label"],
            "text": ordered_text(material, set(ids) - set(present)), "block_ids": ids,
            "already_present_ids": [bid for bid in ids if bid in present]}


def load_reply(items):
    return json.dumps({"items": items}, ensure_ascii=False, separators=(",", ":"))


def _full_roots(material):
    enabled = {bid for b in material["bundles"] if b["enabled"] for bid in b["block_ids"]}
    disabled = {bid for b in material["bundles"] if not b["enabled"] for bid in b["block_ids"]} - enabled
    return [bid for bid in material["classifications"] if usable(material, bid)
            and (bid not in disabled or bid in material["core_block_ids"])]


def full_ids(material):
    ids, excluded = set(), []
    for bid in _full_roots(material):
        try:
            ids.update(closure(material, [bid]))
        except ValueError as exc:
            excluded.append({"block_id": bid, "reason": str(exc)})
    return [b["block_id"] for b in material["blocks"] if b["block_id"] in ids], excluded


def project_full(material, *, purpose="chat", budget=None):
    if purpose != "chat":
        raise ValueError("原文材料首版只支持本机网页表达；此用途暂未开放")
    require_material(material)
    ids, _ = full_ids(material)
    body = "## 底色 · 气质与风格\n\n" + USAGE + "\n\n" + ordered_text(material, ids)
    if not ids and "interpretation" in material:
        body += "（当前没有已采用的表达参考。）"
    if (not ids and "interpretation" not in material) or (budget is not None and len(body) > budget):
        raise ValueError("原文完整投影为空或超过预算，请调整范围或使用可用的按需模式")
    return body


def projection_plan(material, configured=None, *, nonce="0000000000000000"):
    """One admission calculation for preview, install, mode switching and runtime.

    Bad scenes remain visible but cannot enter the issued catalogue. Runtime
    still checks the exact aggregate receipts and total request token budget.
    """
    require_material(material)
    material = assembly_material(material)
    cap = effective_limits(material, configured)
    demotions = []
    if "interpretation" in material:
        selected = []
        for bid in list(material["core_block_ids"]):
            try:
                candidate = "## 底色 · 核心（表达参考）\n\n" + REGISTRY_USAGE + "\n\n" + ordered_text(material, closure(material, selected + [bid]))
                if len(candidate) > cap["max_core_chars"]:
                    raise ValueError("常驻核心超过预算，改为按需")
                selected.append(bid)
            except ValueError as exc:
                material["classifications"][bid]["placement"] = "按需"
                demotions.append({"block_id": bid, "reason": str(exc)})
        material["core_block_ids"] = selected
    core_ids, core_body, core_issues = [], "", []
    try:
        core_ids = core_closure(material)
        core_body = core_reference(material)
        if not core_ids and "interpretation" not in material:
            core_issues.append("核心为空")
        if len(core_body) > cap["max_core_chars"]:
            core_issues.append("核心及使用说明超过预算")
    except ValueError as exc:
        core_issues.append(str(exc))
    rows, excluded = [], []
    # Unassigned accepted blocks get bounded, individually addressable entries.
    # Never split an excluded manual scene to evade its conditions or budgets.
    candidates = list(material["bundles"])
    used_bundle_ids = {b["bundle_id"] for b in candidates}
    groups = dialogue_groups(material)
    assigned = {bid for b in candidates for bid in b["block_ids"]}
    for bid in list(assigned):
        assigned.update(groups.get(bid, []))
    for block in material["blocks"]:
        bid = block["block_id"]
        if bid in assigned or bid in core_ids or not usable(material, bid):
            continue
        row = material["classifications"][bid]
        heading = block["heading_path"] if "interpretation" not in material else ""
        label = (row["cues"][0] if row["cues"] else heading or interpretation.effective_text(material, bid).strip().splitlines()[0])[:MAX_LABEL_CHARS]
        auto_id = "auto_" + _sha256(bid)[:16]
        while auto_id in used_bundle_ids:
            auto_id += "_"
        used_bundle_ids.add(auto_id)
        candidates.append({"bundle_id": auto_id, "label": label or bid,
                           "cues": list(row["cues"]), "block_ids": [bid], "enabled": True})
        assigned.update(groups.get(bid, [bid]))
    for b in candidates:
        if not b["enabled"]:
            excluded.append({"bundle_id": b["bundle_id"], "reason": "已停用"})
            continue
        try:
            closure(material, b["block_ids"])
            if len(rows) >= cap["max_registry_bundles"]:
                raise ValueError("场景数量超过冻结额度")
            trial = rows + [b]
            cat, refs = catalogue_block(trial, nonce)
            ref = next(ref for ref, bid in refs.items() if bid == b["bundle_id"])
            item = load_item(material, b, ref, set(core_ids))
            if len(load_reply([item])) > cap["max_scene_expanded_chars"]:
                raise ValueError("完整场景回执超过额度")
            if len(cat) > cap["max_registry_chars"]:
                raise ValueError("场景目录超过额度")
            if len(core_body) + len(cat) + len(load_reply([item])) > cap["max_projection_chars"]:
                raise ValueError("核心、目录及场景回执合计超过表达额度")
            rows.append(b)
        except ValueError as exc:
            excluded.append({"bundle_id": b["bundle_id"], "reason": str(exc)})
    cat = catalogue_block(rows, nonce)[0] if rows else ""
    registry_issues = list(core_issues)
    if len(core_body) + len(cat) > cap["max_projection_chars"]:
        registry_issues.append("核心与目录合计超过表达额度")
    full, full_issues = "", [] if "interpretation" in material else list(core_issues)
    try:
        full = project_full(material, budget=cap["max_projection_chars"])
    except ValueError as exc:
        full_issues.append(str(exc))
    reached = set(core_ids)
    for b in rows:
        reached.update(closure(material, b["block_ids"]))
    coverage = []
    for block in material["blocks"]:
        bid = block["block_id"]
        if bid in core_ids:
            state, reason = "core", "常驻（包含必要共同上下文）"
        elif bid in reached:
            state, reason = "scene", "活动场景可读取"
        elif not usable(material, bid):
            state, reason = "inactive", "待确认、暂停或仅存档"
        else:
            failures = [e["reason"] for e in excluded for b in candidates
                        if b["bundle_id"] == e["bundle_id"] and bid in (bundle_closure(material, b) or b["block_ids"])]
            state, reason = "excluded", "；".join(dict.fromkeys(failures)) or "无可发布路径，请核对共同上下文与预算"
        coverage.append({"block_id": bid, "state": state, "reason": reason})
    return {"material": material, "assembly_version": 4, "interpretation": interpretation.summary(material),
            "core_demotions": demotions, "coverage": coverage,
            "unreachable_block_ids": [r["block_id"] for r in coverage if r["state"] == "excluded"],
            "candidate_bundles": candidates,
            "limits": cap, "core_ids": core_ids, "core_text": core_body,
            "core_chars": len(core_body), "catalogue": rows, "catalogue_text": cat,
            "excluded_bundles": excluded, "full_text": full,
            "full_exclusions": full_ids(material)[1],
            "mode_issues": {"full": full_issues, "registry": registry_issues},
            "supported_modes": [m for m, issues in (("full", full_issues), ("registry", registry_issues)) if not issues]}


def catalogue_rows(material, *, purpose="chat"):
    return projection_plan(material)["catalogue"] if purpose == "chat" else []


def apply_classification(material, rows):
    result = copy.deepcopy(material)
    report = {"accepted": [], "isolated": [], "conflicts": []}
    if not isinstance(rows, list) or len(rows) > 1200:
        report["isolated"].append({"block": None, "reason": "rows_invalid"})
        return result, report
    known, by_id = set(result["classifications"]), {}
    for row in rows:
        bid = row.get("block_id") if isinstance(row, dict) else None
        if not isinstance(bid, str) or bid not in known:
            report["isolated"].append({"block": bid if isinstance(bid, str) else None, "reason": "block_invalid"})
            continue
        if set(row) - {"block_id", "kind", "placement", "cues"} or not {"kind", "placement"} <= set(row):
            report["isolated"].append({"block": bid, "reason": "fields_invalid"})
            by_id.setdefault(bid, []).append(None)
            continue
        candidate = {"kind": row["kind"], "placement": row["placement"],
                     "cues": copy.deepcopy(row.get("cues", [])), "status": "accepted"}
        if not valid_classification(candidate):
            report["isolated"].append({"block": bid, "reason": "classification_invalid"})
            by_id.setdefault(bid, []).append(None)
            continue
        by_id.setdefault(bid, []).append(candidate)
    for bid, values in by_id.items():
        if any(v is None for v in values) or len({_canonical(v) for v in values}) != 1:
            result["classifications"][bid] = pending_row()
            report["conflicts"].append({"block": bid, "reason": "invalid_or_conflicting_rows"})
        else:
            result["classifications"][bid] = values[0]
            report["accepted"].append(bid)
    return result, report


def remaining_blocks(material):
    return [bid for bid, row in material["classifications"].items() if row["status"] in ("pending", "isolated")]


def split_block(material, bid, offsets):
    """User-selected source offsets; no generated text or implicit approvals."""
    require_material(material)
    old = next((b for b in material["blocks"] if b["block_id"] == bid), None)
    if old is None or not isinstance(offsets, list) or not offsets or any(type(n) is not int for n in offsets):
        raise ValueError("请选择原文块和有效的字符分割位置")
    length = old["end"] - old["start"]
    if offsets != sorted(set(offsets)) or not all(0 < n < length for n in offsets):
        raise ValueError("分割位置须递增、不重复并位于块内部")
    if _FENCE.match(block_text(material, bid)) or any(d["block_id"] == bid for d in material["derivatives"]):
        raise ValueError("围栏或已关联派生样例的块不能直接拆开，请先核对原稿")
    result, children = copy.deepcopy(material), []
    boundaries = [0, *offsets, length]
    known = {b["block_id"] for b in material["blocks"]}
    for i, (a, z) in enumerate(zip(boundaries, boundaries[1:]), 1):
        child = {**old, "block_id": f"{bid}_{i}", "start": old["start"] + a, "end": old["start"] + z}
        if child["block_id"] in known or not _ID.fullmatch(child["block_id"]) or not material["source_text"][child["start"]:child["end"]].strip():
            raise ValueError("分割产生重复ID或空白块，请重新选择位置")
        children.append(child)
    result["blocks"] = [c for b in result["blocks"] for c in (children if b["block_id"] == bid else [b])]
    result["classifications"].pop(bid)
    result["classifications"].update({b["block_id"]: pending_row() for b in children})
    if "interpretation" in result:
        result["interpretation"]["blocks"].pop(bid)
        result["interpretation"]["blocks"].update({b["block_id"]: interpretation.pending() for b in children})
    result["core_block_ids"] = [b for b in result["core_block_ids"] if b != bid]
    for b in result["blocks"]:
        b["context_ids"] = [c for cid in b["context_ids"] for c in ([n["block_id"] for n in children] if cid == bid else [cid])]
    for b in result["bundles"]:
        if bid in b["block_ids"]:
            b["block_ids"] = [c for cid in b["block_ids"] for c in ([n["block_id"] for n in children] if cid == bid else [cid])]
    result["revision"] += 1
    require_material(result)
    return result


def encode_sections(material, *, title="底色 · 气质与风格"):
    require_material(material)
    data = base64.b64encode(_canonical(material).encode()).decode("ascii")
    return {title: f"{MARKER_PREFIX}{data} -->\n\n（原文及导航由宿主按用途投影，封套不直接发送。）"}


def decode_sections(sections, *, title="底色 · 气质与风格"):
    hits = [(k, b) for k, b in sections.items() if MARKER_PREFIX in b]
    if not hits:
        return None
    if len(hits) != 1 or hits[0][0] != title or len(hits[0][1]) > 8000000:
        raise ValueError("原文封套标记或资源范围无效")
    text = hits[0][1]
    match = _MARKER_RE.match(text)
    if match is None or MARKER_PREFIX in text[match.end():]:
        raise ValueError("原文封套损坏，不能降级成普通文本")
    from .expression import strict_json
    try:
        material = strict_json(base64.b64decode(match[1], validate=True).decode("utf-8"))
    except (ValueError, UnicodeError) as exc:
        raise ValueError("原文材料封套不可解析") from exc
    # Earlier unshipped v1 candidates had no frozen limits or enabled flag.
    # Freeze their documented ceilings rather than adopting a larger live cfg.
    if isinstance(material, dict) and set(material) == MATERIAL_KEYS - {"limits"}:
        material["limits"] = dict(_DEFAULT_LIMITS)
    if isinstance(material, dict) and isinstance(material.get("bundles"), list):
        for b in material["bundles"]:
            if isinstance(b, dict) and set(b) == BUNDLE_KEYS - {"enabled"}:
                b["enabled"] = True
    require_material(material)
    return material


def apply_derivative(material, row):
    require_material(material)
    if (not isinstance(row, dict) or set(row) != {"block_id", "purpose", "patch", "reason"}
            or not isinstance(row["block_id"], str) or row["block_id"] not in material["classifications"]
            or row["purpose"] != "chat" or not isinstance(row["patch"], dict)
            or set(row["patch"]) != {"quote", "replacement"}
            or any(not isinstance(v, str) for v in row["patch"].values())
            or not isinstance(row["reason"], str) or not row["reason"].strip()):
        raise ValueError("派生片段记录无效")
    body, quote = block_text(material, row["block_id"]), row["patch"]["quote"]
    if not quote or body.count(quote) != 1 or row["patch"]["replacement"] == quote:
        raise ValueError("派生片段须唯一匹配且确有修改")
    record = {"derivative_id": f"d{len(material['derivatives']) + 1:04d}", "block_id": row["block_id"],
              "base_sha256": _sha256(body), "patch": dict(row["patch"]), "purpose": "chat",
              "status": "proposed", "version": 1}
    result = copy.deepcopy(material)
    result["derivatives"].append(record)
    return result, record


def derivative_text(material, derivative_id):
    for row in material["derivatives"]:
        if row["derivative_id"] == derivative_id and row["status"] == "accepted":
            body = block_text(material, row["block_id"])
            return body.replace(row["patch"]["quote"], row["patch"]["replacement"], 1) if _sha256(body) == row["base_sha256"] else None
    return None
