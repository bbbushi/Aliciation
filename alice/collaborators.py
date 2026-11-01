"""协作者名册：三态分离、有效能力视图与 adapter 固定映射（契约 §三）。

安装描述在账本（worker_registrations）；本模块是它的**视图与守门层**：
模型名单、工具挂载、实际派工三处读同一个 effective 视图，每次派工重新核验。
adapter_id → 启动入口的映射是核心内固定常量（可审计、模型不可影响）；
manifest/注册项永不含可执行命令与凭据。
"""
from __future__ import annotations

import sys
from pathlib import Path

from .worker_tasks import WorkerLedgerError

REPO_ROOT = Path(__file__).resolve().parents[1]

# 固定 adapter 映射：adapter_id → 已审核 addon 启动入口（相对仓库根的脚本 + 说明）。
# 新增真实 adapter 时在此登记（经人工注册验收后），禁止把映射暴露给模型或放 manifest。
ADAPTER_ENTRYPOINTS: dict[str, dict] = {
    "testbed_v1": {
        "script": "addons/testbed_worker/worker.py",
        "note": "零成本机制验证 worker：回显任务书、产出一个文件；无网络无模型。",
    },
    # "zcode_local_v1": 待 D0 核验通过后登记（开发方案 §14 唯一技术门槛）。
}

PROTOCOL_VERSION = 1


def resolve_argv(adapter_id: str) -> list[str]:
    """固定 argv：[当前解释器, 仓库内脚本]。不拼接 shell、不接受外部提供的命令。"""
    spec = ADAPTER_ENTRYPOINTS.get(adapter_id)
    if spec is None:
        raise WorkerLedgerError("unknown_adapter", f"adapter 未登记：{adapter_id}")
    script = (REPO_ROOT / spec["script"]).resolve()
    if not script.is_file():
        raise WorkerLedgerError("adapter_missing", f"adapter 脚本缺失：{spec['script']}")
    return [sys.executable, str(script)]


def check_dispatch(store, *, name: str, scope: str, now: float | None = None) -> dict:
    """派工守门：三态核验，失败码即对 Alice 的话术（都 ≠ 没找到）。"""
    entry = store.worker_entry(name)
    if entry is None:
        raise WorkerLedgerError("unknown_worker", f"名册中没有 {name}")
    if scope not in entry["channels"]:
        raise WorkerLedgerError("channel_forbidden", f"{name} 不对渠道 {scope} 开放")
    if not entry["enabled"]:
        raise WorkerLedgerError("worker_disabled", f"{name} 已被用户停用")
    if entry["verified_at"] is None:
        raise WorkerLedgerError("worker_unverified", f"{name} 尚未通过人工注册验收")
    if entry["protocol_version"] != PROTOCOL_VERSION:
        raise WorkerLedgerError("worker_unverified", f"{name} 协议版本不匹配，需重新注册验收")
    if entry["adapter_id"] not in ADAPTER_ENTRYPOINTS:
        raise WorkerLedgerError("adapter_missing", f"{name} 的 adapter 未在核心登记")
    return entry


def effective_view(store, *, scope: str, now: float | None = None) -> list[dict]:
    """有效能力视图 = 认可安装 ∩ 用户政策 ∩ 渠道范围 ∩ 运行条件（运行条件本期=adapter 已登记）。"""
    view = []
    for entry in store.worker_entries():
        try:
            check_dispatch(store, name=entry["name"], scope=scope, now=now)
        except WorkerLedgerError:
            continue
        view.append({
            "name": entry["name"],
            "kind": entry["kind"],
            "provides": entry["provides"],
            "use_when": entry["use_when"],
            "not_for": entry["not_for"],
            "permission_profile": entry["permission_profile"],
        })
    return view


def render_constitution_block(view: list[dict]) -> str:
    """渲染存在事实清单的「我能请协作者做的」段（D3 接线 retrieval.py 时使用）。"""
    if not view:
        return "- 我能请协作者做的：（当前没有可用的协作者）"
    lines = ["- 我能请协作者做的（经宿主派工，结果带来源或产物，需我评估后再当真）："]
    for item in view:
        lines.append(f"  · {item['name']}：{item['provides']}")
        if item["not_for"]:
            lines.append(f"      不适合：{item['not_for']}")
    return "\n".join(lines)
