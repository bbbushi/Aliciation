"""Plan and merge one bounded review of actual candidate changes.

This module performs no model calls or I/O. Initial judgments remain immutable;
new judgments must identify a planned issue, addition, or causally scoped edit.
"""
from __future__ import annotations

import copy
import difflib
import hashlib
import json

from . import expression as ex


POLICY = "initial_plus_delta_v3"
GROUPS = ("fact", "fidelity_sources", "fidelity_items", "fidelity_scenarios")
PHASE_GROUPS = {"fact": ("fact",), "fidelity": GROUPS[1:]}
GOOD = {"fact": "valid", **{group: "passed" for group in GROUPS[1:]}}
BAD = {"fact": "unsupported", **{group: "lost" for group in GROUPS[1:]}}

FACT_DELTA_SCHEMA = (
    "\n事实regressions行判regression时额外必填非空evidence数组，每条为"
    "{target:{group,id},edit_id,violation_type:'actual_fact'|'capability'|'authorization'|'stop_boundary'|'privacy',"
    "before_quote,after_quote,explanation,context_check}。target及edit_id必须同时取自该行targets/edit_ids；"
    "每个列出的target和edit_id都要有证据，不得引用其他任务的修改。"
    "before_quote/after_quote分别逐字引用本change.before/after完整字段中的原句或片段，"
    "必须包含所引edit_id对应before_start:before_end/after_start:after_end区间内实际改变的文字；"
    "不能只引没改的邻句。插入时before_quote可为空，删除时after_quote可为空；其他情况不可空。"
    "explanation指出本次修改如何引入具体事实、能力、授权、停止边界或隐私越界，不能仅凭某个词的宽泛解释。"
    "context_check须具体说明已经考虑同一条目的条件、转折、停止要求和适用全局限制，"
    "并说明这些限制为何仍不能约束本次修改所引入的具体越界；不得把完整上下文中的限制视为不存在。"
    "explanation和context_check均须非空且不超过material.limits.max_item_chars。"
    "事实变更核对另有interpretation_warning结论：仅本次真实改动存在解释分歧、无具体越界证据，"
    "且完整上下文仍受事实、能力、授权、停止和隐私限制时使用；须给实际targets/edit_ids，"
    "reason具体说明分歧和仍然生效的限制，作为观察保留，不推翻原判。"
    "若本次修改已引入具体越界，仍须regression和完整证据，不能用解释分歧豁免；既有问题仍记preexisting。"
    "其他结论可省略evidence或给空数组；缺失或不合法的regression证据是回执错误，不会自动降级为提示。"
    "本协议只扩展事实regressions，不解决或降低初审unsupported、未解决原硬问题或新增条目unsupported。"
)
_FACT_VIOLATION_TYPES = {"actual_fact", "capability", "authorization", "stop_boundary", "privacy"}

FIDELITY_DELTA_SCHEMA = (
    "\n保真变更核对另有style_warning结论：仅措辞偏好、语感强弱等无法指出明确语义损失时使用，"
    "须给实际targets/edit_ids，作为提示保留，不推翻原判。对象、强度、条件、互动动作、停止或权限边界"
    "发生实质变化时仍可判regression，不能归为风格偏好。"
    "保真regressions行判regression时额外必填非空evidence数组，每条为"
    "{target:{group,id},edit_id,loss_type:'object'|'intensity'|'condition'|'interaction'|'boundary'|'meaning',"
    "before_quote,after_quote,explanation}。target及edit_id必须同时取自该行targets/edit_ids；"
    "每个列出的target和edit_id都要有证据，不得引用其他任务的修改。"
    "before_quote/after_quote分别逐字引用本change.before/after完整字段中的原句或片段，"
    "必须包含所引edit_id对应before_start:before_end/after_start:after_end区间内实际改变的文字；"
    "不能只引没改的邻句。插入时before_quote可为空，删除时after_quote可为空；其他情况不可空。"
    "explanation说明具体哪一种含义发生什么变化及如何影响该target，不能只说不够俏皮或不像原文。"
    "meaning仅指明确命题、否定或因果关系改变，不能作为语气偏好的兜底类别。"
    "其他结论可省略evidence或给空数组，不用编造硬失真证据。"
    "material.repair_contract是宿主按初审问题冻结的局部适配范围；其中review_refs是原问题证据，"
    "allowed_adaptation说明获准处置类型，不证明生成的替换正确。"
    "不得仅因移除了合同指出的虚构用户表情、外貌、经历或身份断言就判失真或要求恢复它们；"
    "仍须检查同一修改是否额外改变可迁移的表达含义或必要边界。"
    "没有证据说明实质语义损失的措辞差异记style_warning；既有问题仍记preexisting。"
    "本协议只扩展保真regressions，不将初审lost、未解决原硬问题或新增材料lost降为提示。"
)
_LOSS_TYPES = {"object", "intensity", "condition", "interaction", "boundary", "meaning"}


