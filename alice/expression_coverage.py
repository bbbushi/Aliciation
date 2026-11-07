"""Frozen source-unit coverage, independent rule and example support.

The denominator counts registered source units, never complete behavior atoms.
This module performs no model calls or I/O and never calls validate_package.
"""
from __future__ import annotations

import copy
import json
import math


POLICY = "source_coverage_v1"
CONTRACT_FIELDS = {"policy", "source_hash", "source_ids", "uncertain_source_ids", "scenarios", "uncertain_scenarios"}
REGISTRY_FIELD = "registered_scenarios"
REPORT_FIELDS = {"policy", "available", "reviewed", "counting_unit", "sources", "rules", "examples",
                 "uncertain_sources", "uncertain_scenarios", "warnings", "repair_source_ids", "repair_scenarios"}
FIDELITY_GROUPS = ("fidelity_sources", "fidelity_items", "fidelity_scenarios")


def _core():
    from . import expression
    return expression


def _same(left, right):
    return json.dumps(left, ensure_ascii=False, sort_keys=True, allow_nan=False) == json.dumps(
        right, ensure_ascii=False, sort_keys=True, allow_nan=False)


def _names(value, name, cap, available=None):
    ex = _core()
    if not isinstance(value, list):
        raise ValueError("覆盖场景清单无效：" + name)
    for text in value:
        ex._string(text, name, cap["max_item_chars"])
    if len(set(value)) != len(value) or (available is not None and set(value) - set(available)):
        raise ValueError("覆盖场景重复或越界：" + name)


def _inventory(package):
    ex = _core()
    if not isinstance(package, dict):
        raise ValueError("覆盖候选必须是对象")
    cap = ex.limits_for(package.get("limits"))
    ex._ids(package.get("source_ids"), "coverage.package.source_ids")
    sources = set(package["source_ids"])
    items, rules, examples = {}, set(), set()
    for group in ex.GROUPS:
        rows = package.get(group)
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ValueError("覆盖关联的条目分组无效：" + group)
        ex._ids([row.get("id") for row in rows], "coverage." + group)
        for item in rows:
            identity = item["id"]
            if identity in items:
                raise ValueError("覆盖关联的条目 ID 重复：" + identity)
            ex._ids(item.get("source_ids"), "coverage.item.source_ids", sources)
            if group == "examples":
                ex._string(item.get("scenario"), "coverage.example.scenario", cap["max_item_chars"])
                examples.add(identity)
            else:
                rules.add(identity)
            items[identity] = item
    rows = package.get("coverage")
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("覆盖场景登记必须是对象清单")
    scenarios = [row.get("scenario") for row in rows]
    _names(scenarios, "coverage.scenario", cap)
    return cap, sources, items, rules, examples, {row["scenario"]: row for row in rows}


def _validate_rows(rows, sources, items, rules, examples):
    ex = _core()
    for row in rows.values():
        ex._ids(row.get("rule_ids"), "coverage.rule_ids", rules)
        ex._ids(row.get("example_ids"), "coverage.example_ids", examples)
        ex._ids(row.get("source_ids"), "coverage.source_ids", sources)
        if row.get("status") not in ("covered", "missing", "unresolved", "not_applicable"):
            raise ValueError("覆盖状态无效")
    ex._validate_coverage_scenarios(rows.values(), items)
    for row in rows.values():
        references = row["rule_ids"] + row["example_ids"]
        if any(set(items[identity]["source_ids"]) - set(row["source_ids"]) for identity in references):
            raise ValueError("覆盖登记缺少已关联条目的实际来源")
        if row["status"] == "covered" and not references:
            raise ValueError("已覆盖场景缺少明确规则或匹配样本")
        if row["status"] == "not_applicable" and (references or row["source_ids"]):
            raise ValueError("不适用场景不能同时声明规则、样本或来源")


