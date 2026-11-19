"""One shared idle execution slot and daily model reservation ledger."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
import threading

_local = threading.local()


class BackgroundRuntime:
    def __init__(self):
        self._slot = threading.Lock()

    @contextmanager
    def claim(self, owner):
        acquired = self._slot.acquire(blocking=False)
        previous = getattr(_local, "owner", None)
        if acquired:
            _local.owner = owner
        try:
            yield acquired
        finally:
            if acquired:
                _local.owner = previous
                self._slot.release()


def day():
    from .discovery_memory import DAY_ZONE
    return datetime.now(DAY_ZONE).strftime("%Y-%m-%d")


def reserve_model(store, *, force=False):
    """Caller holds Store lock. Reservations remain spent if outcome is unknown.

    Legacy behaviour is unchanged until exploration model use is authorized;
    then old and new background model calls share this extra ceiling.
    """
    from .thought_workspace import settings
    config = settings(store)
    if not force and (not getattr(_local, "owner", None) or not config["allow_paid"]):
        return
    with store.source_transaction():
        current_day = day()
        row = store.conn.execute("SELECT models FROM background_usage WHERE day=?", (current_day,)).fetchone()
        from .autonomy_policy import TEST_CONTROLS_ONLY
        if not (TEST_CONTROLS_ONLY and getattr(_local, "owner", None) == "autonomy") and (row[0] if row else 0) >= config["daily_models"]:
            raise ValueError("background_model_budget_exhausted")
        store.conn.execute("INSERT INTO background_usage(day,models) VALUES(?,1) "
                           "ON CONFLICT(day) DO UPDATE SET models=models+1", (current_day,))


def in_background():
    return bool(getattr(_local, "owner", None))
