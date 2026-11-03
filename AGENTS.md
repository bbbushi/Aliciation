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
- **分支流程（2026-11-02 当前用户纠正）**：当前用户在 `ytq` 上工作，后续改动以 `ytq` 为工作分支及用户明确要求推送时的目标；此前 `trial/windows-v0.2-20261028` 的归属说明不适用于当前用户。Claude直调仍限定 `ytq`，不合 main；其他获准合入的改动验收后走 **PR 合入 main**（不再本地 merge 推 main）；**main 不接受直接 push**。合并前跑共存检查：`compileall` + 起服验证 + grep 对方分支可能触碰的路径（语义冲突 git 报不出来，三处旧路径 bug 的教训）。当前 origin 使用 HTTPS。

## 当前

- 2026-11-03 ZCode 续：**C0 完成（199f08a+f7d4839）**——alice/context.py（块级 ContextManifest/保守 token 估算/完整往返后缀：孤行丢弃+预算内整对回收+一问多答不拆散）；build_messages 接往返选择/资料去重/历史预算（总预算−稳定−资料−工具预留−回复预留，默认 100k/4k 可配）；宪法四态（未载入≠遗忘/未命中≠未发生/普通知识≠共同经历）；loop 工具 schema 预留+每次调用前预算终检（超额明确报告）。离线自检全场景过（含修复往返回扫真 bug）；**C1（memory_search/memory_load+候选索引）待下轮**；两文档真人验收均待排。
- 2026-11-03 ZCode：**按两份架构优化设计文档施工（人格连续性/上下文组装）**——评估 10/10 定位属实后定序 P0→P1a→C0→C1；**P0 导入管线完成（771ade2）**：五路提取/收养审查传底色+存在事实+带ID/样本适配（风险改写+适配生成标记+丢弃留档）/来历中性（拒绝理由进 imports 审计）/两阶段提交（快照+op_id 幂等先验+原子替换+中断恢复）/取消整卡兜底/reset 先准备后归档；**P1a 完成（77194c9+48e6d71）**：persona_context.py（快照/指纹/预算化渲染/purpose 分权），loop 每轮固定快照+render_full+人格依据元数据，worker_reports 换共用解析。未动 user/ 活人格与既有数据；C0/C1（上下文组装）待下轮；P0/P1a 真人验收按设计 §9 待排。
- 2026-11-03 Codex网络P7：日常链路/浓缩/工具上限/35-36后台interpret抢占已验；真人GUI正常退出后已核实停服并协助起服，原df093fee…cancelled任务及4条attempt与基线完全一致，f44f7973…仍unknown/cancelled，日预算q5/n14/m2/body2663137/shares3未变，无旧任务重发/新记忆，9/10/11正文hash/期限/有效性未改，HTTP200/聊天ready、epoch保留、新instance正常，配置17/4轮保留。正常重启样本通过，不泛化强制崩溃/其他输运/额度耗尽。其余固定边界未全验，N09方向已确认待设计；未提交推送。 本轮动user/data/：用户实际正常关闭，确认8800停服且无serve/discovery_worker后新增停服备份/private/tmp/alice-stopped-before-cancelled-task-restart-20261103-111821.db，再用`.venv/bin/python -m alice serve --no-open`起服；未改源代码、配置、旧账本或预留。对账基线/private/tmp/alice-cancelled-task-restart-baseline.json、停服前备份111504及历史备份保留；当前服务运行，用户刷新退出页即可使用，禁止双实例写同库，user/dist不入库。
- Claude直调仍限定ytq、不合main；前轮Codex R1~R6修复及GUI/worker/Claude未提交改动保留，诊断见docs/设计文档/claude直调-质询卡缺失与闭环故障诊断-2026-11-02.md。旧8ea702ea任务核实进程组消失后标interrupted+停止确认、未重派改费；此前备份/private/tmp/alice-before-cli-repair-20261102-160849.db。真实GUI权限质询及跨CLI能力待验。
- PR20分屏浮窗及PR19 DSH协作/头像交互已交付，动画及业务待人工；历史任务书/截图/重复v2评审/QQ群聊方案本地未提交。正典沿用f983fa7：trial3732ffe、P1唯一路径DSH插件桥，旧D1撤回；worker_artifacts.py/testbed未接线，文件ID导入/产物下载/跨任务来源转发未开放，P2来源契约待设计。前轮ZCode已备份并清7张空worker表后重建对方10表，起服即写库的协作风险保留。
- QQ私聊检索PR17及安装PR15/16已合main，qq-owner仍需内容访问+聊天查找+QQ资料许可，共享额度、来源分渠道；群聊未合。GUI知乎candidate/url、观察器/见闻/深海UI/GUI与QQ共存、A02/A05及旧SDK适配待人工；DSH插件0.4.0本地link、原生权限非OS沙箱、1执行+4待办、NEXT无永久删除。活库元先验待下次人格重装；本机无timeout/Chrome，PR走compare页手动建，推送须用户明示且目标ytq。人格连续性/上下文组装两份设计已互链，另一棒P0/P1a实施进度见首条。本轮按用户要求将名册发现-本机agent盘点设计-v1.md改为v1.1：macOS静态发现/选定批量探测分开，候选身份与证据分级、独立报告/恢复、POST发起/GET只读、GUI读取与派生shadow；首版不新增通用注册器。链接/格式过；本轮仅改设计与交接，未改运行代码或user/、未执行CLI探测或重启、未提交推送。