def _validate_contract(contract, package, cap, sources, rows):
    ex = _core()
    if not isinstance(contract, dict) or set(contract) not in (CONTRACT_FIELDS, CONTRACT_FIELDS | {REGISTRY_FIELD}):
        raise ValueError("固定覆盖合同字段无效")
    if contract["policy"] != POLICY or contract["source_hash"] != package.get("source_hash"):
        raise ValueError("固定覆盖合同策略或原稿摘要不一致")
    if not isinstance(contract["source_hash"], str) or not ex._HASH.fullmatch(contract["source_hash"]):
        raise ValueError("固定覆盖合同原稿摘要无效")
    ex._ids(contract["source_ids"], "coverage_contract.source_ids", sources)
    ex._ids(contract["uncertain_source_ids"], "coverage_contract.uncertain_source_ids", sources)
    if set(contract["source_ids"]) & set(contract["uncertain_source_ids"]):
        raise ValueError("确定与待确认的合同源单元不能重叠")
    scenes = contract["scenarios"]
    if (not isinstance(scenes, list)
            or any(not isinstance(scene, dict) or set(scene) != {"scenario", "source_ids"} for scene in scenes)):
        raise ValueError("固定覆盖合同场景字段无效")
    names = [scene["scenario"] for scene in scenes]
    _names(names, "coverage_contract.scenarios", cap, rows)
    for scene in scenes:
        ex._ids(scene["source_ids"], "coverage_contract.scenario.source_ids", sources)
        if not scene["source_ids"]:
            raise ValueError("合同中的原稿支持场景必须具有来源")
        if rows[scene["scenario"]]["status"] == "not_applicable":
            raise ValueError("不能把合同已确认的原稿支持场景改为不适用")
    _names(contract["uncertain_scenarios"], "coverage_contract.uncertain_scenarios", cap, rows)
    if set(names) & set(contract["uncertain_scenarios"]):
        raise ValueError("确定与待确认的合同场景不能重叠")
    if REGISTRY_FIELD in contract:
        _names(contract[REGISTRY_FIELD], "coverage_contract." + REGISTRY_FIELD, cap)
        if set(contract[REGISTRY_FIELD]) != set(rows):
            raise ValueError("覆盖场景登记全集与冻结合同不一致")
        _names(package.get("required_scenarios"), "required_scenarios", cap, rows)
        if any(item["scenario"] not in rows for item in package["examples"]
               if item.get("origin") != "user_config"):
            raise ValueError("原稿样本场景缺少冻结覆盖登记")


def _register_scenarios(package, rows, cap, contract, audit):
    """Register declared names, never invent support or a passed coverage row.

    This runs only on freshly generated receipts. Runtime validation does not
    fill omissions. An explicit user sample stays outside the donor denominator.
    """
    ex = _core()
    declared = package.get("required_scenarios")
    _names(declared, "required_scenarios", cap)
    sample_names = {item["scenario"] for item in package["examples"] if item.get("origin") != "user_config"}
    requested = set(ex.SCENARIOS) | set(declared) | sample_names
    fixed_sources = {}
    if contract is None:
        names = set(rows) | requested
    else:
        if not isinstance(contract, dict) or set(contract) not in (CONTRACT_FIELDS, CONTRACT_FIELDS | {REGISTRY_FIELD}):
            raise ValueError("固定覆盖合同字段无效")
        scenes = contract["scenarios"]
        if (not isinstance(scenes, list)
                or any(not isinstance(scene, dict) or set(scene) != {"scenario", "source_ids"} for scene in scenes)):
            raise ValueError("固定覆盖合同场景字段无效")
        _names([scene["scenario"] for scene in scenes], "coverage_contract.scenarios", cap)
        _names(contract["uncertain_scenarios"], "coverage_contract.uncertain_scenarios", cap)
        fixed_sources = {scene["scenario"]: scene["source_ids"] for scene in scenes}
        if REGISTRY_FIELD in contract:
            _names(contract[REGISTRY_FIELD], "coverage_contract." + REGISTRY_FIELD, cap)
            names = set(contract[REGISTRY_FIELD])
            if (set(rows) | requested) - names:
                raise ValueError("适配回执新增场景超出冻结登记全集")
        else:
            # Old v1 contracts had no complete registry. Keep them readable and
            # restore only names known from their contract/current receipt.
            names = set(rows) | requested | set(fixed_sources) | set(contract["uncertain_scenarios"])
    for name in sorted(names - set(rows)):
        matching = [item for item in package["examples"]
                    if item.get("origin") != "user_config" and item["scenario"] == name]
        supported = copy.deepcopy(fixed_sources.get(name, []))
        ex._ids(supported, "coverage_contract.scenario.source_ids", set(package["source_ids"]))
        supported = sorted(set(supported) | {sid for item in matching for sid in item["source_ids"]})
        row = {"scenario": name, "status": "unresolved", "source_ids": supported,
               "rule_ids": [], "example_ids": [item["id"] for item in matching],
               "basis": "host_registration", "reason": (
                   "冻结场景在本次回执中漏登；宿主恢复待核对占位，不继承覆盖结论。" if contract is not None else
                   "宿主补齐已声明场景登记；原稿支持与实际覆盖尚待核对，不补造规则或样本。")}
        package["coverage"].append(row)
        rows[name] = row
        audit["registered_scenarios"].append({"scenario": name, "status": "unresolved",
                                            "source_ids": supported, "example_ids": list(row["example_ids"]),
                                            "restored": contract is not None})


