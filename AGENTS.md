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
- **分支流程（2026-10-29 起）**：改动一律先推 `ytq`，验收后走 **PR 合入 main**（不再本地 merge 推 main）；**main 不接受直接 push**。合并前跑共存检查：`compileall` + 起服验证 + grep 对方分支可能触碰的路径（语义冲突 git 报不出来，三处旧路径 bug 的教训）。remote 已切 SSH。

## 当前

- 2026-10-30 ZCode：需求文档更新（dd2c7e0）——GUI 三件（退出/检测仪/配置）**已交付合入 main**；新增需求四~六：联网搜索、QQ 等即时通讯接入、B 站内容消费（只读优先、素材≠指令红线、跨渠道同一她）；建议顺序 四→六→五。
- 2026-10-30 DSH：新增 `docs/设计文档/联网能力-设计.md`（设计稿，未实施）——把上网拆成查证/搜索/冲浪/社交观察四形态，P0 只做 `web_open`；含记忆同构、注入边界、SSRF、预算、审计、P0~P3 分期与 9 条验收——即需求四的设计稿。
- 2026-10-29 ZCode：表达层从简已合入 main（b4d3444）；A01 部分整改入库（2dbc19f）。
- 待做：ZCode 侧 A05（remember 绕行）、A02（共享锚点阻止首次自动思辨）；人工验收：观察器。
- 待适配：`tests/pressure/launch.py:69`、`deploy.py:146/221/237/250` 仍按旧 `data/` 布局，需要改到 `user/`。
- 雷：本机无 `timeout`、无 Chrome；`dist/` 与 `user/` 都不入库。
