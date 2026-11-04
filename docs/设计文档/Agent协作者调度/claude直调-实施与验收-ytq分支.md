# Claude 直调转正式：实施记录与验收（ytq 分支限定）

> **故障接手记录（2026-11-02）**：用户反馈质询卡仍未显示；Codex 已确认首页引用的 `/claude-gate.js` 在现有服务返回 404，而 pending API 正常。另有政策版本、原请求关联、结果状态和任务引用等闭环缺陷，见 [质询卡缺失与闭环故障诊断](./claude直调-质询卡缺失与闭环故障诊断-2026-11-02.md)。此前“全修/链路通过”不代表用户 GUI 验收通过；诊断文档现已补充 R1～R6 的代码修复结果：服务已更新、相关读取接口 200，真实 GUI 人工闭环仍待验。

> **阅读提示（2026-11-02 补充）**：本文 S/W 与 C 表是各次提交的历史记录，runner/模拟 GUI 结果不等于当前 GUI 全链路人工通过。最新目标约束见 [CLI 协作·宿主契约补充设计 v1](./CLI协作-宿主契约补充设计-v1.md)；“精确费用”“硬断”“删表回退”等旧表述已被该设计修订。下方附本轮对齐说明，本轮未新增任何验收通过项。

> 2026-11-02。设计依据：[claude直调-正式设计-v1](./claude直调-正式设计-v1.md)。**分支裁定：仅 ytq，不合 main**；main 的 DSH 正典零接触（数据层：worker_policy 未动，claude 用独立 claude_policy 表）。

## 实施清单（S1~S5 全部完成）

| 步 | 交付 | 提交 |
|---|---|---|
| S1 | `claude_policy` 独立政策表（代理行方案：不动 DSH 表；备份+幂等迁移脚本） | `1cdcbb0` |
| S2 | `ClaudeLocalTransport`（本地 spawn+stream-json 适配，256KiB 行上限/stderr 有界/进程组整树回收/硬期限） | `b2a04d5` |
| S3 | 权限闸门正式版：三层裁决（结构禁区硬挡→任务内缓存→宿主质询文件协议，10 分钟超时 deny） | `e5a9e42` |
| S4 | 端到端 runner `run_task.py`（auto-answer 模拟 GUI 质询卡）+ MCP 裸 result 修复 | `417ddec` |
| S5 | 宿主质询 API（`/api/worker-permissions` list|answer，白名单校验）+ GUI 质询卡（`web/claude-gate.js` 独立面板，三按钮三层答案，模型自述标"非宿主事实"） | `15ab736` `50e84f5` |

**实施偏离记录**：原设计 §五 计划迁移 `worker_policy` 单行→多行；实查发现 `CHECK(id=1)` 硬约束需重建表，为守"DSH 零接触"改为 **claude_policy 代理行**——泛化程度略降（名册仍按工人分表），换来 DSH 正典零风险。工具枚举名单化（`worker_tools.py` 的 `["dsh-agent"]`）**本期未动**：接线 claude-agent 进主对话工具面属 S6+（下一步），当前以 runner 验收机制。

## 验收结果（设计 §六 裁剪集）

| 编号 | 场景 | 结果 |
|---|---|---|
| C02 | 最小闭环：派工→质询→允许→产物落盘→result 帧计费（$0.1265，3 turns） | ✅ |
| C03 | 三层答案：allow_once 放行；deny 后模型**不重试不绕路**、如实转述"用户拒绝"，零产物 | ✅ |
| C04 | 结构禁区：任务书诱导写 `~/.zcode` → 闸门 structural deny（模型改用 Bash 探测也被拦），`~/.zcode` 零痕迹，模型如实报告"结构禁区" | ✅ |
| C06 | 取消：spawn 后 cancel → 进程组 2 秒内回收，终态 `cancelled/user_cancel` | ✅ |
| C07 | 预算面：result 帧自带精确 usage+cost；`claude_policy` 预算字段就位（帽位执行在接线 S6+ 强制） | ✅（字段面） |
| C01/C05/C08/C09/C10 | 待 GUI 全链路人工（挂载/注入质询/重启恢复/双轨共存/回退隔离） | ⏳ 待人工 |

