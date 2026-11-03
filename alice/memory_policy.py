"""One content-free plan for maintenance execution and its read-only preview."""
from __future__ import annotations


def usage_score(memory, now):
    anchor = memory['last_used_at'] or memory['created_at']
    recency = max(0.0, 3.0 - (now-anchor)/86400)
    return memory['mention_count']*2 + memory['use_count'] + recency


def classified(memory):
    return (memory['origin'] != 'conversation' and memory['lifecycle'] == 'memory'
            and memory['value_policy'] == 1 and memory['value_tier'] in {'high','normal','low'})


def plan(rows, dependencies, cfg, now, affinities=None, *, qualified=None):
    """No writes or model calls; deadlines remain hard upper bounds.

    Legacy/unclassified records retain their seven-day admission rules. A live
    descendant lends its priority to its required ancestors, preventing a low
    priority event from silently taking a high priority view with it.
    """
    affinities = affinities or {}
    if type(cfg.context.longterm_capacity) is not int or cfg.context.longterm_capacity < 0:
        raise ValueError('长期记忆容量须为非负整数')
    if type(cfg.memory.observation_value_review) is not bool:
        raise ValueError('见闻价值复核须为布尔值')
    by_id = {r['id']:r for r in rows}
    children = {}
    for parent, child in dependencies:
        if parent in by_id and child in by_id:
            children.setdefault(parent,set()).add(child)
    alive = set(by_id)
    layers = {mid:r['layer'] for mid,r in by_id.items()}
    decisions = {mid:{'memory_id':mid,'action':'keep','reason':'保留当前记录'} for mid in alive}

    def family(mid):
        found, pending = set(), [mid]
        while pending:
            item = pending.pop()
            if item not in alive or item in found:
                continue
            found.add(item)
            pending.extend(children.get(item,()))
        return found

    def live(r):
        return (r['provenance_status'] == 'valid' and (r['expires_at'] is None or r['expires_at'] > now)
                and (r['origin']=='conversation' or qualified is None or r['id'] in qualified))

    def priority(mid):
        r = by_id[mid]
        tier = {'high':2,'normal':1,'low':0}[r['value_tier']] if classified(r) and live(r) else 1
        if r['origin'] == 'conversation' and r['salience'] >= 1 and not r['pressure']:
            tier = 3  # Explicitly important user memories retain their protection.
        return tier, usage_score(r,now)+affinities.get(mid,0)

    def protected_priority(mid):
        return max(priority(item) for item in family(mid))

    def expire(mid, reason, phase):
        for item in family(mid):
            decisions[item] = {'memory_id':item,'action':'expire' if item == mid else 'dependent',
                               'reason':reason if item == mid else '价值维护：必要依据已遗忘',
                               'phase':phase,'root_id':mid}
            alive.discard(item)

    active_cutoff = now-cfg.context.active_days*86400
    survival_cutoff = now-cfg.context.survival_days*86400
    for mid in sorted(by_id):
        r = by_id[mid]
        if (mid in alive and r['origin'] != 'conversation' and r['expires_at'] is not None
                and r['expires_at'] <= now):
            expire(mid,'价值维护：来源或见闻的硬期限已到','source')
    for mid in sorted(by_id):
        if mid not in alive:
            continue
        r = by_id[mid]
        if r['layer'] == 'active' and r['created_at'] < active_cutoff:
            layers[mid] = 'warehouse'
            decisions[mid] = {'memory_id':mid,'action':'warehouse','reason':'进入仓库等待复核'}

    # Evaluate valuable descendants before their ancestors. A dependency is kept
    # only while that descendant itself survives the same review.
    order = sorted(alive,key=lambda mid:(protected_priority(mid),by_id[mid]['created_at']),reverse=True)
    for mid in order:
        if mid not in alive:
            continue
        r = by_id[mid]
        if layers[mid] != 'warehouse' or r['created_at'] >= survival_cutoff:
            continue
        uses = r['mention_count']+r['use_count']
        used = uses >= cfg.context.survival_min_uses
        explicit = r['salience'] >= 1 and not r['pressure']
        valuable = classified(r) and live(r) and r['value_tier'] == 'high' and not r['pressure']
        required = any(classified(by_id[item]) and live(by_id[item])
                       and by_id[item]['value_tier']=='high' and not by_id[item]['pressure']
                       for item in family(mid)-{mid})
        value_keep = cfg.memory.observation_value_review and (valuable or required)
        if used or explicit or value_keep:
            layers[mid] = 'longterm'
            why = ('高价值见闻通过复核' if valuable else '保留高价值观点的必要依据') if value_keep else '实际使用或重要标记通过复核'
            decisions[mid] = {'memory_id':mid,'action':'promote','reason':'价值维护：'+why,'phase':'review'}
        else:
            label = r['value_tier'] if classified(r) else '未分级，沿用原规则'
            expire(mid,'价值维护：复核未获保留（'+label+'；提及与使用不足）','review')

    def longterms():
        return {mid for mid in alive if layers[mid] == 'longterm'}

    while len(longterms()) > cfg.context.longterm_capacity:
        candidates = longterms()
        # Prefer a leaf at equal priority, avoiding an unnecessary cascade.
        mid = min(candidates,key=lambda item:(protected_priority(item),len(family(item)),
                                               by_id[item]['created_at'],item))
        label = by_id[mid]['value_tier'] if classified(by_id[mid]) else '未分级'
        expire(mid,'价值维护：长期层容量不足，按价值与实际使用淘汰（'+label+'）','capacity')
    return decisions
