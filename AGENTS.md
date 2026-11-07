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
- **分支流程（2026-11-03 当前用户更新，2026-11-05 合并后保留）**：当前用户在 `ytq` 上工作，后续改动以 `ytq` 为工作分支及用户明确要求推送时的目标；此前 `trial/windows-v0.2-20261028` 的归属说明不适用于当前用户。其他改动（含 Claude 直调）在完成对应验收及共存检查后走 **PR 合入 main**；**main 不接受直接 push**。合并前跑共存检查：`compileall` + 起服验证 + grep 对方分支可能触碰的路径。本机 origin 为 SSH。


## 当前

- 2026-11-07 ZCode 续九：**人格注册表 R1+R2 已按用户指令施工**——按《人格注册表与模型按需装配-架构设计-v1-20261107.md》（其头部已记实装状态与未接线项）：alice/persona_residency.py（合同/摘要/闭包/用途交集/预算校验/模型辅助分类准备，纯逻辑）+ envelope 双格式兼容（encode_sections 带 assembly 深校验保存；decode_sections_with_assembly 形状校验，旧格式可读、新格式不被旧程序降级）+ alice/context_persona.py（请求内注册表：nonce 引用/persona_load 七态/三重额度/已读送入集合）+ persona_context.render_registry_persona（核心常驻投影，不截断快照冒充核心）+ loop 接线（GUI+registry+有效合同才激活，失效 full 回退记录 fallback_reason；目录=宿主标识资料块；失效检查入既有链；persona_projection 含 projection_strategy=core_registry）+ 配置（persona.expression_mode、expression 六上限）。本机人格无 assembly 维持 full；GUI 分层准备界面未接线（prepare_classification 函数已备）；QQ/worker 按 design 保持 full。自检 29 项过（/tmp/alice_residency_check/）+ 三套旧回归过。persona_context.py 同时包含 Codex 未提交的人格使用层（457字符使用说明等）——同文件随批入库，效果待真人。未推送。
- 2026-11-07 Codex按用户要求交付 `docs/设计文档/上下文组装优化/人格注册表与模型按需装配-架构设计-v1-20261107.md`：同一Markdown可选assembly驻留合同，核心/全局边界/日常示范常驻，场景注册表由主模型persona_load批量完整读取；独立版本/用途/依赖闭包/预算、请求内生命周期、旧包full回退、GUI先行、真实送入观测及R0–R4真人验收明确。评估基线37条/6120字符/估算输入占36%，不混旧15条。仅文档，未改运行代码/配置/人格/服务，未读写user/或测试；full仍是当前方案默认，registry不得凭该设计宣称可用。
- ZCode插件最新已做dsh-context单页仪表板：请求卡/KPI/组成/趋势/DNA/树、正文保持、入口done与历史GUI补挂；自包含CSS修滚动/主题混搭。上一棒报告浏览器滚动与正文展开实测通过，非本轮复验；记录服务PID91208（17:44起服加载SDK+插件）后纯前端更新，备份 `/private/tmp/alice-before-obsplugin-restart-20261107-174413.db`。
- SDK9728a00/插件60f79c9及后续7f4650e接线已在ytq：核心冻结/收据/正文保管、公开请求投影及request_id+hash绑定、固定同源addon与启用配置；元数据/正文/插件分开。正式差异/备注导出与P4迁移/辅助细绑定依后续实际交付核对；10请求自动停止此前发现仅额度文字未见扣减，不能按说明认证已实现。
- Codex已修复普通聊天review_audit未赋值导致模型成功却无法保存回复的问题，代码中审计初始化和该处观测异常隔离保留；报告 `docs/设计文档/上下文组装优化/观测SDK启用后普通聊天失败-定位与修复-20261107.md`。人格运行层persona_context.py仍有未提交改动：457字符使用说明及可选215字符提醒，完整包/用途/合同不改、usage版本/hash在metadata；anchor此前关，当前效果需真人验，不混用代码或页面就绪证据。
- 已合origin/main 7bb4573（PR24/25）进ytq；worker_permissions/LLM收据队列结构已接，persona.py三处导入观测遗留见未完成总览。协议12/initial_plus_delta_v3事实/能力/许可/停止严格、必修可选共一次内容修复。Windows与本机导入分开；双工具/GUI/长期人格待验，Claude同任务续跑引用未开放，私有资料/快照/冻结头像/备份/stash/其他worktree及W6—W9/蓝图待裁决范围保留；ytq工作及明确要求时推送，main只走PR。