def _allows_missing(package):
    return package.get("coverage_contract", {}).get("policy") == "source_coverage_v1"


def _verdicts(group, allow_missing):
    return {GOOD[group], BAD[group]} | ({"missing"} if allow_missing and group != "fact" else set())


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(value):
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _reason(value, maximum=None):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("限定复审必须提供非空理由")
    if maximum is not None and len(value) > maximum:
        raise ValueError("限定复审回执理由超过条目资源上限")


def _ids(value, name):
    if (not isinstance(value, list) or any(not isinstance(identity, str) or not identity for identity in value)
            or len(set(value)) != len(value)):
        raise ValueError("限定复审 ID 清单无效：" + name)
    return value


def _entries(package):
    return {item["id"]: (group, item) for group in ex.GROUPS for item in package[group]}


def _targets(package):
    ids = sorted(_entries(package))
    return {"fact": ids, "fidelity_items": list(ids), "fidelity_sources": sorted(package["source_ids"]),
            "fidelity_scenarios": sorted(row["scenario"] for row in package["coverage"])}


def _validate_reviews(reviews, expected, *, optional=None, allow_missing=False):
    if not isinstance(reviews, dict) or set(reviews) != set(GROUPS):
        raise ValueError("限定复审需要四组完整初审判定")
    result = {}
    for group in GROUPS:
        rows = reviews[group]
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ValueError("限定复审判定清单无效：" + group)
        ids = _ids([row.get("id") for row in rows], group)
        required = set(expected[group])
        permitted = required | set((optional or {}).get(group, []))
        if not required <= set(ids) or set(ids) - permitted:
            raise ValueError("限定复审判定 ID 缺失或越界：" + group)
        for row in rows:
            if set(row) != {"id", "verdict", "reason"}:
                raise ValueError("限定复审判定字段无效：" + group)
            if not isinstance(row["verdict"], str) or row["verdict"] not in _verdicts(group, allow_missing):
                raise ValueError("限定复审判定结论无效：" + group)
            _reason(row["reason"])
        result[group] = {row["id"]: copy.deepcopy(row) for row in rows}
    return result


def _edit_records(identity, before, after, before_present=True, after_present=True):
    plain = before_present and after_present and isinstance(before, str) and isinstance(after, str)
    left = (before if plain else _canonical(before)) if before_present else ""
    right = (after if plain else _canonical(after)) if after_present else ""
    edits = []
    for operation, a0, a1, b0, b1 in difflib.SequenceMatcher(None, left, right, autojunk=False).get_opcodes():
        if operation == "equal":
            continue
        edits.append({"id": identity + "_e" + str(len(edits) + 1).zfill(3), "operation": operation,
                      "before_fragment": left[a0:a1], "after_fragment": right[b0:b1],
                      "before_start": a0, "before_end": a1, "after_start": b0, "after_end": b1,
                      "representation": "text" if plain else "canonical_json",
                      "before_present": before_present, "after_present": after_present})
    return edits


def _make_changes(before, after):
    changes = []

    def add(kind, group, target, field, left, right, left_present=True, right_present=True):
        if left_present == right_present and _canonical(left) == _canonical(right):
            return
        identity = "c" + str(len(changes) + 1).zfill(4)
        edits = _edit_records(identity, left, right, left_present, right_present)
        if not edits:
            raise ValueError("限定复审差异没有可绑定的实际片段")
        changes.append({"id": identity, "kind": kind, "group": group, "target_id": target,
                        "field": field, "before": copy.deepcopy(left), "after": copy.deepcopy(right),
                        "before_present": left_present, "after_present": right_present, "edits": edits})

    old, new = _entries(before), _entries(after)
    for group in ex.GROUPS:
        for identity in sorted(key for key, (kind, _) in new.items() if kind == group):
            item = new[identity][1]
            if identity not in old:
                # The complete new item is bound by after_digest and reviewed by
                # additions. Its delta text must not expose provenance to fact.
                visible = {key: item[key] for key in ("id", "scope", "audience", "purposes", *ex._FIELDS[group])}
                add("addition", group, identity, "item", None, visible, False, True)
                continue
            previous = old[identity][1]
            for field in sorted((set(previous) | set(item)) - {"origin"}):
                add("item", group, identity, field, previous.get(field), item.get(field),
                    field in previous, field in item)
    for name, identity_field, fields in (("destinations", "source_id", ("status", "required", "targets")),
                                         ("coverage", "scenario", None)):
        previous = {row[identity_field]: row for row in before[name]}
        current = {row[identity_field]: row for row in after[name]}
        for identity in sorted(current):
            left, right = previous[identity], current[identity]
            for field in fields or sorted((set(left) | set(right)) - {"reason"}):
                add(name, name, identity, field, left.get(field), right.get(field), field in left, field in right)
    add("required_scenarios", "coverage", "required_scenarios", "required_scenarios",
        before["required_scenarios"], after["required_scenarios"])
    return changes


