# AGENTS.md

ZCode 与 DSH 轮流在这个工作区干活：开工先读「当前」，收工把「当前」整节替换（≤5 行），历史留在 git log，
不要往这里追加段落——这个文件每轮都会进上下文，长了就是负担。
分工（2026-10-27 起）：**ZCode 负责架构优化设计，DSH 负责测试环境搭建**。

## 项目

Alice：陪伴型 harness，单一永久会话 + 三层记忆 + 可生长人格。Python 3.11+（用 `tomllib`）。

## 不变量

- `alice/` 只用标准库；`openai` 在 `alice/llm.py:13` 惰性导入（离线测试不装也能跑）。
- GUI 同样零依赖：`alice/server.py` 纯标准库，`web/` 原生 JS，不引入构建链/框架。
- `matplotlib` 只用于 `tools/`、`tests/growth/` 出图，不进运行时。
- `data/` 是 Alice 的记忆（对话/记忆/人格），不入 git；动过就在「当前」写一句。
- `config.toml` 不入 git；服务只监听回环地址。
- 不碰 `.zcodeignore`、`~/.zcode/`。
- 提交只暂存自己碰的路径，不用 `git add -A`（工作区常有另一棒的半成品）。

## 当前

- 2026-10-27 ZCode：简易 GUI 已建并真机验证（`python -m alice serve`：service/server 分层 + SSE 流式 + 思考过程 + 监测侧边栏 + 思考开关；`tests/smoke_serve.py` 18 项全绿）。
- 2026-10-27 DSH：压力测试范本在 `tests/pressure/`（P2 记忆质疑：TOML 三幕剧本 → 真实 `chat_turn` 管道 → 规则判级 → md 报告）。`python3 tests/pressure/test_pressure.py` 14 项全过；`demo.py` 出健康/溃败对照报告。
- 未入库：`alice/{metrics,server,service}.py`、`web/`、`tests/smoke_serve.py`、`tools/make_cortico_diagram.py`、docs 新增（harness研究/*、压力测试规范.md、会话历史/）。
- 雷：监测仪三张评审卡是烟雾报警器；压力判级是规则初筛（S/A 需复核原文、C 需人听），只覆盖 P2，P7 坍塌曲线未做。
- 未完：压力测试其余七类无用例；实验室 `pressure_*` 探针、阈值标定（B2）、C1 记忆/人格面板与手机适配未做。
