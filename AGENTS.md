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

- 2026-11-05 Codex按用户要求交付人格导入跨用途依赖冲突修复：仅alice/expression.py、expression_prepare.py、修复说明及本交接4路径提交推trial/windows-v0.2-20261028并向main新提PR。PR23已合main 57f1b93（与原HEAD eaf2974提交树相同），本次以最新main为基线；快照.git/pr-delivery/20261105-232045保留。禁止直推/直接合并main，旧私有资料与头像不入本次提交。
- 修复仅在两个生成阶段捕获完整结构校验后的专用用途冲突，深拷贝并按依赖队列保守收窄purposes，覆盖传递/环，保留chat/正文/ID/来源/links；提示补齐用途子集约束。原适配changes先对原模型回执核账，再采用宿主候选，用途差异独立私有记账；用户修订合入后、运行时及激活仍严格校验/双审，无新增模型调用或权限，不变准备协议3/宪法能力清单。GUI未新增用途调整明细。
- 本轮两份源码compileall、源码diff空白和独立只读交付核对通过；未运行机器自测/冒烟/业务/模型/假模型/临时DB/故障注入/真人验收，未启停应用或重新导入，未读写user/。原定位轮仅查询本机回执路径清单且无匹配，c_fame具体引用对象/失败阶段及另一开发者的真实复验仍待确认；GUI未新增用途调整明细，不宣称本机复现或整链通过。
- 既有F0—F3及R1—R8/N1—N3和b1ffbd7/ef99c42已随PR23入main；旧报告及修前95场景93通过2失败/修后独立28/28仍为历史限定证据，不回填本次。W5、起服共存与实料效果仍待验，W6—W9/前情退休为设计、D1—D5/蓝图待裁决、P1b默认关；仍HTTPS、trial经PR合main，备份/stash/其他worktree保留。
- 并行头像：橙发V15/V16及小图v5保留；白龙小图pixel-finish-v4/small-pixel-v4.png已获确认并冻结。大图V5压暗虹膜被用户否决；按新附件1509506c（与V3大图SHA一致）重新出V6，只以源图保留紫瞳亮度/瞳孔/高光与眉眼嘴构图，增强细像素阶梯。候选docs/assets/avatar-concepts/white-dragon-20261104/source-repixel-v6/large-face-preserved-v6.png、prompts-large.txt与原图对照compare.html已存，preferred.json记录小图通过/V5拒绝/V6待审；内置image_gen及pixel_avatar_design协作，1254透明RGBA无损复制，已目视/alpha核验，仍有少量发束与虹膜下部亮块转绘差异。未改运行时资源/源码/UI/user/、跑应用测试或提交推送。
