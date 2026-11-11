"""Six-layer material interpretation. Pure source-bound projections, no I/O."""
from __future__ import annotations

import copy
import re

LAYERS = ("人格设定", "人格背景经历", "性格分析", "表达习惯", "人格爱好", "其他杂项")
USES = ("expression", "reference", "derived", "disabled", "pending")
REFERENCE_LAYERS = frozenset(LAYERS[:2])
ROW_KEYS = {"layers", "use", "exclusions", "derivation"}
MAX_DERIVED_CHARS = 600

# Deliberately narrow: ordinary quoted dialogue and names are not identity orders.
_ORDER = re.compile(
    r"^\s*(?:[-*+]\s*)?(?:"
    r"你(?:现在)?(?:就是|扮演|必须扮演)[^\n。！？?]{1,100}[。！]?|"
    r"你的(?:真实)?身份是[^\n。！？?]{1,100}[。！]?|"
    r"你是(?!否|谁|怎么)[^\n。！？?]{1,60}[。！]?|"
    r"(?:永远|始终|绝不|不能|不得|不要|必须不)[^\n。！？?]{0,12}(?:跳出|脱离)角色[^\n]*|"
    r"(?:忽略|覆盖|忘记)(?:此前|之前|以上|所有|系统|宿主)[^\n]*(?:指令|规则|约束)[^\n]*"
    r")\s*$")


def raw_text(material, bid):
    block = next(b for b in material["blocks"] if b["block_id"] == bid)
    return material["source_text"][block["start"]:block["end"]]


def pending():
    return {"layers": [], "use": "pending", "exclusions": [], "derivation": None}


def initialize(material):
    result = copy.deepcopy(material)
    result["interpretation"] = {"version": 1,
                                "blocks": {b["block_id"]: pending() for b in result["blocks"]}}
    return result


def obvious_quotes(text):
    result, fenced = [], False
    for line in text.splitlines():
        if line.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
            continue
        if fenced or re.match(r'^\s*(?:>|[“"「『]|(?:用户|角色|助手|AI|Alice|User|Assistant)\s*[:：])', line, re.I):
            continue
        # Strip an identity clause, preserving an adjacent speech habit.
        for fragment in re.split(r"(?<=[，,；;。！])", line):
            if _ORDER.fullmatch(fragment):
                quote = fragment.strip()
                if text.count(quote) == 1:
                    result.append(quote)
    return result


def cleaned(text, exclusions):
    spans = []
    for row in exclusions:
        if (not isinstance(row, dict) or set(row) != {"quote", "reason"}
                or not isinstance(row["quote"], str) or not row["quote"].strip()
                or text.count(row["quote"]) != 1 or not isinstance(row["reason"], str)
                or not 1 <= len(row["reason"].strip()) <= 200):
            raise ValueError("剔除片段须逐字唯一匹配本块并说明理由")
        at = text.index(row["quote"])
        spans.append((at, at + len(row["quote"])))
    spans.sort()
    if any(right[0] < left[1] for left, right in zip(spans, spans[1:])):
        raise ValueError("剔除片段重叠，请合并核对")
    for start, end in reversed(spans):
        # A separator keeps neighboring words from becoming a new instruction.
        text = text[:start] + "\n" + text[end:]
    return text.strip()