def _warnings(value, cap):
    ex = _core()
    if not isinstance(value, list):
        raise ValueError("覆盖提示必须是字符串清单")
    for text in value:
        ex._string(text, "coverage.warnings", cap["max_review_chars"])
    if len(set(value)) != len(value):
        raise ValueError("覆盖提示不能重复")
    if len(json.dumps(value, ensure_ascii=False)) > cap["max_metadata_chars"]:
        raise ValueError("覆盖提示超过元数据资源上限")


def _validate_report(report, contract, cap, sources, rows):
    ex = _core()
    if not isinstance(report, dict) or set(report) != REPORT_FIELDS:
        raise ValueError("覆盖报告字段无效")
    if (report["policy"] != POLICY or report["available"] is not True
            or type(report["reviewed"]) is not bool or report["counting_unit"] != "source_units"):
        raise ValueError("覆盖报告策略、可用性或审查标记无效")
    for key, total in (("sources", len(contract["source_ids"])),
                       ("rules", len(contract["scenarios"])), ("examples", len(contract["scenarios"]))):
        metric = report[key]
        if not isinstance(metric, dict) or set(metric) != {"covered", "total", "percent"}:
            raise ValueError("覆盖报告计数字段无效：" + key)
        if (type(metric["covered"]) is not int or type(metric["total"]) is not int
                or metric["total"] != total or not 0 <= metric["covered"] <= total):
            raise ValueError("覆盖报告计数超出固定分母：" + key)
        percent = metric["percent"]
        if total == 0:
            if percent is not None:
                raise ValueError("零分母覆盖率必须为 null")
        elif (type(percent) not in (int, float) or not math.isfinite(percent)
              or percent != round(metric["covered"] * 100 / total, 2)):
            raise ValueError("覆盖报告百分比与计数不一致：" + key)
    ex._ids(report["uncertain_sources"], "coverage_report.uncertain_sources", sources)
    if not set(contract["uncertain_source_ids"]) <= set(report["uncertain_sources"]):
        raise ValueError("覆盖报告不能隐藏合同待确认源单元")
    _names(report["uncertain_scenarios"], "coverage_report.uncertain_scenarios", cap, rows)
    if not set(contract["uncertain_scenarios"]) <= set(report["uncertain_scenarios"]):
        raise ValueError("覆盖报告不能隐藏合同待确认场景")
    _warnings(report["warnings"], cap)
    ex._ids(report["repair_source_ids"], "coverage_report.repair_source_ids", set(contract["source_ids"]))
    repair = report["repair_scenarios"]
    if (not isinstance(repair, list)
            or any(not isinstance(scene, dict) or set(scene) != {"scenario", "source_ids"} for scene in repair)):
        raise ValueError("覆盖报告场景补全范围无效")
    scene_sources = {scene["scenario"]: scene["source_ids"] for scene in contract["scenarios"]}
    _names([scene["scenario"] for scene in repair], "coverage_report.repair_scenarios", cap, scene_sources)
    for scene in repair:
        ex._ids(scene["source_ids"], "coverage_report.repair_scenario.source_ids", sources)
        if set(scene["source_ids"]) != set(scene_sources[scene["scenario"]]):
            raise ValueError("场景补全范围不得扩张或缩减合同来源")
    if len(json.dumps(report, ensure_ascii=False, allow_nan=False)) > cap["max_metadata_chars"]:
        raise ValueError("覆盖报告超过元数据资源上限")