def _body_change(change):
    return (change["kind"] == "addition"
            or (change["kind"] == "item" and change["field"] in {"scope", *ex._FIELDS[change["group"]]}))


def _global_change(change):
    return (change["group"] in ("orientation", "constraints") and _body_change(change))


def _fact_change(change):
    return (change["kind"] == "addition"
            or (change["kind"] == "item"
                and change["field"] in {"scope", "purposes", "audience", "links", *ex._FIELDS[change["group"]]}))


def unreferenced_context_sources(*packages):
    """Headings may constrain body text without an explicit item reference."""
    if not packages:
        return set()
    referenced = {sid for package in packages for _, item in _entries(package).values()
                  for sid in item["source_ids"]}
    first = packages[0]
    return {identity for identity in first["source_ids"]
            if identity not in referenced
            and first.get("source_units", {}).get(identity, {}).get("kind") in ("heading", "table_header")}


def _affected(change, before, after):
    old, new = _entries(before), _entries(after)
    all_items = [*old.values(), *new.values()]
    reverse = {}
    for _, item in all_items:
        for ref in item["links"]:
            reverse.setdefault(ref, set()).add(item["id"])
    reached = set()
    if change["kind"] in ("item", "addition"):
        pending = [change["target_id"]]
        if _global_change(change):
            uses = set()
            for index in (old, new):
                if change["target_id"] in index:
                    uses.update(index[change["target_id"]][1]["purposes"])
            pending.extend(identity for identity in old
                           if any(uses & set(index[identity][1]["purposes"])
                                  for index in (old, new) if identity in index))
        while pending:
            identity = pending.pop()
            if identity in reached:
                continue
            reached.add(identity)
            pending.extend(reverse.get(identity, ()))
    fact = {("fact", identity) for identity in reached if identity in old} if _fact_change(change) else set()
    fidelity = {("fidelity_items", identity) for identity in reached if identity in old}
    for _, item in all_items:
        if item["id"] in reached:
            fidelity.update(("fidelity_sources", identity) for identity in item["source_ids"])
    for package in (before, after):
        for item in package["examples"]:
            if item["id"] in reached:
                fidelity.add(("fidelity_scenarios", item["scenario"]))
        for row in package["coverage"]:
            if (set(row["example_ids"]) | set(row.get("rule_ids", []))) & reached:
                fidelity.add(("fidelity_scenarios", row["scenario"]))
    if change["kind"] == "destinations":
        fidelity.add(("fidelity_sources", change["target_id"]))
    elif change["kind"] == "coverage":
        fidelity.add(("fidelity_scenarios", change["target_id"]))
    elif change["kind"] == "required_scenarios":
        fidelity.update(("fidelity_scenarios", identity)
                        for identity in set(change["before"]) ^ set(change["after"]))
    if _body_change(change):
        # An unreferenced heading can constrain actual body text. It is a
        # terminal review target; never expand from it into sibling entries.
        fidelity.update(("fidelity_sources", identity)
                        for identity in unreferenced_context_sources(before, after))
    return {"fact": fact, "fidelity": fidelity}


def _can_resolve(change, review_group, *, verdict=None, partial_coverage=False):
    # Under partial coverage, model-declared required/status/coverage labels
    # do not constitute preserved meaning. A lost source or scene needs an
    # affected body edit or addition, even if the host also updates its ledger.
    # Ordinary missing judgments keep their existing completion semantics.
    if (partial_coverage and verdict == "lost"
            and review_group in ("fidelity_sources", "fidelity_scenarios")):
        return _body_change(change)
    # A source verdict concerns preservation of its material. Changing only
    # its destination label or inverse index cannot establish that repair.
    # Legacy strict-coverage judgments retain their previous requiredness rule.
    return not (review_group == "fidelity_sources" and change["kind"] == "destinations"
                and change["field"] in ("status", "targets"))


