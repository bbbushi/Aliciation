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
- `data/` 是 Alice 的记忆（对话/记忆/人格），不入 git；动过就在「当前」写一句。
- `config.toml` 不入 git；服务只监听回环地址。
- 不碰 `.zcodeignore`、`~/.zcode/`。
- 四工作区（核心 `alice/`+`web/` / 测试 `tests/` / 附加 `addons/` / 资料 `docs/`、`tools/`）依赖单向指向核心；测试与 addons 只走公共面（`service.ChatService`、HTTP API、CLI），不 import `alice` 内部。详见 `docs/工作区约定.md`。
- 提交只暂存自己碰的路径，不用 `git add -A`（工作区常有另一棒的半成品）。

## 当前

- 2026-10-28 Codex：trial/windows-v0.2-20261028 已 rebase 到 origin/main 的 e3fd835（含 v0.2 与 GUI 三件需求立项）；本轮提交项目审查文档与状态记录，未修改实现。
- 审查报告：[项目问题清单与分层整改建议](docs/项目问题清单与分层整改建议.md)，基于 214014b；含优先级、源码定位、修复依赖和人工验收，全部待处理；先补证据/压力写回再放开自动思辨，先修测试记录链再评价成长。
- GUI 三件需求已立项：[需求文档](docs/需求文档-GUI功能三件.md)（退出出口/移除检测仪/GUI 改接口配置）；功能侧开发者接手核心 alice/ 与 web/，遵守需求第 0 节全局约束，实现疑问找 ZCode。
- Windows D:\alice：Python 3.12.7，.venv 已安装 requirements；config.toml 沿用 DeepSeek 配置，仅本地保存；未运行冒烟测试、机器自测或模型调用。
- data/：本轮未读取或修改用户会话数据；沿用 http://127.0.0.1:8800 回环后台服务，未重启或重置；日志与启动进程记录位于 data/，不入库。
