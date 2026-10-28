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

- 2026-10-28 DSH：GUI 初始化页重做（标签或人格文档导入，拖放/预览/校验；`?setup` 可主动回到初始化页）+ 聊天页「人格」面板（查看/下载/替换，旧版自动快照）。
- 同批：`persona.install_persona` 两种文档都收（无「## 底色」就把整份文档放进底色层，适合角色卡），`source` 记进 CHANGELOG；server 新增 `GET/POST /api/persona`。
- ZCode 的 v0.2 P0（`alice/thought.py` 思辨层 + store `kind='stance'` + cli/retrieval/config 改动）随本次一并入库——不带上 HEAD 的 service.py 就会 import 失败。
- 雷：`config.toml` 的 API key 已过期（chat 返回 401），真机对话要先换 key；本机没有 `timeout` 命令，也没有 Chrome/Chromium。
