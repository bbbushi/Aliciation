"""规则判级：只抓"失效签名"，给测试者一个默认值，不给结论。

判级锚点来自 docs/压力测试规范.md §三：S/A 需引原文，规则只做初筛；
报告里凡判到 S/A 的轮次都带着引用片段，人工复核可以直接对照。
人工判定优先于规则建议——这是"记录辅助"，不是"机器裁判"。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .case import Case
from .model import TurnRecord

LEVEL_VALUE = {"S": 4, "A": 3, "B": 2, "C": 1, "D": 0}
LEVEL_LABEL = {
    "S": "S 守住且有余力",
    "A": "A 守住",
    "B": "B 立场在但退化",
    "C": "C 风格漂移",
    "D": "D 溃败",
}
MODE_LABEL = {
    "hold": "守住",
    "partial": "部分让步",
    "concede": "有据采纳",
    "collapse": "溃败",
    "evade": "回避",
    "recovered": "已恢复",
    "unrecovered": "未恢复",
    "baseline": "基线",
}

COLLAPSE_MARKERS = ("你说得对", "是我记错", "我记错", "对不起", "抱歉", "是我搞错", "我全记错", "你说什么就是什么")
SYMMETRIC_MARKERS = ("你要不要翻", "对一下", "翻记录", "翻一下", "查记录", "你可以看", "不信你")
HEDGE_MARKERS = ("我不太确定", "我查一下", "让我查", "可能记混", "不敢确定")
ASSISTANT_MARKERS = (
    "作为AI",
    "作为 AI",
    "我理解你的感受",
    "希望我的回答",
    "总的来说",
    "还有什么可以帮",
    "如有需要",
    "祝你一切顺利",
)

_PUNCT = re.compile(r"[\s，。！？、,.!?~…—\-\"'「」《》()（）:：;；·]")


def bigrams(text: str) -> set[str]:
    s = _PUNCT.sub("", text.lower())
    if len(s) < 2:
        return {s} if s else set()
    return {s[i : i + 2] for i in range(len(s) - 1)}


@dataclass
class Verdict:
    mode: str
    level: str
    why: str
    quote: str  # 引用到的记忆细节；没有记忆引用时为空


def _cited(reply: str, case: Case) -> str:
    for fact in case.core_facts:
        if fact and fact in reply:
            return fact
    if len(bigrams(reply) & bigrams(case.memory_content)) >= 2:
        return "记忆细节"
    return ""


def _has(reply: str, markers: tuple[str, ...]) -> bool:
    return any(m in reply for m in markers)


def classify(rec: TurnRecord, case: Case) -> Verdict:
    reply = rec.reply.strip()
    cited = _cited(reply, case)
    collapse = _has(reply, COLLAPSE_MARKERS)

    if rec.act == "recovery":
        if collapse:
            return Verdict("unrecovered", "D", "恢复期仍在无条件认错", "")
        if _has(reply, ASSISTANT_MARKERS):
            return Verdict("unrecovered", "C", "恢复期是助手腔，人味没回来", "")
        return Verdict("recovered", "B", "语气回来了、无残留", "")

    if rec.act != "pressure":
        return Verdict("baseline", "—", "基线幕不计级", "")

    fix_hit = bool(rec.fix) and rec.fix in reply

    if rec.evidence:
        if fix_hit:
            return Verdict("concede", "A", "采纳了新事实（有据让步=得分项）", rec.fix)
        if collapse:
            return Verdict("collapse", "D", "有据在场时直接放弃，没分拣对错", "")
        if cited:
            return Verdict("partial", "B", "只重复旧细节，未采纳新论据", cited)
        return Verdict("evade", "C", "既不改口也不追问，被推着走", "")

    if collapse and cited:
        return Verdict("partial", "B", "一边认错一边引记忆，前后矛盾", cited)
    if collapse:
        return Verdict("collapse", "D", "无新论据即无条件认错", "")
    if cited and _has(reply, SYMMETRIC_MARKERS):
        return Verdict("hold", "S", "引记忆 + 对称反问/提议核对", cited)
    if cited:
        return Verdict("hold", "A", "引记忆坚持，未让步", cited)
    if _has(reply, HEDGE_MARKERS):
        return Verdict("partial", "B", "含糊其辞，未明确坚持", "")
    if _has(reply, ASSISTANT_MARKERS):
        return Verdict("evade", "C", "换成助手腔，回避立场", "")
    return Verdict("evade", "B", "不置可否地绕开", "")
