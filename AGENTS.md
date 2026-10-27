# AGENTS.md

ZCode 与 DSH 轮流在这个工作区干活：开工先读「当前」，收工把「当前」整节替换（≤5 行），历史留在 git log，
不要往这里追加段落——这个文件每轮都会进上下文，长了就是负担。

## 项目

Alice：陪伴型 harness，单一永久会话 + 三层记忆 + 可生长人格。Python 3.11+（用 `tomllib`）。

## 不变量

- `alice/` 只用标准库；`openai` 在 `alice/llm.py:13` 惰性导入（离线测试不装也能跑）。
- `matplotlib` 只用于 `tools/`、`tests/growth/` 出图，不进运行时。
- `data/` 是 Alice 的记忆（对话/记忆/人格），不入 git；动过就在「当前」写一句。
- `config.toml` 不入 git；服务只监听回环地址。
- 不碰 `.zcodeignore`、`~/.zcode/`。
- 提交只暂存自己碰的路径，不用 `git add -A`（工作区常有另一棒的半成品）。

## 当前

- 2026-10-27 14:20 DSH：`docs/工作拆分.md` 改为并行编排（四条队各跑各的，接口先冻、闸门只一处），一棒一个编号。
- 可开工的四个起始编号（互不阻塞）：A1 上下文分段 / B1 实验可复现 / C1 GUI 架构 / D3 插件扫描。
- 未完：`alice/server.py`、`alice/service.py`、`web/`、`tests/smoke_serve.py` 未入库。
- 雷：未验证启动与测试（需真实 API key）；实验室阈值仍是经验值，B2 未做前别把指标当定论。
