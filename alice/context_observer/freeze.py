"""快照冻结与绑定校验（SDK 补充设计 v1 §两种接入/事件合同）。

冻结发生在业务发送之前：记录的是应用交给模型客户端的参数投影，
不声称是 HTTP 原始字节或供应商内部上下文。消息顺序与工具定义分开保存；
块绑定带角色、序号与字符区间，校验失败记缺口，不伪造位置。
"""
from __future__ import annotations

from .contract import (BODY_NONE, BODY_TOO_LARGE, BODY_UNSERIALIZABLE, PARAM_WHITELIST,
                       canonical_json, content_hash)


def freeze_text(content, *, capture: str, head_chars: int, body_max_chars: int) -> dict:
    """按政策冻结一段正文：默认只留 hash 与长度；不截断后冒充完整。"""
    out = {"chars": len(content) if isinstance(content, str) else None,
           "content_hash": content_hash(content) if isinstance(content, str) else None}
    if not isinstance(content, str):
        out["body"] = BODY_UNSERIALIZABLE
        return out
    if capture == "none":
        out["body"] = BODY_NONE
    elif capture == "head":
        out["body"] = "head"
        out["head"] = content[:head_chars]
        out["head_truncated"] = len(content) > head_chars
    elif len(content) <= body_max_chars:
        out["body"] = "stored"
        out["content"] = content
    else:
        out["body"] = BODY_TOO_LARGE
    return out


def freeze_messages(messages, *, capture: str, head_chars: int, body_max_chars: int) -> dict:
    """最终 messages 的有序投影：序号、role、name、tool_call_id、hash；正文按政策。"""
    order, total = [], 0
    for seq, message in enumerate(messages or []):
        if not isinstance(message, dict):
            order.append({"seq": seq, "role": "unserializable", "body": BODY_UNSERIALIZABLE,
                          "content_hash": None, "chars": None})
            continue
        content = message.get("content")
        if not isinstance(content, str):
            content = "" if content is None else canonical_json(content) or ""
        total += len(content)
        entry = {"seq": seq, "role": str(message.get("role", "?")),
                 "chars": len(content), "content_hash": content_hash(content)}
        if message.get("name") is not None:
            entry["name"] = str(message["name"])[:120]
        if message.get("tool_call_id") is not None:
            entry["tool_call_id"] = str(message["tool_call_id"])[:160]
        tool_calls = message.get("tool_calls")
        if tool_calls:
            entry["tool_call_count"] = len(tool_calls)
        entry.update(freeze_text(content, capture=capture, head_chars=head_chars,
                                 body_max_chars=body_max_chars))
        order.append(entry)
    return {"count": len(order), "total_chars": total, "order": order}


def freeze_tools(tools) -> dict:
    """工具定义单独保存：名称 + 定义 hash（schema 不与消息混排）。"""
    items = []
    for index, tool in enumerate(tools or []):
        name = ""
        if isinstance(tool, dict):
            function = tool.get("function")
            name = str((function or {}).get("name", "") or tool.get("name", ""))
        items.append({"index": index, "name": name[:120],
                      "definition_hash": content_hash(tool)})
    return {"count": len(items), "items": items}


def freeze_parameters(kwargs: dict) -> dict:
    """白名单模型参数逐项记录；未覆盖键显式列出，不丢业务参数后冒充完整请求。"""
    covered, uncovered = {}, []
    for key, value in (kwargs or {}).items():
        if key in ("messages", "tools"):
            continue  # 各自单独冻结
        if key in PARAM_WHITELIST:
            canonical = canonical_json(value)
            if canonical is None:
                uncovered.append(key)
            else:
                covered[key] = value
        else:
            uncovered.append(key)
    return {"covered": covered, "uncovered": sorted(uncovered)}


def match_bindings(messages, blocks) -> tuple[list[dict], list[dict]]:
    """把登记块定位到最终消息：整条一致优先，其次唯一子串区间；定位不到记缺口。

    块与消息均为宿主装配产物；同一块只绑定第一处。匹配在调用边界现算，
    因此锚重建、工具轮重建消息后位置天然正确。
    """
    bindings, gaps = [], []
    used = set()
    texts = []
    for seq, message in enumerate(messages or []):
        content = message.get("content") if isinstance(message, dict) else None
        texts.append(content if isinstance(content, str) else "")
    for block in blocks or []:
        content = block.get("content")
        if not isinstance(content, str) or not content:
            continue
        placed = False
        for seq, text in enumerate(texts):
            if not text:
                continue
            if text == content:
                bindings.append(_binding(block, seq, 0, len(text)))
                placed = True
                break
        if not placed:
            for seq, text in enumerate(texts):
                if not text or len(text) <= len(content):
                    continue
                start = text.find(content)
                if start >= 0:
                    bindings.append(_binding(block, seq, start, start + len(content)))
                    placed = True
                    break
        if not placed:
            gaps.append({"block": block.get("name"), "block_seq": block.get("seq"),
                         "reason": "block_not_found_in_messages"})
        used.add(block.get("seq"))
    return bindings, gaps


def _binding(block, message_seq: int, start: int, end: int) -> dict:
    return {"block_seq": block.get("seq"), "kind": block.get("kind"), "name": block.get("name"),
            "message_seq": message_seq, "start": start, "end": end,
            "content_hash": block.get("content_hash"), "chars": block.get("chars")}


def validate_bindings(bindings, messages_order) -> list[dict]:
    """SDK 侧绑定复核：序号存在、区间合法；不符记缺口，不修正业务消息。"""
    gaps = []
    known = {entry["seq"]: entry["chars"] for entry in messages_order}
    for binding in bindings or []:
        seq = binding.get("message_seq")
        chars = known.get(seq)
        if chars is None:
            gaps.append({"reason": "message_seq_unknown", "binding": binding.get("name"), "message_seq": seq})
            continue
        start, end = binding.get("start"), binding.get("end")
        if not (isinstance(start, int) and isinstance(end, int) and 0 <= start <= end <= chars):
            gaps.append({"reason": "binding_range_invalid", "binding": binding.get("name"), "message_seq": seq})
    return gaps