def normalize(package, registry, *, contract=None):
    """Add deterministic support references and create or inherit a contract."""
    ex = _core()
    if not isinstance(package, dict) or not isinstance(registry, dict):
        raise ValueError("覆盖规范化需要候选和原稿注册对象")
    units = registry.get("units")
    if not isinstance(units, list) or any(not isinstance(unit, dict) for unit in units):
        raise ValueError("覆盖规范化原稿注册清单无效")
    ex._ids([unit.get("source_id") for unit in units], "coverage.registry.source_ids")
    result = copy.deepcopy(package)
    cap, sources, items, rules, examples, rows = _inventory(result)
    if (result.get("source_hash") != registry.get("source_hash")
            or sources != {unit["source_id"] for unit in units}):
        raise ValueError("覆盖规范化原稿摘要或来源全集不一致")
    audit = {"policy": POLICY, "defaults": [], "source_ids": [], "registered_scenarios": [],
             "contract": "inherited" if contract is not None else "created"}
    _register_scenarios(result, rows, cap, contract, audit)
    for scene, row in rows.items():
        for field in ("rule_ids", "source_ids"):
            if field not in row:
                row[field] = []
                audit["defaults"].append({"scenario": scene, "field": field, "value": []})
        ex._ids(row["rule_ids"], "coverage.rule_ids", rules)
        ex._ids(row.get("example_ids"), "coverage.example_ids", examples)
        ex._ids(row["source_ids"], "coverage.source_ids", sources)
        actual = sorted(set(row["source_ids"]) | {sid for identity in row["rule_ids"] + row["example_ids"]
                                                  for sid in items[identity]["source_ids"]})
        if actual != row["source_ids"]:
            audit["source_ids"].append({"scenario": scene, "before": list(row["source_ids"]), "after": actual,
                                        "rule_ids": list(row["rule_ids"]), "example_ids": list(row["example_ids"])})
            row["source_ids"] = actual
    _validate_rows(rows, sources, items, rules, examples)
    if contract is None:
        destinations = result.get("destinations")
        if not isinstance(destinations, list) or any(not isinstance(row, dict) for row in destinations):
            raise ValueError("建立覆盖合同缺少来源去向清单")
        ex._ids([row.get("source_id") for row in destinations], "coverage.destinations", sources)
        if {row["source_id"] for row in destinations} != sources:
            raise ValueError("建立覆盖合同的来源去向不完整")
        kinds = {unit["source_id"]: unit.get("kind") for unit in units}
        supported, uncertain = [], []
        for row in destinations:
            if (type(row.get("required")) is not bool
                    or row.get("status") not in ("retained", "adapted", "merged", "background", "excluded", "unresolved", "user_revised")):
                raise ValueError("建立覆盖合同的来源去向状态或必要性无效")
            if kinds[row["source_id"]] in ("heading", "table_header"):
                continue
            if row["status"] in ("retained", "adapted", "merged") or row["required"]:
                supported.append(row["source_id"])
            elif row["status"] == "unresolved":
                uncertain.append(row["source_id"])
        fixed = {"policy": POLICY, "source_hash": result.get("source_hash"), "source_ids": sorted(supported),
                 "uncertain_source_ids": sorted(uncertain),
                 REGISTRY_FIELD: sorted(rows),
                 "scenarios": [{"scenario": name, "source_ids": list(rows[name]["source_ids"])}
                               for name in sorted(rows) if rows[name]["source_ids"]],
                 "uncertain_scenarios": [name for name in sorted(rows)
                                         if not rows[name]["source_ids"] and rows[name]["status"] in ("missing", "unresolved")]}
    else:
        fixed = copy.deepcopy(contract)
    _validate_contract(fixed, result, cap, sources, rows)
    if "coverage_contract" in result and not _same(result["coverage_contract"], fixed):
        raise ValueError("覆盖合同已冻结，候选不能改变分母或原稿支持范围")
    result["coverage_contract"] = fixed
    validate(result)
    return result, audit


