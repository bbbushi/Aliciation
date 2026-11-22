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

- 2026-11-22 基于 main e4b7c50（PR44），按用户授权将 27 项源码修改、7 项复审补修、Orbit 设置样式与卡死回归修正提交到 trial/windows-v0.2-20261028，并以 PR 交 main；沿用 HTTPS，不直推 main。ytq 的 7ff3174 尚未合入本批。
- 最新结论见 docs/代码审查/Alice源码修复总体报告-20261121.md：源码改动不代表全部真实验收完成。F25 的 keepVisible 仅在隐藏状态存在时改写 class，拆环前停用并断开监听；用户已确认对话与模型打开、切外观返回与关闭，其他路径待验。F10 原并发路径由基线消除，本轮补 busy 回执。
- 提交前 31 个 Python 内存编译、8 个 JS 语法、能力 JSON 和差异空白检查通过；既有 160 Python/spec、48 JS/MJS、9 JSON、7 TOML、36 资产引用及未定义名检查仅作静态证据。TEST_CONTROLS_ONLY=False；未读写 user/、起停服务、调用模型或运行机器行为测试，人工入口为 Alice Lab 与 tests/lab。
- 旧 Brave 密钥须重录。F05 旧往返补提取、F06 历史遮罩原话恢复与原文选择仍待裁定。来源到期/断线、提取、Lab 撤销与忙碌、人格恢复、邀请版本、四主题、QQ 与 macOS/Linux TUI 待真人。新增 alice/persona_mode_install.py 和四份审查文档一并纳入本批。
- 提交备份 .git/pr-prep/20261122-184055-source-fixes 与 codex/before-source-fixes-pr-20261122-184055 保存 48 项改动和 86 项私有资料引用，恢复按 manifest.stored_path。旧 main-sync/20261121-212134、pr-prep/20261120-104342-debt-governance、stash、worktree 与私有 UI 预览保留，不纳入提交。
