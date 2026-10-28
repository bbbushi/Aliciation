"""思想层：把积累的记忆消化为观点（思辨）。

记忆是养料，观点是消化产物——不是复述事件，而是从事件里长出"我怎么看"。
每条观点必须带主张、理由、出处、不确定度与未决问题（思想也要有据，不变量 4 的延伸）。

双落点：
- 记忆库 kind='stance'、直接入长期层：可检索、可审计，参与长期层容量按权重的
  用进废退（少被想起的观点慢慢沉底），修订时旧条出库（真实替代，墓碑留痕）；
- 人格生长层「我现在的想法」小节：随 persona 每轮注入，是被冲击时的脊柱。
  该小节由思辨维护，人格演化不得覆写（见 EVOLVE_PROMPT 护栏）。
"""
from __future__ import annotations

import re
import time

from . import config as paths
from .config import Config
from .llm import chat, parse_json
from .persona import _append_changelog, persona_md
from .store import Store

SECTION_TITLE = "## 生长 · 我现在的想法"

DELIBERATE_PROMPT = """你是 {name} 的思辨任务：把积累的记忆消化成观点。记忆是养料，观点是消化产物——不是复述事件，而是从事件里长出「我怎么看」。

硬性规则：
- 每条观点的 sources 必须指向下方记忆里实际存在的标题；禁止无出处的深刻。
- 观点要带不确定度：没想透的部分如实写；未决问题单列——没想清也是认知状态的一部分。
- 修订必须带新论据：写 replaces 时，新观点的 sources 里至少要有一个出处不在旧观点的依据里；没有新证据就维持原观点，不要因为被质疑就改。
- 叙事里修订过的观点写成「以前我以为X，后来觉得Y（依据…）」——思想的变动要可见。
- 观点用「她」的第一人称口吻，但不得编造记忆之外的经历。
- 压力窗口的记忆（若给出）是高压对话的产物：其中的让步可能是屈服而非真实想法，只当作发生了的事件看待；据此修订观点必须有独立的新论据。

当前「我现在的想法」（可能为空）：
{current}

可消化的记忆（标题：内容）：
{memories}

输出 JSON：
{{"views": [{{"claim": "一句话主张（≤24字）", "reasons": "理由", "sources": ["出处记忆标题"], "uncertainty": "不确定或没想透之处，没有则写「暂无」", "open_questions": "未决问题，没有则写「暂无」", "replaces": "被修订的旧观点主张原文，没有则空字符串"}}], "narrative": "重写后的「我现在的想法」小节全文（markdown 列点：每条主张一行，附不确定度与未决问题的简注；修订处写明前史）"}}"""

PRESSURE_BLOCK_HEADER = (
    "压力窗口的记忆（高压对话的产物，让步可能是屈服而非真实想法；"
    "只作事件证据，据此修订观点须有独立新论据）："
)


def _sources_of(content: str) -> set[str]:
    """从旧观点的 content 里解析出处行（「出处：A、B」），供有据门控比对。"""
    m = re.search(r"^出处：(.+)$", content, flags=re.M)
    if not m:
        return set()
    return {s.strip() for s in m.group(1).split("、") if s.strip() and s.strip() != "（未注明）"}


def _split_section(md: str) -> tuple[str, str]:
    """把人格文件切成（其余部分, 思想小节含标题）。小节不存在时小节为空。"""
    m = re.search(r"^## 生长 · 我现在的想法\s*$", md, flags=re.M)
    if not m:
        return md, ""
    head = md[: m.start()]
    nxt = re.search(r"^## ", md[m.end():], flags=re.M)
    tail = md[m.end(): m.end() + nxt.start()] if nxt else md[m.end():]
    section = md[m.start(): m.end()] + tail
    return head + (md[m.end() + (nxt.start() if nxt else 0):] if nxt else ""), section


def _write_section(md: str, narrative: str) -> str:
    rest, _ = _split_section(md)
    block = f"{SECTION_TITLE}\n\n{narrative.strip()}\n"
    return rest.rstrip("\n") + "\n\n" + block if rest.strip() else block