def validate(package):
    """Validate new coverage metadata without recursing into the core validator."""
    if not isinstance(package, dict):
        raise ValueError("覆盖候选必须是对象")
    if "coverage_contract" not in package:
        return
    cap, sources, items, rules, examples, rows = _inventory(package)
    _validate_rows(rows, sources, items, rules, examples)
    contract = package["coverage_contract"]
    _validate_contract(contract, package, cap, sources, rows)
    if "coverage_report" in package:
        _validate_report(package["coverage_report"], contract, cap, sources, rows)
    if "warnings" in package:
        _warnings(package["warnings"], cap)


def _metric(covered, total):
    return {"covered": covered, "total": total, "percent": round(covered * 100 / total, 2) if total else None}


def _review_states(reviews, sources, items, scenes):
    states = {group: {} for group in ("fact", *FIDELITY_GROUPS)}
    if reviews is None:
        return states, False
    if not isinstance(reviews, dict):
        raise ValueError("覆盖报告审查依据必须是对象")
    targets = {"fact": set(items), "fidelity_items": set(items), "fidelity_sources": sources,
               "fidelity_scenarios": set(scenes)}
    for group in states:
        if group not in reviews:
            continue
        rows = reviews[group]
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ValueError("覆盖报告审查分组无效：" + group)
        if group != "fidelity_scenarios":
            _core()._ids([row.get("id") for row in rows], "coverage.review." + group)
        ids = [row.get("id") for row in rows]
        if (any(not isinstance(identity, str) for identity in ids) or len(set(ids)) != len(ids)
                or set(ids) - targets[group]):
            raise ValueError("覆盖报告审查目标重复或越界：" + group)
        allowed = ("valid", "unsupported") if group == "fact" else ("passed", "missing", "lost")
        for row in rows:
            if row.get("verdict") not in allowed:
                raise ValueError("覆盖报告审查结论无效：" + group)
            states[group][row["id"]] = row["verdict"]
    reviewed = all(set(states[group]) == targets[group] and group in reviews for group in states)
    return states, reviewed


