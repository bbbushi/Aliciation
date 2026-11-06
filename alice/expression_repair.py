"""Bounded, local application of an independently requested donor-content patch.

This module never calls a model, writes files, installs a candidate, or marks a
review as passed. The caller must accept initial issues and review the actual change impact.
User-edited packages permit only the separately checked donor-sample reply path.
"""
from __future__ import annotations

import copy
import json
import re

from . import expression as ex

POLICY = "exact_review_spans_v1"

REPAIR_TARGET_SCHEMA = (
    "\nrepair_targets必须嵌在所属的每一条审查意见对象内，不能放到顶层，也不能把多个问题的定位合成总表。"
    "问题行形状为{id,verdict,reason,repair_targets:[{item_id,field,quote}]}；"
    "例如items数组内的某一问题行，repair_targets与该行id/verdict/reason同级；sources/scenarios问题行同理。"
    "只定位当前候选中需要修改的具体片段，不生成替换文案。"
    "事实unsupported及保真lost必须有repair_targets字段并定位全部问题片段；无法准确定位时明确返回空数组，不能猜测位置。"
    "普通missing可给现有表达的局部插入锚点，也可空数组；通过项不需要定位。"
    "item_id是当前候选条目ID，field是scope或该类型专用文本字段，禁止user_input/scenario或其他元数据。"
    "quote须逐字引用该字段内唯一出现的连续原片段，保持标点和空白；选足以定位问题的最小片段，不含无关意象或整段已成立内容。"
    "同一条目多个字段/多处问题分开定位，不把多个原片段用省略号拼接。"
    "事实items和保真items只能定位本条目；sources只能定位实际引用本源的条目；scenarios只能定位该coverage行明确引用的条目。"
)

PATCH_SCHEMA = (
    "\n现有条目只能使用repair_contract.targets授权的精确片段修改，不能返回完整字段或完整条目。"
    "返回{checks:[{id,action:'update'|'keep',reason}],patches:[{target_id,base_hash,replacement,reason}],additions:[]}。"
    "checks按scope.update_ids每个ID恰好一条；update的条目须有实际patch，keep不得夹带修改。"
    "target_id和base_hash逐字复制repair_contract中该目标的值，不自行计算hash、不改item_id/field/quote。"
    "replacement只是替换该quote的新片段，可为空表示删除；不得照抄原quote形成无变化。"
    "同一个target_id最多一次；同字段不能同时提交重叠片段，所有patch基于同一个旧字段版本。"
    "宿主会按原位置应用，未获准文字逐字保留；无关字段、用户输入、场景、ID、来源、links、用途、受众、required均不可修改。"
    "定位不足不能扩大quote或退回整字段重写，应keep并说明；修复仍须通过事实与保真核对。"
)


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _supported_scenarios(package):
    contract = package.get("coverage_contract", {})
    if contract.get("policy") != "source_coverage_v1":
        return {}
    return {row["scenario"]: set(row["source_ids"]) for row in contract["scenarios"]}


def _donor_items(package, *, registry=None, cap=None):
    ex.validate_package(package, registry=registry, limits=cap)
    items = {item["id"]: (group, item) for group in ex.GROUPS for item in package[group]}
    if (ex.has_user_revisions(package) or any(item["origin"] == "user_config" for _, item in items.values())
            or package.get("pending_associations")
            or any(row["status"] == "user_revised" for row in package["destinations"])):
        raise ValueError("同次表达内容修复不能改写用户修订或旧格式保留内容")
    return items


