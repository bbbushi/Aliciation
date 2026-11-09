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

- 2026-11-09 Codex审查10e419f（基线4036af2）暂不通过，9项见 `docs/设计文档/上下文组装优化/前缀宪法与双维护-代码审查-20261109.md`：R01旧同步提取/退出提取仍在，R02scoped漏主体条件保护，R03prefix早于来源接续冻结误报无来源，R04二次compact旧状态无审核原文，R05tidy模型后重读快照可误删新编辑，R06提交资格/保护复核不全，R07回退无after版本覆盖新编辑，R08停runner只等5秒资源关闭竞态，R09无聊天空闲优先判断。另观点整理/重复指引归并与界面帮助待补；原canned/e2e/冒烟不符合当前测试约定，不作真人通过证据。本轮仅读代码/文档、compileall与diff检查通过，未模型调用/行为自测/读写user/切换启用/重启/修复代码/提交推送。
- 2026-11-09 ZCode preserve入库Codex未提交改动（4036af2）后施工前缀宪法+双维护（10e419f：prefix_policy scoped五类块/legacy双轨、maintenance compact/tidy、/api/maintenance、CLI三命令、自动触发默认关），并按上列审查完成R01–R09全项返工：R01 adds maintenance.replaces_extraction（默认关，需auto_compact；compact提交推进extract_cursor去重）+轮末/退出/close三路径统一；R02最小宪法回补改写主体段并更正记录错误声明；R03接续计算前置+prefix_plan_factory按最终计数构造+能力行非否定陈述；R04 processed_pairs带对端id+证据逐字重建交审查；R05/R06 tidy冻结快照+提交事务内全资格重核（整组跳过）；R07回退before/after版本条件更新；R08 MaintenanceStopped门控（调用前后/提交前）+runner有界等待不丢引用+让位回queued；R09 channels.activity聊天优先判断。顺带修tidy epoch比较未归一化。开发期检查（compileall/断言/双批compact证据链/冻结跳过/回退冲突/门控/起服冒烟）全过，均不构成验收；行为待真人，见实施记录§八。挂账：scoped人格使用说明归并（涉另一棒管线）、QQ帮助、维护web面板、混合来源场景。
- 11-09 07:56备份 `/private/tmp/alice-before-interaction-20261109-075634.db`（0600），旧29213经SIGINT正常退出，当前PID38939（.venv/bin/python、显式user/config.toml、8800/no-open）仅回环。该进程仍载preserve前代码且DB已由启动维护迁移（48turn/13memory）；两设计要生效需重启（scoped还需改config）。24轮复盘与互动v1行为待验结论保留：M01独立表达/C01接续边界未打通，未启用见闻保存，不能把新文件等同运行效果通过。
- HEAD=ytq本地未推送（4036af2 preserve + 双设计施工提交）；ytq工作/明确推送目标、main只PR；提交只暂存自己碰的路径。既有挂账不变：A05/A02、tests与deploy旧data/布局、QQ真实扫码等人工验收清单；宪法「存在事实」随能力增减同步约定保留（scoped模式同步至capability_fixed块）。
