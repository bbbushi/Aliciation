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

- 2026-10-27 DSH：`tests/pressure/` 改成人工压测录制器。`session.py` = 部署隔离副本（复制 `alice/`+`web/` 到 `tests/.sessions/`，优先 `.venv/bin/python`）→ 剧本驱动（回车发剧本/可直接改口）→ 逐轮人工判定（规则只给建议）→ 报告+transcript+人格快照；`demo.py` 离线看样式。已真机跑通 P2（deepseek-flash）。
- 真机那轮的结果值得看：最差 D、无据让步 25%、测后新增"杭州记录作废"与"用户希望AI直接认错"两条记忆（结构性沦陷=是）——高压下她会把作废写进长期记忆。
- 未做/待清理：`tests/growth/`、`tests/smoke*.py`（工作区约定点名 DSH）；P3/P5/P7 等其余压力类型无用例；P7 坍塌曲线没做。
- 雷：判级是规则初筛（S/A 需复核原文、C 需人听语气），恢复轮数建议手填；`config.toml` 的 key 只在项目根，副本靠复制它工作。
