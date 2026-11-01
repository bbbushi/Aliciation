# testbed_worker（机制验证专用）

零成本、无网络、无模型、无凭据的 testbed adapter，用于《协作者调度-协议契约-v1》机制层的人工验收（清单第 3~6 项：协议顺序/取消/产物/重启），以及开发期自检。**不构成业务验收**；真实场景验收按开发方案 §12 的 A01~A12 以真实 adapter 进行。

- 行为模式（start 载荷 `limits.behavior`）：`normal / slow / crash / protocol_violation / oversize`。
- 注册：经 `store.worker_register({"name": ..., "kind": "agent", "adapter_id": "testbed_v1", ...})`；**默认禁用**，启用与预算由用户政策（`worker_set_enabled`），派工前须通过人工注册验收（`worker_mark_verified`）。
- adapter 启动入口：核心 `alice/collaborators.py::ADAPTER_ENTRYPOINTS` 固定映射 `testbed_v1` → 本目录 worker.py；不经过任何模型或 manifest 提供的命令。
