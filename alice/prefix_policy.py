"""Unified modular constitution, with request-local host state.

legacy/scoped configuration values are retained for compatibility. Both use
one five-module source; no old constitution or interaction rules are appended.
"""
from __future__ import annotations

import hashlib
from . import constitution

VERSION = constitution.VERSION
# Compatibility names: text has one authority in constitution.json.
MINIMAL_CONSTITUTION = constitution.template()
INTERACTION_DEFAULTS = ""
FIXED_CAPABILITY_FACTS = ""
SPECIAL_RULES = ()
_CHANNEL_LABELS = {"gui": "本机网页", "qq-owner": "已绑定用户的QQ私聊"}


class PrefixPlan:
    def __init__(self, *, mode_requested, blocks, activated=(), inactive=(), fallback_reason=None):
        self.mode_requested = mode_requested
        self.mode_effective = "modular"
        self.blocks = blocks
        self.activated = list(activated)
        self.inactive = list(inactive)
        self.fallback_reason = fallback_reason

    def system_prefix(self):
        return "\n\n".join(b["content"] for b in self.blocks)

    def metadata(self):
        content = self.system_prefix()
        return {"version": VERSION, "mode_requested": self.mode_requested,
                "mode_effective": self.mode_effective, "fallback_reason": self.fallback_reason,
                "sha256": hashlib.sha256(content.encode()).hexdigest(), "chars": len(content),
                "constitution": constitution.metadata(),
                "blocks": [{"kind": b["kind"], "name": b["name"], "chars": len(b["content"])}
                           for b in self.blocks],
                "activated_rules": self.activated, "inactive_rules": self.inactive}


def interaction_metadata():
    return {**constitution.metadata(), "source": "constitution.json:3"}


def build_prefix_plan(cfg, *, name, scope, tool_names=(), worker_tool_names=(),
                      learning_tools=False, memory_mode="direct", has_registry=False,
                      source_packet_count=0, continued_count=0, worker_ref_count=0,
                      external_recall_allowed=False, source_dependent=False):
    blocks = constitution.blocks(name)
    state = {"channel": _CHANNEL_LABELS.get(scope, scope),
             "memory_mode": memory_mode, "persona_index_available": has_registry,
             "initial_source_count": source_packet_count,
             "continued_source_count": continued_count,
             "worker_ref_count": worker_ref_count}
    import json
    blocks.append({"kind": "宿主状态", "name": "capability_current",
                   "content": "## 本轮运行状态\n" + json.dumps(state, ensure_ascii=False, separators=(",", ":"))})
    if source_dependent:
        blocks.append({"kind": "宿主状态", "name": "source_dependency",
                       "content": "本轮资料按宿主标明的渠道、来源和任务范围使用；需要对外操作时由能力层复核其授权。"})
    return PrefixPlan(mode_requested=cfg.context.prefix_policy_mode, blocks=blocks)