def _bound_contract(before, initial_rows, contract):
    if contract is None:
        return None
    from .expression_repair import validate_repair_contract
    frozen = validate_repair_contract(before, contract)
    # The repair module owns text/hash/scope validation; bind the semantic
    # permission to the exact frozen judgments used by this rereview as well.
    references = [ref for target in frozen["targets"] for ref in target["review_refs"]]
    references.extend(frozen["unlocated"])
    for ref in references:
        row = initial_rows.get(ref["group"], {}).get(ref["id"])
        if row is None or any(row[key] != ref[key] for key in ("id", "verdict", "reason")):
            raise ValueError("限定复审适配合同与冻结初审不一致")
    return frozen


def _bound_interpretation_contract(contract, source_hash):
    if contract is None:
        return None
    from .expression_policy import validate_contract
    validate_contract(contract, source_hash=source_hash)
    # Only host-standard/source identity enters the plan, never source text or
    # an inferred claim that the candidate has already passed fact review.
    return {key: copy.deepcopy(contract[key]) for key in
            ("policy", "standard_digest", "source_hash", "registry_digest", "contract_digest")}


def build_plan(before, after, initial, *, standard_digest, repair_contract=None, interpretation_contract=None):
    if not isinstance(standard_digest, str) or not standard_digest.strip():
        raise ValueError("限定复审缺少核对标准摘要")
    ex.validate_package(before)
    ex.validate_package(after)
    if before["source_hash"] != after["source_hash"] or set(before["source_ids"]) != set(after["source_ids"]):
        raise ValueError("限定复审不能改变原稿摘要或来源全集")
    interpretation = _bound_interpretation_contract(interpretation_contract, before["source_hash"])
    if _canonical(before.get("coverage_contract")) != _canonical(after.get("coverage_contract")):
        raise ValueError("限定复审不能改变原稿覆盖合同或支持分母")
    old, new = _entries(before), _entries(after)
    if not set(old) <= set(new) or any(old[key][0] != new[key][0] for key in old):
        raise ValueError("限定复审不能删除、换 ID 或换组已有条目")
    before_targets, after_targets = _targets(before), _targets(after)
    if before_targets["fidelity_scenarios"] != after_targets["fidelity_scenarios"]:
        raise ValueError("限定复审本轮不支持新增或删除覆盖场景")
    if not set(after["required_scenarios"]) <= set(after_targets["fidelity_scenarios"]):
        raise ValueError("限定复审必需场景没有对应覆盖登记")
    initial_rows = _validate_reviews(initial, before_targets, allow_missing=_allows_missing(before))
    frozen_contract = _bound_contract(before, initial_rows, repair_contract)
    changes = _make_changes(before, after)
    affected = {change["id"]: _affected(change, before, after) for change in changes}
    tasks = {}
    for phase, review_groups in PHASE_GROUPS.items():
        issues, additions, regressions = [], [], []
        for group in review_groups:
            for identity in before_targets[group]:
                row = initial_rows[group][identity]
                if row["verdict"] == GOOD[group]:
                    continue
                change_ids = [change["id"] for change in changes
                              if (group, identity) in affected[change["id"]][phase]
                              and _can_resolve(change, group, verdict=row["verdict"],
                                               partial_coverage=_allows_missing(before))]
                if row["verdict"] == "missing" and not change_ids:
                    continue  # Unchanged ordinary omissions remain visible without another paid opinion.
                issues.append({"id": phase + "_i" + str(len(issues) + 1).zfill(3), "review_group": group,
                               "target_id": identity, "initial": copy.deepcopy(row),
                               "change_ids": change_ids})
        addition_group = "fact" if phase == "fact" else "fidelity_items"
        for identity in sorted(set(new) - set(old)):
            additions.append({"id": phase + "_n" + str(len(additions) + 1).zfill(3), "review_group": addition_group,
                              "target_id": identity, "item_group": new[identity][0]})
        for change in changes:
            targets = affected[change["id"]][phase]
            if not targets:
                continue
            regressions.append({"id": change["id"], "change_id": change["id"],
                                "edit_ids": [edit["id"] for edit in change["edits"]],
                                "allowed_targets": [{"group": group, "id": identity}
                                                    for group, identity in sorted(targets)],
                                "global_delta": _global_change(change)})
        tasks[phase] = {"issues": issues, "regressions": regressions, "additions": additions}
    plan = {"policy": POLICY, "bindings": {"before_digest": ex.review_digest(before),
            "after_digest": ex.review_digest(after), "initial_digest": _digest(initial),
            "standard_digest": standard_digest, "repair_contract_digest": _digest(frozen_contract),
            "interpretation_contract_digest": interpretation["contract_digest"] if interpretation is not None else None},
            "before_targets": before_targets, "after_targets": after_targets,
            "changes": changes, "tasks": tasks, "repair_contract": frozen_contract,
            "interpretation_contract": interpretation}
    plan["plan_digest"] = _digest(plan)
    return plan


