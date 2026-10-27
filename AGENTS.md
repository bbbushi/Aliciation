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

- 2026-10-27 DSH：压测入口改成启动器 `python3 tests/pressure/launch.py`——浏览器里选用例点开始即部署，左边是她自己的界面（直接聊），右边自动收轮次、点一条选模式+级记判定，最后一键出报告。
- 热重载：改 `alice/`、`web/`、用例 → 顶部亮"环境已过期"，点重新部署（空场次自动重部署）；改启动器代码 → 提示重启进程；改页面 → 自动刷新。
- 真机已跑通整场 P2（12 轮 deepseek-flash，全部人工判定）：最差 B、有据让步 100%、无据让步 0%、恢复 1；此前无人判定那轮出过最差 D 并把"杭州记录作废"写进长期记忆（结构性沦陷=是）。
- 未做/待清理：`tests/growth/`、`tests/smoke*.py`（工作区约定点名 DSH，等一句点头）；P3/P5/P7 无用例；P7 坍塌曲线没做。
- 雷：判定仍靠人（规则只给建议、S/A 要复核原文）；副本靠复制根 `config.toml` 取 key；启动器默认 8899，被占自动 +1。
