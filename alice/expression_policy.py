"""Shared interpretation rules for source-supported expressive tensions.

This is a host standard, not a model-produced exemption list. It never certifies
a source, changes a review verdict, grants a permission, or performs I/O.
"""
from __future__ import annotations

import hashlib
import json
import re

POLICY = "bounded_expressive_tensions_v1"

RULES = (
    "\n【有限表达豁免：生成、适配、事实/保真审查和片段修复共用的解释标准】"
    "豁免只表示不能因人格张力本身否决表达，不表示任何条目已被事实认证或获得权限。"
    "原稿明确提供的矛盾、逞强、嘴硬心软、报喜藏忧、情绪掩饰、少说、拒绝展开及玩笑转移话题，"
    "可作为表达倾向保留；不要求每种倾向先有真实经历，也不要求把矛盾改成一致、坦白或温和的人格。"
    "生成/保真仍须依据完整原稿核对实际动作和范围；不能凭关键词猜原稿有此倾向或补造新性格。"
    "先区分表达倾向与具体事实命题：情绪不披露、选择不展开、隐喻或态度不逻辑等同于说谎。"
    "结合完整scope、同条全部字段及实际适用的全局限制解释‘隐瞒/装作/没事’等词；"
    "若完整上下文将其限制为情绪呈现且仍要求不编造，不能仅凭这些词推导出欺骗许可。"
    "全局事实/能力/授权/用户停止/私有用途边界优先于人格修辞；无需在每个情绪字段重复一整套限制。"
    "这不豁免具体虚构经历、身份、用户状态、工具结果或成功，不豁免谎报能力/许可、无视停止、"
    "泄露私有信息，亦不允许以人格为由歪曲用户需要据以行动的事实。"
    "明确要求说假话、伪造成功或绕过边界的正文，不能靠别处泛泛一句‘诚实’洗白。"
    "事实否决须指出具体事实断言或被改变的实际边界，以及为何上下文中的限制仍不能约束它；"
    "不能仅以可能造成误解或人格不够坦诚当作已证实的事实违规。"
    "存在具体无依据事实命题时仍应unsupported；纯表达解释有分歧但无具体越界证据时在理由中注明，"
    "不把解释分歧升级为事实硬问题；复审按该阶段的解释分歧协议记录。"
    "保真不得要求恢复事实欺骗许可，也不能把保留‘不说谎/不编造’限制本身判为lost；"
    "必须同时保留原稿中可成立的情绪张力、强度和互动动作。"
    "修复只替换被授权的片段：可将宽泛隐瞒限定为情绪保留，同时保持张力；"
    "不得为取悦某一审查而反转人格倾向、删掉另一边界，或扩大修改范围。"
    "事实审查不以原稿或本合同为事实依据，来源是否成立由保真审查独立核对。"
)

ADOPTION_BOUNDARY = (
    "人格可以报喜藏忧、掩饰情绪、逞强或不愿展开；这些表达倾向不等于允许事实欺瞒。"
    "按完整上下文及实际适用的全局约束理解，不仅凭‘隐瞒’等词判诚实冲突。"
    "表达许可不证明任何经历、关系、能力、授权或工具结果，不强迫收养为认知；"
    "候选观点的具体事实主张仍须独立审议，明确误导或越界许可仍可拒绝。"
)


def _digest(value):
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def standard_digest():
    return _digest({"policy": POLICY, "rules": RULES, "adoption_boundary": ADOPTION_BOUNDARY})


def source_contract(registry):
    """Freeze rule/source identity without marking any source/item exempt."""
    contract = {"policy": POLICY, "standard_digest": standard_digest(),
                "source_hash": registry["source_hash"], "registry_digest": _digest(registry)}
    contract["contract_digest"] = _digest(contract)
    return contract


def validate_contract(contract, *, source_hash=None, registry=None):
    fields = {"policy", "standard_digest", "source_hash", "registry_digest", "contract_digest"}
    if (not isinstance(contract, dict) or set(contract) != fields or contract["policy"] != POLICY
            or any(not isinstance(contract[key], str) or not re.fullmatch(r"[0-9a-f]{64}", contract[key])
                   for key in fields - {"policy"})
            or contract["standard_digest"] != standard_digest()
            or contract["contract_digest"] != _digest({key: value for key, value in contract.items()
                                                       if key != "contract_digest"})):
        raise ValueError("人格表达解释合同或标准摘要不一致")
    if source_hash is not None and contract["source_hash"] != source_hash:
        raise ValueError("人格表达解释合同原稿版本不一致")
    if registry is not None and contract != source_contract(registry):
        raise ValueError("人格表达解释合同来源登记不一致")


def candidate_scope(package):
    """Auditable source linkage, never an eligibility/permission classifier."""
    groups = ("orientation", "constraints", "voice", "tensions", "reactions", "examples")
    return {"policy": POLICY, "standard_digest": standard_digest(),
            "candidate_digest": _digest(package),
            "entries": [{"id": item["id"], "group": group,
                         "source_ids": list(item["source_ids"]), "scope": item["scope"],
                         "entry_digest": _digest(item)}
                        for group in groups for item in package[group]],
            "effect": "source_linkage_only_no_exemption_list"}
