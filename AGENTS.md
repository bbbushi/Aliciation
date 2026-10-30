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
- **分支流程（2026-10-29 起）**：改动一律先推 `ytq`，验收后走 **PR 合入 main**（不再本地 merge 推 main）；**main 不接受直接 push**。合并前跑共存检查：`compileall` + 起服验证 + grep 对方分支可能触碰的路径（语义冲突 git 报不出来，三处旧路径 bug 的教训）。remote 已切 SSH。

## 当前

- 2026-10-30 Codex UI：world.execute(me)深海静态版完成，单会话导航/主聊天/观察器P1/设置QQ保持可达，细线亮角与静态字符；本次提交仅UI及两份实施文档、交接，用户授权先推ytq再继续动效。参考与验收见docs/UI升级-world-execute-me风格.md；旧Codewhale视觉仅作历史。
- 并行联网规划保留：Kanadego/dsh-heartbeat@329d73f修订入口docs/联网能力-heartbeat参考修订报告.md；重点冲浪活动/分享双闸、素材池/单格待续、来源/保留/记忆资格与送达分账，搜索Q5受限主对话、社交增量inbox；B站先行/参与思辨分期等仍待审核与开发授权，未开发。
- QQ分条未提交改动保留，见docs/QQ自然分条回复-实施与人工验收.md：natural默认1～3泡/至少1秒、length回退、账本v2固定计划/unknown停轮；仍待受控重启桥与人工验收；本轮不改QQ拆分/账本/投递、核心会话/记忆/P1语义。
- 分支codex/qq-natural-replies，静态UI以b3c30c6为基线提交后推远端ytq；不直接推main。QQ代码与联网规划留在本地，app.js既有QQ文案按块排除；stash pre-main-sync-20261030-qq-plans保留。本轮未读取/手改user/、未启停Alice或QQ/调用业务模型/发消息/运行自动测试；现有服务此前按用户授权启动。
- 深海UI已做静态ID/差异和隔离合成桌面/手机视觉查看，真实功能人工验收未执行；GUI三件与既有QQ/P1/观察器已入main但人工仍待。A02、A05（remember压力绕行）、旧匿名HTTP人工SDK边界及tests/pressure的user/路径适配待办；临时合成预览已停，截图见新实施文档。