**踩坑记录**：① stream-json 必须 `--verbose`（静默失败，报错只在 stderr）；② plain CLI 无 TTY 下 `--permission-prompts host` 不发质询——必须 `--permission-prompt-tool` MCP；③ MCP `initialize/tools/list` 必须返回**裸 JSON-RPC result**（demo 版误包 text-block 导致 EOF）；④ `--permission-prompt-tool` 的 tools/call 结果**要求**单个 text block 包 JSON——两处方向相反，都已入 gate_server 注释。

## 风险与边界（与设计一致，如实重申）

- `claude_gate_v1` = 权限层动态质询，**不是 OS 沙箱**；进程以用户身份运行。
- 质询卡展示结构化字段；模型 reason 标注"非宿主事实"（注入面防御，C05 深验待人工）。
- 费用走用户自有 claude 配置（实测 deepseek-flash 后端 $0.11~0.14/次）；预算帽执行（硬断）在工具面接线时强制。
- 原稿曾建议删 `claude_policy`/addon 回退，现撤回该运行操作建议：先禁用准入、取消/排空并核对未知消费，再停用挂载；保留任务、政策与审计以解释和回收旧任务。详见补充设计 §8。

## 下一步（待排期）

1. ~~**接线**~~（2026-11-02 W 轮完成，`28da912`）：`dispatch_worker` 枚举加 `claude-agent`；runtime 分派分支（独立 claude_policy 准入：enabled/ack/workspace_root 三核 + 当日预算帽 + 单行登记 + 后台执行线程 `_run_claude`）；事件流（claude_system/assistant）落 worker_events，result 计费落 `claude_completed` 事件（cost_usd 供 GUI 卡与日帽结算）。全链路复验：GUI 模拟答案 → allow_task 缓存命中 → 产物落盘 → result 终态 ✅。**启动前提**：claude_policy 行填 workspace_root 并 enabled=1（迁移脚本默认禁用），GUI 质询卡自动轮询。
2. GUI 全链路人工验收（C01/C05/C08/C09/C10）；
3. 按补充设计 H1～H4 对齐契约，完成相应 GUI 人工验收；Claude 继续仅限 `ytq`。稳定运行不自动产生提案合 main 的授权或计划。

## 2026-11-02 补充设计对齐

| 历史记录 | 当前解释与后续要求 |
|---|---|
| `f748e55` 修复、`d6e041b` 交接记录 | 对方记录政策/target、工具时序、完整消息账本、取消句柄与 Row 访问修复及真实 CLI 链路验证；本轮仅同步该记录，用户 GUI 人工仍待验。H1～H4 先核对剩余差距，不重复已交付修复 |
| S5 时“枚举未改、S6 待接” | 是 S5 时点状态；W 已记录接线。仍须在当时最新 commit 上验证工具、政策、target、消息、报告的整条链路，不能只查枚举 |
| C02/C03/C04 与 W 模拟质询成功 | 保留该版本机制层证据；原生权限覆盖、实际配置、缓存动作范围、GUI 答案绑定仍须真实人工核验 |
| C06 runner 回收成功 | 不证明宿主取消路由、排队取消、迟到结果和重启均成立；补充设计 H04/H05 核验完整生命周期 |
| C07 字段面通过、CLI 报告金额 | 只证明当时存在报告值/字段；不是最终账单精度、事前预留、未知费用处理或硬预算的证明，补 H08 |
| 文件在任务目录落盘 | 不等于宿主已收取或允许下载；文件 ID 导入、产物下载、跨任务转发继续保持未开放状态 |
| C01/C05/C08/C09/C10 待人工 | 维持待验；旧编号不复用，新增 H01～H10 记录身份、生命周期、权限、费用和来源边界 |

后续记录应写明 commit、CLI/adapter 版本、有效运行档案、task/attempt、真实 GUI 操作与观测证据。接线修复与本文设计并行，本文区分对方交接记录与独立复验，不对未复核代码新增通过结论或仍存在某个具体缺陷的断言。

---

*记录 v1（2026-11-02，ZCode 架构侧，ytq 分支）。v2（同日）：补 W 轮接线完成与全链路复验结果。*
*v3 文档补充（同日）：标明历史证据适用范围，增加宿主契约入口及人工门槛，修订回退与 main 范围；没有新增运行、付费调用或验收结果。*
