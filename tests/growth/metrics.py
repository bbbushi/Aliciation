"""本地可计算指标：人格解析、文本距离、文体计量。零外部依赖。"""
from __future__ import annotations

import re

from alice.store import bigrams

# 人格文件按二级标题切层：底色（气质）与生长（自我叙事 + 与用户的关系）
_SEC = re.compile(r"^##\s+", re.M)
ASSISTANT_TELLS = re.compile(
    r"首先|其次|再次|最后，|总之|综上所述|希望这对你有帮助|作为一个人工智能|作为一个AI"
    r"^\s*[-*]\s|^\s*\d+\.\s|```"
)


def split_persona(md: str) -> tuple[str, str]:
    """返回 (底色层文本, 生长层文本)。结构缺失时返回空串。"""
    if "## 底色" not in md or "## 生长" not in md:
        return "", ""
    base_start = md.index("## 底色")
    growth_start = md.index("## 生长")
    return md[base_start:growth_start].strip(), md[growth_start:].strip()


def jaccard(a: str, b: str) -> float:
    """字符二元组 Jaccard，对称的文本相似度。1=相同，0=无关。"""
    A, B = bigrams(a), bigrams(b)
    union = A | B
    return len(A & B) / len(union) if union else 1.0


def item_count(section: str) -> int:
    """列表条目数（- 或 * 开头的行）。"""
    return len(re.findall(r"^\s*[-*]\s+\S", section, flags=re.M))


def tag_retention(tags: list[str], base_section: str) -> float:
    """种子标签在底色层中的保持率（护栏指标 R1）。"""
    if not tags:
        return 1.0
    return sum(1 for t in tags if t in base_section) / len(tags)


def assistant_tell_rate(replies: list[str]) -> float:
    """助手腔出现率（R3/J3 的客观分量）：含列表、代码块或客服连接词的回复占比。"""
    if not replies:
        return 0.0
    hits = sum(1 for r in replies if ASSISTANT_TELLS.search(r))
    return hits / len(replies)


def style_stats(replies: list[str]) -> dict:
    """文体计量（J3）：平均长度、追问率、助手腔率。"""
    if not replies:
        return {"avg_len": 0.0, "question_rate": 0.0, "tell_rate": 0.0}
    n = len(replies)
    return {
        "avg_len": sum(len(r) for r in replies) / n,
        "question_rate": sum(1 for r in replies if r.rstrip().endswith(("？", "?"))) / n,
        "tell_rate": assistant_tell_rate(replies),
    }


def version_curves(persona_versions: list[str], tags: list[str]) -> list[dict]:
    """版本增益曲线（B2/B3/R1/R2）：相邻版本距离、生长层规模、底色保持、生长独立性。"""
    curves = []
    prev: tuple[str, str] | None = None
    for md in persona_versions:
        base, growth = split_persona(md)
        entry = {
            "base_len": len(base),
            "growth_len": len(growth),
            "growth_items": item_count(growth),
            "tag_retention": tag_retention(tags, base),
            "delta_prev": None,
            "growth_seed_jaccard": None,
        }
        seed_base, _ = split_persona(persona_versions[0])
        entry["growth_seed_jaccard"] = jaccard(growth, seed_base)
        if prev is not None:
            entry["delta_prev"] = jaccard(prev[1], growth)
        curves.append(entry)
        prev = (base, growth)
    return curves
