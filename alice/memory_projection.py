"""Conversation-only views of memory bodies; stored evidence stays untouched.

The usage notice is shared by direct/indexed conversation memory areas.
Only the exact prior template emitted by persona._commit_import is rewritten.
This is not a permission check or a generic field scrubber: unknown/changed
formats remain verbatim, and all callers retain their existing access checks.
"""
from __future__ import annotations

import re

RECORD_USAGE_GUIDANCE = (
    "按记录的说话者与来源取用资料；旧AI自述即使已保存，也只证明说过，"
    "不是该事件发生或身体体验的依据。"
)


_FIELDS = ("遭遇", "事实", "主张", "解释", "原候选", "不确定", "下次回应", "出处", "状态")
_SOURCE = re.compile(
    r"底色文档·《[\s\S]*》；候选 [A-Za-z0-9_-]{1,80}；导入 "
    r"imp_(?:[0-9a-f]{32}|[0-9]{8}_[0-9]{6}_[0-9a-f]{6})\Z")


def conversation_content(row) -> str:
    """Hide an original adoption candidate only in an intact imported prior.

    Keep the adopted claim, adaptation reason, provenance and uncertainty in
    full. The source body still determines memory versions and remains available
    to host inspection and deliberation. Never change an ordinary event because
    its prose happens to contain a line labelled 原候选.
    """
    body = row["content"]
    if (row["kind"] != "stance" or row["origin"] != "conversation"
            or row["provenance_status"] != "valid"
            or row["keywords"] != "观点 来历 收养 未验证先验"):
        return body
    blocks = []
    for line in body.splitlines(keepends=True):
        if line.startswith("  ") and blocks:
            blocks[-1] += line
        elif len(blocks) < len(_FIELDS) and line.startswith(_FIELDS[len(blocks)] + "："):
            blocks.append(line)
        else:
            return body
    if len(blocks) != len(_FIELDS):
        return body
    values = {name: block[len(name) + 1:].rstrip("\r\n")
              for name, block in zip(_FIELDS, blocks)}
    if (values["遭遇"] != "人格文档捐赠的认知材料；不是 Alice 亲历"
            or not values["事实"].startswith("文档出处说明：")
            or not values["事实"].endswith("；不证明设定事件发生")
            or not values["主张"].strip() or not values["原候选"].strip()
            or not values["解释"].startswith(("捐赠——adopt；理由：", "捐赠——adapt；理由："))
            or values["不确定"] != "未经自身经历验证的初始化先验，不是已证事实"
            or values["下次回应"] != "在真实经历中检验适用条件，不冒认捐赠人的经历"
            or _SOURCE.fullmatch(values["出处"]) is None
            or values["状态"] != "先验"):
        return body
    return "".join(block for name, block in zip(_FIELDS, blocks) if name != "原候选")