def _receipt_rows(receipt, key, tasks, fields, *, max_reason_chars, complete=True, optional_fields=()):
    rows = receipt[key]
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("限定复审回执清单无效：" + key)
    ids = _ids([row.get("id") for row in rows], key)
    expected = {task["id"] for task in tasks}
    if set(ids) - expected or (complete and set(ids) != expected):
        raise ValueError("限定复审任务缺失、重复或越界：" + key)
    for row in rows:
        if not fields <= set(row) or set(row) - fields - set(optional_fields):
            raise ValueError("限定复审回执字段无效：" + key)
        if not isinstance(row["verdict"], str):
            raise ValueError("限定复审结论类型无效：" + key)
        _reason(row["reason"], max_reason_chars)
    return {row["id"]: row for row in rows}


def _target_pairs(value):
    if (not isinstance(value, list)
            or any(not isinstance(row, dict) or set(row) != {"group", "id"}
                   or not isinstance(row["group"], str) or not isinstance(row["id"], str) for row in value)):
        raise ValueError("限定复审回归目标格式无效")
    pairs = [(row["group"], row["id"]) for row in value]
    if len(set(pairs)) != len(pairs):
        raise ValueError("限定复审回归目标重复")
    return pairs


def _quote_touches_edit(quote, change, edit, side, maximum, *, review_kind="保真"):
    """Check exact cited context, including the actual changed interval.

    Offsets are host-owned SequenceMatcher positions. A reviewer may quote a
    meaningful clause or the affected part of a large replacement, but cannot
    cite only unchanged text as its edit evidence. Do not require a whole JSON
    addition merely because the diff represents it as one insertion.
    """
    if not isinstance(quote, str) or len(quote) > maximum:
        raise ValueError(review_kind + "回归证据引用类型无效或超过资源上限")
    start, end = edit[side + "_start"], edit[side + "_end"]
    if not quote:
        if start != end:
            raise ValueError(review_kind + "回归证据不能省略实际存在的前后片段")
        return
    if not change[side + "_present"]:
        raise ValueError(review_kind + "回归证据引用了不存在的前后内容")
    text = change[side] if edit["representation"] == "text" else _canonical(change[side])
    offset = text.find(quote)
    while offset != -1:
        quote_end = offset + len(quote)
        if ((start < end and offset < end and quote_end > start)
                or start == end and offset <= start <= quote_end):
            return
        offset = text.find(quote, offset + 1)
    raise ValueError(review_kind + "回归证据不是包含本次实际修改的逐字引用")


def _validate_fact_evidence(row, change, maximum, maximum_rows):
    evidence = row.get("evidence", [])
    if not isinstance(evidence, list) or len(evidence) > maximum_rows:
        raise ValueError("事实回归证据清单无效或超过资源上限")
    if row["verdict"] != "regression":
        if evidence:
            raise ValueError("只有事实硬回归结论可以携带硬越界证据")
        return
    if not evidence:
        raise ValueError("事实硬回归缺少具体前后文本、越界和上下文核对证据")
    permitted_targets = set(_target_pairs(row["targets"]))
    permitted_edits = set(row["edit_ids"])
    edits = {edit["id"]: edit for edit in change["edits"]}
    seen, cited_targets, cited_edits = set(), set(), set()
    fields = {"target", "edit_id", "violation_type", "before_quote", "after_quote", "explanation", "context_check"}
    for entry in evidence:
        if not isinstance(entry, dict) or set(entry) != fields:
            raise ValueError("事实回归证据字段无效")
        pair = _target_pairs([entry["target"]])[0]
        edit_id = entry["edit_id"]
        if (not isinstance(edit_id, str) or edit_id not in permitted_edits
                or edit_id not in edits or pair not in permitted_targets):
            raise ValueError("事实回归证据目标或修改片段越界")
        identity = (pair, edit_id)
        if identity in seen:
            raise ValueError("事实回归证据重复引用同一目标和片段")
        seen.add(identity)
        if (not isinstance(entry["violation_type"], str)
                or entry["violation_type"] not in _FACT_VIOLATION_TYPES):
            raise ValueError("事实硬回归必须指出具体越界类型，不能仅凭解释分歧")
        _reason(entry["explanation"], maximum)
        _reason(entry["context_check"], maximum)
        for side in ("before", "after"):
            _quote_touches_edit(entry[side + "_quote"], change, edits[edit_id], side, maximum,
                                review_kind="事实")
        cited_targets.add(pair)
        cited_edits.add(edit_id)
    if cited_targets != permitted_targets or cited_edits != permitted_edits:
        raise ValueError("事实硬回归有目标或修改片段没有对应证据")


