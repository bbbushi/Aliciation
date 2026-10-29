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
- 四工作区（核心 `alice/`+`web/` / 测试 `tests/` / 附加 `addons/` / 资料 `docs/`、`tools/`）依赖单向指向核心；测试与 addons 只走公共面（`service.ChatService`、HTTP API、CLI），不 import `alice` 内部。详见 `docs/工作区约定.md`。
- 提交只暂存自己碰的路径，不用 `git add -A`（工作区常有另一棒的半成品）。

## 当前

- 2026-10-29 AgentMerge：已将 `origin/main` 合并进 `ytq` 并解掉冲突（`AGENTS.md`、`alice/config.py`）；`alice/config.py` 继续默认读取 `user/config.toml`，同时保留 `str|Path` 入参。
- 保留两侧已落地内容：`user/` 迁移（`user/config.toml` + `user/data/`）与“观察器”只读侧栏功能（入口“观察”）。
- 待后续人工验收：观察器真实环境验收；以及测试区对 `user/` 新会话布局的适配（见此前 DSH 提示点）。
