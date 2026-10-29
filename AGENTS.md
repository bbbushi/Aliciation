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
- **分支流程（2026-10-29 起）**：改动一律先推 `ytq`，验收后 `--no-ff` 合并（或 PR）进 main；**main 不接受直接 push**。合并前跑共存检查：`compileall` + 起服验证 + grep 对方分支可能触碰的路径（语义冲突 git 报不出来，三处旧路径 bug 的教训）。remote 已切 SSH。

## 当前

- 2026-10-29 ZCode：**A01 部分整改（未提交→已提交待推）**——思辨 sources 逐字校验（虚构/空出处整条丢弃，计数"丢弃无实据观点"）；本批有被拒/被丢内容时人格小节改由库内已接纳 stance 确定性重建（`_render_stances`）；演化对压力记忆改硬隔离（正文不进输入）+ 思想节/来历节代码级保护（`_protect_sections` 正则剥除+回挂，模型改不掉删不掉）；观点出处含压力材料则 stance 沿链带 pressure=1。离线六项验证全过；清单 A01 状态已更新，**remember 绕行留待 A05 轮次身份**。
- 2026-10-29 ZCode：护栏已退役（宪法删「观点与被冲击时」，契约+P1 表达保留）；合并遗留三处旧配置路径已修（USER_DIR）。
- 待人工验收：观察器、GUI 三件；**A02（共享锚点阻止首次自动思辨）为下一架构项**；DSH 启动器待适配 user/ 布局。
- 雷：服务跑在 8800；主库 11 轮在 user/data/；网络对 github 间歇超时，弥生清理与 A01 提交可能需重推；本机无 timeout/Chrome。
