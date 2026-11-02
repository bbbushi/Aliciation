# testbed_worker（机制验证专用）

零成本、无网络、无模型、无凭据的 testbed adapter，保留为《协作者调度-协议契约-v1》的本地 CLI 帧协议参考。**当前尚未接线或注册，不会随 Alice 启动或派工，也不构成业务验收**；P1 真实场景验收按开发方案 §12 的 A01~A12 通过现有 DSH 插件桥人工进行。

- 行为模式（start 载荷 `limits.behavior`）：`normal / slow / crash / protocol_violation / oversize`。
- 2026-11-02 裁决：以 trial `3732ffe` 的 DSH 桥实现为正典；另一套 D1 实现已撤回，本目录作为 P3 本地 CLI 路径的参考保留（见 `f983fa7` 与设计稿 v2.3）。
- 旧 D1 的 `store.worker_register`、`worker_set_enabled`、`worker_mark_verified` 和 `collaborators.ADAPTER_ENTRYPOINTS` 不存在于当前核心，不能作为当前安装或启用步骤。历史实现见 `f3acba0`。
- 若后续接入，须另行实现固定入口、用户授权与人工验收；不能通过运行本文件证明现有 DSH 桥已通过协议、取消或产物验收。
