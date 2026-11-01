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
- 四工作区（核心 `alice/`+`web/` / 测试 `tests/` / 附加 `addons/` / 资料 `docs/`、`tools/`）依赖单向指向核心；测试与 addons 只走公共面（`service.ChatService`、HTTP API、CLI），不 import `alice` 内部。详见 `docs/工作约束/工作区约定.md`。
- **能力增减须同步宪法「存在事实」清单**（`alice/retrieval.py`：我有什么/没有什么）——自模型与实际能力的漂移本身就是认知错误源（2026-10-31 烟味事件的教训）。
- 提交只暂存自己碰的路径，不用 `git add -A`（工作区常有另一棒的半成品）。
- **分支流程（2026-10-30 用户纠正）**：当前用户的改动推 `trial/windows-v0.2-20261028`；`ytq` 属于其他协作者，不作为当前用户的推送目标。验收后走 **PR 合入 main**（不再本地 merge 推 main）；**main 不接受直接 push**。合并前跑共存检查：`compileall` + 起服验证 + grep 对方分支可能触碰的路径（语义冲突 git 报不出来，三处旧路径 bug 的教训）。当前 origin 使用 HTTPS。

## 当前

- 2026-11-01 ZCode：**协作者调度设计稿 v1**（docs/设计文档/协作者调度-设计.md，待评审排期）——分层能力观定案：薄能力亲历（discovery_search 检索循环）、重/危/常驻能力委托。机制：manifest 标准（addons 即注册表，名单/校验/计账单一事实源）+ 统一 `dispatch_worker(name, brief)` 同步工具（单前台槽/共享额度/失败分型）+ 回传 findings 绑来源逐字 quote（禁预消化结论）+ 存在事实清单改 manifest 渲染（自模型漂移机制化对治）。P0=manifest 扫描渲染/dispatch 工具/browser_reader 迁移首个 worker。
- 待人工验收与待做不变（QQ 扫码、P1、观察器、深海 UI、对话内检索、见闻通道、v0.4+分寸首思辨、身体层复测；A05/A02、tests 旧路径适配 user/），详见 git log。
- 雷：本机无 `timeout`、无 Chrome；`dist/` 与 `user/` 不入库；PR 走 compare 页手动建；推送等用户明确指示；**活库元先验待下次人格重装种入**。
