"""Host-owned interest evidence, source decisions and value-aware maintenance.

Methods run under the Store lock. Model decisions arrive through the current
conversation or the supervised reader; this module never calls a model/network.
"""
from __future__ import annotations

import hashlib
import json
import math
import time
import uuid

from .observation_memory import version

TIERS = {'high', 'normal', 'low'}
ATTITUDES = {'interested', 'likes', 'dislikes', 'concerned', 'curious', 'neutral'}


def detail_targets(cfg):
    values = {tier:getattr(cfg.memory,'observation_'+tier+'_chars') for tier in ('high','normal','low')}
    if any(type(v) is not int or not 1 <= v <= 2500 for v in values.values()):
        raise ValueError('见闻建议长度须为1至2500的整数')
    if not values['low'] <= values['normal'] <= values['high']:
        raise ValueError('见闻建议长度应为低≤中≤高')
    return values

SCHEMA = """
CREATE TABLE IF NOT EXISTS learning_model_attempts(identity TEXT PRIMARY KEY, day TEXT NOT NULL, state TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS interest_evidence (
 interest_id TEXT NOT NULL, turn_id INTEGER NOT NULL, scope TEXT NOT NULL,
 quote TEXT NOT NULL, attitude TEXT NOT NULL, confidence REAL NOT NULL,
 created_at REAL NOT NULL, PRIMARY KEY(interest_id,turn_id)
);
CREATE TABLE IF NOT EXISTS learning_decisions (
 identity TEXT PRIMARY KEY, scope TEXT NOT NULL, purpose TEXT NOT NULL,
 source_versions TEXT NOT NULL, memory_id INTEGER, outcome TEXT NOT NULL,
 reason TEXT NOT NULL, created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS deliberation_seen (
 memory_id INTEGER NOT NULL, input_version TEXT NOT NULL, processed_at REAL NOT NULL,
 verdict TEXT NOT NULL, PRIMARY KEY(memory_id,input_version)
);
CREATE TABLE IF NOT EXISTS view_revisions (
 old_id INTEGER NOT NULL, new_id INTEGER NOT NULL, reason TEXT NOT NULL,
 created_at REAL NOT NULL, PRIMARY KEY(old_id,new_id)
);
CREATE TABLE IF NOT EXISTS memory_condensations (
 memory_id INTEGER NOT NULL, old_version TEXT NOT NULL, new_version TEXT NOT NULL,
 reason TEXT NOT NULL, created_at REAL NOT NULL,
 PRIMARY KEY(memory_id,old_version,new_version)
);
"""


def _text(value, limit, *, required=True):
    if not isinstance(value, str) or len(value.encode('utf-8')) > limit or required and not value.strip():
        raise ValueError('判断字段为空或过长')
    return value.strip()


def _body_fields(content, uncertainty):
    text = _text(content,7500)
    note = _text(uncertainty,1200)
    if text.startswith('见闻记录（') or '条件与不确定：' in text or '条件与不确定：' in note:
        raise ValueError('content仅写正文，不含见闻包装头或条件与不确定标签；条件、已读范围与不确定性独立填uncertainty，不带标签，不遗漏原条件')
    return text,note


def _condense_ratio(value):
    if type(value) not in (int,float) or not math.isfinite(value) or not 0 < value < 1:
        raise ValueError('浓缩比例须为大于0小于1的有限数值')
    return value


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def source_versions(packets):
    # A later fetch timestamp is not independently new evidence.
    return sorted({(p['source_id'], p['version'].split(':', 1)[0],
                    _json([p.get('coverage'), p.get('reading_policy')])) for p in packets})


def material_packet(*, packet_id, source_id, version_id, title, url, text, quote,
                    created_at, expires_at, scope, coverage, reading_policy,
                    author_view='', interpretation='', uncertainty='', keywords=''):
    """The shared publication shape; transports supply host-validated read facts."""
    if not isinstance(text,str) or not quote or quote not in text:
        raise ValueError('引文不在实际读取材料中')
    start = max(0, text.index(quote)-150)
    excerpt = text[start:].encode('utf-8')[:4096].decode('utf-8',errors='ignore')
    if quote not in excerpt:
        excerpt = text[text.index(quote):].encode('utf-8')[:4096].decode('utf-8',errors='ignore')
    if source_id.startswith('tieba:') and reading_policy.get('tieba_reader_version') == 2:
        # Preserve the actual bounded discussion, including later-page opinions.
        # The source store independently enforces the user's current text quota.
        excerpt = text
    return {'id':packet_id,'source_id':source_id,'version':version_id,'title':title,'url':url,
            'evidence':excerpt,'evidence_kind':'page_excerpt','origin':'external',
            'scope':scope,'created_at':created_at,'expires_at':expires_at,
            'memory_eligible':False,'observation_eligible':True,'coverage':coverage,
            'reading_policy':reading_policy,'author_view':author_view,
            'model_interpretation':interpretation,'uncertainty':uncertainty,'keywords':keywords}


