"""人格驻留合同（persona-residency-v1）：分类、摘要、依赖闭包与结构校验。

设计依据《人格注册表与模型按需装配-架构设计-v1-20261107.md》§3/§4。纯标准库、
纯逻辑：不读文件、不访问 Store、不调用模型（prepare_classification 除外，由调用方
注入 client）。宿主校验结构、ID、闭包、用途、版本和预算；语义分类（全局约束
识别、日常样本选择）仍需准备审议及真人查看，结构校验不宣称能替代。
"""
from __future__ import annotations

import hashlib
import json

from . import expression

FORMAT = "persona-residency-v1"
DIGEST_VERSION = "presdig-v1"
_CONTRACT_KEYS = {"format", "expression_digest", "core_ids", "bundles", "classification_revision"}
_BUNDLE_KEYS = {"id", "label", "when", "entry_ids", "required_ids", "purposes"}
_MAX_TEXT_FIELDS = 40000


def _canonical(value) -> str | None:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                          allow_nan=False)
    except (TypeError, ValueError, OverflowError):
        return None


def expression_digest(package: dict) -> str | None:
    """按确定规范化表示绑定整个表达 package（不含 assembly；算法独立版本）。"""
    canonical = _canonical(package)
    if canonical is None:
        return None
    return DIGEST_VERSION + ":sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def classification_digest(contract: dict) -> str | None:
    """分类自身的版本指纹（本轮引用绑定；生长小节变化不影响它）。"""
    canonical = _canonical({k: v for k, v in contract.items() if k != "expression_digest"})
    if canonical is None:
        return None
    return DIGEST_VERSION + ":sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def parse_contract(raw) -> dict | None:
    """形状校验：合法返回合同 dict，任何违例返回 None（不部分接受）。"""
    if not isinstance(raw, dict) or raw.get("format") != FORMAT:
        return None
    if set(raw) != _CONTRACT_KEYS:
        return None
    if not isinstance(raw.get("expression_digest"), str) or not raw["expression_digest"]:
        return None
    core = raw.get("core_ids")
    if not isinstance(core, list) or not core or len(set(core)) != len(core) \
            or any(not isinstance(x, str) or not x for x in core):
        return None
    bundles = raw.get("bundles")
    if not isinstance(bundles, list) or not bundles or len(bundles) > 64:
        return None
    ids = set()
    for bundle in bundles:
        if not isinstance(bundle, dict) or set(bundle) != _BUNDLE_KEYS:
            return None
        if not isinstance(bundle.get("id"), str) or not bundle["id"] or bundle["id"] in ids:
            return None
        ids.add(bundle["id"])
        for key in ("label", "when"):
            if not isinstance(bundle.get(key), str) or not bundle[key] or len(bundle[key]) > 600:
                return None
        for key in ("entry_ids", "required_ids", "purposes"):
            value = bundle.get(key)
            if not isinstance(value, list) or any(not isinstance(x, str) or not x for x in value) \
                    or len(set(value)) != len(value):
                return None
        if not bundle["entry_ids"]:
            return None
    if not isinstance(raw.get("classification_revision"), int) or isinstance(
            raw["classification_revision"], bool) or raw["classification_revision"] < 1:
        return None
    return raw


def _by_id(package: dict) -> dict:
    by_id = {}
    for group in expression.GROUPS:
        for item in package[group]:
            by_id[item["id"]] = {**item, "__group__": group}
    return by_id


def _group_of(by_id: dict, entry_id: str) -> str:
    return by_id.get(entry_id, {}).get("__group__", "")


def _closure(seed: set[str], by_id: dict) -> tuple[set[str], set[str]]:
    """按访问集合遍历 links；返回 (闭包, 未知引用)。依赖环不无限展开。"""
    seen, unknown = set(), set()
    stack = list(seed)
    while stack:
        entry_id = stack.pop()
        if entry_id in seen:
            continue
        item = by_id.get(entry_id)
        if item is None:
            unknown.add(entry_id)
            continue
        seen.add(entry_id)
        stack.extend(ref for ref in item.get("links", []) if ref not in seen)
    return seen, unknown


def entry_ids_of(package: dict) -> set[str]:
    return set(_by_id(package))


def core_closure(contract: dict, by_id: dict) -> tuple[set[str], set[str]]:
    return _closure(set(contract["core_ids"]), by_id)


def bundle_closure(contract: dict, bundle: dict, by_id: dict) -> tuple[set[str] | None, set[str]]:
    """场景闭包 = entry_ids + required_ids + 递归 links。未知ID → (None, unknown)。"""
    seed = set(bundle["entry_ids"]) | set(bundle["required_ids"])
    closure, unknown = _closure(seed, by_id)
    if unknown:
        return None, unknown
    return closure, unknown