def _selected_reviews(package, reviews, mode):
    """Derive editable targets for exactly one host-selected repair class.

    Mandatory repairs never acquire ordinary omissions just because they share
    a large source unit. Source/scene *lost* verdicts still need their actual
    referenced targets; they cannot be resolved by pretending they are missing.
    """
    if mode not in ("mandatory", "optional"):
        raise ValueError("内容修复类型无效")
    ex.validate_package(package)
    items = {item["id"]: (group, item) for group in ex.GROUPS for item in package[group]}
    fidelity_verdicts = {"passed", "lost"}
    if package.get("coverage_contract", {}).get("policy") == "source_coverage_v1":
        fidelity_verdicts.add("missing")
    expected = {
        "fact": (set(items), {"valid", "unsupported"}, "valid"),
        "fidelity_items": (set(items), fidelity_verdicts, "passed"),
        "fidelity_sources": (set(package["source_ids"]), fidelity_verdicts, "passed"),
        "fidelity_scenarios": ({row["scenario"] for row in package["coverage"]}, fidelity_verdicts, "passed"),
    }
    if not isinstance(reviews, dict) or set(reviews) != set(expected):
        raise ValueError("内容修复缺少完整独立核对结果")
    selected = {}
    for key, (known, verdicts, passed) in expected.items():
        rows = reviews[key]
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ValueError("内容修复核对分组无效：" + key)
        ids = [row.get("id") for row in rows]
        if (any(not isinstance(identity, str) for identity in ids)
                or len(set(ids)) != len(ids) or set(ids) != known):
            raise ValueError("内容修复核对 ID 不完整：" + key)
        if any(not isinstance(row.get("verdict"), str) or row["verdict"] not in verdicts
               or not isinstance(row.get("reason"), str) or not row["reason"].strip() for row in rows):
            raise ValueError("内容修复核对结论或理由无效：" + key)
        wanted = ("unsupported" if key == "fact" else "lost") if mode == "mandatory" else "missing"
        selected[key] = [row for row in rows if row["verdict"] == wanted]
    return items, selected


def _associated_ids(package, review_group, identity):
    if review_group in {"fact", "fidelity_items"}:
        return {identity}
    if review_group == "fidelity_sources":
        return {item["id"] for item in ex.entries(package) if identity in item["source_ids"]}
    if review_group == "fidelity_scenarios":
        row = next((row for row in package["coverage"] if row["scenario"] == identity), None)
        return set(row.get("rule_ids", [])) | set(row["example_ids"]) if row is not None else set()
    raise ValueError("内容修复核对分组无效")


def _adaptation(ref):
    if ref["group"] == "fact":
        return "fact_premise_correction"
    return "semantic_restore" if ref["verdict"] == "lost" else "ordinary_completion"


def _location(package, items, ref, target):
    if not isinstance(target, dict) or set(target) != {"item_id", "field", "quote"}:
        raise ValueError("初审修复定位必须包含item_id、field和quote")
    identity, field, quote = target["item_id"], target["field"], target["quote"]
    if (not isinstance(identity, str) or identity not in items
            or identity not in _associated_ids(package, ref["group"], ref["id"])):
        raise ValueError("初审修复定位超出该问题的实际关联条目")
    group, item = items[identity]
    if item["origin"] == "user_config":
        raise ValueError("初审修复定位不能授权改写用户配置条目")
    allowed = {"scope", *ex._FIELDS[group]} - {"scenario", "user_input"}
    if not isinstance(field, str) or field not in allowed:
        raise ValueError("初审修复定位不能修改用户输入、场景或元数据")
    ex._string(quote, "repair_target.quote", ex.limits_for(package.get("limits"))["max_item_chars"])
    value = item[field]
    start = value.find(quote)
    if start < 0 or value.find(quote, start + 1) >= 0:
        raise ValueError("初审修复片段必须在候选字段中唯一逐字匹配：" + identity + "." + field)
    return {"item_id": identity, "field": field, "quote": quote,
            "base_hash": ex.digest(value), "start": start, "end": start + len(quote)}


