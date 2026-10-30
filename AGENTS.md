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
- 提交只暂存自己碰的路径，不用 `git add -A`（工作区常有另一棒的半成品）。
- **分支流程（2026-10-30 用户纠正）**：当前用户的改动推 `trial/windows-v0.2-20261028`；`ytq` 属于其他协作者，不作为当前用户的推送目标。验收后走 **PR 合入 main**（不再本地 merge 推 main）；**main 不接受直接 push**。合并前跑共存检查：`compileall` + 起服验证 + grep 对方分支可能触碰的路径（语义冲突 git 报不出来，三处旧路径 bug 的教训）。当前 origin 使用 HTTPS。

## 当前

- 2026-10-30 Codex 分支纠正：用户明确自己的分支为trial/windows-v0.2-20261028；已用精确lease将误推的ytq从2260950复原760b592，远端无他人新增提交；静态UI提交2260950与本次分支约定修订改推用户分支，不推main。
- 并行联网规划保留：Kanadego/dsh-heartbeat@329d73f修订入口docs/联网能力-heartbeat参考修订报告.md；重点冲浪活动/分享双闸、素材池/单格待续、来源/保留/记忆资格与送达分账，搜索Q5受限主对话、社交增量inbox；B站先行/参与思辨分期等仍待审核与开发授权，未开发。
- QQ分条未提交改动保留，见docs/QQ自然分条回复-实施与人工验收.md：natural默认1～3泡/至少1秒、length回退、账本v2固定计划/unknown停轮；仍待受控重启桥与人工验收；不改QQ拆分/账本/投递、核心会话/记忆/P1语义。
- 本地codex/qq-natural-replies；静态UI提交排除了app.js既有QQ文案，QQ代码/联网规划与此前轻量动效仍留工作树；stash pre-main-sync-20261030-qq-plans保留。用户下一步授权截图主聊天区动效，分支纠正后实施；不读/手改user/，不启停Alice或QQ/调用模型/发消息/运行机器测试。
- 既有轻量动效含顶部缓流/微光/面板入场/外观关闭及减少动态，静态检查与隔离视觉查看已做，真实功能人工未验收；GUI/QQ/P1/观察器人工、A02、A05（remember压力绕行）、旧匿名HTTP人工SDK边界与tests/pressure的user/适配待办。
