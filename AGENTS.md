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

- 2026-11-07 ZCode 续五：插件 GET 路由漏挂 do_GET 分发（404）已补并重启验证通过——PID 91208 加载 SDK+插件；status 显示 enabled=true/body=true（用户已自行配置 [plugins] 与 observer_body_capture），正文库 48.7KB，已有真实请求进投影（46fb54f7…，绑定/尝试/块齐全）。停服备份 /private/tmp/alice-before-obsplugin-restart-20261107-174413.db。
- 2026-11-07 ZCode 续四：观测插件展示形态按用户参考 dsh-context 复刻（原生 JS/CSS）：分类颜色编码、组成堆叠条与图例、逐调用趋势带与差异徽标（vs 上次：±消息/±字）、DNA 模型读取顺序逐项（位置色块+正文按钮）、树状上下文浏览器与选择决策折叠；数据全来自公开投影，估算字符与供应商 usage 分列。提交 7f4650e；SDK/插件自检与 JS/compileall 全过；服务未重启加载。
- 2026-11-07 Codex修复“这一轮没走通”：两次真实“在？”均模型成功、用户turn保存而助手turn缺失；loop.py新增观测无条件读仅在审查分支赋值的review_audit，普通回复必触发UnboundLocalError。已将审计选择移到分支前，记录selected=false/not_selected，并隔离该处观测异常；未重放旧请求。单文件compileall/diff检查过，真人新消息待验。17:20:25正常停旧89153、备份 `/private/tmp/alice-before-chat-observer-fix-20261107-172025.db` 后起新PID90350（8800/no-open），首页/插件200、日志就绪；日志同目录alice-chat-observer-fix-20261107-172025.log。读user活库/备份及正常起服，未手改配置/人格/历史。详见 `docs/设计文档/上下文组装优化/观测SDK启用后普通聊天失败-定位与修复-20261107.md`。
- Codex人格运行层仍为未提交alice/persona_context.py：457字符使用说明及可选215字符使用提醒，完整表达/用途/合同保留，usage版本/hash进既有metadata，anchor仍关；此前15:42启动已载入这部分。实施与人工剧本见 `docs/设计文档/上下文组装优化/人格表达运行时强化-实施交接与人工验收-20261107.md`；备份 `/private/tmp/alice-before-persona-runtime-restart-20261107-154258.db`。
- ZCode SDK9728a00及插件P0–P2提交60f79c9已在ytq，新增采集/边界冻结/队列正文/请求查询/同源插件及request_id+hash归属校验；P3差异/备注/导出、P4迁移/辅助细绑定待续。上一棒报告SDK27/插件12自检及静态检查过，非本轮真人验收。启用时发现额度缺口：body_quota标10请求，但policy_for/AliceSink/SDK未见请求额度扣减或自动停；不能承诺十轮停，交施工侧补齐，容量/期限实现仍待真人核对。
- 已合origin/main 7bb4573（PR24/25）进ytq，含导入恢复/DSH-Claude分离/双浮窗等；worker_permissions迁移与LLM收据队列结构已接。persona.py三处导入观测接线遗留以 `docs/设计文档/整体设计迭代概览/Alice未完成工作总览-20261104.md` 为准。人格协议12/initial_plus_delta_v3保留情绪张力但事实/能力/许可/停止严格，JSON仅语法恢复，必修可选共一次内容修复、可选失败整包回退。
- Windows凌晨导入与本机13:45导入分开，不混指标；双工具/GUI/人格长期效果待真人，Claude同任务续跑引用未开放。旧交付快照.git/pr-delivery/20261107-043450、私有资料/assets、冻结头像/待审大V6及备份/stash/其他worktree、W6—W9/蓝图待裁决范围保留。当前用户以ytq为工作及明确要求时推送目标，main只走PR。
