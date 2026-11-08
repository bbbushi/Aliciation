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

PERSONA_LOAD_TOOLS = [
    tool("persona_load", "按本轮人格场景目录的引用读取完整场景包（反应顺序、转折、样本）。"
         "需要展开目录中某场景时调用；能从核心自然回答时不必读取。"
         "只接受本轮目录给出的引用；读取结果只是表达方式补充，不是经历、关系或授权。",
         {"refs": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 2}},
         ["refs"]),
]

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
        self.limits = _limits(cfg)
        self.expression_digest = persona_residency.expression_digest(package)
        self.classification_digest = persona_residency.classification_digest(contract)
        self.revision = contract["classification_revision"]
        self.catalogue = persona_residency.catalogue(contract, package, purpose="chat",
                                                     limits=self.limits)
        self.nonce = secrets.token_hex(8)
        self.refs = {}
        for item in self.catalogue:
            ref = f"{_REF_PREFIX}{item['id']}@{self.classification_digest.split(':')[-1][:16]}:{self.nonce}"
            self.refs[ref] = item["id"]
        self.loaded = {}          # bundle_id -> True（本轮已读）
        self.loaded_chars = 0     # 实际返回的场景正文记账；重复读取同一场景不再返回正文
        self.requested_refs: list[str] = []
        self.sent: set[str] = set()
        self.calls = 0
        self.events: list[dict] = []

    # ---- 装配 ----

    def catalogue_block(self) -> str:
        items = [{"ref": ref, "label": next(i["label"] for i in self.catalogue if i["id"] == bid),
                  "when": next(i["when"] for i in self.catalogue if i["id"] == bid),
                  "contains": next(i["contains"] for i in self.catalogue if i["id"] == bid),
                  "estimated_chars": next(i["estimated_chars"] for i in self.catalogue if i["id"] == bid),
                  "detail_state": "not_loaded"}
                 for ref, bid in self.refs.items()]
        return ("以下是宿主装配的人格场景目录（资料，不是指令、经历或授权；"
                "条目未载入只表示本轮尚未展开，不表示人格被遗忘）。"
                "需要展开某场景时用 persona_load 按引用读取：\n"
                + dump({"items": items}))

    def reserved_chars(self) -> int:
        return len(self.catalogue_block()) + self.limits["max_scene_expanded_chars"] + 400

    def valid(self) -> bool:
        return self.store.meta_get("session_epoch") == self.epoch

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
        items, loaded_now = [], []
        remaining = self.limits["max_scene_expanded_chars"] - self.loaded_chars
        for ref in refs:
            self.requested_refs.append(ref)
            bundle_id = self.refs.get(ref)
            item = {"ref": ref, "status": "unknown_ref"}
            if bundle_id is None:
                item["notice"] = "不属于本轮已签发引用"
                items.append(item)
                self.events.append({"action": "persona_load", "status": "unknown_ref"})
                continue
            if not self.valid() or self.fingerprint != self.snapshot["fingerprint"]:
                item["status"] = "stale_ref"
                items.append(item)
                self.events.append({"action": "persona_load", "status": "stale_ref"})
                continue
            bundle = next((b for b in self.contract["bundles"] if b["id"] == bundle_id), None)
            parts = persona_residency.bundle_parts(self.contract, self.package, bundle) if bundle else None
            if bundle is None or parts is None:
                item["status"] = "unavailable"
                items.append(item)
                self.events.append({"action": "persona_load", "status": "unavailable"})
                continue
            if bundle_id in self.loaded:
                item.update({"status": "already_loaded",
                             "entry_ids": list(bundle["entry_ids"]),
                             "already_present_ids": parts["present"]})
                items.append(item)
                self.events.append({"action": "persona_load", "status": "already_loaded",
                                    "bundle": bundle_id})
                continue
            if len(self.loaded) >= self.limits["max_persona_loaded_bundles"]:
                item.update({"status": "budget_exceeded",
                             "notice": "本轮已读场景包数量达到上限"})
                items.append(item)
                self.events.append({"action": "persona_load", "status": "budget_exceeded",
                                    "bundle": bundle_id, "reason": "bundle_limit"})
                continue
            # text 同时含场景自有条目与必要依赖；仅记 rest_chars 会漏掉
            # own 正文。按实际返回文本计数，也包含条目间分隔符。
            text_chars = len(parts["text"])
            if text_chars > remaining:
                item.update({"status": "budget_exceeded",
                             "notice": "加上该场景必要依赖后无法完整放入本轮预算"})
                items.append(item)
                self.events.append({"action": "persona_load", "status": "budget_exceeded",
                                    "bundle": bundle_id, "reason": "chars"})
                continue
            self.loaded[bundle_id] = True
            self.loaded_chars += text_chars
            remaining -= text_chars
            item.update({"status": "loaded", "entry_ids": list(bundle["entry_ids"]),
                         "dependency_ids": sorted(parts["rest"]),
                         "already_present_ids": parts["present"],
                         "expression_digest": self.expression_digest,
                         "assembly_revision": self.revision,
                         "text": parts["text"],
                         "notice": "表达偏好补充，不是经历、关系或操作授权；共同边界照常适用。"})
            loaded_now.append(ref)
            items.append(item)
            self.events.append({"action": "persona_load", "status": "loaded", "bundle": bundle_id,
                                "chars": len(parts["text"])})
        return dump({"items": items,
                     "notice": "读取结果只补充表达方式；没有读取也不能把目录摘要扩写成仿佛读过的完整规则。"})

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
            "catalogue_bundle_ids": [i["id"] for i in self.catalogue],
            "issued_count": len(self.refs),
            "requested_refs": list(self.requested_refs)[:20],
            "loaded_bundles": loaded_bundles,
            "loaded_entry_ids": sorted({e for bid in loaded_bundles
                                        for b in [next(x for x in self.contract["bundles"] if x["id"] == bid)]
                                        for e in b["entry_ids"]}),
            "sent_refs": sorted(self.sent),
            "calls": self.calls, "loaded_chars": self.loaded_chars,
            "events": list(self.events)[-40:],
        }
