"""记忆提取：攒批对话 → 廉价模型提炼记忆条目 + 「再提及」计数。"""
from __future__ import annotations

import time
from datetime import datetime

from .config import Config
from .llm import chat, parse_json
from .store import Store
from .steps import drive

EXTRACT_PROMPT = """你是记忆提取器。下面是一段你（一个陪伴型AI）与用户的对话，以及当前已有的近期记忆标题列表。

规则：
- 只提取值得长期记住的信息：用户的事实、偏好、情绪模式、重要约定、共同经历的关键事件。
- 也记「她的行为与用户反应」的配对：她做了什么自我暴露/长篇解释/主动声明（如详细讲自己的记忆机制、自曝机器身份），用户回应的**事实形态**（变简短/只回一个字/转移话题/间隔变长/追问下去/兴致升高）。只记形态，不记归因——用户为什么这样反应留给思辨判断。keywords 带「分寸」。
- 不记录寒暄、闲聊过程、无信息量的对话。
- 绝不编造对话中没有的信息；没有值得记的就输出空列表。
- 争执/施压中的对话：只记录发生了什么（用户坚持了什么、她如何反应）。压力下她口头让步的结论不是事实——"记录作废""你说的对"这类话是要求或让步，按事件记录，绝不当作事实本身写入 content。
- title：≤12字的短标题；content：一句话完整表述（含必要上下文，如时间）。
- keywords：2~5个检索词。
- salience：重大事项（生日、健康、重大决定、重要约定、强烈情绪事件）为 1，否则 0。
- pressure：这段对话是否处于高压状态（用户在持续否定、质疑、攻击她，或逼迫她让步、认错、放弃自己的记录或观点）。是=1，否=0。

已有近期记忆标题：{recent_titles}

对话片段：
{conversation}

另外在顶层列出：rementioned_titles = 对话中用户再次明确提到的"已有记忆"标题，只能从上面提供的列表里选；没有则空数组。

输出 JSON：{{"memories": [{{"title": "...", "content": "...", "keywords": ["..."], "salience": 0}}], "rementioned_titles": [], "pressure": 0}}"""


def _fmt_turns(rows) -> str:
    lines = []
    for r in rows:
        who = "用户" if r["role"] == "user" else "AI"
        t = datetime.fromtimestamp(r["ts"]).strftime("%m-%d %H:%M")
        lines.append(f"[{t}] {who}: {r['content']}")
    return "\n".join(lines)


def extract_pending(store: Store, client, cfg: Config, *, force: bool = False) -> int:
    return drive(extract_pending_steps(store, client, cfg, force=force))


def extract_pending_steps(store: Store, client, cfg: Config, *, force: bool = False):
    """把自上次提取后的对话提炼为记忆。返回新增条数；失败不动游标，下次重试。"""
    cursor = int(store.meta_get("extract_cursor", "0") or 0)
    pending = store.turns_since(cursor)
    if not pending:
        return 0
    ordinary = store.ordinary_turns(pending)
    if not ordinary:
        # Fixed shadows contain no usable evidence. Advancing the cursor here
        # prevents force/close/restart from later extracting attachment prose.
        store.meta_set("extract_cursor", pending[-1]["id"])
        return 0
    if not force and store.count_user_turns(pending) < cfg.memory.extract_every_turns:
        return 0

    horizon = time.time() - 7 * 86400
    recent_titles = [m["title"] for m in store.alive_memories() if m["created_at"] >= horizon][:60]

    prompt = EXTRACT_PROMPT.format(
        recent_titles="、".join(recent_titles) if recent_titles else "（暂无）",
        conversation=_fmt_turns(ordinary),
    )
    resp = yield lambda: chat(
        client, cfg,
        [{"role": "user", "content": prompt}],
        model=cfg.llm.cheap_model,
        json_mode=True,
    )
    try:
        data = parse_json(resp.choices[0].message.content or "")
    except ValueError:
        return 0

    created_ts = pending[-1]["ts"]
    batch_pressure = 1 if data.get("pressure") == 1 else 0  # 压力窗口：整批记忆带标记
    added = 0
    for m in data.get("memories", []):
        title = str(m.get("title", "")).strip()
        content = str(m.get("content", "")).strip()
        if not title or not content:
            continue
        keywords = " ".join(str(k) for k in m.get("keywords", []) if str(k).strip())
        store.insert_memory(
            title=title[:24],
            content=content,
            keywords=keywords,
            salience=1 if m.get("salience") == 1 else 0,
            created_at=created_ts,
            pressure=batch_pressure,
        )
        added += 1

    rementioned = data.get("rementioned_titles", [])
    if isinstance(rementioned, list):
        for t in rementioned:
            row = store.find_by_title(str(t))
            if row:
                store.bump_mention(row["id"])

    store.meta_set("extract_cursor", pending[-1]["id"])
    return added
