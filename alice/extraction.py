"""记忆提取：攒批对话 → 廉价模型提炼记忆条目 + 「再提及」计数。"""
from __future__ import annotations

import time
import json
import hashlib
from datetime import datetime

from .config import Config
from .llm import chat, parse_json
from .store import Store
from .steps import drive
from . import grounding, metrics
from .context import messages_tokens

EXTRACT_PROMPT = """你是记忆提取器。下面是一段你（一个陪伴型AI）与用户的对话，以及当前已有的近期记忆标题列表。

规则：
- 只提取值得长期记住的信息：用户的事实、偏好、情绪模式、重要约定、共同经历的关键事件。
- 也记「她的行为与用户反应」的配对：她做了什么自我暴露/长篇解释/主动声明（如详细讲自己的记忆机制、自曝机器身份），用户回应的**事实形态**（变简短/只回一个字/转移话题/间隔变长/追问下去/兴致升高）。只记形态，不记归因——用户为什么这样反应留给思辨判断。keywords 带「分寸」。
- 不记录寒暄、闲聊过程、无信息量的对话。
- 绝不编造对话中没有的信息；没有值得记的就输出空列表。
- AI的自述、猜测或道歉不是已验证事实；需要记录时写成“AI曾说/随后更正”，不能把自述的内部成因当成事实。用户转移话题、继续聊天或没有反对，不等于接受解释、认可判断或达成约定。
- 归因按发言者逐条分开：用户与AI先后说过的话不得合并成一句归给其中一方；“用户明确表示/要求/认可”只能写用户自己原话的内容。用户复述或赞同AI的说法时记“用户引用/认可了AI的某说法”，不当作用户的独立主张；同一立场由双方分别表达时如实分开记录。
- 单次行为不能概括成稳定偏好或“一般会这样”。改写素材、假设和测试情境不是用户真实经历或工作期限；AI单方面提出的陪伴方式不是双方约定，约定须有用户明确同意的原话。
- 争执/施压中的对话：只记录发生了什么（用户坚持了什么、她如何反应）。压力下她口头让步的结论不是事实——"记录作废""你说的对"这类话是要求或让步，按事件记录，绝不当作事实本身写入 content。
- title：≤12字的短标题；content：一句话完整表述（含必要上下文，如时间）。标题、正文、keywords、salience须全部保持相同来源资格；素材改写事件的标题不得写成约定，关键词不能写约定/确认后发布，不能因素材出现期限就标重大事项。
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
        lines.append(f"[turn:{r['id']} {t}] {who}: {r['content']}")
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
    epoch = store.meta_get('session_epoch')
    snapshot = json.dumps([dict(r) for r in ordinary], ensure_ascii=False, sort_keys=True)
    source = {'epoch': epoch, 'turn_ids': [r['id'] for r in ordinary],
              'sha256': hashlib.sha256(snapshot.encode()).hexdigest()}
    request = [{"role": "user", "content": prompt}]
    if cfg.context.total_budget_tokens and messages_tokens(request) + cfg.context.reply_reserve_tokens > cfg.context.total_budget_tokens:
        return 0
    _et0 = time.time()
    resp = yield lambda: chat(
        client, cfg,
        request,
        model=cfg.llm.cheap_model,
        json_mode=True, retry=False,
    )
    metrics.record_llm_call(store, 'extraction', _et0, resp, model=cfg.llm.cheap_model)
    raw = resp.choices[0].message.content or ''
    def invalid_output(reason):
        current = store.ordinary_turns([r for r in store.turns_since(cursor) if r['id'] <= pending[-1]['id']])
        if (store.meta_get('session_epoch') != epoch or store.meta_get('extract_cursor', '0') != str(cursor)
                or json.dumps([dict(r) for r in current], ensure_ascii=False, sort_keys=True) != snapshot):
            return
        payload = {'sha256': hashlib.sha256(raw.encode()).hexdigest(), 'chars': len(raw)}
        if len(raw) <= 16000:
            payload['raw'] = raw
        u = getattr(resp, 'usage', None)
        audit = {'error': reason, 'calls': 1,
                 'usage': [{'prompt': getattr(u, 'prompt_tokens', 0) or 0,
                            'completion': getattr(u, 'completion_tokens', 0) or 0}]}
        with store.source_transaction():
            store.conn.execute('INSERT INTO memory_extraction_batches '
                '(created_at,session_epoch,source_json,candidates_json,review_json,status,memory_ids) VALUES(?,?,?,?,?,?,?)',
                (time.time(), epoch, json.dumps(source), json.dumps(payload, ensure_ascii=False),
                 json.dumps(audit), 'invalid_output', '[]'))
    try:
        data = parse_json(raw)
    except ValueError:
        invalid_output('invalid_json')
        return 0

    if not isinstance(data, dict):
        invalid_output('invalid_envelope')
        return 0
    candidates = data.get('memories')
    if not isinstance(candidates, list) or len(candidates) > 32:
        invalid_output('invalid_candidates')
        return 0
    for m in candidates:
        if (not isinstance(m, dict) or set(m) - {'title', 'content', 'keywords', 'salience'}
                or not isinstance(m.get('title'), str)
                or not 1 <= len(m['title'].strip()) <= 24
                or not isinstance(m.get('content'), str) or not 1 <= len(m['content'].strip()) <= 6000
                or not isinstance(m.get('keywords'), list) or not 2 <= len(m['keywords']) <= 5
                or any(not isinstance(k, str) or not 1 <= len(k.strip()) <= 50 for k in m['keywords'])
                or type(m.get('salience')) is not int or m['salience'] not in (0, 1)):
            invalid_output('invalid_candidate_fields')
            return 0
    audit = {'decisions': [], 'calls': 1, 'usage': [], 'model': cfg.llm.cheap_model,
             'policy_sha256': hashlib.sha256(grounding.REVIEW_POLICY.encode()).hexdigest()}
    def account(response):
        u = getattr(response, 'usage', None)
        audit['usage'].append({'prompt': getattr(u, 'prompt_tokens', 0) or 0,
                               'completion': getattr(u, 'completion_tokens', 0) or 0})
    account(resp)
    status = 'reviewed'
    if candidates:
        review_messages = grounding.messages('memory', _fmt_turns(ordinary),
                            [{'id': i, **m} for i, m in enumerate(candidates)])
        try:
            if (cfg.context.total_budget_tokens and messages_tokens(review_messages)
                    + cfg.context.reply_reserve_tokens > cfg.context.total_budget_tokens):
                raise ValueError('review_budget_exceeded')
            audit['calls'] += 1
            _et0 = time.time()
            response = yield lambda: chat(client, cfg, review_messages,
                                  model=cfg.llm.cheap_model, json_mode=True, retry=False)
            metrics.record_llm_call(store, 'memory_review', _et0, response, model=cfg.llm.cheap_model)
            account(response)
            audit['decisions'] = grounding.decisions(response.choices[0].message.content or '', len(candidates))
        except Exception as exc:
            status = 'review_failed'
            audit['error'] = type(exc).__name__
    current = store.ordinary_turns([r for r in store.turns_since(cursor) if r['id'] <= pending[-1]['id']])
    if (store.meta_get('session_epoch') != epoch or store.meta_get('extract_cursor', '0') != str(cursor)
            or json.dumps([dict(r) for r in current], ensure_ascii=False, sort_keys=True) != snapshot):
        return 0  # Epoch/source revocation cannot publish even an old audit body.
    ids = []
    with store.source_transaction():
        if status == 'reviewed':
            for m, decision in zip(candidates, audit['decisions']):
                if decision['valid']:
                    ids.append(store.insert_memory(title=m['title'], content=m['content'],
                        keywords=' '.join(m['keywords']), salience=m['salience'],
                        created_at=pending[-1]['ts'], pressure=1 if data.get('pressure') == 1 else 0))
            # Count only exact titles explicitly present in an actual user turn.
            mentions = data.get('rementioned_titles', [])
            if isinstance(mentions, list):
                for title in set(t for t in mentions if isinstance(t, str)):
                    if title not in recent_titles or not any(title in r['content'] for r in ordinary if r['role'] == 'user'):
                        continue
                    row = store.find_by_title(title)
                    if row and row['provenance_status'] == 'valid':
                        store.conn.execute('UPDATE memories SET mention_count=mention_count+1 WHERE id=?', (row['id'],))
            store.conn.execute("INSERT INTO meta(key,value) VALUES('extract_cursor',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(pending[-1]['id']),))
        store.conn.execute('INSERT INTO memory_extraction_batches '
            '(created_at,session_epoch,source_json,candidates_json,review_json,status,memory_ids) VALUES(?,?,?,?,?,?,?)',
            (time.time(), epoch, json.dumps(source), json.dumps(candidates, ensure_ascii=False),
             json.dumps(audit, ensure_ascii=False), status, json.dumps(ids)))
    return len(ids)