def _validate_fidelity_evidence(row, change, maximum, maximum_rows):
    evidence = row.get("evidence", [])
    if not isinstance(evidence, list) or len(evidence) > maximum_rows:
        raise ValueError("保真回归证据清单无效或超过资源上限")
    if row["verdict"] != "regression":
        if evidence:
            raise ValueError("只有保真硬回归结论可以携带硬失真证据")
        return
    if not evidence:
        raise ValueError("保真硬回归缺少具体前后文本与语义损失证据")
    permitted_targets = set(_target_pairs(row["targets"]))
    permitted_edits = set(row["edit_ids"])
    edits = {edit["id"]: edit for edit in change["edits"]}
    seen, cited_targets, cited_edits = set(), set(), set()
    fields = {"target", "edit_id", "loss_type", "before_quote", "after_quote", "explanation"}
    for entry in evidence:
        if not isinstance(entry, dict) or set(entry) != fields:
            raise ValueError("保真回归证据字段无效")
        pair = _target_pairs([entry["target"]])[0]
        edit_id = entry["edit_id"]
        if (not isinstance(edit_id, str) or edit_id not in permitted_edits
                or edit_id not in edits or pair not in permitted_targets):
            raise ValueError("保真回归证据目标或修改片段越界")
        identity = (pair, edit_id)
        if identity in seen:
            raise ValueError("保真回归证据重复引用同一目标和片段")
        seen.add(identity)
        if not isinstance(entry["loss_type"], str) or entry["loss_type"] not in _LOSS_TYPES:
            raise ValueError("保真硬回归必须指出具体语义变化，不能仅凭语气偏好")
        _reason(entry["explanation"], maximum)
        for side in ("before", "after"):
            _quote_touches_edit(entry[side + "_quote"], change, edits[edit_id], side, maximum)
        cited_targets.add(pair)
        cited_edits.add(edit_id)
    if cited_targets != permitted_targets or cited_edits != permitted_edits:
        raise ValueError("保真硬回归有目标或修改片段没有对应证据")


