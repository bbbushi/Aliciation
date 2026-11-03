"""表达参考解析（人格连续性设计 v1 · P1a）。

只读解析人格快照并渲染表达参考：不调用模型、不查询任务正文、不写记忆。
- load_persona_snapshot()：一次读取 persona.md，含正文指纹与分节；
- render_expression_reference(snapshot, purpose)：按用途渲染——
    "chat"           主聊天：气质/情境（≤1200 字）+ 样本（≤1200 字，逐条完整保留）；
    "worker_summary" 协作总结：仅气质/情境与样本节头注（不读关系、来历细节）；
  预算是初始工程上限（设计 §6.1），不是效果保证；超限按条目完整保留到放不下为止，
  不截半句；放不下时在末尾注明降级（"（其余 N 条样本因预算未展示）"）。
指纹用于"本轮人格依据"元数据（设计 §8.2）：同一请求的多个子调用版本一致由
调用方（loop 每轮装配一次）保证。
"""
from __future__ import annotations

import hashlib
import re

from . import config as paths

STYLE_BUDGET_CHARS = 1200
SAMPLES_BUDGET_CHARS = 1200
_SAMPLE_LINE = re.compile(r"^- 【(?P<kind>[^·】]+)·(?P<sid>[A-Za-z0-9_-]+)】(?P<body>.*)$")


def load_persona_snapshot() -> dict | None:
    """读取人格文件并做只读解析。文件缺失返回 None；异常向上（调用方降级为无人格）。"""
    if not paths.PERSONA_FILE.exists():
        return None
    raw = paths.PERSONA_FILE.read_text(encoding="utf-8")
    sections: dict[str, str] = {}
    current, buf = None, []
    for line in raw.splitlines():
        m = re.match(r"^## (.+)$", line.strip())
        if m:
            if current is not None:
                sections[current] = "\n".join(buf).strip()
            current, buf = m.group(1).strip(), []
        elif current is not None:
            buf.append(line)
    if current is not None:
        sections[current] = "\n".join(buf).strip()
    samples = []
    body = sections.get("底色 · 表达样本", "")
    if body:
        for ln in body.splitlines():
            m = _SAMPLE_LINE.match(ln.strip())
            if m:
                samples.append({"kind": m.group("kind"), "id": m.group("sid"),
                                "body": m.group("body").strip()})
    return {
        "raw": raw,
        "fingerprint": hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16],
        "sections": sections,
        "sample_ids": [s["id"] for s in samples],
        "samples": samples,
    }


def _bounded(items: list[str], budget: int, label: str) -> str:
    """按条目完整保留到预算耗尽；不截半句，降级如实注明。"""
    kept, used = [], 0
    for it in items:
        cost = len(it) + 1
        if used + cost > budget and kept:
            break
        kept.append(it)
        used += cost
    text = "\n".join(kept)
    rest = len(items) - len(kept)
    if rest > 0:
        text += f"\n（其余 {rest} 条{label}因预算未展示）"
    return text


def _style_items(snapshot: dict) -> list[str]:
    out = []
    for key in ("底色 · 气质与风格", "底色 · 情境反应"):
        body = snapshot["sections"].get(key, "")
        out.extend(ln.strip() for ln in body.splitlines()
                   if ln.strip() and not ln.strip().startswith("（"))
    return out


def render_expression_reference(snapshot: dict | None, purpose: str = "chat") -> str:
    """渲染表达参考。purpose 只决定可读字段（设计 §6），不定义两种人格。"""
    if not snapshot:
        return ""
    style_text = _bounded(_style_items(snapshot), STYLE_BUDGET_CHARS, "条风格/情境")
    parts = []
    if style_text:
        parts.append("【说话方式与情境（用户选定的长期表达偏好；普通演化不改写）】\n" + style_text)
    if purpose == "chat":
        if snapshot["samples"]:
            sample_lines = [f"【{s['kind']}·{s['id']}】{s['body']}" for s in snapshot["samples"]]
            parts.append("【表达样本（虚构的表达参考——示范说话方式，不是发生过的对话，"
                         "样本中的事件不是记忆）】\n"
                         + _bounded(sample_lines, SAMPLES_BUDGET_CHARS, "条样本"))
    elif purpose == "worker_summary":
        if snapshot["samples"]:
            parts.append("（另有表达样本若干，仅供聊天表达参考；总结任务不读取样本正文）")
    return "\n\n".join(parts)


def render_full(snapshot: dict | None) -> str:
    """主聊天的人格正文渲染（P1a）：结构化重排 + 预算约束 + 样本注记。

    与旧版直接注入全文的差异：样本节获得预算与"虚构参考"标注（若 P0 导入已写入
    标注则不重复）；其余小节按原文保留（来历/思想/关系不走本渲染的表达预算）。
    """
    if not snapshot:
        return ""
    reference = render_expression_reference(snapshot, purpose="chat")
    sections = dict(snapshot["sections"])
    if reference:
        sections["底色 · 气质与风格"] = reference  # 参考已含风格+情境+样本三块内容
        sections.pop("底色 · 情境反应", None)
        sections.pop("底色 · 表达样本", None)
    title = "Alice"
    head = re.match(r"^#\s+(.+)$", snapshot["raw"].strip())
    if head:
        title = head.group(1).strip()
    order = ["底色 · 气质与风格", "底色 · 情境反应", "底色 · 表达样本",
             "生长 · 我的来历", "生长 · 自我叙事", "生长 · 我现在的想法", "生长 · 与用户的关系"]
    out = [f"# {title}"]
    for key in order:
        if key in sections and sections[key]:
            out.append(f"## {key}\n\n{sections[key]}")
    for key, body in sections.items():  # 未知新小节按原顺序追加（前向兼容）
        if key not in order and body:
            out.append(f"## {key}\n\n{body}")
    return "\n\n".join(out) + "\n"
