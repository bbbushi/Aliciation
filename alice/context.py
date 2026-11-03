"""上下文装配清单与预算（模型参与上下文组装设计 v1 · 阶段 C0）。

只读工具：估算、完整往返选择、去重与总预算守卫——不调用模型、不写库。
块级清单把一次请求的内容分为五类（设计 §11 C0）：
  stable（宪法/人格/协议）、background（资料块）、index（候选索引，C1 起）、
  history（完整往返）、tool_result（工具结果，随消息保留）。
预算按保守长度估算（中文字符≈1 token、ASCII≈1/4），不宣称等于真实
tokenizer；真实 usage 仅用于事后校准（设计 §7.1）。
"""
from __future__ import annotations

from dataclasses import dataclass, field

# CJK 区间按 1 字符≈1 token 保守估算；其余按 4 字符≈1 token。
def estimate_tokens(text: str) -> int:
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff" or "\u3000" <= ch <= "\u303f")
    other = len(text) - cjk
    return cjk + other // 4 + (1 if other % 4 else 0)


def messages_tokens(messages: list[dict], tools: list[dict] | None = None) -> int:
    total = sum(estimate_tokens(m.get("content") or "") for m in messages)
    if tools:
        import json
        total += sum(estimate_tokens(json.dumps(t, ensure_ascii=False)) for t in tools)
    return total


@dataclass
class Block:
    kind: str          # stable | background | index | history | tool_result
    name: str
    tokens: int


@dataclass
class ContextManifest:
    """一次请求的装配清单（只读记录，供观察与预算决策）。"""
    blocks: list[Block] = field(default_factory=list)

    def add(self, kind: str, name: str, content: str) -> None:
        self.blocks.append(Block(kind, name, estimate_tokens(content)))

    def total(self) -> int:
        return sum(b.tokens for b in self.blocks)

    def by_kind(self, kind: str) -> int:
        return sum(b.tokens for b in self.blocks if b.kind == kind)


def complete_round_suffix(rows: list[dict], max_messages: int,
                          token_budget: int | None = None) -> list[dict]:
    """从最近端取连续后缀，按完整往返（user 起）组织（设计 §8.1）。

    - 不产生"无对应问题的孤立答复"：切掉开头的 assistant 孤行；
    - token 预算耗尽时停在最后一个能完整放下的往返；
    - max_messages 仍作为条数上限（现有 recent_turns 语义保留）。
    """
    suffix = rows[-max_messages:] if max_messages and len(rows) > max_messages else list(rows)
    while suffix and suffix[0]["role"] != "user":
        suffix.pop(0)  # 孤立答复/残首丢弃，保完整交流
    if token_budget is None:
        return suffix
    kept: list[dict] = []
    used = 0
    i = len(suffix) - 1
    while i >= 0:
        # 从尾部整对回收：user 行与其后所有 assistant 行构成一个往返
        start = i
        while start > 0 and suffix[start - 1]["role"] == "assistant":
            start -= 1
        # start 现在指向一对往返的 user 行（或 0）
        if suffix[start]["role"] != "user":
            break
        pair = suffix[start:i + 1]
        cost = sum(estimate_tokens(r["content"]) for r in pair)
        if kept and used + cost > token_budget:
            break
        kept = pair + kept
        used += cost
        i = start - 1
    return kept or suffix[-2:]  # 至少保最近一轮（预算极小也不切空）