def build_repair_contract(package, reviews, *, mode="mandatory"):
    """Freeze actual initial-review locations; never derive free rewrite rights."""
    items, selected = _selected_reviews(package, reviews, mode)
    targets, unlocated, location_errors = {}, [], []
    for group, rows in selected.items():
        for row in rows:
            ref = {"group": group, **{key: row[key] for key in ("id", "verdict", "reason")}}
            try:
                locations = row.get("repair_targets", [])
                if (not isinstance(locations, list) or not locations
                        or len(locations) > ex.limits_for(package.get("limits"))["max_items"]):
                    raise ValueError("初审问题缺少修复定位或清单超限")
                checked, used = [], set()
                for raw in locations:
                    location = _location(package, items, ref, raw)
                    key = (location["item_id"], location["field"], location["quote"])
                    if key in used:
                        raise ValueError("同一初审问题重复声明修复片段")
                    used.add(key)
                    checked.append((key, location))
            except ValueError as exc:
                # An invalid location cannot grant a broad rewrite. The caller
                # stops mandatory work, or leaves this optional gap unchanged.
                unlocated.append(ref)
                location_errors.append({"review_ref": copy.deepcopy(ref), "error": str(exc)})
                continue
            for key, location in checked:
                if key not in targets:
                    targets[key] = {"id": "repair_t" + str(len(targets) + 1).zfill(4), **location,
                                    "review_refs": [], "allowed_adaptation": []}
                target = targets[key]
                target["review_refs"].append(ref)
                adaptation = _adaptation(ref)
                if adaptation not in target["allowed_adaptation"]:
                    target["allowed_adaptation"].append(adaptation)
    contract = {"policy": POLICY, "mode": mode, "candidate_digest": ex.review_digest(package),
                "reviews_digest": ex.digest(_canonical(reviews)), "targets": list(targets.values()),
                "unlocated": unlocated, "location_errors": location_errors}
    contract["contract_digest"] = ex.digest(_canonical(contract))
    return validate_repair_contract(package, contract)


def validate_repair_contract(package, contract):
    """Validate a host contract against its exact before-candidate snapshot."""
    items = {item["id"]: (group, item) for group in ex.GROUPS for item in package[group]}
    if (not isinstance(contract, dict)
            or set(contract) != {"policy", "mode", "candidate_digest", "reviews_digest", "targets", "unlocated", "location_errors", "contract_digest"}
            or contract["policy"] != POLICY or contract["mode"] not in {"mandatory", "optional"}
            or contract["candidate_digest"] != ex.review_digest(package)
            or not isinstance(contract["reviews_digest"], str)
            or not re.fullmatch(r"[0-9a-f]{64}", contract["reviews_digest"])
            or contract["contract_digest"] != ex.digest(_canonical({key: value for key, value in contract.items() if key != "contract_digest"}))):
        raise ValueError("片段修复合同身份、候选版本或摘要不一致")
    if any(not isinstance(contract[key], list) for key in ("targets", "unlocated", "location_errors")):
        raise ValueError("片段修复合同清单无效")
    known = {"fact": set(items), "fidelity_items": set(items), "fidelity_sources": set(package["source_ids"]),
             "fidelity_scenarios": {row["scenario"] for row in package["coverage"]}}

    def check_ref(ref):
        if (not isinstance(ref, dict) or set(ref) != {"group", "id", "verdict", "reason"}
                or not isinstance(ref["group"], str) or ref["group"] not in known
                or not isinstance(ref["id"], str) or ref["id"] not in known[ref["group"]]):
            raise ValueError("片段修复合同原问题引用无效")
        expected = ("unsupported" if ref["group"] == "fact" else "lost") if contract["mode"] == "mandatory" else "missing"
        if ref["verdict"] != expected or contract["mode"] == "optional" and ref["group"] == "fact":
            raise ValueError("片段修复合同混入其他类别问题")
        ex._string(ref["reason"], "repair_ref.reason", ex.limits_for(package.get("limits"))["max_review_chars"])

    locations, identities = set(), set()
    for number, target in enumerate(contract["targets"], 1):
        if (not isinstance(target, dict)
                or set(target) != {"id", "item_id", "field", "quote", "base_hash", "start", "end", "review_refs", "allowed_adaptation"}
                or target["id"] != "repair_t" + str(number).zfill(4)
                or type(target["start"]) is not int or type(target["end"]) is not int
                or not isinstance(target["review_refs"], list) or not target["review_refs"]):
            raise ValueError("片段修复合同目标字段无效")
        refs, adaptations = set(), []
        for ref in target["review_refs"]:
            check_ref(ref)
            ref_key = (ref["group"], ref["id"])
            if ref_key in refs:
                raise ValueError("片段修复合同重复原问题引用")
            refs.add(ref_key)
            location = _location(package, items, ref, {key: target[key] for key in ("item_id", "field", "quote")})
            if any(target[key] != value for key, value in location.items()):
                raise ValueError("片段修复合同位置或字段版本不一致")
            adaptation = _adaptation(ref)
            if adaptation not in adaptations:
                adaptations.append(adaptation)
        if target["allowed_adaptation"] != adaptations:
            raise ValueError("片段修复合同不能自行扩大适配权限")
        key = (target["item_id"], target["field"], target["quote"])
        if key in locations:
            raise ValueError("片段修复合同重复目标")
        locations.add(key)
        identities.update(refs)
    unlocated = set()
    for ref in contract["unlocated"]:
        check_ref(ref)
        key = (ref["group"], ref["id"])
        if key in identities or key in unlocated:
            raise ValueError("未定位问题与授权片段矛盾或重复")
        unlocated.add(key)
    errors = set()
    for row in contract["location_errors"]:
        if not isinstance(row, dict) or set(row) != {"review_ref", "error"}:
            raise ValueError("片段定位诊断字段无效")
        check_ref(row["review_ref"])
        key = (row["review_ref"]["group"], row["review_ref"]["id"])
        if key not in unlocated or key in errors:
            raise ValueError("片段定位诊断与未定位清单不一致")
        if row["review_ref"] not in contract["unlocated"]:
            raise ValueError("片段定位诊断改变了原问题")
        ex._string(row["error"], "repair_location.error", ex.limits_for(package.get("limits"))["max_item_chars"])
        errors.add(key)
    if errors != unlocated:
        raise ValueError("未定位问题缺少诊断")
    return copy.deepcopy(contract)


