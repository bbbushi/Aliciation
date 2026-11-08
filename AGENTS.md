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

- 2026-11-08 ZCode：导入失败定位与修复——用户重置后（旧会话归档 archive/20261108-075758，含爱弥斯人格）导入爱弥斯报「表达资源上限字段无效」0.005s 瞬时 400：根因是注册表六项上限新增进 ExpressionConfig 但不在 expression.DEFAULT_LIMITS 白名单，limits_for(cfg) 全量拒绝（导入管线第一站的回归）。修复：六项收编为一等上限。修复后真实导入成功（installed=true version1 47460字，sources 75%/rules 100%）。四套自检过；服务 PID 7041 已加载。
- 2026-11-08 Codex按用户要求交付 `docs/设计文档/上下文组装优化/人格预引导-核心流程与插件控制台-架构设计-v1-20261108.md`：核心可选流程+固定官方插件控制台；唯一默认关业务开关，安装成功后幂等两阶段/已有安装显式启动，独立状态与存储/提取隔离、最多两次调用/可跳过、首次仅完整阶段二试写≤800字且一次性领取；API/候选版本/取消/并发/观测与G0–G4真人对照明确。第三四步反馈学习/沉淀不做。仅设计未施工/未改user或服务/未模型调用。注册表7项修复仍在工作区待真人（见同目录实施交接），不由本设计认证效果。
- 修复已加载：旧99153忽略SIGINT，CUA和AppleEvents超时；核对无未完成对话、worker_tasks为空，SQLite备份后SIGTERM退出空闲旧服务，00:42:27启动新PID1045（显式user/config.toml、8800/no-open），首页/插件200、日志就绪。备份 `/private/tmp/alice-before-residency-fixes-20261108-004227.db`；日志同目录alice-residency-fixes-20261108-004227.log。仅读user状态/备份与正常起服写入，未手改配置/人格/历史；full保持，未推送/提交，部署不等于registry真人验收。
- ZCode 11-08检查过近轮观测并修match_bindings为单调配对（3aacb8b），历史绑定不回写；报告重启加载，备份 `/private/tmp/alice-before-registry-restart-20261107-234717.db` 及后续更新备份，当前PID本轮未核实。插件已有单页KPI/组成/趋势/DNA/树及正文展开、自包含CSS滚动/入口done和历史GUI修复，上一棒报告浏览器实测；正式备注导出/P4迁移细绑定仍依后续交付核对。
- SDK/插件原9728a00/60f79c9等采集与正文保管、request_id+hash归属和同源addon保留；观测10请求自动停止此前仅标语未见扣减，修复状态待核实。普通回复review_audit未赋值与该处观测异常隔离修复已并入f579412，不自动重放旧失败请求；旧数据库/配置备份均保留。
- ytq已合origin/main 7bb4573，worker_permissions/LLM收据队列接线；persona.py导入观测遗留见未完成总览。协议12/initial_plus_delta_v3边界及内容修复额度不变，Windows与本机导入分开；双工具/GUI/长期人格待验、Claude同任务续跑引用未开放，私有资料/冻结头像/快照/备份/stash/其他worktree及W6—W9/蓝图范围保留；ytq工作及明确要求时推送、main只走PR。