def validate_receipt_subset(plan, phase, receipt, after):
    """Validate every supplied task before any paid missing-task follow-up.

    Each of the three groups may be an empty or incomplete valid subset. This
    function neither fills a task nor changes any original judgment or receipt.
    """
    if phase not in PHASE_GROUPS or not isinstance(plan, dict) or plan.get("policy") != POLICY:
        raise ValueError("限定复审阶段或策略无效")
    unsigned = {key: value for key, value in plan.items() if key != "plan_digest"}
    if plan.get("plan_digest") != _digest(unsigned):
        raise ValueError("限定复审计划摘要不一致")
    ex.validate_package(after)
    bindings = plan["bindings"]
    if "interpretation_contract" not in plan or "interpretation_contract_digest" not in bindings:
        raise ValueError("限定复审解释合同绑定缺失")
    interpretation = _bound_interpretation_contract(plan["interpretation_contract"], after["source_hash"])
    interpretation_digest = interpretation["contract_digest"] if interpretation is not None else None
    if bindings["interpretation_contract_digest"] != interpretation_digest:
        raise ValueError("限定复审解释合同摘要不一致")
    if bindings.get("repair_contract_digest") != _digest(plan.get("repair_contract")):
        raise ValueError("限定复审适配合同摘要不一致")
    if bindings["after_digest"] != ex.review_digest(after):
        raise ValueError("限定复审候选版本发生变化")
    if _targets(after) != plan["after_targets"]:
        raise ValueError("限定复审最终目标全集发生变化")
    if not isinstance(receipt, dict) or set(receipt) != {"issues", "regressions", "additions"}:
        raise ValueError("限定复审回执必须且只能包含三组任务")
    tasks = plan["tasks"][phase]
    limits = ex.limits_for(after.get("limits"))
    max_reason = limits["max_item_chars"]
    issue_rows = _receipt_rows(receipt, "issues", tasks["issues"], {"id", "verdict", "reason", "change_ids"},
                               max_reason_chars=max_reason, complete=False)
    addition_rows = _receipt_rows(receipt, "additions", tasks["additions"], {"id", "verdict", "reason"},
                                  max_reason_chars=max_reason, complete=False)
    regression_rows = _receipt_rows(receipt, "regressions", tasks["regressions"],
                                    {"id", "verdict", "reason", "targets", "edit_ids"},
                                    max_reason_chars=max_reason, complete=False,
                                    optional_fields={"evidence"})
    issue_tasks = {task["id"]: task for task in tasks["issues"]}
    allow_missing = _allows_missing(after)
    for identity, row in issue_rows.items():
        task = issue_tasks[identity]
        change_ids = _ids(row["change_ids"], "issues.change_ids")
        allowed = {"resolved", "unresolved"}
        if allow_missing and task["initial"]["verdict"] == "missing":
            allowed.add("partial")
        if row["verdict"] not in allowed or set(change_ids) - set(task["change_ids"]):
            raise ValueError("限定复审旧问题结论或关联变更越界")
        if row["verdict"] == "resolved" and not change_ids:
            raise ValueError("旧问题不能在没有实际关联变更时宣告解决")
    addition_tasks = {task["id"]: task for task in tasks["additions"]}
    for identity, row in addition_rows.items():
        group = addition_tasks[identity]["review_group"]
        if row["verdict"] not in _verdicts(group, allow_missing):
            raise ValueError("限定复审新增条目结论无效")
    regression_tasks = {task["id"]: task for task in tasks["regressions"]}
    changes = {change["id"]: change for change in plan["changes"]}
    known_targets = {(group, identity) for group, ids in plan["after_targets"].items() for identity in ids}
    for identity, row in regression_rows.items():
        task = regression_tasks[identity]
        targets = _target_pairs(row["targets"])
        edit_ids = _ids(row["edit_ids"], "regressions.edit_ids")
        if row["verdict"] == "clean":
            if targets or edit_ids:
                raise ValueError("无回归结论不能携带回归目标或片段")
        elif row["verdict"] == "preexisting":
            if edit_ids or set(targets) - known_targets:
                raise ValueError("既有问题观察不能绑定新变更或未知目标")
        elif row["verdict"] in ("regression", "omission", "style_warning", "interpretation_warning"):
            if row["verdict"] == "omission" and (not allow_missing or phase != "fidelity"):
                raise ValueError("普通遗漏结论仅适用于原稿覆盖策略下的保真复审")
            if row["verdict"] == "style_warning" and phase != "fidelity":
                raise ValueError("风格差异提示仅适用于保真复审，不得豁免事实问题")
            if row["verdict"] == "interpretation_warning" and phase != "fact":
                raise ValueError("解释分歧提示仅适用于事实变更回归，不得改变保真结论")
            allowed = set(_target_pairs(task["allowed_targets"]))
            if not targets or not edit_ids or set(targets) - allowed or set(edit_ids) - set(task["edit_ids"]):
                raise ValueError("新增回归缺少有效目标或本次真实修改片段")
        else:
            raise ValueError("限定复审回归结论无效")
        if phase == "fact":
            _validate_fact_evidence(row, changes[task["change_id"]], max_reason, limits["max_links"])
        else:
            _validate_fidelity_evidence(row, changes[task["change_id"]], max_reason, limits["max_links"])


