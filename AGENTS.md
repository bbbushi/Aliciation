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
- **分支流程（2026-11-03 当前用户更新）**：当前用户在 `ytq` 上工作，后续改动以 `ytq` 为工作分支及用户明确要求推送时的目标；此前 `trial/windows-v0.2-20261028` 的归属说明不适用于当前用户。**Claude 直调纳入可通过 PR 合入 `main` 的范围**，与其他改动一样在完成对应验收及共存检查后走 **PR 合入 main**；是否拆分 PR 按实际改动边界与审阅需要确定，包含 Claude 直调本身不要求拆分。**main 不接受直接 push**。合并前跑共存检查：`compileall` + 起服验证 + grep 对方分支可能触碰的路径（语义冲突 git 报不出来，三处旧路径 bug 的教训）。本机 origin 当前为 SSH（2026-11-03 上传时核实），沿用现有远端配置。

## 当前

- 2026-11-04 ZCode：提交前 bug 复查（用户指示先不提交）——实锤并修 persona.py 三处 record_llm_call(store) 作用域 NameError（导入管线跑到 LLM 提取即炸；store 穿透 _extract_import/_adapt_expression/_review_adoption/prepare_import 及 persona.py 两处、service.py:514 调用方）；补 3 处 claude 任务终态事件文案（原落 DSH 默认措辞）。交叉核对通过：claude 双轨派工链（排队/单槽/监视/落账/取消收敛）、质询身份四元链（gate→pending→list→GUI echo→answer）、normalize_result 契约、ledger.task 异常面、loop 观测作用域。离线自检 34 项全过（/tmp/alice_bugcheck_20261104/，合成数据未触 user/）+compileall。设计文档「模型参与上下文组装」加 §13 实装对齐记录、修头部状态与 §12 过时表述（legacy→活库全开）；架构图升 v0.5「共同组装与协作」（make_arch_diagram.py；docs/架构图.png 曾被 docs 搬迁删除断链，本轮恢复并留档+版本史自动追加；按用户确认加 ②→① 工具轮回环虚线）。按用户要求新增docs/设计文档/整体设计迭代概览/Alice未完成工作总览-20261104.md：施工队列（worker P2来源链/N09/气质审议/Step2审查开销/对照深审）、待真人验收边界、流程收尾（未提交/未推送/未重启）、他方协作面简报及关联文档，相对链接已核。本轮修复未重启加载。
- 2026-11-03 ZCode 续二：**评估报告 Step1 观测 + P1b 接线完成（未提交，随批）**——metrics.record_llm_call（用途/耗时/usage/错误/回退/模型指纹，未知记 unknown 不记零），接线七处：main/recap_generate/recap_review/rewrite|recap_reply_review/extraction/memory_review/import_extract/import_review/expression_adapt（共九处，均 try 包裹不阻塞调用）；P1b：PersonaConfig.expression_anchor（默认关）+ persona_context.render_anchor（精简气质+一条完整样本+边界，≤560 字符）+ loop 锚在每次模型请求前重建替换（同一份不堆积）；config.example.toml 补 expression_anchor。离线自检过（观测 unknown/错误/回退、锚渲染、P1a 回归）；**未重启未验**：服务仍 21504/legacy，P1b 效果按设计 §6.2 需 P0 验收后人工对照。
- 2026-11-03 Codex按用户要求新增docs/设计文档/上下文组装优化/Alice剩余工作-详细评估与推进报告-20261103.md：基于54b8eee/97d23ad及现代码/真人记录，详分审查耗时优化、P1b近位提醒、气质候选审议、N09定向重读、worker P2来源契约，列待验边界、依赖/取舍/验收/推进顺序及发布遗留，旧C3状态/旧不合main头部标作历史，不据其判当前。报告本地未提交推送，Markdown链接/表格核对过；本轮仅文档与交接，无运行代码/user/配置修改、重启/模型调用/测试/新PR。现行约定已由54b8eee同步ytq：Claude可经对应验收/共存检查后PR合main，main禁直推；此前上传97d23ad已核远端，工作树Claude/worker/UI/资料重排半成品仍在。上次运行记录53797/127.0.0.1:8800、50消息28记忆(22valid/6候审)旧卡2/新卡2/提取审计4/persona6f5f692a59409af1/cursor48，窗口16/indexed+C2+history+recap开，本轮未复核新活数据。验收证据及待验见实施记录§19~21；最新备份/private/tmp/alice-recap-answer-guard-20261103-222607及旧备份/repairs/38条归档保留。报告为建议非新施工，P1b/气质/N09/worker来源契约未做，性能未优化，不标全项目完工。
- 旧会话网络验收（现已归档，不是当前空白活库）2026-11-03 Codex网络P7：日常链路/浓缩/工具上限/35-36后台interpret抢占已验；真人GUI正常退出后已核实停服并协助起服，原df093fee…cancelled任务及4条attempt与基线完全一致，f44f7973…仍unknown/cancelled，日预算q5/n14/m2/body2663137/shares3未变，无旧任务重发/新记忆，9/10/11正文hash/期限/有效性未改，HTTP200/聊天ready、epoch保留、新instance正常，配置17/4轮保留。正常重启样本通过，不泛化强制崩溃/其他输运/额度耗尽。其余固定边界未全验，N09方向已确认待设计；未提交推送。 本轮动user/data/：用户实际正常关闭，确认8800停服且无serve/discovery_worker后新增停服备份/private/tmp/alice-stopped-before-cancelled-task-restart-20261103-111821.db，再用`.venv/bin/python -m alice serve --no-open`起服；未改源代码、配置、旧账本或预留。对账基线/private/tmp/alice-cancelled-task-restart-baseline.json、停服前备份111504及历史备份保留；当前服务运行，用户刷新退出页即可使用，禁止双实例写同库，user/dist不入库。 用户已决定结束本轮人工验收，未验项仍为未验。新增docs/设计文档/Alice现状-模块分工与宿主边界-20261103.md，覆盖50个顶层py及宿主/模型/数据/协作边界；仅文档梳理，未改运行代码或user/、未重启。
- Claude直调按2026-11-03用户新约定可经验收/共存检查后PR合main；前轮Codex R1~R6修复及GUI/worker/Claude未提交改动保留，诊断见docs/设计文档/claude直调-质询卡缺失与闭环故障诊断-2026-11-02.md。旧8ea702ea任务核实进程组消失后标interrupted+停止确认、未重派改费；此前备份/private/tmp/alice-before-cli-repair-20261102-160849.db。真实GUI权限质询及跨CLI能力待验。
- PR20分屏浮窗及PR19 DSH协作/头像交互已交付，动画及业务待人工；历史任务书/截图/重复v2评审/QQ群聊方案本地未提交。正典沿用f983fa7：trial3732ffe、P1唯一路径DSH插件桥，旧D1撤回；worker_artifacts.py/testbed未接线，文件ID导入/产物下载/跨任务来源转发未开放，P2来源契约待设计。前轮ZCode已备份并清7张空worker表后重建对方10表，起服即写库的协作风险保留。
- QQ私聊检索PR17及安装PR15/16已合main，qq-owner仍需内容访问+聊天查找+QQ资料许可，共享额度、来源分渠道；群聊未合。GUI知乎candidate/url、观察器/见闻/深海UI/GUI与QQ共存、A02/A05及旧SDK适配待人工；DSH插件0.4.0本地link、原生权限非OS沙箱、1执行+4待办、NEXT无永久删除。活库元先验待下次人格重装；本机无timeout/Chrome，PR走compare页手动建，推送须用户明示且目标ytq。人格/上下文实施进度见首条。本轮按用户要求将名册发现-本机agent盘点设计-v1.md改为v1.1：macOS静态发现/选定批量探测分开，候选身份与证据分级、独立报告/恢复、POST发起/GET只读、GUI读取与派生shadow；首版不新增通用注册器。名册v1.1仍仅设计，未执行CLI探测或重启。
