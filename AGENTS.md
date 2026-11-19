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
- **能力增减须同步能力层JSON注册表与必要自我事实**（`alice/capabilities/commands.json`、`alice/constitution.json`）；注册表与SDK工具同源，宪法不重复工具目录或用户操作教程——自模型与实际能力须一致（2026-11-11用户确认的新组织方式）。
- 提交只暂存自己碰的路径，不用 `git add -A`（工作区常有另一棒的半成品）。
- **分支流程（2026-11-03 当前用户更新，2026-11-05 合并后保留）**：当前用户在 `ytq` 上工作，后续改动以 `ytq` 为工作分支及用户明确要求推送时的目标；此前 `trial/windows-v0.2-20261028` 的归属说明不适用于当前用户。其他改动（含 Claude 直调）在完成对应验收及共存检查后走 **PR 合入 main**；**main 不接受直接 push**。合并前跑共存检查：`compileall` + 起服验证 + grep 对方分支可能触碰的路径。本机 origin 为 SSH。


## 当前

- 11-17/18 Orbit主题（多Agent团队制全链路+真机迭代）：05轮次0–12；层级口径达标（L1帽90：P1 38/P2 89/P3 66/P4 49）+hint收纳引擎；11-18用户裁定背景全局化——神经球+房间恒定居中不随页迁移（五档POSE/迁移弹簧删除，OrbitBG.setPage空操作），页壳玻璃化--orbit-surface-glass #0d162466（orbit-page/.chat/.welcome-card，窗/浮层实面不动），四页真机验证球透壳可见文字可读；提交9746082及本轮新提交均未推送；动效观感与句级揭示实观待真人按docs/设计文档/主题-orbit-多页面重构-20261117/07-人工验收清单.md验收。
- 11-18按用户授权完成联系等待+思考价值：新增autonomy_v2/contact_wait.py/planning.py、v2_contact_holds/v2_activity_plans；超时/忙持久跨主题等待并撤销旧prepared，迟来相关回复可绑定真实请求，contact_availability前台busy/available、话题等待独立，首次保守接纳旧deferred；仅request关联开放疑问挂waiting_user，独立研究继续。后台activity_plan四问+下一步→研究执行门→activity_finish.goal_result同事务，restatement/登记自写不算新研究，价值不足原版本退出调度，后台新疑问先paused、激活需理由/下一步，free defer/rest同输入不反复付费找事；索引仅近3摘要，详情/等待/价值状态接UI，来源空间/失效清理同步；能力/宪法v8。设计实施见自主活动-联系等待与思考价值-优化记录-20261118.md。0.1.19最终20261118T002840Z-97460823-dirty（149资源摘要一致/DMG41,723,709字节/清单v20/App与DMG及更新签验过/六材料仅本机，002710初包替代）；静态编译/AST/导入/注解/JSON/TOML/JS/版本过，未机器业务自测/模型调用/真实资料配置修改/迁移/安装启停/提交推送；行为人工验收待。临时TEST_CONTROLS_ONLY=True仍跳过额度时段间隔，同材料硬上限测试例外保留；回应/价值等待不随测试放宽，正式发布前撤回临时策略。
- 11-19 合入main PR #37（c3cc4fa）：沿用分组设置、联网额度和观察器双栏，保留Orbit、桌面气泡、上下文观测、自主活动v2及当前渠道策略；共存检查与未提交改动的处理见本轮回执。既有design-lab真人体验仍待。
- 11-16桌面0.1.6–0.1.9透明气泡/两级入口/逐句气泡与addons/ui-skills-lab 19技能已提交（8f498f2/b45e515），跟随/回看/动效与19技能真人体验均待；ProfileLease实际恢复与中断撤销继续暂缓；QQ人格/阶段5/七日非永久等原未完项保留（详见git log与原报告）。
- 11-17按用户要求新增设置→上下文观测：入口/正文留存独立开关，GET/POST /api/observer/settings独立GUI意图+会话/文件版本，原子修改当前配置三布尔项，保存入口立即生效/采集从新请求起生效，在途Trace保留原策略；源码config_edit/service/server、web index/app/context-viewer.css，Orbit仅补中文名/图标，桌面dialog同源iframe绕过原新窗拦截并卸载关闭，普通浏览器独立页；探针失败不永久禁用，保存后重挂消息入口。0.1.18最终20261117T141412Z-eb6807dc-dirty（147资源/DMG41,726,510字节，清单v19，源码包摘要/App/DMG/更新签验/六材料仅本机），文档上下文观测器-设置开关-设计与实施-20261117.md。保留0.1.17气泡收尾/重播/同步超时修复及0.1.12资料GUI投影（见对应报告），真实UI验收均待；11-18上轮只读确认安装版0.1.18，工作区观测三开关true/独立桌面默认插件与正文关闭的差异未自动修改。用户另报小作文，近两轮638/487字，建议分回合表达，未改人格或表达行为。仅静态/构建，未业务机器自测/调模型/写user或桌面资料/迁移/安装启停/提交推送；8800仍原Orbit进程。 只读核对记忆装配差异：桌面config无context覆盖、安装包默认assembly_mode=legacy（前台直接正文、工作集/history/recap默认关）；工作区user/config为indexed且三项开；后台自主路径直接MemoryContext按索引并受模式读取权限约束。未改配置，已向用户说明未统一启用。 用户本轮授权上推ytq：本批源码/设计/0.1.10–0.1.18说明按明确路径暂存提交，包含必要v2接班实现；design-lab未提交样例保留，安装包/本机配置不入库，远端结果见本轮回执。
