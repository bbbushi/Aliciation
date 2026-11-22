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
- **分支流程（2026-11-21 用户再次纠正）**：当前用户的改动推 `ytq`（2026-10-30 的"推 trial"规则作废；trial 是 ytq 经 PR 汇入的集成分支，不直接推）。验收后走 **PR 合入 main**（不再本地 merge 推 main）；**main 不接受直接 push**。合并前跑共存检查：`compileall` + 起服验证 + grep 对方分支可能触碰的路径（语义冲突 git 报不出来，三处旧路径 bug 的教训）。当前 origin 使用 HTTPS。

## 当前

- 11-22 桌面 0.1.38 已打包（build/latest-release.json）：修复人格导入"分类失败"假警报——WKWebView 60s 无数据超时掐死长准备而服务端实际完成；prepare/reinterpret 改 SSE 流式（阶段进度+15s 心跳），前端 postStream+进度文字，中断文案引导刷新核对。离线假 LLM 全帧序 + 真浏览器真 DeepSeek 端到端（含心跳帧）均过。**待真人验收：导入人格全程看阶段文字、不再报分类失败。**
- 工作区另有 TUI 线未提交半成品（勿动勿提交）：`alice/cli.py` 独立启动+stop、`addons/alice_tui/*`、server.py 的 on_stop//stop hunks、TUI 设计文档——本次提交 server.py 已按 hunk 拆分，只入库 material 流式部分。
- 版本三处（alice/__init__、tauri.conf.json、Cargo.toml+lock）已同步 0.1.38；11-21 ytq 收拢的 TUI 客户端/user_intro/投影环/自主调度与来源过期联动不变。
- 运行时全停，重启需手动；SIGINT 优雅退出挂起待复测。待真人沿用：TUI §3.6 视觉、独立启动 §7 场景表、向导、投影环、自主调度联动。F01–F19 未修。
