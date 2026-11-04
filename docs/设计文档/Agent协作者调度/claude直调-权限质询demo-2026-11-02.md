# Claude 直调 demo：终端派工 + 权限质询闸门（2026-11-02）

> 目的：验证 P3 本地 CLI 干将轨的核心设想——**Alice 以终端方式直调本机完整 agent，且权限在运行中由"AI 质询 → 宿主裁决 → 启用/拒绝"动态确定**（用户 2026-11-02 提出交互设想；验证发现 Claude Code 已原生支持该机制）。
> 状态：demo 完成（两分支全通）；概念验证，非业务验收，未接入 Alice 主对话。
> 代码：`addons/claude_agent/`（demo_dispatch.py stream-json 驱动 + gate_server.py MCP 闸门，均标准库）。

## 一、D0 静态核验结论（claude CLI 2.1.278，macOS）

- 无头模式 `-p` / `--output-format json|stream-json` 可用；**stream-json 输出必须配 `--verbose`**（缺了静默失败，报错只在 stderr——本次踩坑记录）；
- `--permission-prompts host` 在 plain CLI 无 TTY 下**不会**把质询发到流（自动拒绝）——质询必须走 **`--permission-prompt-tool`（MCP 闸门）**；
- 本机 claude 配置为 deepseek-flash 后端（CLI 警告不认识该模型但正常工作）；费用走用户自有配置，**凭据不过 Alice 手**（demo 实测单次 $0.10~0.15）；
- `--add-dir` 可作可见目录上限；`--max-turns` 有效限轮。

## 二、质询机制实测（你设想的原生实现）

链路：`claude -p <任务书> --permission-prompt-tool mcp__alice_gate__confirm --mcp-config <闸门配置> --add-dir <沙箱>`

1. 模型遇到权限点（Write 等）→ 调用闸门的 confirm 工具，**带上完整意图**（工具名 + 输入，如 `Write{file_path, content}`）——即"AI 向宿主质询"；
2. 闸门按策略裁决 allow/deny，**逐条写审计 jsonl**（谁、想干什么、裁决、理由）；
3. allow → 模型继续执行；deny → 模型**不重试、不绕路**（拒写后未改用 Bash 硬闯），向派工方如实转述拒绝原因。

| 分支 | 结果 |
|---|---|
| 允许 | 2 次质询全过（含一次 Bash 绕路企图，同样过闸留痕）→ hello.txt 落盘 → 确认存在（3 turns，$0.13） |
| 拒绝 | 1 次质询被拒 → 0 产物、模型如实报告原因（2 turns，$0.12） |

## 三、对 Alice 接入的映射（demo → 真实）

demo 的闸门裁决点是命令行策略（allow/deny）；接入时替换为：

- **Alice 转述质询**：闸门把 `{tool, input, reason}` 作为结构化质询事件回投 Alice（复用 worker 事件回投机制），她用自己的话向用户转述（"协作者想在沙箱写一个文件，内容是……"）；
- **用户三层答案**：允许一次 / 本任务内允许（同工具+同路径前缀）/ 拒绝——"本任务内允许"映射为闸门内存的会话级规则缓存，任务结束即失效；
- **结构禁区不进质询**：命中禁区（如 `~/.zcode`、Alice 仓库、凭据文件）直接 deny，不劳用户——质询只裁"政策内但要用户点头"的事。

**边界如实标注**：这是权限层的动态质询，不是 OS 级沙箱（对齐 dsh_native_v1 的诚实原则）；`--add-dir` 提供目录可见性上限，但进程仍在用户身份下运行。成本面实测透明（result 帧自带 usage 与 total_cost_usd），可直采进预算账本（token/金额分列）。

## 四、下一步（若转正式）

1. demo_dispatch 驱动并入 P3 本地轨 Transport（取 f3acba0 的 GO 门控/整树回收补齐）；
2. 闸门 → Alice 质询回投（事件结构复用 worker_events）；
3. 注册 `claude-agent`（固定 argv + 档位声明 + 预算帽）进名册；
4. 真实业务验收按 DSH A01~A12 同规执行。

---

*记录 v1（2026-11-02，ZCode 架构侧）。demo 沙箱：/tmp/alice_claude_demo/（可删，不影响工作区）。*