def review_patch_scope(package, reviews, *, mode="mandatory", contract=None):
    """Only host-frozen spans grant existing-item modification permission."""
    items, selected = _selected_reviews(package, reviews, mode)
    expected_contract = build_repair_contract(package, reviews, mode=mode)
    if contract is None:
        contract = expected_contract
    if _canonical(validate_repair_contract(package, contract)) != _canonical(expected_contract):
        raise ValueError("修复合同不对应本次完整初审结果")
    if mode == "mandatory" and contract["unlocated"]:
        details = "、".join(ref["group"] + ":" + ref["id"] for ref in contract["unlocated"][:3])
        raise ValueError("必修问题缺少准确字段片段定位，未授权整字段重写：" + details)
    supported = _supported_scenarios(package)
    rejected = {key: {row["id"] for row in rows} for key, rows in selected.items()}
    sources = rejected["fidelity_sources"]
    targets = {target["item_id"] for target in contract["targets"]}
    return {"update_ids": [identity for identity in items if identity in targets],
            "source_ids": [identity for identity in package["source_ids"] if identity in sources],
            "scenarios": [{"scenario": row["scenario"],
                           "source_ids": [sid for sid in package["source_ids"] if sid in supported[row["scenario"]]]}
                          for row in package["coverage"]
                          if row["scenario"] in rejected["fidelity_scenarios"] and row["scenario"] in supported]}


def apply_review_patch(package, response, registry, cap, scope, *, contract):
    return _apply_review_patch(package, response, registry, cap, scope, contract=contract)


def apply_sample_patch(package, response, registry, cap, contract, affected):
    """Preserve user revisions while changing only authorized donor reply spans."""
    ex.validate_package(package, registry=registry, limits=cap)
    examples = {item["id"]: item for item in package["examples"] if item["origin"] != "user_config"}
    ex._ids(list(affected), "sample_patch.affected", set(examples))
    authorized = {target["item_id"] for target in validate_repair_contract(package, contract)["targets"]}
    if not authorized <= set(affected):
        raise ValueError("片段合同包含受保护样本修复范围外的条目")
    scope = {"update_ids": [item["id"] for item in package["examples"] if item["id"] in authorized],
             "source_ids": [], "scenarios": []}
    return _apply_review_patch(package, response, registry, cap, scope, contract=contract,
                               protected_samples=set(affected))