def entry_text_of(item: dict, group: str) -> str:
    return expression.entry_text(item, group)


def _ordered_text(package: dict, closure: set[str]) -> str:
    parts = []
    for group in expression.GROUPS:
        for item in package[group]:
            if item["id"] in closure:
                parts.append(expression.entry_text(item, group))
    return "\n\n".join(parts)


def core_text(contract: dict, package: dict) -> str:
    closure, _ = core_closure(contract, _by_id(package))
    return _ordered_text(package, closure)


def bundle_parts(contract: dict, package: dict, bundle: dict) -> dict | None:
    """把闭包拆成 (bundle 自有成员 / core 已常驻 / 其余依赖)，供去重正文与 already_present。"""
    by_id = _by_id(package)
    closure, unknown = bundle_closure(contract, bundle, by_id)
    if closure is None:
        return None
    core, _ = core_closure(contract, by_id)
    own = list(dict.fromkeys(list(bundle["entry_ids"]) + list(bundle["required_ids"])))
    present = sorted(core & closure)
    rest = [entry_id for entry_id in closure
            if entry_id not in core and entry_id not in own]
    own_text = _ordered_text(package, set(own))
    rest_text = _ordered_text(package, set(rest))
    return {"own": own, "present": present, "rest": rest, "closure": closure,
            "text": "\n\n".join(x for x in (own_text, rest_text) if x),
            "rest_chars": len(rest_text), "chars": len(own_text) + len(rest_text)}


GROUP_LABELS = {"orientation": "取向", "constraints": "全局约束", "voice": "语感",
                "tensions": "张力", "reactions": "反应", "examples": "样本"}


def _contains_labels(package: dict, closure: set[str]) -> list[str]:
    labels = []
    for group in expression.GROUPS:
        if any(item["id"] in closure for item in package[group]):
            labels.append(GROUP_LABELS[group])
    return labels


def catalogue(contract: dict, package: dict, *, purpose: str, limits: dict) -> list[dict]:
    """当前用途的注册表条目：闭包/用途/预算全部有效的 bundle 才列出（设计 §4）。"""
    by_id = _by_id(package)
    out = []
    for bundle in contract["bundles"]:
        if purpose not in bundle["purposes"]:
            continue
        closure, unknown = bundle_closure(contract, bundle, by_id)
        if closure is None or unknown:
            continue
        if any(purpose not in by_id[entry_id]["purposes"] for entry_id in closure):
            continue
        text = _ordered_text(package, closure)
        if len(text) > limits.get("max_scene_expanded_chars", 2400):
            continue
        out.append({"id": bundle["id"], "label": bundle["label"], "when": bundle["when"],
                    "contains": _contains_labels(package, closure),
                    "estimated_chars": len(text),
                    "entry_ids": list(bundle["entry_ids"]),
                    "bundle_index": contract["bundles"].index(bundle)})
    return out


