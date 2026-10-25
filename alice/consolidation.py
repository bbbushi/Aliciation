"""分层维护：活跃→仓库→（晋升长期 | 真实过期），以及长期层容量清理。"""
from __future__ import annotations

import time

from .config import Config
from .store import Store, memory_score


def run(store: Store, cfg: Config, now: float | None = None) -> dict:
    now = now if now is not None else time.time()
    active_cutoff = now - cfg.context.active_days * 86400
    survival_cutoff = now - cfg.context.survival_days * 86400

    to_warehouse = 0
    for m in store.memories_in_layer("active"):
        if m["created_at"] < active_cutoff:
            store.set_layer(m["id"], "warehouse")
            to_warehouse += 1

    promoted: list[str] = []
    expired: list[str] = []
    for m in store.memories_in_layer("warehouse"):
        if m["created_at"] >= survival_cutoff:
            continue  # 仓库宽限期内，等下一次跨线
        uses = m["mention_count"] + m["use_count"]
        if m["salience"] >= 1 or uses >= cfg.context.survival_min_uses:
            store.set_layer(m["id"], "longterm")
            promoted.append(m["title"])
        else:
            store.expire_memory(
                m["id"], f"七天线未存活（提及+使用 {uses} 次，重要度 {m['salience']}）"
            )
            expired.append(m["title"])

    evicted: list[str] = []
    longterms = store.memories_in_layer("longterm")
    overflow = len(longterms) - cfg.context.longterm_capacity
    if overflow > 0:
        ranked = sorted(longterms, key=lambda m: memory_score(m, now))
        for m in ranked[:overflow]:
            store.expire_memory(m["id"], "长期层超容量，按权重淘汰")
            evicted.append(m["title"])

    store.meta_set("last_consolidate_at", now)
    return {
        "to_warehouse": to_warehouse,
        "promoted": promoted,
        "expired": expired,
        "evicted": evicted,
    }


def run_if_due(store: Store, cfg: Config, now: float | None = None) -> dict | None:
    now = now if now is not None else time.time()
    if now - store.meta_get_time("last_consolidate_at") < 3600:
        return None
    return run(store, cfg, now)