def _apply_review_patch(package, response, registry, cap, scope, *, contract, protected_samples=None):
    """Apply one explicit patch without changing identities, sharing, or verdicts.

    Existing entries retain metadata and source membership. New material is
    private chat-only and must cite an authorized source or supported scene.
    All changes remain tentative until bounded fact/fidelity acceptance completes.
    """
    cap = ex.limits_for(cap)
    if protected_samples is None:
        original = _donor_items(package, registry=registry, cap=cap)
    else:
        ex.validate_package(package, registry=registry, limits=cap)
        original = {item["id"]: (group, item) for group in ex.GROUPS for item in package[group]}
    contract = validate_repair_contract(package, contract)
    if contract["mode"] == "mandatory" and contract["unlocated"]:
        raise ValueError("必修问题缺少片段定位，不能应用自由改写")
    if not isinstance(scope, dict) or set(scope) != {"update_ids", "source_ids", "scenarios"}:
        raise ValueError("内容修复范围无效")
    ex._ids(scope["update_ids"], "repair.update_ids", set(original))
    ex._ids(scope["source_ids"], "repair.source_ids", set(package["source_ids"]))
    update_ids, source_ids = set(scope["update_ids"]), set(scope["source_ids"])
    targets = {row["id"]: row for row in contract["targets"]}
    if update_ids != {row["item_id"] for row in targets.values()}:
        raise ValueError("内容修复范围与精确片段合同不一致")
    if protected_samples is not None:
        if source_ids or scope["scenarios"] or any(
                target["item_id"] not in protected_samples or target["field"] != "reply"
                or original[target["item_id"]][0] != "examples"
                or original[target["item_id"]][1]["origin"] == "user_config"
                for target in targets.values()):
            raise ValueError("受保护修订只能修改获准捐赠样本的reply片段")
    supported = _supported_scenarios(package)
    registered = {row["scenario"] for row in package["coverage"]}
    if not isinstance(scope["scenarios"], list):
        raise ValueError("内容修复场景范围无效")
    scenario_sources = {}
    for row in scope["scenarios"]:
        if not isinstance(row, dict) or set(row) != {"scenario", "source_ids"}:
            raise ValueError("内容修复场景范围字段无效")
        scenario = row["scenario"]
        if (not isinstance(scenario, str) or scenario not in registered or scenario not in supported
                or scenario in scenario_sources):
            raise ValueError("内容修复场景重复、未登记或没有原稿合同支持")
        ex._ids(row["source_ids"], "repair.scenarios.source_ids", supported[scenario])
        if not row["source_ids"]:
            raise ValueError("内容修复场景缺少原稿支持来源")
        scenario_sources[scenario] = set(row["source_ids"])
    if not isinstance(response, dict) or set(response) != {"checks", "patches", "additions"}:
        raise ValueError("内容修复回执必须且只能包含 checks、patches 和 additions")
    if protected_samples is not None and response["additions"] != []:
        raise ValueError("受保护样本修复不能新增条目")
    if len(json.dumps(response, ensure_ascii=False)) > cap["max_review_chars"]:
        raise ValueError("内容修复回执超过资源上限")
    for key in ("checks", "patches", "additions"):
        if not isinstance(response[key], list) or any(not isinstance(row, dict) for row in response[key]):
            raise ValueError("内容修复条目清单无效：" + key)
        if len(response[key]) > (len(targets) if key == "patches" else cap["max_items"]):
            raise ValueError("内容修复条目清单超过资源上限")

    # A repair must account for every editable item, not only echo the earlier
    # negative comments. This is task coverage, never an independent verdict.
    checks = response["checks"]
    for row in checks:
        if set(row) != {"id", "action", "reason"}:
            raise ValueError("内容修复检查记录字段无效")
        if not isinstance(row["action"], str) or row["action"] not in ("update", "keep"):
            raise ValueError("内容修复检查只能声明 update 或 keep")
        ex._string(row["reason"], "内容修复检查理由", cap["max_item_chars"])
    checked_ids = [row["id"] for row in checks]
    ex._ids(checked_ids, "repair.checks", update_ids)
    if set(checked_ids) != update_ids:
        raise ValueError("内容修复检查未完整覆盖全部可编辑条目")
    patch_ids = [row.get("target_id") for row in response["patches"]]
    ex._ids(patch_ids, "repair.patches", set(targets))
    planned_ids = {targets[identity]["item_id"] for identity in patch_ids}
    if planned_ids != {row["id"] for row in checks if row["action"] == "update"}:
        raise ValueError("内容修复 checks 的 update/keep 与实际 patches 不一致")

    candidate = copy.deepcopy(package)
    current = {item["id"]: item for item in ex.entries(candidate)}
    audit = {"policy": POLICY, "scope": copy.deepcopy(scope), "contract_digest": contract["contract_digest"],
             "checks": copy.deepcopy(checks),
             "patches": [], "updates": [], "additions": [], "destinations": [], "coverage": [], "changed": False}
    changed = set()
    by_field = {}
    for row in response["patches"]:
        if set(row) != {"target_id", "base_hash", "replacement", "reason"}:
            raise ValueError("内容修复patch字段无效；不得返回自由整字段更新")
        target = targets[row["target_id"]]
        if row["base_hash"] != target["base_hash"]:
            raise ValueError("内容修复patch没有引用原字段版本")
        ex._string(row["replacement"], "patch.replacement", cap["max_item_chars"], empty=True)
        ex._string(row["reason"], "内容修复理由", cap["max_item_chars"])
        if row["replacement"] == target["quote"]:
            raise ValueError("内容修复patch没有实际变化：" + target["id"])
        by_field.setdefault((target["item_id"], target["field"]), []).append((target, row))
    for (identity, field), patches in by_field.items():
        ordered = sorted(patches, key=lambda pair: pair[0]["start"])
        if any(left[0]["end"] > right[0]["start"] for left, right in zip(ordered, ordered[1:])):
            raise ValueError("同一原字段的修复片段重叠，未应用任何改写")
        value = original[identity][1][field]
        for target, row in reversed(ordered):
            value = value[:target["start"]] + row["replacement"] + value[target["end"]:]
            audit["patches"].append({**copy.deepcopy(target), "replacement": row["replacement"], "reason": row["reason"]})
        ex._string(value, "patch完成字段", cap["max_item_chars"], empty=field == "exceptions")
        if value == original[identity][1][field]:
            raise ValueError("组合patch没有改变原字段")
        current[identity][field] = value
        changed.add(identity)
    for identity in original:
        if identity not in changed:
            continue
        group, before = original[identity]
        item = current[identity]
        item["origin"] = "replacement" if protected_samples is not None else "adapted"
        reasons = [row["reason"] for row in response["patches"] if targets[row["target_id"]]["item_id"] == identity]
        audit["updates"].append({"id": identity, "group": group, "reason": "；".join(reasons),
                                 "before": copy.deepcopy(before), "after": copy.deepcopy(item)})

    coverage = {row["scenario"]: row for row in candidate["coverage"]}
    coverage_additions = []
    for row in response["additions"]:
        if not {"group", "item", "reason"} <= set(row) or set(row) - {"group", "item", "reason", "scenarios"}:
            raise ValueError("内容修复新增记录字段无效")
        group, item = row["group"], row["item"]
        if not isinstance(group, str) or group not in ex.GROUPS or not isinstance(item, dict):
            raise ValueError("内容修复新增条目类型无效")
        ex._string(row["reason"], "内容修复新增理由", cap["max_item_chars"])
        ex._ids([item.get("id")], "repair.addition.id")
        identity = item["id"]
        if identity in current:
            raise ValueError("内容修复新增 ID 与已有条目冲突：" + identity)
        if item.get("origin") not in ("source", "adapted"):
            raise ValueError("内容修复新增条目来源只能是 source 或 adapted")
        if (item.get("audience") != "private" or item.get("purposes") != ["chat"]
                or item.get("pure_expression") is not False):
            raise ValueError("内容修复新增条目必须明确为私有 chat 表达，不得自动共享")
        ex._ids(item.get("source_ids"), "repair.addition.source_ids", set(package["source_ids"]))
        item_sources = set(item["source_ids"])
        assigned = []
        if group == "examples":
            if "scenarios" in row:
                raise ValueError("新增样本只能通过其 scenario 声明场景")
            scenario = item.get("scenario")
            if not isinstance(scenario, str) or scenario not in coverage:
                raise ValueError("内容修复新增样本不能声明未登记场景")
            target = coverage[scenario]
            if scenario in scenario_sources:
                if not item_sources & scenario_sources[scenario]:
                    raise ValueError("新增样本未引用其授权场景的原稿来源")
                if target["status"] not in ("covered", "missing", "unresolved"):
                    raise ValueError("内容修复不能改写已排除的场景范围")
            elif target["status"] != "covered":
                raise ValueError("内容修复新增样本须有已覆盖场景或明确原稿支持的补全范围")
            assigned = [scenario]
        elif "scenarios" in row:
            assigned = row["scenarios"]
            if (not isinstance(assigned, list) or any(not isinstance(value, str) for value in assigned)
                    or len(set(assigned)) != len(assigned)):
                raise ValueError("内容修复新增规则场景清单无效")
            for scenario in assigned:
                if scenario not in scenario_sources or not item_sources & scenario_sources[scenario]:
                    raise ValueError("新增规则场景越界或未引用该场景的原稿来源")
                if coverage[scenario]["status"] not in ("covered", "missing", "unresolved"):
                    raise ValueError("内容修复不能改写已排除的场景范围")
        assigned_sources = set().union(*(scenario_sources.get(scenario, set()) for scenario in assigned))
        if not item_sources & (source_ids | assigned_sources):
            raise ValueError("内容修复新增条目没有引用待恢复的原稿或场景来源")
        for scenario in assigned:
            coverage_additions.append((scenario, group, identity))
        item = copy.deepcopy(item)
        candidate[group].append(item)
        current[identity] = item
        changed.add(identity)
        audit["additions"].append({"id": identity, "group": group, "reason": row["reason"],
                                   "scenarios": copy.deepcopy(assigned),
                                   "before": None, "after": copy.deepcopy(item)})
    if not changed:
        # A bounded attempt may leave ordinary omissions visible. It never
        # resolves an unchanged hard objection or supplies a passing verdict.
        return candidate, audit
    audit["changed"] = True

    for scenario, group, identity in coverage_additions:
        row = coverage[scenario]
        field = "example_ids" if group == "examples" else "rule_ids"
        row[field].append(identity)
        if package.get("coverage_contract", {}).get("policy") == "source_coverage_v1":
            linked = set(row["example_ids"]) | set(row["rule_ids"])
            actual_sources = {sid for target in linked for sid in current[target]["source_ids"]}
            # A completion may link only part of a scene's known support. Keep
            # the original explicit evidence, including sources whose content
            # is still missing; additions cannot silently shrink that record.
            retained_sources = set(row["source_ids"]) | actual_sources
            row["source_ids"] = [sid for sid in package["source_ids"] if sid in retained_sources]
        if row["status"] in ("missing", "unresolved"):
            row["status"] = "covered"
            row["reason"] = "同次修复新增了原稿支持的场景条目；仍须独立核对实际覆盖"

    affected_sources = {sid for identity in changed for sid in current[identity]["source_ids"]}
    source_ids |= set().union(*scenario_sources.values())
    required_sources = {sid for identity in changed if current[identity].get("required") is True
                        for sid in current[identity]["source_ids"] if sid in source_ids}
    # Derive only the inverse for sources actually touched by changed content.
    # Status/required changes express repair intent, never a passed verdict.
    for row in candidate["destinations"]:
        sid = row["source_id"]
        if sid not in affected_sources:
            continue
        before = copy.deepcopy(row)
        row["targets"] = [item["id"] for item in ex.entries(candidate) if sid in item["source_ids"]]
        if sid in source_ids and row["status"] in ("background", "excluded", "unresolved"):
            row["status"] = "adapted"
        if sid in required_sources:
            row["required"] = True
        if sid in source_ids:
            row["reason"] = "同次审查后修复关联表达；修改见私有修复审计，仍须独立保真核对"
        if _canonical(row) != _canonical(before):
            audit["destinations"].append({"source_id": sid, "before": before, "after": copy.deepcopy(row)})
    for before, after in zip(package["coverage"], candidate["coverage"]):
        if _canonical(before) != _canonical(after):
            audit["coverage"].append({"scenario": before["scenario"],
                                      "before": copy.deepcopy(before), "after": copy.deepcopy(after)})
    candidate.pop("reviews", None)
    candidate.pop("review_digest", None)
    candidate.pop("coverage_report", None)
    candidate.pop("warnings", None)
    candidate.pop("review_notices", None)
    candidate["gaps"] = []
    gaps = ex.validate_package(candidate, registry=registry, limits=cap)
    if gaps:
        raise ValueError("内容修复仍有结构覆盖缺口：" + "；".join(gaps[:3]))
    return candidate, audit
