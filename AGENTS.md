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

- 2026-11-07 ZCode 续三：**观测插件 P0–P2 已按用户指令实装**——按《上下文观测插件-架构与施工设计-v1-20261107.md》（其头部已记实装状态）：P0 正文归属校验（context_body 必须 request_id+hash 在该请求事件登记过；裸 hash/未知请求/外来 hash 分立拒绝，observer.js 轨迹视图已改为携带 request_id）+ plugin_status 功能发现；P1 addons/context_inspector/（manifest+README+web 四文件）、[plugins] context_inspector_enabled（默认关）、固定静态托管（清单白名单+解析路径+类型/大小限制）、/api/plugins/context-inspector/{status,requests,request,body}（observer 意图+启用检查，委托同一核心投影）；P2 inspector 三栏页+聊天回复旁「查看本轮上下文」入口（done 事件 request_id，缺 ID 不伪造入口）。P3 差异/备注/导出与 P4 旧面板迁移待续。插件自检 12 项过（/tmp/alice_ctxobs_check/check_plugin.py）+ SDK 自检 27 项（context_body 新签名已同步）+ 旧回归过；compileall/8 个 JS/import 冒烟过。config.example.toml 已加 [plugins] 示例。服务未重启（现 PID 85822 不含 SDK 与插件）；真人验收按设计 §12 清单。
- 2026-11-07 ZCode 续二：**上下文观测 SDK 已按用户指令实装**——按《上下文观测SDK-补充设计-v1-20261107.md》施工（其头部已记实装状态与一处有意 API 差异：call_prepared 在 freeze_final 模型客户端边界发出）：alice/context_observer/ 四模块（contract/freeze/observer/sink，纯标准库，Null 同形）+ alice/context_observation.py 适配（收据队列复用、正文专属存储 user/data/observation/ 7天50MiB、observer_enabled/body_capture 配置）+ 接线（context.Block 存 content、loop 七处、llm.chat 与 _stream_round freeze_final+attempt 三态）+ 查询面（observation.context_requests/context_body、/api/observer/requests|body）+ observer.js「调用轨迹」视图。离线自检 27 项过（/tmp/alice_ctxobs_check/）+ 旧回归过（两条断言按合并后现实放宽：锚开头语/样本行属对方 11-07 重构）；compileall/JS/import 冒烟过。**服务现跑 PID 85822 不含本轮 SDK**，需重启加载；真实对话/工具往返/正式改写/失败场景按交付顺序 2~3 步真人核对待验。Codex 的插件化架构设计（同目录）属部署形态层，与其「SDK 内部复用」衔接，未在本轮范围。
- 2026-11-07 Codex交付 `docs/设计文档/上下文组装优化/上下文观测插件-架构与施工设计-v1-20261107.md` 并关联旧观测器/SDK方案：核心采集/保管+固定官方同源addon独立页，SDK内部复用，开关分层、接口归属/正文资格、元数据降级、迁移及P0–P4人工验收明确。工作区已见ZCode在建SDK/查询/页面接线，仅源码观察未认证部署效果；本轮只写设计/交接，未改其代码、user/或服务，未测试。Codex先前人格使用层已写、15:42启动PID85822/8800首页200（当时核对），anchor仍关，效果待真人；备份 `/private/tmp/alice-before-persona-runtime-restart-20261107-154258.db`。未提交推送。
- 2026-11-07 ZCode已将origin/main 7bb4573（PR24/25）合入ytq，HEAD5106274；上一棒报告静态/起服等检查通过，效果仍待真人验收。13:43合并后重启为旧PID74318，旧备份 `/private/tmp/alice-premerge-restart-20261107-134334.db`；当前进程以上一条为准。质询协议迁alice/worker_permissions.py，LLM观测改收据队列；persona.py三处导入观测接线待重接，见 `docs/设计文档/整体设计迭代概览/Alice未完成工作总览-20261104.md`。
- 合并交付含人格导入恢复/覆盖/片段修复/限定复审/检查点/有限表达豁免、DSH与Claude分离及双浮窗/头像/设置页。人格协议12、initial_plus_delta_v3：合同/回归证据绑定实际修改，解释分歧仅提示；JSON仅语法恢复，结构纠错一次，必修/可选共用一次内容修复，可选失败整包回退；保留情绪张力但事实/能力/授权/停止/私有用途严格，无关键词放行。
- 上游Windows真实导入imp_20261107_041623_efc6d9（586.766秒、9阶段）完成，事实25/25、源40/41、规则5/5、样本2/5，晚间/食物可选整包回退，证据D:/alice_tmp/有限表达豁免实测-20261107/index.html；不同于本机13:45导入，不混用指标。DSH/Claude归属/epoch/版本锁内核验、写前作废旧响应且不自动重试写，共享Store锁最多0.5秒、各1执行+4待办，Claude同任务续跑引用未开放；GUI/双工具实际效果仍待验。
- 旧交付快照.git/pr-delivery/20261107-043450、82份私有资料与docs/assets候选保留；橙发V15/V16及小v5、白龙小v4冻结/大V6待审；备份/stash/其他worktree、W6—W9/蓝图待裁决范围不变。当前用户以ytq为工作及明确要求时推送目标，main只走PR；旧上游trial归属不覆盖当前用户约定。
