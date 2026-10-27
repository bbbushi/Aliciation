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

- 2026-10-27 ZCode：**v0.2 P0（思想层）已实施并真机验证**（未入库）——宪法 v2（四分认识论/被冲击行为链/论据门控）、`alice/thought.py` 思辨（观点=claim/reasons/sources/uncertainty/open_questions；双落点：stance 入长期层+人格「我现在的想法」小节；修订=旧观点出库留墓碑）、memories 加 kind 列（老库已迁移）、每 15 条新事件触发 + CLI `deliberate`。首次真实思辨产出 6 条观点（消化修钟人压力对话），端到端一轮通过。设计见 `docs/架构设计-Alice-v0.2.md`。
- ZCode 待做：P1 表达层（言语行为协议/情绪词表/GUI 渲染）、P2 修订完善+演化降权——DSH 的 P2 真机已抓到结构性沦陷实例（"杭州记录作废"入长期记忆），正是演化降权的靶子。
- 2026-10-27 DSH：压测启动器 `python3 tests/pressure/launch.py`（浏览器选用例，左聊天右判定，一键报告，热重载）；卡片上「发给她 / Alt+Enter」把剧本当成你说的话发出去，不用复制粘贴；已按新策略删 `tests/growth/`、`tests/smoke*.py`；`docs/架构图.png` 重生成（横切层=压测录制器）。
- DSH 真机整场 P2 跑通（12 轮全人工判定）：最差 B、有据让步 100%、无据 0%、恢复 1。
- 雷：判定靠人（S/A 须复核原文）；P3/P5/P7 无用例、P7 坍塌曲线未做；压测副本复制根 config.toml 取 key，启动器 8899 起跳；回归保障只剩编译+真机跑一轮。
