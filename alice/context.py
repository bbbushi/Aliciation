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
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import ContextConfig

# CJK 区间按 1 字符≈1 token 保守估算；其余按 4 字符≈1 token。
def estimate_tokens(text: str) -> int:
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff" or "\u3000" <= ch <= "\u303f")
    other = len(text) - cjk
    return cjk + other // 4 + (1 if other % 4 else 0)


def messages_tokens(messages: list[dict], tools: list[dict] | None = None) -> int:
    import json
    # Include tool arguments/IDs and message framing, not only visible content.
    total = estimate_tokens(json.dumps(messages, ensure_ascii=False))
    if tools:
        total += sum(estimate_tokens(json.dumps(t, ensure_ascii=False)) for t in tools)
    return total


def input_token_limit(config: ContextConfig) -> int | None:
    """Configured input allowance after reserving the reply budget."""
    if not config.total_budget_tokens:
        return None
    return config.total_budget_tokens - config.reply_reserve_tokens


def input_headroom(config: ContextConfig, messages: list[dict], tools: list[dict] | None = None) -> int | None:
    """Estimated space before adding new material."""
    limit = input_token_limit(config)
    return None if limit is None else limit - messages_tokens(messages, tools)


def over_input_budget(config: ContextConfig, messages: list[dict], tools: list[dict] | None = None) -> bool:
    headroom = input_headroom(config, messages, tools)
    return headroom is not None and headroom < 0


@dataclass
class Block:
    kind: str          # stable | background | index | history | tool_result
    name: str
    tokens: int
    chars: int | None = None      # 观测 SDK：块字符数与原文引用（同一记录的投影）
    content: str | None = None


@dataclass
class ContextManifest:
    """一次请求的装配清单（只读记录，供观察与预算决策）。"""
    blocks: list[Block] = field(default_factory=list)
    history: dict = field(default_factory=dict)

    def add(self, kind: str, name: str, content: str) -> None:
        self.blocks.append(Block(kind, name, estimate_tokens(content),
                                 len(content) if isinstance(content, str) else None,
                                 content if isinstance(content, str) else None))

    def total(self) -> int:
        return sum(b.tokens for b in self.blocks)

    def by_kind(self, kind: str) -> int:
        return sum(b.tokens for b in self.blocks if b.kind == kind)


def complete_round_suffix(rows: list[dict], max_messages: int,
                          token_budget: int | None = None, *, selection_out: dict | None = None) -> list[dict]:
    """从最近端取连续后缀，按完整往返（user 起）组织（设计 §8.1）。

    - 不产生"无对应问题的孤立答复"：切掉开头的 assistant 孤行；
    - token 预算耗尽时停在最后一个能完整放下的往返；
    - max_messages 仍作为条数上限（现有 recent_turns 语义保留）。
    """
    dropped = max(0, len(rows) - max_messages) if max_messages else 0
    suffix = list(rows[dropped:])
    excluded = [{"id": r.get("id"), "reason": "message_limit"} for r in rows[:dropped]]
    while suffix and suffix[0]["role"] != "user" and not suffix[0].get("proactive"):
        row = suffix.pop(0)
        excluded.append({"id": row.get("id"), "reason": "orphan_prefix"})
    def finish(kept, used=0):
        if selection_out is not None:
            selection_out.update(exclusions=excluded,
                minimum_retained_over_budget=token_budget is not None and used > token_budget,
                current_user_over_budget=bool(token_budget is not None and kept and
                    kept[-1]["role"] == "user" and messages_tokens([
                        {"role": "user", "content": kept[-1]["content"]}]) > token_budget))
        return kept
    if token_budget is None:
        return finish(suffix)
    kept: list[dict] = []
    used = 0
    i = len(suffix) - 1
    while i >= 0:
        # 从尾部整对回收：从 i 回扫过连续 assistant 行，落到本往返的 user 行
        start = i
        while start > 0 and suffix[start]["role"] == "assistant" and not suffix[start].get("proactive"):
            start -= 1
        # start 现在指向一对往返的 user 行（或 0）
        if suffix[start]["role"] != "user" and not suffix[start].get("proactive"):
            break
        pair = suffix[start:i + 1]
        cost = messages_tokens([{ "role": r["role"], "content": r["content"]} for r in pair])
        if kept and used + cost > token_budget:
            excluded.extend({"id": r.get("id"), "reason": "token_budget"} for r in suffix[:i + 1])
            break
        kept = pair + kept
        used += cost
        i = start - 1
    return finish(kept, used)  # 最新用户原话完整保留；无法容纳时由总预算守卫明确拒绝。
