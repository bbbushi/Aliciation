# AGENTS.md

ZCode 与 DSH 轮流在这个工作区干活：开工先读「当前」，收工把「当前」整节替换（≤5 行），历史留在 git log，
不要往这里追加段落——这个文件每轮都会进上下文，长了就是负担。
分工（2026-10-27 起）：**ZCode 负责架构优化设计，DSH 负责测试环境搭建（面向人工测试的 SDK：部署/剧本执行/数据回收/记录辅助）**。
测试策略（同日定）：**取消冒烟测试与机器自测，只保留真实环境人工测试**；`tests/growth/`、`tests/smoke*.py` 待清理，蓝图实验室验收维度的口子未收。

## 项目

Alice：陪伴型 harness，单一永久会话 + 三层记忆 + 可生长人格。Python 3.11+（用 `tomllib`）。

## 不变量

- `alice/` 只用标准库；`openai` 在 `alice/llm.py:13` 惰性导入（离线测试不装也能跑）。
- GUI 同样零依赖：`alice/server.py` 纯标准库，`web/` 原生 JS，不引入构建链/框架。
- `matplotlib` 只用于 `tools/`、`tests/growth/` 出图，不进运行时。
- `data/` 是 Alice 的记忆（对话/记忆/人格），不入 git；动过就在「当前」写一句。
- `config.toml` 不入 git；服务只监听回环地址。
- 不碰 `.zcodeignore`、`~/.zcode/`。
- 四工作区（核心 `alice/`+`web/` / 测试 `tests/` / 附加 `addons/` / 资料 `docs/`、`tools/`）依赖单向指向核心；测试与 addons 只走公共面（`service.ChatService`、HTTP API、CLI），不 import `alice` 内部。详见 `docs/工作区约定.md`。
- 提交只暂存自己碰的路径，不用 `git add -A`（工作区常有另一棒的半成品）。

## 当前

- 2026-10-27 ZCode：简易 GUI 已建并真机验证（`python -m alice serve`：service/server 分层 + SSE 流式 + 思考过程 + 监测侧边栏 + 思考开关）。
- 2026-10-27 DSH：压力测试范本在 `tests/pressure/`（P2 记忆质疑：TOML 三幕剧本 → 真实 `chat_turn` 管道 → 规则判级 → md 报告）。`python3 tests/pressure/test_pressure.py` 14 项全过；`demo.py` 出健康/溃败对照报告。
- 测试策略已定（取消冒烟/机器自测，只剩人工，SDK 为人工服务）：`tests/growth/`、`tests/smoke*.py` 待清理；DSH 的 `tests/pressure/` 属剧本驱动的人工辅助，保留。工作区四区约定已立：`docs/工作区约定.md`（含公共面清单与 worktree 规则），`addons/` 占位。
- 已入库（21406d0）：GUI 全套、研究文档库、工作区约定；仍未入库（作者未提交）：`docs/harness研究/待动工模块/`、`docs/harness研究/插件参考/`（Cortico 克隆已 gitignore）、`docs/架构设计草稿.md`。
- 雷：监测仪三张评审卡是烟雾报警器；压力判级是规则初筛（S/A 需复核原文、C 需人听），只覆盖 P2，P7 坍塌曲线与其余七类无用例；回归保障只剩编译+真机跑一轮。
