"""分层维护：活跃→仓库→（晋升长期 | 真实过期），以及长期层容量清理。"""
from __future__ import annotations

import time

from .config import Config
from .store import Store


def run(store: Store, cfg: Config, now: float | None = None, *, decay: bool = True) -> dict:
    now = now if now is not None else time.time()
    # Hard source deadlines and revocations always precede value maintenance.
    store.purge_source_packets(now)
    if decay:
        # 兴趣衰减只在兴趣表存在时执行（表由 serve 侧协调器建；裸库路径无兴趣可衰减）
        store.decay_interests(cfg.memory.interest_decay_days)
    decisions = store.memory_maintenance_plan(cfg,now)
    to_warehouse, promoted, expired, evicted = 0, [], [], []
    for decision in decisions.values():
        row = store.get_memory(decision['memory_id'])
        if row is None:
            continue  # A previous required ancestor already removed this row.
        action = decision['action']
        title = row['title'] if row['origin']=='conversation' else '带来源见闻'
        if action == 'warehouse':
            store.set_layer(row['id'],'warehouse')
            to_warehouse += 1
        elif action == 'promote':
            store.set_layer(row['id'],'longterm')
            promoted.append(title)
        elif action in {'expire','dependent'}:
            store.expire_memory(row['id'],decision['reason'])
            (evicted if decision.get('phase')=='capacity' else expired).append(title)
    store.meta_set('last_consolidate_at',now)
    return {'value_preview':store.learning_preview(cfg,now=now),
            'to_warehouse':to_warehouse,'promoted':promoted,'expired':expired,'evicted':evicted}


def run_if_due(store: Store, cfg: Config, now: float | None = None) -> dict | None:
    now = now if now is not None else time.time()
    if now - store.meta_get_time("last_consolidate_at") < 3600:
        return None
    return run(store, cfg, now)
