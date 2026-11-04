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

- 2026-11-04 Codex按用户指示整理本地架构更新并提交推送至trial/windows-v0.2-20261028；基于main b1dba3e，fetch后远端trial仍5fe643d，无分叉。交付146个白名单文件：W1—W4/R01—R07实现、交接、20份设计/复审/快照和88份隔离检测证据；不建PR、不合并或直推main。推送前备份.git/push-delivery/20261104-200434，原回退分支/stash/其他worktree保留。
- 已有隔离检测仍为95场景93通过、2失败（同一P2），本轮未修：partial_receipt回退或未知失败attempt后成功时，usage_out及context_basis.usage缺incomplete=true（loop.py:213/241/550、llm.py:97）。报告为docs/设计文档/整体设计迭代概览/Alice架构完善-隔离自动检测与人工交接-20261104.md，两失败main_loop_partial_stream_aggregate/main_loop_retry_unknown_aggregate需后续修复复跑，不能宣告验收完成。
- 推送前核对70源码/87证据文件原始SHA均与历史manifest一致，tracked diff检查无误；历史权限11、Windows受控进程8、安装/reset异常24+中断恢复10、人格20、历史12通过，观测8通过2失败。历史67Python/2JS/TOML检查不代表本轮重跑；真实部署共存/模型回执/Claude与DSH许可/GUI报告编辑/长期人格体验仍待人工，W5未改通过。
- 本轮仅更新交接及Git交付，未改业务源码、运行测试、读写user/、启停Alice、调用真实模型/工人/QQ或迁移安装。W6—W9/前情退休为设计交付，D1—D5/蓝图待裁决、P1b默认关；未开放文件下载/跨任务/worker正式见闻。旧4份私有资料及待确认头像方案继续留本地、不入本次提交；本用户仍HTTPS、trial经PR合main。
- 并行头像：小图selected-edits-v5/small-crown-v5.png冻结，V9头发已认可不再改；用户认可V10方向，V11已协调大小眼与浅笑，最新指出嘴线未随脸倾斜且偏高。pixel_avatar_design经内置image_gen唯一调用，以V11底图＋原画9c4138654feaa7222f3747026adcef01.png共同输入生成V12，仅将嘴线略下移、整条浅笑随脸倾斜（画面右端略高），保留眉眼/头发/抱书/Q版细腻像素。1254×1254透明RGBA与完整prompts.txt在docs/assets/avatar-concepts/orange-flower-20261104/selected-edits-v12/large-mouth-alignment-v12.png旁，已看图核alpha/无损保存，待用户确认；非目标结构未见明显漂移、细纹有生成差异。后续每轮必须加入原画作结构/气质参照，禁只沿生成图递推、粗块或阴冷半闭眼；旧图保留、v4中止。本头像轮未改源码/UI/user/、运行应用测试或提交推送。
