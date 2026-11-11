"""R2：请求内人格注册表与 persona_load 读取工具（注册表设计 §4/§5/§7）。

目录、nonce、签发引用与已读集合归属于一个用户 request_id；模型传回的字符串
只在本轮签发映射中查找，不通过 ID 拼路径，不接受未签发引用。读取结果作为
工具回执进入同一工具循环；不写人格，不延长工具循环上限。
"""
from __future__ import annotations

import json
import secrets

from . import persona_residency
from .context_memory import dump, tool
from .context import estimate_tokens

from .capability_registry import sdk_group

PERSONA_LOAD_TOOLS = sdk_group("persona")

_REF_PREFIX = "persona:"


def _limits(cfg) -> dict:
    expression = cfg.expression
    return {"max_core_chars": expression.max_core_chars,
            "max_registry_chars": expression.max_registry_chars,
            "max_registry_bundles": expression.max_registry_bundles,
            "max_scene_expanded_chars": expression.max_scene_expanded_chars,
            "max_persona_load_calls": expression.max_persona_load_calls,
            "max_persona_loaded_bundles": expression.max_persona_loaded_bundles}


class PersonaRegistryContext:
    """一次用户请求的人格注册表：目录、签发引用、读取与送入集合。"""

    def __init__(self, store, cfg, snapshot: dict, contract: dict, package: dict, *, scope: str):
        self.store = store
        self.cfg = cfg
        self.snapshot = snapshot
        self.contract = contract
        self.package = package
        self.scope = scope
        self.epoch = store.meta_get("session_epoch")
        self.fingerprint = snapshot["fingerprint"]
        self.limits = persona_residency.effective_limits(contract, _limits(cfg))
        self.expression_digest = persona_residency.expression_digest(package)
        self.classification_digest = persona_residency.classification_digest(contract)
        self.revision = contract["classification_revision"]
        self.catalogue = persona_residency.catalogue(contract, package, purpose="chat",
                                                     limits=self.limits)
        self.nonce = secrets.token_hex(8)
        self._catalogue_block, self.refs = persona_residency.catalogue_block(
            self.catalogue, contract, self.nonce)
        if len(self._catalogue_block) > self.limits["max_registry_chars"]:
            raise ValueError("人格目录超过完整注册表预算")
        self.core_ids, _ = persona_residency.core_closure(contract, persona_residency._by_id(package))
        self.loaded_ids: set[str] = set()
        self.receipt_chars = 0
        self.remaining_tokens = None
        self.loaded = {}          # bundle_id -> True（本轮已读）
        self.loaded_chars = 0     # 去重后正文的运行记账
        self.requested_refs: list[str] = []
        self.sent: set[str] = set()
        self.calls = 0
        self.events: list[dict] = []

    # ---- 装配 ----

    def catalogue_block(self) -> str:
        return self._catalogue_block

    def reserved_chars(self) -> int:
        # Failed/status receipts have bounded room independent of successful expansion.
        return len(self.catalogue_block()) + self.limits["max_scene_expanded_chars"] + 1024 * self.limits["max_persona_load_calls"]

    def valid(self) -> bool:
        if self.store.meta_get("session_epoch") != self.epoch:
            return False
        from .persona_context import load_persona_snapshot
        try:
            current = load_persona_snapshot()
            return bool(current and current.get("assembly") and
                        persona_residency.expression_digest(current.get("expression_package")) == self.expression_digest and
                        persona_residency.classification_digest(current["assembly"]) == self.classification_digest)
        except (ValueError, OSError, TypeError):
            return False

    # ---- persona_load ----

    def execute(self, name: str, args) -> str:
        if name != "persona_load" or not isinstance(args, dict):
            return dump({"error": "invalid_arguments"})
        if self.calls >= self.limits["max_persona_load_calls"]:
            return dump({"error": "call_limit",
                         "notice": "人格读取额度已用完；可凭核心直接回答，不执行额外展开。"})
        refs = args.get("refs")
        if (set(args) != {"refs"} or not isinstance(refs, list) or not 1 <= len(refs) <= 2
                or any(not isinstance(r, str) or not r.startswith(_REF_PREFIX) or len(r) > 200
                       for r in refs)):
            return dump({"error": "invalid_arguments"})
        self.calls += 1
        items = []
        pending = dict(self.loaded)
        pending_ids = set(self.loaded_ids)
        remaining = self.limits["max_scene_expanded_chars"] - self.receipt_chars
        def reply(rows):
            return persona_residency.load_reply(rows)
        def fits(rows, ids):
            text = reply(rows)
            cap = min(self.package.get("limits", {}).get("max_projection_chars", 6000),
                      self.cfg.expression.max_projection_chars)
            body = persona_residency._ordered_text(self.package, ids)
            return (len(text) <= remaining and
                    (self.remaining_tokens is None or estimate_tokens(text) <= self.remaining_tokens) and
                    len(persona_residency.core_text(self.contract, self.package)) +
                    len(self.catalogue_block()) + len(body) <= cap)
        current_valid = self.valid()
        for index, ref in enumerate(refs):
            self.requested_refs.append(ref)
            bundle_id = self.refs.get(ref)
            item = {"ref": ref, "status": "unknown_ref"}
            bundle = next((b for b in self.contract["bundles"] if b["id"] == bundle_id), None)
            if bundle_id is not None:
                if not current_valid:
                    item["status"] = "stale_ref"
                elif bundle is None:
                    item["status"] = "unavailable"
                elif bundle_id in pending:
                    item["status"] = "already_loaded"
                elif len(pending) >= self.limits["max_persona_loaded_bundles"]:
                    item["status"] = "budget_exceeded"
                else:
                    parts = persona_residency.bundle_parts(
                        self.contract, self.package, bundle, already_present=pending_ids)
                    if parts is None:
                        item["status"] = "unavailable"
                    else:
                        item = persona_residency.loaded_item(
                            ref, bundle, parts, self.contract, self.package)
                        next_ids = pending_ids | parts["new_ids"]
                        # Reserve statuses for the remainder before committing a successful item.
                        tail = [{"ref": r, "status": "budget_exceeded"} for r in refs[index + 1:]]
                        if fits([*items, item, *tail], next_ids):
                            pending[bundle_id] = True
                            pending_ids = next_ids
                        else:
                            item = {"ref": ref, "status": "budget_exceeded"}
            items.append(item)
        result = reply(items)
        if any(item["status"] == "loaded" for item in items):
            self.loaded = pending
            self.loaded_ids = pending_ids
            self.receipt_chars += len(result)
            self.loaded_chars = len(persona_residency._ordered_text(self.package, self.loaded_ids))
        else:
            # Status-only responses are bounded separately, never charged as loaded text.
            self.receipt_chars += len(result)
        for item in items:
            self.events.append({"action": "persona_load", "status": item["status"],
                                "bundle": self.refs.get(item["ref"]),
                                "deduplicated_count": len(item.get("already_present_ids", [])),
                                "chars": len(item.get("text", ""))})
        return result

    def note_sent(self) -> None:
        """模型请求实际携带后标记（对应 memory 的 sent 集合；观测用）。"""
        self.sent |= {ref for ref, bid in self.refs.items() if bid in self.loaded}

    def metadata(self) -> dict:
        loaded_bundles = [bid for bid in self.loaded]
        return {
            "mode_requested": self.cfg.persona.expression_mode,
            "mode_effective": "registry",
            "fallback_reason": None,
            "expression_digest": self.expression_digest,
            "classification_digest": self.classification_digest,
            "assembly_revision": self.revision,
            "contract_format": self.contract["format"],
            "projection_version": persona_residency.PROJECTION_VERSION,
            "core_entry_ids": sorted(self.core_ids),
            "core_chars": len(persona_residency.core_text(self.contract, self.package)),
            "catalogue_chars": len(self.catalogue_block()),
            "ref_bindings": dict(self.refs),
            "cold_bundle_ids": [i["id"] for i in self.catalogue if i["id"] not in self.loaded],
            "catalogue_bundle_ids": [i["id"] for i in self.catalogue],
            "issued_count": len(self.refs),
            "requested_refs": list(self.requested_refs)[:20],
            "loaded_bundles": loaded_bundles,
            "loaded_entry_ids": sorted(self.loaded_ids),
            "sent_refs": sorted(self.sent),
            "calls": self.calls, "loaded_chars": self.loaded_chars, "receipt_chars": self.receipt_chars,
            "deduplicated_entry_count": sum(event.get("deduplicated_count", 0) for event in self.events),
            "effective_limits": dict(self.limits),
            "events": list(self.events)[-40:],
        }


