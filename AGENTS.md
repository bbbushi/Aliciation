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

- 2026-10-28 ZCode（未入库）：v0.2 P0 思想层（宪法 v2 认识论/论据门控、`alice/thought.py` 观点双落点、memories.kind、CLI `deliberate`）；自定义初始人格导入（`persona.install_persona`、GUI 种子页、`POST /api/seed`、CLI `seed`）。待做 P1 表达层、P2 修订+演化降权。
- 2026-10-28 DSH：新增 `docs/测试说明书.md`（策略/启动器/日常三线/压测流程/判级/留档/验收清单），README、测试指南、压力测试规范的过时引用同步。
- DSH 工具：`python3 tests/pressure/launch.py`（左聊右判、卡片「发给她」、一键报告、热重载、右下退出）；真机 P2 整场通过：最差 B、有据让步 100%、无据 0%、恢复 1。
- 雷：判定靠人（S/A 须原文复核）；只有 P2 用例，P3/P5/P7 缺、P7 坍塌曲线未做；副本复制根 `config.toml` 取 key；启动器 8899 起跳；回归=编译闸 + 一场真机。
- 改 `tests/pressure/*.py` 要重启启动器；改 `alice/`、`web/`、用例只需点「重新部署」。
