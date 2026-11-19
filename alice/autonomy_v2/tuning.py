"""Tuning comes from the existing thoughts settings; no writable weights."""
from __future__ import annotations

from .models import ActivityMode, ModePolicy, Tuning

_SAME_MATERIAL = ("same_material_windows", 2)
_INVITE_WAIT = ("invitation_wait_seconds", 600)
_TOPIC_RETRY = ("same_topic_retry_seconds", 86400)


def _setting(config, pair) -> int:
    key, default = pair
    value = config.get(key, default)
    return value if isinstance(value, int) and 0 <= value <= 86400 else default


def tuning_from_settings(config_or_store) -> Tuning:
    config = config_or_store
    if not isinstance(config, dict):
        from .. import thought_workspace as thoughts
        config = thoughts.settings(config_or_store)
    from .. import activity, autonomy_policy
    if autonomy_policy.TEST_CONTROLS_ONLY:
        config = {**config, "invitation_wait_seconds": 600, "same_topic_retry_seconds": 0}
    activities = config.get("activities") or {}
    modes = []
    for mode in ("interest", "problem", "iteration", "idle"):
        if mode == "idle":
            modes.append(ModePolicy(mode=ActivityMode.IDLE, seconds=activity.fixed_idle_seconds(config), tool_groups=()))
            continue
        item = activities.get(mode) or {}
        modes.append(ModePolicy(mode=ActivityMode(mode), seconds=int(item.get("seconds", 0) or 0),
                                tool_groups=tuple(item.get("tools", ()))))
    return Tuning(modes=tuple(modes),
                  same_material_windows=max(1, min(_setting(config, _SAME_MATERIAL), 20)),
                  invitation_wait_seconds=_setting(config, _INVITE_WAIT),
                  same_topic_retry_seconds=_setting(config, _TOPIC_RETRY))