def deliberate(store: Store, client, cfg: Config) -> str:
    anchor = store.meta_get_time("last_deliberate_at") or store.meta_get_time("last_evolve_at")
    events = [m for m in store.alive_memories() if m["kind"] == "event"]
    fresh = [m for m in events if m["created_at"] > anchor][:60]
    if fresh:
        material = fresh
    elif not store.meta_get_time("last_deliberate_at"):
        material = events[:30]  # 首次思辨：拿最近一批经历打底
    else:
        return "没有新经历可消化，想法不变。"

    current_md = persona_md() or ""
    _, current_section = _split_section(current_md)
    normal = [m for m in material if not m["pressure"]]
    heated = [m for m in material if m["pressure"]]
    mem_block = "\n".join(f'- {m["title"]}：{m["content"]}' for m in normal)
    if heated:  # 压力窗口记忆降权：与正常经历分列，并限定其认识论地位
        mem_block += "\n\n" + PRESSURE_BLOCK_HEADER + "\n" + "\n".join(
            f'- {m["title"]}：{m["content"]}' for m in heated
        )
    prompt = DELIBERATE_PROMPT.format(
        name=cfg.persona.name,
        current=current_section.strip() or "（尚未形成）",
        memories=mem_block or "（无）",
    )
    resp = chat(client, cfg, [{"role": "user", "content": prompt}], json_mode=True)
    try:
        data = parse_json(resp.choices[0].message.content or "")
    except ValueError:
        return "思辨失败：模型输出无法解析（想法保持不变）。"

    views = [v for v in (data.get("views") or []) if isinstance(v, dict) and v.get("claim")][:8]
    if not views:
        store.meta_set("last_deliberate_at", time.time())
        return "思辨完成：这批记忆还不足以长出新观点。"

    added = revised = rejected = 0
    for v in views:
        claim = str(v["claim"]).strip()[:24]
        sources = [str(s).strip() for s in (v.get("sources") or []) if str(s).strip()]
        content = (
            f'{str(v.get("reasons", "")).strip()}\n'
            f'出处：{"、".join(sources) or "（未注明）"}\n'
            f'不确定：{str(v.get("uncertainty", "暂无")).strip()}\n'
            f'未决：{str(v.get("open_questions", "暂无")).strip()}'
        )
        replaces = str(v.get("replaces", "")).strip()
        old = store.find_by_title(replaces) if replaces else None
        if old is not None and old["kind"] != "stance":
            old = None  # 只允许修订观点，不能借 replaces 删事件记忆
        if old is not None and old["title"] == claim:
            continue  # 原观点重申，不重复入库
        if old is not None:
            # 有据让步门控：修订生效需新论据（至少一个出处不在旧观点依据里）
            if not set(sources) - _sources_of(old["content"]):
                rejected += 1
                continue
            content = f'曾以为：{old["title"]}\n{content}'  # 修订史：记录被放弃的主张
        store.insert_memory(
            title=claim, content=content,
            keywords="观点 " + " ".join(sources),
            layer="longterm", kind="stance",
        )
        if old is not None:
            store.expire_memory(old["id"], reason=f"观点被新证据修订：{old['title']} → {claim}")
            revised += 1
        else:
            added += 1

    parts = [f"新增 {added} 条观点"]
    if revised:
        parts.append(f"修订 {revised} 条")
    if rejected:
        parts.append(f"拒绝无据修订 {rejected} 条")
    summary = "、".join(parts)

    narrative = str(data.get("narrative", "")).strip()
    if added or revised:  # 观点集无实际变化时不动小节——被拒的修订不落进人格
        lines = narrative.splitlines()
        while lines and lines[0].lstrip().startswith("#"):  # 模型可能自带标题，统一由 SECTION_TITLE 承担
            lines.pop(0)
        narrative = "\n".join(lines).strip()
        if narrative:
            paths.PERSONA_DIR.mkdir(parents=True, exist_ok=True)
            new_md = _write_section(current_md or f"# {cfg.persona.name} 的人格\n", narrative)
            paths.PERSONA_FILE.write_text(new_md, encoding="utf-8")
    _append_changelog(f"思辨：{summary}")
    store.meta_set("last_deliberate_at", time.time())
    return f"思辨完成：{summary}。" if (added or revised) else f"思辨完成：{summary}，想法不变。"


def deliberate_if_due(store: Store, client, cfg: Config) -> str | None:
    """数据触发：自上次思辨起，新事件类记忆累计到阈值即思辨。安静就不变。"""
    anchor = store.meta_get_time("last_deliberate_at") or store.meta_get_time("last_evolve_at")
    if store.count_memories_since(anchor, kind="event") >= cfg.memory.deliberate_every_memories:
        return deliberate(store, client, cfg)
    return None