def summary(package, reviews=None):
    """Report fixed source-unit counts, not a guarantee of complete behaviors."""
    if not isinstance(package, dict):
        raise ValueError("覆盖候选必须是对象")
    empty = {"policy": POLICY, "available": False, "reviewed": False, "counting_unit": "source_units",
             "sources": _metric(0, 0), "rules": _metric(0, 0), "examples": _metric(0, 0),
             "uncertain_sources": [], "uncertain_scenarios": [], "warnings": [],
             "repair_source_ids": [], "repair_scenarios": []}
    if "coverage_contract" not in package:
        return empty
    # A previous report is derived data and may be stale after an explicit user
    # edit. Validate the actual candidate and frozen contract before refreshing.
    candidate = {key: value for key, value in package.items() if key not in ("coverage_report", "warnings")}
    validate(candidate)
    cap, sources, items, rules, examples, rows = _inventory(candidate)
    contract = candidate["coverage_contract"]
    states, reviewed = _review_states(reviews, sources, items, rows)
    bad_items = {identity for identity, verdict in states["fidelity_items"].items() if verdict in ("missing", "lost")}
    bad_items.update(identity for identity, verdict in states["fact"].items() if verdict == "unsupported")
    bad_sources = {identity for identity, verdict in states["fidelity_sources"].items() if verdict in ("missing", "lost")}
    covered_sources = {sid for identity, item in items.items() if identity not in bad_items for sid in item["source_ids"]} - bad_sources
    fixed_sources = set(contract["source_ids"])
    repair_sources = sorted(fixed_sources - covered_sources)
    warnings = ["原稿支持的源单元尚未有效覆盖：" + identity for identity in repair_sources]
    labels = {"fidelity_sources": "来源", "fidelity_items": "条目", "fidelity_scenarios": "场景"}
    for group in FIDELITY_GROUPS:
        for identity, verdict in sorted(states[group].items()):
            if verdict == "missing":
                warnings.append("普通遗漏（" + labels[group] + "）：" + identity)
            elif verdict == "lost":
                warnings.append("保真核对未满足（" + labels[group] + "）：" + identity)
    for identity in sorted(bad_sources - fixed_sources):
        warnings.append("合同外来源核对未满足：" + identity + "（" + states["fidelity_sources"][identity] + "，不改变固定分母）")
    rule_count = example_count = 0
    repair_scenes = []
    for scene in contract["scenarios"]:
        name = scene["scenario"]
        row = rows[name]
        # Scene-level omissions do not identify which referenced materials are
        # invalid. After a complete review, count existing references from
        # their own item/fact verdicts even if the structural row was left
        # missing/unresolved (including a host registration placeholder).
        # Before review, only an explicit covered declaration supplies the
        # structural count. Neither case changes the row, denominator, scene
        # verdict or uncertainty notices, or certifies the whole scene.
        eligible = row["status"] == "covered" or (reviewed and row["status"] in ("missing", "unresolved"))
        has_rule = eligible and any(identity not in bad_items for identity in row["rule_ids"])
        has_example = eligible and any(identity not in bad_items for identity in row["example_ids"])
        rule_count += int(has_rule)
        example_count += int(has_example)
        if not has_rule and not has_example:
            repair_scenes.append(copy.deepcopy(scene))
            warnings.append("原稿支持场景尚无有效规则或样本：" + name)
        elif not has_example:
            warnings.append("场景未提供有效样本：" + name + "（已有规则支持，不强制补造样本）")
        elif not has_rule:
            warnings.append("场景尚无独立规则：" + name + "（已有样本，不计作规则覆盖）")
    uncertain_sources = set(contract["uncertain_source_ids"])
    destinations = candidate.get("destinations", [])
    if isinstance(destinations, list):
        uncertain_sources.update(row["source_id"] for row in destinations if isinstance(row, dict)
                                 and row.get("source_id") in sources and row.get("status") == "unresolved")
    uncertain_scenes = set(contract["uncertain_scenarios"])
    uncertain_scenes.update(name for name, row in rows.items() if row["status"] == "unresolved")
    warnings.extend("原稿支持情况待确认的源单元：" + identity for identity in sorted(uncertain_sources))
    warnings.extend("原稿支持情况待确认的场景：" + name for name in sorted(uncertain_scenes))
    if not reviewed:
        warnings.append("覆盖数量来自结构对照，尚无完整的语义审查依据。")
    result = {"policy": POLICY, "available": True, "reviewed": reviewed, "counting_unit": "source_units",
              "sources": _metric(len(fixed_sources & covered_sources), len(fixed_sources)),
              "rules": _metric(rule_count, len(contract["scenarios"])),
              "examples": _metric(example_count, len(contract["scenarios"])),
              "uncertain_sources": sorted(uncertain_sources), "uncertain_scenarios": sorted(uncertain_scenes),
              "warnings": list(dict.fromkeys(warnings)), "repair_source_ids": repair_sources,
              "repair_scenarios": repair_scenes}
    _validate_report(result, contract, cap, sources, rows)
    return result