def validate_contract(contract: dict, package: dict, *, limits: dict | None = None) -> list[dict]:
    """结构校验：返回问题列表（空=可激活）。不宣称识别全部语义全局约束。"""
    issues: list[dict] = []
    cap = dict(expression.limits_for(None))
    cap.update(limits or {})
    max_core = cap.get("max_core_chars", 2200)
    max_registry = cap.get("max_registry_chars", 1600)
    max_bundles = cap.get("max_registry_bundles", 12)
    max_scene = cap.get("max_scene_expanded_chars", 2400)

    if contract.get("expression_digest") != expression_digest(package):
        issues.append({"code": "digest_mismatch",
                       "detail": "assembly 绑定的表达摘要与当前 package 不一致（正文已变更？）"})
    by_id = _by_id(package)
    known = set(by_id)
    core_ids = set(contract["core_ids"])
    if not core_ids <= known:
        issues.append({"code": "unknown_core_id", "detail": sorted(core_ids - known)})
    bundle_ids = [b["id"] for b in contract["bundles"]]
    if len(bundle_ids) != len(set(bundle_ids)):
        issues.append({"code": "duplicate_bundle_id"})
    if len(bundle_ids) > max_bundles:
        issues.append({"code": "too_many_bundles", "detail": f"{len(bundle_ids)} > {max_bundles}"})

    core, core_unknown = core_closure(contract, by_id)
    if core_unknown:
        issues.append({"code": "unknown_core_id", "detail": sorted(core_unknown)})

    covered = set(contract["core_ids"])
    catalogue_chars = 0
    for bundle in contract["bundles"]:
        closure, unknown = bundle_closure(contract, bundle, by_id)
        if unknown:
            issues.append({"code": "bundle_unknown_ref", "detail": {"bundle": bundle["id"],
                                                                    "unknown": sorted(unknown)}})
            continue
        covered |= set(bundle["entry_ids"])
        if not set(bundle["purposes"]):
            issues.append({"code": "bundle_purpose_empty", "detail": bundle["id"]})
        for entry_id in closure:
            member = by_id[entry_id]
            missing = [p for p in bundle["purposes"] if p not in member["purposes"]]
            if missing:
                issues.append({"code": "purpose_mismatch",
                               "detail": {"bundle": bundle["id"], "entry": entry_id, "missing": missing}})
        parts = bundle_parts(contract, package, bundle)
        if parts is None:
            continue
        text = _ordered_text(package, closure)
        if len(text) > max_scene:
            issues.append({"code": "scene_too_large", "detail": {"bundle": bundle["id"],
                                                                 "chars": len(text), "max": max_scene}})
        label = bundle["label"] + bundle["when"]
        catalogue_chars += len(label) + 24
    if not core_ids <= covered:
        pass  # core 自身计入 covered
    missing = known - covered
    if missing:
        issues.append({"code": "uncovered_entries", "detail": sorted(missing)})

    # core 依赖闭包必须也在 core（§3.2）：准备阶段修正分类或拒绝候选
    for entry_id in core:
        item = by_id.get(entry_id)
        if item is None:
            continue
        outside = [ref for ref in item.get("links", []) if ref not in core and ref in known]
        if outside:
            issues.append({"code": "core_dependency_on_demand",
                           "detail": {"entry": entry_id, "needs_core": sorted(outside)}})

    core_text_value = _ordered_text(package, core) if core else ""
    if len(core_text_value) > max_core:
        issues.append({"code": "core_too_large", "detail": {"chars": len(core_text_value), "max": max_core}})
    if catalogue_chars > max_registry:
        issues.append({"code": "registry_too_large", "detail": {"chars": catalogue_chars, "max": max_registry}})
    return issues


def contract_from_package_shape(package: dict) -> dict:
    """给准备阶段的素材清单：条目ID、组、scope 摘要与用途（模型分类输入）。"""
    by_id = _by_id(package)
    items = []
    for group in expression.GROUPS:
        for item in package[group]:
            items.append({"id": item["id"], "group": group, "scope": item["scope"][:120],
                          "purposes": list(item["purposes"]), "links": list(item["links"])})
    return {"digest": expression_digest(package), "items": items,
            "groups": list(expression.GROUPS), "group_labels": GROUP_LABELS}


def prepare_classification(package: dict, client, cfg) -> tuple[dict | None, list[dict]]:
    """模型辅助分类（R1 准备阶段）：提出 core/bundles → 结构校验。不写任何状态。"""
    from .llm import chat, parse_json
    material = contract_from_package_shape(package)
    prompt = (
        "下面是一个表达人格包的全部条目清单（ID/类别/范围摘要/用途/依赖）。"
        "请把它们分为「常驻核心 core」与若干「场景包 bundle」。\n"
        "规则：默认相处取向、常用语感、全局约束与日常样本进 core；"
        "专门反应、条件张力、相关样本与台词按完整场景登记为 bundle；"
        "每个条目必须归入 core 或至少一个 bundle；bundle 依赖的条目若被该场景需要，"
        "放进 entry_ids 或 required_ids；bundle.purposes 只能是成员条目用途的交集；"
        "不新增心理事实、用户状态或指令，只按原材料归类。\n"
        "只输出 JSON：{\"core_ids\":[...],\"bundles\":[{\"id\":\"英文或拼音短ID\",\"label\":\"场景名\","
        "\"when\":\"何时适用\",\"entry_ids\":[...],\"required_ids\":[...],\"purposes\":[...]}]}\n"
        + json.dumps(material, ensure_ascii=False))
    resp = chat(client, cfg, [{"role": "user", "content": prompt}],
                model=cfg.llm.cheap_model, json_mode=True)
    data = parse_json(resp.choices[0].message.content or "")
    contract = {"format": FORMAT, "expression_digest": expression_digest(package),
                "core_ids": list(data.get("core_ids") or []),
                "bundles": list(data.get("bundles") or []),
                "classification_revision": 1}
    issues = validate_contract(contract, package, limits=dict(cfg.expression.__dict__) if hasattr(
        cfg.expression, "__dict__") else None)
    return (contract if not issues else None), issues
