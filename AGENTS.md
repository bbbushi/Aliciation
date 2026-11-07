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

- 2026-11-07 ZCode：fetch 后按用户裁定合并 `origin/main`（7bb4573，PR#24/25——人格导入恢复与审查、DSH/Claude 控制分离、web 浮窗/头像/设置页）进 `ytq`；ytq 自 57f1b93 仅 AGENTS 文档差异，代码全量干净并入，冲突仅本文件（按对方重构「当前」）。共存检查过：compileall/node --check/import 冒烟（service/loop/server/expression*）；接缝核查过：render_anchor（对方加固边界语）、build_messages 全参签名、workspace 接线、claude 轨与 waiting_permission 存活、离线自检 26 项全过（/tmp/alice_bugcheck_20261104/）。两点结构变化：质询宿主侧协议由 addons/permission_gate 迁 alice/worker_permissions.py（嵌套 gate-dir+身份绑定的超集，新增 action/resource 摘要，HTTP 面不变）；record_llm_call 改收据队列制。遗留：persona.py 三处导入观测接线随对方重构移除，待在其新结构上重接（已记未完成总览）。已推送未做；服务已于 11-07 13:43 经 /api/shutdown 优雅停机后重启（旧 27100→新 PID 74318，同参数 --port 8800 --no-open），加载合并后代码（/claude-settings.js 200 佐证）；停服备份 /private/tmp/alice-premerge-restart-20261107-134334.db。合并代码（导入恢复/DSH-Claude 分离/双浮窗）效果待真人 GUI 验收。
- 2026-11-07 Codex按用户要求交付44白名单文件：人格导入恢复/覆盖/片段修复/限定复审/检查点/有限表达豁免，DSH与Claude分离及浮窗、2张运行头像、12份人格说明和CLI契约。用户分支trial/windows-v0.2-20261028，PR24已合main 0b64f56且与原HEAD369e808提交树相同，本次同步最新main后推分支提新PR；快照.git/pr-delivery/20261107-043450保留，不直接推/合main。
- 人格准备协议12、initial_plus_delta_v3：五字段合同绑定标准/原稿/registry与实现摘要，7字段硬回归证据和context_check必须绑定实际修改，缺失仍拒绝；interpretation_warning仅记录解释分歧。JSON恢复仅语法；结构纠错一次、必修与可选共用一次，后者失败整包回退已验基线，成功阶段显式续跑绑定与额度不重置。保留情绪张力，事实/能力/授权/停止/私有用途仍严格；无关键词自动放行。
- 开发轮真实操作imp_20261107_041623_efc6d9单次HTTP200，586.766秒、9阶段各1attempt、事务completed/人格version1；事实25/25，可选r_late_night失真后连同r_food整包回退，最终源40/41、规则5/5、样本2/5，认知6adopt/2adapt。证据D:/alice_tmp/有限表达豁免实测-20261107/index.html本机保留；该轮经公共HTTP写user/data安装审计、未改配置，新硬证据/warning/JSON恢复等未触发，安装后聊天未验，不将旧失败回填。
- DSH/Claude分工具status/tasks/detail/events/控制及旧API兼容；归属/epoch/版本锁内核验，独立政策凭据控制/历史权限退避，写前作废旧响应、不自动重试写；仍共享Store、读取等锁最多0.5秒，各1执行+4待办，Claude引用未开放同任务续跑。双浮窗/已读/引用保留，未连接64px、可信就绪/派工放大，位置v2右上纵排/短视口换列和拖动取消保持；报错消失反馈不等于实际功能验收。
- 本轮全alice编译、4JS语法/源码补丁检查及3路只读交付核对通过；仅GET首页/运行接口200，未重启或新做模型/导入/业务/机器测试、未读写user/。现有服务可访问不代表本提交重新起服共存通过，双工具/GUI/人格效果仍待验；原82份私有资料和docs/assets候选保留。橙发V15/V16及小v5、白龙小v4冻结/大V6待审；备份/stash/其他worktree、W6—W9/蓝图待裁决范围不变。
