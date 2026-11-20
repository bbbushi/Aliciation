# AGENTS.md

ZCode 与 DSH 轮流在这个工作区干活：开工先读「当前」，收工把「当前」整节替换（≤5 行），历史留在 git log，
不要往这里追加段落——这个文件每轮都会进上下文，长了就是负担。
分工（2026-10-27 起）：**ZCode 负责架构优化设计，DSH 负责测试环境搭建（面向人工测试的 SDK：部署/剧本执行/数据回收/记录辅助）**。
测试策略（同日定）：**取消冒烟测试与机器自测，只保留真实环境人工测试**；蓝图实验室验收维度的口子未收。

## 项目

Alice：陪伴型 harness，单一永久会话 + 三层记忆 + 可生长人格。Python 3.11+（用 `tomllib`）。

## 不变量

- `alice/` 只用标准库；`openai` 在 `alice/llm.py:13` 惰性导入（离线测试不装也能跑）。
- GUI 同样零依赖：`alice/server.py` 纯标准库，`web/` 原生 JS，不引入构建链/框架。
- `matplotlib` 只用于 `tools/` 出图，不进运行时。
- `user/` 是本机专属目录：`user/config.toml`（接口配置）+ `user/data/`（Alice 的记忆：对话/人格/归档），整目录已 gitignore；动过就在「当前」写一句。
- 服务只监听回环地址。
- 不碰 `.zcodeignore`、`~/.zcode/`。
- 四工作区（核心 `alice/`+`web/` / 测试 `tests/` / 附加 `addons/` / 资料 `docs/`、`tools/`）依赖单向指向核心；测试与 addons 只走公共面（`service.ChatService`、HTTP API、CLI），不 import `alice` 内部。详见 `docs/工作约束/工作区约定.md`。
- **能力增减须同步能力层JSON注册表与必要自我事实**（`alice/capabilities/commands.json`、`alice/constitution.json`）；注册表与SDK工具同源，宪法不重复工具目录或用户操作教程——自模型与实际能力须一致（2026-11-11用户确认的新组织方式）。
- 提交只暂存自己碰的路径，不用 `git add -A`（工作区常有另一棒的半成品）。
- **分支流程（2026-10-30 用户纠正）**：当前用户的改动推 `trial/windows-v0.2-20261028`；`ytq` 属于其他协作者，不作为当前用户的推送目标。验收后走 **PR 合入 main**（不再本地 merge 推 main）；**main 不接受直接 push**。合并前跑共存检查：`compileall` + 起服验证 + grep 对方分支可能触碰的路径（语义冲突 git 报不出来，三处旧路径 bug 的教训）。当前 origin 使用 HTTPS。

## 当前

- 2026-11-20 基于 main b003f46 完成 D01–D10 技术债治理：统一预算、工具解析和模型回执，收口观测归属，补维护回执，退出旧人格生成、旧自主选题及主网页旧诊断资产，整理 Orbit、Lab 人工入口和桌面组件清单；交付见 `docs/代码审查/Alice技术债治理与源码复审-20261120.md`。
- 全仓审查 F01–F17 仍待指定修复范围，复审另确认已有 F18 Orbit 思想忙闲误判和 F19 流式结束观测误报。本轮不宣称这些缺陷已修复；累计 compact 证据、legacy 装配、人格/QQ、旧留存等边界见两份 `docs/代码审查/*20261120.md` 报告。
- 按本次授权提交 `trial/windows-v0.2-20261028` 并以 PR 交 main，沿用 HTTPS、不直推 main。提交前备份 `.git/pr-prep/20261120-104342-debt-governance` 保存 38 项改动和 86 项私有资料引用，私有文件及载荷 hash 已核对；按 manifest.stored_path 恢复。旧 `.git/main-sync/20261120-100353`、备份分支、stash、worktree 与私有 UI 预览保留。
- 运行时差异 hash 与治理报告一致；提交前另核 16 个修改 Python 内存编译、3 个 JS 语法、示例 TOML 和差异空白通过。治理会话全仓静态记录为 154 Python/spec、47 JS、9 JSON、7 TOML、29 资产引用。TEST_CONTROLS_ONLY=False；Lab 授权、GUI/QQ/探索、维护回退、人格恢复、四主题及桌面行为待真人。
- 本轮未读写 user/、起停服务、调用模型/业务接口、构建安装包、迁移或运行机器行为测试。现行人工入口是 Alice Lab 与 tests/lab，旧 pressure 仅供历史参考；既有 UI 证据在 D:/alice_tmp/PR40设置适配-20261119/20261119-215827。