class LearningStoreMixin:
    def init_learning(self):
        self.conn.executescript(SCHEMA)
        if self.conn.execute("SELECT 1 FROM sqlite_master WHERE name='discovery_interests'").fetchone():
            self.migrate_interest_evidence()
        if not self.meta_get('learning_started_at'):
            self.meta_set('learning_started_at', time.time())

    def migrate_interest_evidence(self):
        columns = {r[1] for r in self.conn.execute('PRAGMA table_info(discovery_interests)')}
        with self.conn:
            for name, declaration in (
                ('attitude', "TEXT NOT NULL DEFAULT 'unknown'"),
                ('confidence', 'REAL NOT NULL DEFAULT 0'),
                ('basis_user_turn_ids', "TEXT NOT NULL DEFAULT '[]'"),
                ('last_confirmed_at', 'REAL'), ('last_decay_at', 'REAL'),
                ('scope_json', "TEXT NOT NULL DEFAULT '[\"gui\"]'"),
                ('evidence_reason', "TEXT NOT NULL DEFAULT ''"),
            ):
                if name not in columns:
                    self.conn.execute(f'ALTER TABLE discovery_interests ADD COLUMN {name} {declaration}')

    def observe_interest(self, args, context):
        settings = self._observation_settings()
        if not settings.get('allow_chat_interests') or context.get('worker_refs'):
            raise ValueError('当前未开放聊天兴趣识别')
        scope, turn_id = context['scope'], context['user_turn_id']
        turn = self.conn.execute("SELECT role,content FROM turns WHERE id=?", (turn_id,)).fetchone()
        topic = _text(args.get('topic'), 240)
        quote = _text(args.get('user_quote'), 1000)
        # The original current user text is host-bound before attachment shadows.
        user_text = context.get('current_user_text', '')
        if turn is None or turn['role'] != 'user' or quote not in user_text:
            raise ValueError('兴趣依据须逐字来自本轮用户表达')
        attitude = args.get('attitude')
        confidence = args.get('confidence')
        if attitude not in ATTITUDES or type(confidence) not in (int, float) or not math.isfinite(confidence) or not 0 <= confidence <= 1:
            raise ValueError('兴趣态度或置信度无效')
        summary = _text(args.get('reason'), 1000)
        # Only public topics can become outward-facing search seeds.
        from .dialogue_search import public_query
        try:
            public_query(topic)
            external = bool(settings.get('enabled') and (scope == 'gui' or settings.get('share_in_qq')))
        except Exception:
            external = False
        old = self.conn.execute('SELECT * FROM discovery_interests WHERE lower(topic)=lower(?) AND scope_json=?',
                                (topic, _json([scope]))).fetchone()
        if old is not None and (old['origin']=='manual' or not old['enabled']):
            external = bool(old['external_allowed'] and old['enabled'])
        now = time.time()
        active_cap = settings.get('active_interest_capacity')
        if (old is None or not old['external_allowed']) and active_cap is not None and self.conn.execute('SELECT count(*) FROM discovery_interests WHERE enabled=1 AND external_allowed=1').fetchone()[0] >= active_cap:
            external = False
        with self.source_transaction():
            if old is None:
                cap = settings.get('interest_capacity')
                if cap is not None and self.conn.execute('SELECT count(*) FROM discovery_interests').fetchone()[0] >= cap:
                    raise ValueError('兴趣容量已满')
                interest_id = uuid.uuid4().hex
                self.conn.execute("INSERT INTO discovery_interests(id,topic,summary,enabled,external_allowed,origin,version,created_at,attitude,confidence,basis_user_turn_ids,last_confirmed_at,scope_json,evidence_reason) VALUES(?,?,?,1,?,'conversation_inferred',1,?,?,?,?,?,?,?)",
                    (interest_id, topic, topic, int(external), now, attitude, confidence, _json([turn_id]), now, _json([scope]), summary))
            else:
                interest_id = old['id']
                if self.conn.execute('SELECT 1 FROM interest_evidence WHERE interest_id=? AND turn_id=?', (interest_id, turn_id)).fetchone():
                    return {'interest_id': interest_id, 'reused': True}
                ids = json.loads(old['basis_user_turn_ids'])
                ids = list(dict.fromkeys(ids + [turn_id]))[-32:]
                # User-stopped/manual interests retain their enable/outbound settings.
                self.conn.execute('UPDATE discovery_interests SET attitude=?,confidence=?,basis_user_turn_ids=?,last_confirmed_at=?,evidence_reason=?,external_allowed=? WHERE id=?',
                    (attitude, confidence, _json(ids), now, summary,
                     old['external_allowed'] if old['origin']=='manual' or not old['enabled'] else int(external),interest_id))
            self.conn.execute('INSERT INTO interest_evidence VALUES(?,?,?,?,?,?,?)',
                (interest_id, turn_id, scope, quote, attitude, confidence, now))
        return {'interest_id': interest_id, 'recorded': True, 'external_allowed': external}

    def decay_interests(self, days=90):
        if type(days) not in (int, float) or days <= 0:
            return
        now = time.time()
        with self.conn:
            self.conn.execute("UPDATE discovery_interests SET confidence=confidence*0.5,last_decay_at=? "
                "WHERE origin='conversation_inferred' AND last_confirmed_at IS NOT NULL "
                "AND max(last_confirmed_at,coalesce(last_decay_at,0))<?", (now, now-days*86400))

    def save_learning(self, args, context, *, purpose='observation'):
        if context.get('worker_refs'):
            raise ValueError('协作任务资料尚未开放见闻写入')
        if not self._observation_settings().get('remember_observations'):
            raise ValueError('未开启见闻保存')
        scope = context['scope']
        packets = self.source_packets_for(context['packet_ids'], scope=scope)
        if not packets:
            raise ValueError('没有本轮实际读取且仍有效的来源')
        keep = args.get('keep', True)
        if type(keep) is not bool:
            raise ValueError('保留决定无效')
        tier = args.get('value_tier', 'normal')
        if tier not in TIERS:
            raise ValueError('价值分档无效')
        reason = _text(args.get('reason'), 1600)
        # Detailed prose belongs to the source-gated memory, not a permanent
        # dedup log that outlives source withdrawal or a decision to skip.
        audit_note = 'AI选择保留已读材料' if keep else 'AI选择跳过已读材料'
        hint = args.get('reflection_hint','ordinary')
        if hint not in {'ordinary','conflict','repeated'}:
            raise ValueError('思辨触发理由无效')
        sources = source_versions(packets)
        identity = hashlib.sha256(_json([scope, purpose, sources, context.get('request_id') or context.get('user_turn_id')]).encode()).hexdigest()
        old = self.conn.execute('SELECT * FROM learning_decisions WHERE identity=?', (identity,)).fetchone()
        if old:
            mid = old['memory_id']
            if mid is not None and not self.observation_sources(mid, scope=scope):
                raise ValueError('旧见闻已失效，不自动复活')
            return {'memory_id': mid, 'outcome': old['outcome'], 'reused': True}
        with self.source_transaction():
            mid = None
            if keep:
                previous = self.conn.execute("SELECT memory_id FROM learning_decisions WHERE scope=? AND source_versions=? AND outcome='saved' ORDER BY created_at DESC LIMIT 1", (scope, _json(sources))).fetchone()
                if previous:
                    mid = previous['memory_id']
                    if not self.observation_sources(mid, scope=scope):
                        raise ValueError('旧见闻已失效，不自动复活')
                    self.conn.execute('UPDATE memories SET value_tier=?,value_note=?,reflection_hint=?,value_policy=1 WHERE id=?',
                                      (tier, reason, hint, mid))
                    self.conn.execute('INSERT INTO learning_decisions VALUES(?,?,?,?,?,?,?,?)',
                        (identity, scope, purpose, _json(sources), mid, 'saved', audit_note, time.time()))
                    context.setdefault('memory_ids', []).append(mid)
                    return {'memory_id': mid, 'outcome':'saved', 'reused':True}
                title = _text(args.get('title'), 96)
                content, uncertainty = _body_fields(args.get('content'),args.get('uncertainty'))
                body = content + '\n条件与不确定：' + uncertainty
                mid = self.save_observation(context['packet_ids'], title=title, content=body,
                    keywords=str(args.get('keywords', ''))[:200], scope=scope, user_turn_id=context.get('user_turn_id'))
                if mid is None:
                    raise ValueError('见闻保存开关、来源资格或共享额度不允许')
                self.conn.execute('UPDATE memories SET value_tier=?,value_note=?,reflection_hint=?,value_policy=1 WHERE id=?', (tier, reason, hint, mid))
            self.conn.execute('INSERT INTO learning_decisions VALUES(?,?,?,?,?,?,?,?)',
                (identity, scope, purpose, _json(sources), mid, 'saved' if keep else 'skipped', audit_note, time.time()))
        if mid is not None:
            context.setdefault('memory_ids', []).append(mid)
        return {'memory_id': mid, 'outcome': 'saved' if keep else 'skipped', 'value_tier': tier}

    def save_initial_view(self, args, context):
        # Event admission and its derived view must succeed or roll back together.
        before = list(context.get('memory_ids', []))
        try:
            with self.source_transaction():
                return self._save_initial_view(args, context)
        except BaseException:
            context['memory_ids'] = before
            raise

    def matching_observations(self, packets, *, scope):
        """Offer identities only, under the original source/audience boundary."""
        cap = self.source_limits()['external_recall_limit']
        if cap == 0:
            return []
        rows = self.conn.execute("SELECT DISTINCT memory_id FROM learning_decisions WHERE scope=? AND source_versions=? AND outcome='saved' ORDER BY memory_id DESC",
                                 (scope,_json(source_versions(packets))))
        found = []
        for record in rows:
            mid = record['memory_id']
            row = self.get_memory(mid)
            if row is not None and row['kind']=='event' and self.observation_sources(mid,scope=scope):
                found.append({'memory_id':mid,'value_tier':row['value_tier']})
            if cap is not None and len(found) >= cap:
                break
        return found

    def reuse_observation(self, args, context):
        if context.get('worker_refs') or not self._observation_settings().get('remember_observations'):
            raise ValueError('当前不允许见闻复用登记')
        mid = args.get('memory_id')
        exposed = context.get('reusable_memory_ids',[]) + context.get('recalled_memory_ids',[])
        if type(mid) is not int or mid not in exposed:
            raise ValueError('只能复用本轮真实查得的见闻')
        scope = context['scope']
        row = self.get_memory(mid)
        sources = self.observation_sources(mid,scope=scope)
        packets = self.source_packets_for(context['packet_ids'],scope=scope)
        if row is None or row['kind']!='event' or not sources or not packets:
            raise ValueError('见闻或当前来源已失效')
        versions = source_versions(sources)
        if not set(versions) <= set(source_versions(packets)):
            raise ValueError('复用依据不在本轮有效材料中')
        identity = hashlib.sha256(_json([scope,'reuse',versions,context.get('request_id') or context.get('user_turn_id'),mid]).encode()).hexdigest()
        with self.source_transaction():
            self.conn.execute('INSERT OR IGNORE INTO learning_decisions VALUES(?,?,?,?,?,?,?,?)',
                (identity,scope,'reuse',_json(versions),mid,'saved','复用原记录，正文和期限未改',time.time()))
        context.setdefault('memory_ids',[]).append(mid)
        return {'memory_id':mid,'outcome':'reused','new_memory':False,'content_changed':False,'retention_extended':False}

    def _save_initial_view(self, args, context):
        claim = _text(args.get('claim'), 96)[:24]
        _text(args.get('interpretation'), 2400)
        old = None
        if args.get('replaces_id') is not None:
            old_id = args['replaces_id']
            if type(old_id) is not int or old_id not in context.get('recalled_memory_ids', []):
                raise ValueError('修订目标须先通过 recall 读取')
            old = self.get_memory(old_id)
            if old is None or old['kind'] != 'stance':
                raise ValueError('只能修订有效观点')
            old_sources = self.observation_sources(old_id,scope=context['scope'])
            current = self.source_packets_for(context['packet_ids'],scope=context['scope'])
            if not old_sources or not set(source_versions(current))-set(source_versions(old_sources)):
                raise ValueError('修订需要新的有效依据')
        decision = self.save_learning(args, context)
        mid = decision.get('memory_id')
        if mid is None:
            return decision
        event = self.get_memory(mid)
        if self.conn.execute("SELECT 1 FROM deliberation_seen WHERE memory_id=? AND input_version=? AND verdict='initial_view'", (mid, version(event))).fetchone():
            return {**decision, 'view_reused': True}
        claim = _text(args.get('claim'), 96)[:24]
        uncertainty = _text(args.get('uncertainty'), 1200)
        content = ('遭遇：阅读本轮获准资料\n事实：' + event['content'][:1500]
            + '\n解释：见闻——' + _text(args.get('interpretation'), 2400)
            + '\n不确定：' + uncertainty + '\n下次回应：保留为可修订的暂时判断'
            + '\n出处：' + event['title'] + '\n状态：先验')
        if old is not None:
            content = '曾以为：' + old['title'] + '\n' + content
        with self.source_transaction():
            self._check_source_space(len((claim + content).encode('utf-8')))
            view_id = self.insert_memory(title=claim, content=content, keywords='观点 见闻', kind='stance', layer='longterm')
            self.bind_derived_memory(view_id, [event], scope=context['scope'])
            self.conn.execute('UPDATE memories SET value_tier=?,value_note=? WHERE id=?', (event['value_tier'], event['value_note'], view_id))
            self.conn.execute('INSERT INTO deliberation_seen VALUES(?,?,?,?) ON CONFLICT(memory_id,input_version) DO UPDATE SET verdict=excluded.verdict,processed_at=excluded.processed_at', (mid, version(event), time.time(), 'initial_view'))
            if old is not None:
                self.conn.execute('INSERT OR IGNORE INTO view_revisions VALUES(?,?,?,?)', (old['id'],view_id,'新证据修订；具体判断见获准的新观点',time.time()))
        return {**decision, 'view_id': view_id, 'state': 'provisional'}

    def memory_maintenance_plan(self, cfg, now=None):
        from .memory_policy import plan
        from .store import text_score
        now = time.time() if now is None else now
        rows = self.conn.execute('SELECT * FROM memories ORDER BY id').fetchall()
        interests = []
        if self.conn.execute("SELECT 1 FROM sqlite_master WHERE name='discovery_interests'").fetchone():
            interests = self.conn.execute('SELECT * FROM discovery_interests WHERE enabled=1').fetchall()
        affinities = {}
        qualified = set()
        for row in rows:
            if row['origin'] == 'conversation':
                continue
            scopes = set(json.loads(row['scope']))
            if any(self.observation_sources(row['id'],scope=scope) for scope in scopes):
                qualified.add(row['id'])
            if row['value_policy'] != 1:
                continue
            for interest in interests:
                if interest['share_after'] > now or not scopes.intersection(json.loads(interest['scope_json'])):
                    continue
                confidence = 1 if interest['origin']=='manual' else interest['confidence']
                if text_score(interest['topic'],row['title']+' '+row['keywords']) >= 0.55:
                    affinities[row['id']] = max(affinities.get(row['id'],0),min(1,confidence*interest['weight']))
        deps = [(r[0],r[1]) for r in self.conn.execute('SELECT parent_memory_id,memory_id FROM memory_dependencies')]
        return plan(rows,deps,cfg,now,affinities,qualified=qualified)

    def learning_preview(self, cfg, *, now=None):
        from .memory_policy import classified, usage_score
        now = time.time() if now is None else now
        decisions = self.memory_maintenance_plan(cfg,now)
        rows = self.memory_rows(purpose='observer', scope='gui')
        rows = [r for r in rows if r['origin'] != 'conversation']
        order = {'expire':0,'dependent':0,'warehouse':1,'promote':2,'keep':3}
        rows.sort(key=lambda r:(order.get(decisions[r['id']]['action'],3),
                               {'low':0,'normal':1,'unknown':1,'high':2}.get(r['value_tier'],1),usage_score(r,now),r['id']))
        return [{'memory_id': r['id'], 'title': r['title'], 'value_tier': r['value_tier'],
                 'reason': r['value_note'], 'compression': {'low':'要点','normal':'摘要','high':'较完整'}.get(r['value_tier'],'未分级'),
                 'retention_action': decisions[r['id']]['action'],'retention_reason':decisions[r['id']]['reason'],
                 'value_policy':classified(r), 'age_days': round((now-r['created_at'])/86400,1)} for r in rows[:50]]

    def condense_observation(self, args, context):
        if context.get('worker_refs') or not self._observation_settings().get('remember_observations'):
            raise ValueError('当前不允许浓缩见闻')
        mid = args.get('memory_id')
        if type(mid) is not int or mid not in context.get('recalled_memory_ids', []):
            raise ValueError('请先通过 recall 实际读取目标见闻')
        row = self.get_memory(mid)
        sources = self.observation_sources(mid, scope=context['scope'])
        if row is None or row['kind'] != 'event' or row['value_tier'] != 'low' or not sources:
            raise ValueError('当前仅允许浓缩已分级为低价值的有效见闻；旧未分级记录保持原文')
        if not set(p['id'] for p in sources) <= set(context['packet_ids']):
            raise ValueError('浓缩依据不在本轮输入中')
        text, uncertainty = _body_fields(args.get('content'),args.get('uncertainty'))
        new_content = '见闻记录（来源说法或讨论中的暂时判断，未独立核实）：\n' + text + '\n条件与不确定：' + uncertainty
        before_bytes, after_bytes = len(row['content'].encode('utf-8')),len(new_content.encode('utf-8'))
        ratio = _condense_ratio(context.get('condense_max_ratio',0.8))
        if after_bytes > before_bytes*ratio:
            raise ValueError(f'有效浓缩应至少缩减{(1-ratio)*100:g}%；请保留关键事实、条件及范围再精练。若做不到请说明，不写入假浓缩')
        reason = _text(args.get('reason'), 1000)
        old_version = version(row)
        with self.source_transaction():
            self.conn.execute('UPDATE memories SET content=? WHERE id=?', (new_content, mid))
            new_version = version(self.get_memory(mid))
            self.conn.execute('INSERT OR IGNORE INTO memory_condensations VALUES(?,?,?,?,?)', (mid, old_version, new_version, reason, time.time()))
            # Changed input text invalidates dependent judgments for re-evaluation.
            affected = self.conn.execute("UPDATE memories SET provenance_status='needs_review' WHERE id IN (SELECT memory_id FROM memory_dependencies WHERE parent_memory_id=?)", (mid,)).rowcount
        return {'memory_id': mid, 'condensed': True, 'affected_dependents': affected,
                'dependent_views': 'needs_review' if affected else 'none',
                'before_bytes':before_bytes,'after_bytes':after_bytes,
                'reduction_percent':round((1-after_bytes/before_bytes)*100,1)}


    def pending_thought_rows(self):
        started = float(self.meta_get('learning_started_at') or 0)
        rows = self.memory_rows(purpose='thought', scope='gui', kind='event')
        result = []
        for row in rows:
            admitted = self.conn.execute('SELECT 1 FROM learning_decisions WHERE memory_id=? AND created_at>=?', (row['id'], started)).fetchone()
            if row['created_at'] < started and not admitted:
                continue
            if not self.conn.execute('SELECT 1 FROM deliberation_seen WHERE memory_id=? AND input_version=?', (row['id'], version(row))).fetchone():
                result.append(row)
        return result

    def mark_thought_rows(self, rows, verdict='processed'):
        with self.source_transaction():
            self.conn.executemany('INSERT OR IGNORE INTO deliberation_seen VALUES(?,?,?,?)',
                [(r['id'],version(r),time.time(),verdict) for r in rows])

    def learning_model_identity(self, rows):
        return hashlib.sha256(_json(sorted((r['id'],version(r)) for r in rows)).encode()).hexdigest()

    def reserve_learning_model(self, rows, *, reserve_budget=True):
        from datetime import datetime
        from .discovery_memory import DAY_ZONE
        settings = self._observation_settings()
        if not settings.get('remember_observations') or not settings.get('allow_paid'):
            return None
        identity = self.learning_model_identity(rows)
        day = datetime.now(DAY_ZONE).strftime('%Y-%m-%d')
        with self.source_transaction():
            if self.conn.execute('SELECT 1 FROM learning_model_attempts WHERE identity=?', (identity,)).fetchone():
                return None  # settled or uncertain; no invisible retry
            if reserve_budget:
                used = self.conn.execute('SELECT models,network,body_bytes FROM activity_budget WHERE day=?', (day,)).fetchone()
                maximum = settings.get('model_response_bytes', 192*1024)
                caps = [settings.get('daily_models'), settings.get('daily_network'),
                        None if settings.get('daily_response_mib') is None else settings['daily_response_mib']*1024*1024]
                if any(cap is not None and (used[i] if used else 0)+amount > cap
                       for i, (cap,amount) in enumerate(zip(caps,(1,1,maximum)))):
                    return None
                # SDK-only explicit deliberation has no byte-level receipt: keep
                # this reservation conservative rather than guessing a refund.
                self.conn.execute("INSERT INTO activity_budget(day,models,network,body_bytes) VALUES(?,1,1,?) ON CONFLICT(day) DO UPDATE SET models=models+1,network=network+1,body_bytes=body_bytes+excluded.body_bytes", (day,maximum))
            self.conn.execute("INSERT INTO learning_model_attempts VALUES(?,?,'unknown',?)", (identity,day,time.time()))
        return identity


