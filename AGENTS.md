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
- **分支流程（2026-11-02 当前用户纠正）**：当前用户在 `ytq` 上工作，后续改动以 `ytq` 为工作分支及用户明确要求推送时的目标；此前 `trial/windows-v0.2-20261028` 的归属说明不适用于当前用户。Claude直调仍限定 `ytq`，不合 main；其他获准合入的改动验收后走 **PR 合入 main**（不再本地 merge 推 main）；**main 不接受直接 push**。合并前跑共存检查：`compileall` + 起服验证 + grep 对方分支可能触碰的路径（语义冲突 git 报不出来，三处旧路径 bug 的教训）。本机 origin 当前为 SSH（2026-11-03 上传时核实），沿用现有远端配置。

## 当前

- 2026-11-03 Codex按用户“先帮我上传”打包人格/上下文修复与必要学习/记忆依赖，共35路径(服务/样式分块)到ytq；本地原领先origin/ytq38提交，无远端分歧，正常快进推送，不推main，最终提交/远端状态见git log。排除Claude直调、worker改动、其他UI及资料重排，未改其工作树；user/dist/配置密钥不入提交。暂存快照compileall/JS语法/本地模块完整性/diff检查过，现有真人验收证据§19：T14确认方、T16前情生成复用、T17定位、T18限制对照与recap_reply审查accepted；提取拒绝/异常/撤回/复杂条件等边界待，历史失败不改判。当前沿53797/127.0.0.1:8800，50消息28记忆(22valid/6待审)旧卡2/新卡2/提取审计4/persona6f5f692a59409af1，cursor48；本轮未动user/或重启/代发模型。窗口16/indexed+C2+history+recap开，备份/private/tmp/alice-recap-answer-guard-20261103-222607及旧备份/repairs/38条归档保留。打包清单/private/tmp/alice-upload-current.txt所指目录；详docs/设计文档/上下文组装优化/人格与上下文优化-实施与人工验收-20261103.md §20。审查26秒成本留作优化，P1b/气质建议/N09/worker P2来源契约未做，不标全项目完工。
- 旧会话网络验收（现已归档，不是当前空白活库）2026-11-03 Codex网络P7：日常链路/浓缩/工具上限/35-36后台interpret抢占已验；真人GUI正常退出后已核实停服并协助起服，原df093fee…cancelled任务及4条attempt与基线完全一致，f44f7973…仍unknown/cancelled，日预算q5/n14/m2/body2663137/shares3未变，无旧任务重发/新记忆，9/10/11正文hash/期限/有效性未改，HTTP200/聊天ready、epoch保留、新instance正常，配置17/4轮保留。正常重启样本通过，不泛化强制崩溃/其他输运/额度耗尽。其余固定边界未全验，N09方向已确认待设计；未提交推送。 本轮动user/data/：用户实际正常关闭，确认8800停服且无serve/discovery_worker后新增停服备份/private/tmp/alice-stopped-before-cancelled-task-restart-20261103-111821.db，再用`.venv/bin/python -m alice serve --no-open`起服；未改源代码、配置、旧账本或预留。对账基线/private/tmp/alice-cancelled-task-restart-baseline.json、停服前备份111504及历史备份保留；当前服务运行，用户刷新退出页即可使用，禁止双实例写同库，user/dist不入库。 用户已决定结束本轮人工验收，未验项仍为未验。新增docs/设计文档/Alice现状-模块分工与宿主边界-20261103.md，覆盖50个顶层py及宿主/模型/数据/协作边界；仅文档梳理，未改运行代码或user/、未重启。
- Claude直调仍限定ytq、不合main；前轮Codex R1~R6修复及GUI/worker/Claude未提交改动保留，诊断见docs/设计文档/claude直调-质询卡缺失与闭环故障诊断-2026-11-02.md。旧8ea702ea任务核实进程组消失后标interrupted+停止确认、未重派改费；此前备份/private/tmp/alice-before-cli-repair-20261102-160849.db。真实GUI权限质询及跨CLI能力待验。
- PR20分屏浮窗及PR19 DSH协作/头像交互已交付，动画及业务待人工；历史任务书/截图/重复v2评审/QQ群聊方案本地未提交。正典沿用f983fa7：trial3732ffe、P1唯一路径DSH插件桥，旧D1撤回；worker_artifacts.py/testbed未接线，文件ID导入/产物下载/跨任务来源转发未开放，P2来源契约待设计。前轮ZCode已备份并清7张空worker表后重建对方10表，起服即写库的协作风险保留。
- QQ私聊检索PR17及安装PR15/16已合main，qq-owner仍需内容访问+聊天查找+QQ资料许可，共享额度、来源分渠道；群聊未合。GUI知乎candidate/url、观察器/见闻/深海UI/GUI与QQ共存、A02/A05及旧SDK适配待人工；DSH插件0.4.0本地link、原生权限非OS沙箱、1执行+4待办、NEXT无永久删除。活库元先验待下次人格重装；本机无timeout/Chrome，PR走compare页手动建，推送须用户明示且目标ytq。人格/上下文实施进度见首条。本轮按用户要求将名册发现-本机agent盘点设计-v1.md改为v1.1：macOS静态发现/选定批量探测分开，候选身份与证据分级、独立报告/恢复、POST发起/GET只读、GUI读取与派生shadow；首版不新增通用注册器。名册v1.1仍仅设计，未执行CLI探测或重启。