class MaterialRegistryContext:
    """Source-backed registry using the same planner as admission and preview."""

    def __init__(self, store, cfg, snapshot, *, scope):
        from . import persona_material as pm
        if scope != "gui":
            raise ValueError("原文注册表只用于本机网页聊天")
        self.store, self.cfg, self.snapshot = store, cfg, snapshot
        self.material, self.scope = snapshot["material"], scope
        self.epoch = store.meta_get("session_epoch")
        self.nonce = secrets.token_hex(8)
        self.plan = pm.projection_plan(self.material, cfg, nonce=self.nonce)
        if "registry" not in self.plan["supported_modes"]:
            raise ValueError("原文按需装配不可用：" + "；".join(self.plan["mode_issues"]["registry"]))
        self.limits, self.digest = self.plan["limits"], pm.material_digest(self.material)
        self.material = self.plan["material"]
        self.revision = self.material["revision"]
        self.catalogue = self.plan["catalogue"]
        self._catalogue_block, self.refs = pm.catalogue_block(self.catalogue, self.nonce) if self.catalogue else ("", {})
        self.core_ids = set(self.plan["core_ids"])
        self.loaded, self.loaded_block_ids = {}, set()
        self.receipt_chars = self.loaded_chars = self.calls = 0
        self.remaining_tokens = None
        self.requested_refs, self.sent, self.events = [], set(), []

    def catalogue_block(self):
        return self._catalogue_block

    def reserved_chars(self):
        return len(self._catalogue_block) + (self.limits["max_scene_expanded_chars"]
               + 1024 * self.limits["max_persona_load_calls"] if self.refs else 0)

    def valid(self):
        from .persona_context import load_persona_snapshot
        from . import persona_material as pm
        if self.store.meta_get("session_epoch") != self.epoch:
            return False
        try:
            current = load_persona_snapshot()
            return bool(current and current.get("format_version") == pm.FORMAT
                        and pm.material_digest(current["material"]) == self.digest
                        and pm.effective_limits(self.material, self.cfg) == self.limits)
        except (ValueError, OSError, TypeError):
            return False

    def execute(self, name, args):
        from . import persona_material as pm
        if (name != "persona_load" or not isinstance(args, dict) or set(args) != {"refs"}
                or not isinstance(args["refs"], list) or not 1 <= len(args["refs"]) <= 2
                or any(not isinstance(r, str) or not 1 <= len(r) <= 200 for r in args["refs"])):
            return dump({"error": "invalid_arguments"})
        if self.calls >= self.limits["max_persona_load_calls"]:
            return dump({"error": "call_limit"})
        self.calls += 1
        items, pending, loaded = [], dict(self.loaded), set(self.loaded_block_ids)
        present = self.core_ids | loaded
        known = {b["bundle_id"]: b for b in self.catalogue}
        valid = self.valid()
        for index, ref in enumerate(args["refs"]):
            self.requested_refs.append(ref)
            bid = self.refs.get(ref)
            item = {"ref": ref, "status": "unknown_ref"}
            if bid is not None:
                if not valid:
                    item["status"] = "stale_ref"
                elif bid in pending:
                    item["status"] = "already_loaded"
                elif len(pending) >= self.limits["max_persona_loaded_bundles"]:
                    item["status"] = "budget_exceeded"
                elif bid not in known:
                    item["status"] = "unavailable"
                else:
                    candidate = pm.load_item(self.material, known[bid], ref, present)
                    tail = [{"ref": r, "status": "budget_exceeded"} for r in args["refs"][index + 1:]]
                    receipt = pm.load_reply([*items, candidate, *tail])
                    chars = self.receipt_chars + len(receipt)
                    fits = (chars <= self.limits["max_scene_expanded_chars"]
                            and len(self.plan["core_text"]) + len(self._catalogue_block) + chars <= self.limits["max_projection_chars"]
                            and (self.remaining_tokens is None or estimate_tokens(receipt) <= self.remaining_tokens))
                    if fits:
                        item = candidate
                        pending[bid] = True
                        loaded.update(set(candidate["block_ids"]) - self.core_ids)
                        present.update(candidate["block_ids"])
                    else:
                        item["status"] = "budget_exceeded"
            items.append(item)
        result = pm.load_reply(items)
        self.loaded, self.loaded_block_ids = pending, loaded
        self.receipt_chars += len(result)
        self.loaded_chars = len(pm.ordered_text(self.material, loaded))
        self.events.extend({"action": "persona_load", "status": i["status"], "bundle": self.refs.get(i["ref"]),
                            "deduplicated_count": len(i.get("already_present_ids", []))} for i in items)
        return result

    def note_sent(self):
        self.sent |= {ref for ref, bid in self.refs.items() if bid in self.loaded}

    def metadata(self):
        return {"mode_requested": self.cfg.persona.expression_mode, "mode_effective": "registry",
                "fallback_reason": None, "format_version": "persona-material-v1", "material_digest": self.digest,
                "material_revision": self.revision, "source_sha256": self.material["source_sha256"],
                "projection_strategy": "material_core_registry", "core_chars": self.plan["core_chars"],
                "assembly_version": self.plan["assembly_version"],
                "interpretation": self.plan["interpretation"], "core_demotions": self.plan["core_demotions"],
                "coverage": self.plan["coverage"], "unreachable_block_ids": self.plan["unreachable_block_ids"],
                "core_block_ids": sorted(self.core_ids), "catalogue_chars": len(self._catalogue_block),
                "catalogue_bundle_ids": [b["bundle_id"] for b in self.catalogue],
                "excluded_bundles": self.plan["excluded_bundles"], "ref_bindings": dict(self.refs),
                "cold_bundle_ids": [b["bundle_id"] for b in self.catalogue if b["bundle_id"] not in self.loaded],
                "issued_count": len(self.refs), "requested_refs": self.requested_refs[:20],
                "loaded_bundles": list(self.loaded), "loaded_block_ids": sorted(self.loaded_block_ids),
                "sent_refs": sorted(self.sent), "calls": self.calls, "loaded_chars": self.loaded_chars,
                "receipt_chars": self.receipt_chars, "effective_limits": dict(self.limits), "events": self.events[-40:]}