def learning_tools(store, cfg=None):
    settings = store._observation_settings()
    tools = []
    def add(name, description, properties, required):
        tools.append({'type':'function','function':{'name':name,'description':description,
            'parameters':{'type':'object','properties':properties,'required':required,'additionalProperties':False}}})
    if settings.get('allow_chat_interests'):
        add('observe_interest', '从本轮用户自然表达自主识别兴趣，无需固定口令。topic须为可公开检索的主题，不含私人经历或身份；user_quote与reason仅在本地保存。区分转述、临时话题和持续关注、喜欢与反对。仅引用用户原话作为依据，不能用自己搜索或网页内容证明用户兴趣。',
            {'topic':{'type':'string'},'user_quote':{'type':'string'},'attitude':{'type':'string','enum':sorted(ATTITUDES)},
             'confidence':{'type':'number','minimum':0,'maximum':1},'reason':{'type':'string'}},
            ['topic','user_quote','attitude','confidence','reason'])
    if settings.get('remember_observations'):
        add('reuse_observation','重新阅读相同内容时，若检索回执existing_observations或recall给出仍有效的原见闻，可直接复用其memory_id，无需反复recall或另写摘要。此工具只确认复用，不修改正文、价值理由或期限；需要新观点或确有新增材料时另行择要保存。',
            {'memory_id':{'type':'integer'}},['memory_id'])
        fields = {'keep':{'type':'boolean'},'title':{'type':'string'},
                  'content':{'type':'string','description':'仅正文，不含见闻包装头或条件与不确定标签；条件、范围和不确定性另填uncertainty'},'keywords':{'type':'string'},
                  'value_tier':{'type':'string','enum':sorted(TIERS)},'reason':{'type':'string'},
                  'uncertainty':{'type':'string','description':'独立写关键条件、实际阅读范围及不确定性，不附加字段标签，不遗漏原条件'},
                  'reflection_hint':{'type':'string','enum':['ordinary','conflict','repeated']}}
        required = ['keep','title','content','value_tier','reason','uncertainty']
        detail = '' if cfg is None else '内容建议字数（可按必要条件调整，不机械截断）：'+_json(detail_targets(cfg))+'。'
        add('learn_from_sources','对本轮实际读到的资料自主决定保留或跳过；兴趣外重要材料也可留。低价值更精练，高价值保留细节；保留条件与不确定性。价值和可信度分别判断，热度仅参考。'+detail,fields,required)
        add('reflect_on_sources','可在用户当前讨论中立即形成并保存带来源的初步观点，不等待记忆数量阈值；允许没有观点时跳过。后续证据可验证或修订。'+detail,
            {**fields,'claim':{'type':'string'},'interpretation':{'type':'string'},'replaces_id':{'type':'integer'}},required+['claim','interpretation'])
        ratio = _condense_ratio(cfg.memory.observation_condense_max_ratio if cfg is not None else 0.8)
        add('condense_observation',f'对本轮recall读到的低价值见闻，依据同轮有效来源浓缩。计入宿主包装和独立条件后，UTF-8字节数至少缩减{(1-ratio)*100:g}%，不是略删几个字；无法保留关键事实和条件时不要强行改写。正文不含包装头或条件标签，条件与范围仅填uncertainty。保留出处和不确定性；受影响派生观点待复核。',
            {'memory_id':{'type':'integer'},'content':fields['content'],'uncertainty':fields['uncertainty'],'reason':{'type':'string'}},
            ['memory_id','content','uncertainty','reason'])
    return tools