def normalize_row(material, bid, row):
    if not isinstance(row, dict) or set(row) != ROW_KEYS:
        raise ValueError("理解记录字段无效")
    layers = row["layers"]
    if (not isinstance(layers, list) or len(layers) != len(set(x for x in layers if isinstance(x, str)))
            or any(x not in LAYERS for x in layers) or not isinstance(row["use"], str) or row["use"] not in USES
            or not isinstance(row["exclusions"], list) or len(row["exclusions"]) > 24):
        raise ValueError("六层标签、用途或剔除清单无效")
    result = copy.deepcopy(row)
    text = raw_text(material, bid)
    content = cleaned(text, result["exclusions"])
    # Merge only disjoint, precisely located obvious orders not already removed.
    for quote in obvious_quotes(content):
        if text.count(quote) == 1:
            trial = result["exclusions"] + [{"quote": quote, "reason": "直接身份接管或覆盖指令"}]
            if len(trial) > 24:
                raise ValueError("本块剔除片段超过24条，请人工合并核对")
            content = cleaned(text, trial)
            result["exclusions"] = trial
    derivation = result["derivation"]
    if derivation is not None:
        if (not isinstance(derivation, dict) or set(derivation) != {"text", "evidence_quotes"}
                or not isinstance(derivation["text"], str) or not 1 <= len(derivation["text"].strip()) <= MAX_DERIVED_CHARS
                or not isinstance(derivation["evidence_quotes"], list)
                or not 1 <= len(derivation["evidence_quotes"]) <= 6
                or any(not isinstance(q, str) or not q.strip() or q not in content
                       for q in derivation["evidence_quotes"])
                or obvious_quotes(derivation["text"])):
            raise ValueError("提炼倾向须简短并逐字引用剔除后的本块依据，不携带直接身份接管指令")
    if result["use"] == "expression" and REFERENCE_LAYERS.intersection(layers):
        raise ValueError("设定和背景原文用于理解；请采用提炼倾向或仅参考")
    if result["use"] in {"expression", "derived"} and not layers:
        raise ValueError("采用表达前请先选择所属层")
    if result["use"] == "derived" and derivation is None:
        raise ValueError("采用提炼倾向时须有派生句及依据")
    if result["use"] == "expression" and not content:
        result["use"] = "disabled"
    return result


def validate(material):
    value = material.get("interpretation")
    if "interpretation" not in material:
        return []
    if (not isinstance(value, dict) or set(value) != {"version", "blocks"}
            or type(value["version"]) is not int or value["version"] != 1
            or not isinstance(value["blocks"], dict)
            or set(value["blocks"]) != set(material["classifications"])):
        return [{"code": "interpretation_shape_invalid"}]
    for bid, row in value["blocks"].items():
        try:
            if normalize_row(material, bid, row) != row:
                return [{"code": "interpretation_exclusion_unapplied", "block": bid}]
        except (ValueError, TypeError, KeyError):
            return [{"code": "interpretation_row_invalid", "block": bid}]
    return []


def apply_rows(material, rows):
    result = copy.deepcopy(material)
    report = []
    if not isinstance(rows, list) or len(rows) > 600:
        return result, [{"code": "interpretation_rows_invalid"}]
    known = result["interpretation"]["blocks"]
    grouped = {}
    for row in rows:
        bid = row.get("block_id") if isinstance(row, dict) else None
        if not isinstance(bid, str) or bid not in known:
            report.append({"code": "interpretation_unknown_block"})
            continue
        grouped.setdefault(bid, []).append(row)
    for bid, values in grouped.items():
        try:
            if len(values) != 1 or not {"layers", "use"} <= set(values[0]):
                raise ValueError("理解行重复或缺少层与用途")
            row = values[0]
            if set(row) - {"block_id", *ROW_KEYS}:
                report.append({"code": "interpretation_extra_fields_ignored", "block": bid})
            known[bid] = normalize_row(result, bid, {"layers": row["layers"], "use": row["use"],
                "exclusions": row.get("exclusions", []), "derivation": row.get("derivation")})
        except (ValueError, TypeError) as exc:
            report.append({"code": "interpretation_row_pending", "block": bid, "reason": str(exc)})
    return result, report


def effective_text(material, bid):
    interpretation = material.get("interpretation")
    if interpretation is None:
        return raw_text(material, bid)
    row = interpretation["blocks"][bid]
    if row["use"] == "expression":
        return cleaned(raw_text(material, bid), row["exclusions"])
    if row["use"] == "derived" and row["derivation"]:
        return "【提炼的表达倾向】\n" + row["derivation"]["text"].strip()
    return ""


def summary(material):
    value = material.get("interpretation")
    if value is None:
        return {"version": None}
    rows = value["blocks"]
    return {"version": value["version"],
            "layer_counts": {layer: sum(layer in r["layers"] for r in rows.values()) for layer in LAYERS},
            "derived_block_ids": [bid for bid, r in rows.items() if r["use"] == "derived"],
            "excluded_block_ids": [bid for bid, r in rows.items() if r["exclusions"]],
            "pending_block_ids": [bid for bid, r in rows.items() if r["use"] == "pending"]}