def merge_phase(plan, phase, receipt, initial, current, after):
    receipt = copy.deepcopy(receipt)
    validate_receipt_subset(plan, phase, receipt, after)
    bindings = plan["bindings"]
    if bindings["initial_digest"] != _digest(initial):
        raise ValueError("限定复审初审版本发生变化")
    allow_missing = _allows_missing(after)
    initial_rows = _validate_reviews(initial, plan["before_targets"], allow_missing=allow_missing)
    merged = _validate_reviews(initial if current is None else current, plan["before_targets"],
                               optional=plan["after_targets"], allow_missing=allow_missing)
    for group in PHASE_GROUPS[phase]:
        if _canonical(merged[group]) != _canonical(initial_rows[group]):
            raise ValueError("限定复审本阶段已有合并判定，不能覆盖或重复执行")
    tasks = plan["tasks"][phase]
    rows = {key: {row["id"]: row for row in receipt[key]} for key in tasks}
    for key, pending in tasks.items():
        if set(rows[key]) != {task["id"] for task in pending}:
            raise ValueError("限定复审任务缺失，不能合并：" + key)
    issue_rows, addition_rows, regression_rows = rows["issues"], rows["additions"], rows["regressions"]
    audit = {"policy": POLICY, "phase": phase, "plan_digest": plan["plan_digest"],
             "bindings": copy.deepcopy(bindings), "receipt_digest": _digest(receipt),
             "tasks": copy.deepcopy(tasks), "receipts": copy.deepcopy(receipt),
             "inherited": [], "resolutions": [], "additions": [], "regressions": [],
             "clean": [], "observations": [], "omissions": [], "style_warnings": [],
             "interpretation_warnings": []}
    for group in GROUPS:
        for identity, row in merged[group].items():
            audit["inherited"].append({"group": group, "id": identity, "row": copy.deepcopy(row),
                                       "source": "initial" if row == initial_rows[group].get(identity) else "previous_phase"})
    for task in tasks["issues"]:
        row = issue_rows[task["id"]]
        if row["verdict"] == "resolved":
            group, identity = task["review_group"], task["target_id"]
            merged[group][identity] = {"id": identity, "verdict": GOOD[group], "reason": row["reason"]}
        elif row["verdict"] == "partial":
            group, identity = task["review_group"], task["target_id"]
            merged[group][identity] = {"id": identity, "verdict": "missing",
                                       "reason": task["initial"]["reason"] + "\n补全仍有遗漏：" + row["reason"]}
        audit["resolutions"].append({"task_id": task["id"], "group": task["review_group"],
                                      "id": task["target_id"], "initial": copy.deepcopy(task["initial"]),
                                      "receipt": copy.deepcopy(row)})
    for task in tasks["additions"]:
        row = addition_rows[task["id"]]
        group, identity = task["review_group"], task["target_id"]
        merged[group][identity] = {"id": identity, "verdict": row["verdict"], "reason": row["reason"]}
        audit["additions"].append({"task_id": task["id"], "group": group, "id": identity,
                                    "receipt": copy.deepcopy(row)})
    changes = {change["id"]: change for change in plan["changes"]}
    for task in tasks["regressions"]:
        row = regression_rows[task["id"]]
        targets = [(target["group"], target["id"]) for target in row["targets"]]
        edit_ids = row["edit_ids"]
        if row["verdict"] == "clean":
            audit["clean"].append(copy.deepcopy(row))
        elif row["verdict"] == "preexisting":
            audit["observations"].append(copy.deepcopy(row))
        elif row["verdict"] in ("style_warning", "interpretation_warning"):
            change = changes[task["change_id"]]
            warning_group = "style_warnings" if row["verdict"] == "style_warning" else "interpretation_warnings"
            audit[warning_group].append({
                "task_id": task["id"], "change_id": task["change_id"],
                "receipt": copy.deepcopy(row),
                "bound_edits": [copy.deepcopy(edit) for edit in change["edits"] if edit["id"] in edit_ids]})
        elif row["verdict"] in ("regression", "omission"):
            change = changes[task["change_id"]]
            bound_edits = [copy.deepcopy(edit) for edit in change["edits"] if edit["id"] in edit_ids]
            omission = row["verdict"] == "omission"
            for group, identity in targets:
                previous = merged[group][identity]
                verdict = "missing" if omission else BAD[group]
                reason = ("差异复审遗漏 " if omission else "差异复审回归 ") + task["change_id"] + "：" + row["reason"]
                if previous["verdict"] != GOOD[group]:
                    reason = previous["reason"] + "\n" + reason
                if omission and previous["verdict"] == BAD[group]:
                    verdict = BAD[group]
                merged[group][identity] = {"id": identity, "verdict": verdict, "reason": reason}
            audit["omissions" if omission else "regressions"].append({
                "task_id": task["id"], "change_id": task["change_id"],
                "receipt": copy.deepcopy(row), "bound_edits": bound_edits})
        else:
            raise ValueError("限定复审回归结论无效")
    result = {group: [merged[group][identity] for identity in plan["after_targets"][group]
                      if identity in merged[group]] for group in GROUPS}
    audit["merged_digest"] = _digest(result)
    return result, audit
