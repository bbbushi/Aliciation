"""技能库：从记忆提炼「怎么做」的可复用知识。MVP 手动触发，对话中暂不注入。"""
from __future__ import annotations

import time

from .config import Config
from .llm import chat, parse_json
from .store import Store

SKILL_CAPACITY = 500

DISTILL_PROMPT = """你是技能提炼器。从下面的记忆中提炼可复用的「技能」。

技能的定义：**怎么做**的可复用知识——方法、流程、模式、技巧、经验教训。
不是「发生了什么」（那是记忆的事，不要提炼成技能）。

规则：
- 只从给定记忆中提炼，禁止编造。
- 与已有技能重复的不输出。已有技能：{existing}
- title ≤16字；content 2~4句，写清「遇到什么情况、怎么做」。
- source_titles：支撑这条技能的记忆标题列表。
- 没有值得提炼的就输出空列表。

记忆（标题：内容）：
{memories}

输出 JSON：{{"skills": [{{"title": "...", "content": "...", "source_titles": ["..."]}}]}}"""


def distill(store: Store, client, cfg: Config) -> str:
    horizon = time.time() - 30 * 86400
    rows = [m for m in store.alive_memories() if m["created_at"] >= horizon][:80]
    if not rows:
        return "没有可提炼的记忆。"

    existing = store.skill_titles()
    prompt = DISTILL_PROMPT.format(
        existing="、".join(existing) if existing else "（暂无）",
        memories="\n".join(f'- {m["title"]}：{m["content"]}' for m in rows),
    )
    resp = chat(
        client, cfg,
        [{"role": "user", "content": prompt}],
        model=cfg.llm.cheap_model,
        json_mode=True,
    )
    try:
        data = parse_json(resp.choices[0].message.content or "")
    except ValueError:
        return "技能提炼失败：模型输出无法解析。"

    added = 0
    known = {t.lower() for t in existing}
    for s in data.get("skills", []):
        title = str(s.get("title", "")).strip()
        content = str(s.get("content", "")).strip()
        if not title or not content or title.lower() in known:
            continue
        src = "、".join(str(t) for t in s.get("source_titles", []) if str(t).strip())
        store.insert_skill(title=title, content=content, source_titles=src)
        known.add(title.lower())
        added += 1

    # 超额清理：低使用频次、久未使用者先淘汰
    overflow = store.count_skills() - SKILL_CAPACITY
    if overflow > 0:
        now = time.time()
        skills = store.list_skills()
        skills.sort(
            key=lambda r: r["use_count"]
            + max(0.0, 3.0 - (now - (r["last_used_at"] or r["created_at"])) / 86400)
        )
        for r in skills[:overflow]:
            store.evict_skill(r["id"])

    store.meta_set("last_distill_at", time.time())
    return f"提炼完成：新增 {added} 条技能，当前共 {store.count_skills()} 条。"


def distill_if_due(store: Store, client, cfg: Config) -> str | None:
    """数据触发：自上次提炼起新记忆累计到阈值即提炼。"""
    anchor = store.meta_get_time("last_distill_at")
    if store.count_memories_since(anchor) >= cfg.memory.distill_every_memories:
        return distill(store, client, cfg)
    return None
