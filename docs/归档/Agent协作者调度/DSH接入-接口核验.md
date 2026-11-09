# DSH 接入：本机接口核验

日期：2026-11-01（Asia/Shanghai）。依据本机《协作者调度-DSH接入与右侧会话-开发任务书-v1》核验本机安装及公开插件接口；任务书及设计评审另行维护。当前唯一目标为 `D:/dshNEXT/DSH NEXT`，插件桥版本 **0.4.0**。已实现专属会话派工、原生权限读回、公开消息/事件、主聊天回报、独立令牌和归档/恢复维护；用户已确认初步功能完成，完整业务与边界验收仍待人工。旧 `D:/dsh` 已由用户删除；受控文件导入/收取链路仍未完成。

原独占 SDK 子进程接入路线不再实施；下文 SDK 事实仅作接口对照背景。用户已撤销 AppContainer 等隔离探索，不再推进或重启这些实验。插件本身不是操作系统沙箱，当前 `task_files_v1` 强隔离未具备，原 `task_files_v1` 仍不能宣称满足。本轮新增独立 `dsh_native_v1` 授权：只有用户明确接受原生权限、模型外发及非费用硬帽后才允许派工；不能写“已接通并验证”。

## 1. 身份、位置和证据

| 项目 | 本机实际证据 |
|---|---|
| 软件 | `D:/dshNEXT/DSH NEXT/DSH NEXT.exe`；PE ProductName 为 `DSH NEXT`，ProductVersion `2.0.21.0`，FileVersion `2.0.21-next` |
| 桌面包 | `resources/app.asar/package.json:2–16`：`dsh-desktop-next@2.0.21-next`，描述为 experimental upstream-inspired Desktop，仓库元数据 `anywhere-labs/deepseek-harness-desktop`，入口 `lib/main.js`，Node 要求 `^22.19.0 || >=24.0.0`；不能混称旧 Desktop 的同一发行包 |
| 内置 Harness | 同归档 `node_modules/@deepseek-ai/dsh/package.json:2–15`：`@deepseek-ai/dsh@0.2.1-rc.4`，bin 为 `lib/bin.js`；本次核对的 agent、session、权限及插件加载相关包均为 `0.2.1-rc.4` |
| Cordis | `node_modules/@deepseek-ai/cordis/package.json:2–15`：`@deepseek-ai/cordis@4.0.5`，ESM，入口 `lib/index.js` |
| 打包运行时 | 归档 `desktop-runtime.json` 的 `release`：Desktop `2.0.21-next`、DSH `0.2.1-rc.4`、Electron `44.7.0`、pnpm `11.28.5`；仅读清单，没有执行版本命令 |
| 资源布局 | `resources/app.asar`、`app.asar.unpacked`、`runtime/x64/primary-runtime`、`runtime/x64/office-skills`；不沿用旧版 `harness-node-entry.mjs` 启动假设 |
| 个人数据边界 | 未读取个人 DSH 配置、凭据或历史；未访问 `.dsh`、`.zcode`、Alice `user/` |

当前归档 SHA256：`16F2C62B1CCC98A968D9C7CF5EDCD7AF7A1E7690C2FBA8CEA361D1726D0DB349`。

安装核验采用有界 ASAR JSON header 和归档 offset 读取随安装发布的代码及文档，没有提取整个归档、复制上游实现或执行安装脚本。第 2–5 节未另行说明的包路径均位于新归档 `app.asar/node_modules/@deepseek-ai/`，行号针对上述 NEXT 归档；安装版本改变后须重新核对。旧版源码结论不自动视为新版本证据。

## 2. 插件桥的已核实接口

官方插件面提供 ESM 导出、服务注入、事件和资源生命周期。连接方向为 `Alice 任务账本 → 受认证本机插件接口 → 插件持有的专属会话`，不借用任意 Host 管理 RPC，也不接管日常会话。

| 能力 | 实际接口与语义 | 安装源码/文档定位 |
|---|---|---|
| 加载和卸载 | 命名 ESM `apply(ctx, config)`、数组 `inject` 继续支持；`ctx.effect` 接受清理函数并等待异步释放 | `cordis-plugin-loader/lib/index.js:451–464,667–671`；`cordis/lib/index.js:1169–1184,1491–1498,1619–1635` |
| 创建专属会话 | `ctx.agents.create({sessionId, meta:{cwd}, agentOptions, setup})` 返回插件持有的 AgentHandle；`setup` 在 agent 发布前注册任务级规则 | `dsh-agent/README.zh.md:30–43` |
| 默认任务工具预设 | 必须先 `agentPresets.resolve()`，会话元数据带 `agentPreset`，异步 setup 中 `await agentPresets.mount(agentCtx,id)`；`composedPreset` 核对身份。原生默认可由用户选择覆盖，不硬编码 standard | `dsh-api-session-controller/lib/index.js:351–362,517–524`；`dsh-agent-preset-registry/lib/index.js:596–607,654–694,717–718` |
| 工具所在作用域 | NEXT将agent plane迁入preset，全局pwsh/fs禁用；内置standard在Windows提供pwsh、fs及fs-search。漏mount时模型仍可回复，但缺终端/文件工具 | `dsh-web-app/cordis.patch.yml:465–470,488–508,591–594`；`dsh-web-app/presets/standard.patch.yml:8,24–32` |
| 同会话继续 | `handle.agent.followup({content, source})` 进入原 agent 的收件箱；NEXT 文档示例使用 text 内容块和来源 | `dsh-agent/README.zh.md:45–58` |
| 消息与轮次关联 | `agent/inbox/inserted`、`claimed {message,turn}`、`discarded`；消息入队不等于本轮完成 | `dsh-agent/README.zh.md:87–91` |
| 公开事件 | `session/event`、`agent/status`；另有临时呈现流 `agent/assistant-stream`，须按公开文本字段过滤，不透传整个事件对象 | `dsh-sdk-jsonrpc-server/lib/index.js:68–89`；`dsh-agent/README.zh.md:65–67` |
| 专属会话权限 | `ctx.permissionPresets.set(session, name)` 只给该 session 追加权限预设、sandbox 和 approval 事件，不写 settings 默认值 | `dsh-permission-presets/lib/index.js:344–360` |
| 核对有效模式 | `permissionPresets.current(session)`、`sandboxPolicy.resolve({session})`、`approval.effectivePolicy(session)`；须读回实际 sandbox/approval，不能只信预设名称 | `dsh-permission-presets/lib/index.js:291–305`；`dsh-sandbox-policy/lib/index.js:141–146`；`dsh-user-approval/lib/index.js:152–154` |
| 活跃会话查询 | `ctx.sessions.get(id)` 查询当前服务的会话映射；不授权插件访问其他会话 | `dsh-session/lib/index.js:1980–1981` |
| 停止本任务 | `handle.agent.cancel({kind:'user'})`、`await handle.agent.whenIdle()`，结束时 `handle.dispose()`；不关闭共享 Host | `dsh-agent-loop/lib/index.js:815–820,870–874,1714–1745` |
| 限制工具 | 在专属 `agent.ctx` 注册 `tools.restrict`/`tools.guard`；restrict 只屏蔽全局工具，scope-local 工具仍可见；新实现还拒绝空条件、未知全局工具及保留的 run_code 名称；guard 拒绝不能被后续监听器撤回 | `dsh-tools/lib/index.js:2991–3025` |
| 本地加载 | bundle 的 `dsh.bundle.patch` 指向 patch，`insert` 可引用相对文件路径；版本声明在导入前受检；NEXT 使用自身选定的 profile，不能假定旧 Desktop 或 CLI web profile 是目标 | `dsh-app-boot/README.zh.md:50–67`；`dsh-plugin-manager/README.zh.md:69,146` |

插件只对自己新建的 Alice 会话设置模式并读回核对，不改其他会话或全局默认。用户的真实派工已确认消息往返及主聊天回报；21:35的写文件任务因遗漏默认工具预设未完成。上述resolve/mount补丁已通过静态核验，仍需加载后的新会话真人验收，不能把会话正常结束表述为产物完成。

NEXT 的实际装载链是归档 `lib/host.js:204–245` 调用 `loadNextProfile` 与 `runProfile`；`lib/recovery-BEvwjPuD.js:583–608,640–657` 经 `loadProfileDirectory("dsh-desktop-next", ...)` 读取其拥有的 profile 和 bundle，再叠加 NEXT patch。`lib/plugin-cli.js:5–20` 把包操作绑定到 NEXT home、profile 和 install anchor。安装后定向只读检查位置记录、profile 选择记录及本插件包元数据，确认当前选中 `desktop`，本插件声明在其 dependencies 和 bundle 列表中；未读取凭据或会话文件，不改旧安装或带入旧认证。

## 3. 本轮派工桥实现与静态接口证据

[bridge_plugin](../addons/dsh_agent/bridge_plugin/README.md) 已由 hello 原型升级为业务桥，包名保留以兼容用户已安装的本地 link。0.3.0 采用凭据门控：配置已启用而无有效凭据时仍不监听；用户在 Alice GUI 生成/复用本机加密令牌后，插件自动读取并监听固定 `127.0.0.1:18765`。`GET /alice-bridge/v1/hello` 不创建或运行任务，协议为 `alice-dsh-plugin-bridge-v1`，返回实例身份、能力和 `execution_enabled`。`boundary_enforced` 始终为 false。

业务 POST 仅开放 `tasks/create|send|status|events|cancel|close`，绑定插件实例、Alice task/message/attempt 与专属 DSH session；修改操作按请求身份和参数摘要去重。只有 Alice 显式 GUI 配对后才访问固定认证回环端点，浏览器不直接持有 DSH 管理 RPC。响应丢失不自动重发，插件重启不按旧身份重建任务。

创建会话在 setup 中设置并读回原生访问模式与 `approval=never`；使用 DSH 当前默认模型，不能设置日常会话或全局默认。模型请求钩子限制本次发送最多 8 次根 agent 请求及单请求最多 4096 输出 token；30–1800 秒到期协作取消，不是 OS 进程树硬截止，也不是金额硬帽。插件阻止自身任务通过已识别的内置委派和插件管理工具扩展执行；shell/网络仍受 DSH 原生实现及其局限约束。

公开事件投影只含 assistant 的 text 内容及工具名，不含隐藏推理、系统提示、工具参数和完整输出；中断消息不作为 completed 正文。游标过期返回明确错误，Alice 标 unknown 而非拼接不完整成功记录。DSH 用量若缺失保持未知；根 agent 的自报 token 不等于完整费用。

Alice 端已接持久消息与尝试、一个执行槽和四个待办、次数预留、五个模型工具、锁内正文资格复核、GUI/QQ/记忆隔离及锁外排空。用户只授权原生目录派工；文件 ID 导入和附件收取/下载仍未开放。实现与人工步骤见[实施报告](DSH接入与协作会话-实施与人工验收.md)。

此前真实安装观察仅确认旧 0.1.0 在 NEXT desktop profile 中被选中、本地 link 指向项目目录、peer 匹配；当时 18765 无监听且未做 hello。此次修改代码不会被当作新的实际连接证据，须由用户重新加载 DSH/Alice 后人工验收。

精确 peer 基线保持 `@deepseek-ai/cordis: 4.0.5`、`@deepseek-ai/dsh: 0.2.1-rc.4`。新版在安装及导入前校验 DSH 版本要求，不能靠兼容豁免绕过适配。证据：`dsh-app-boot/README.zh.md:52–56`、`dsh-plugin-manager/README.zh.md:69–75`。新增模型、请求 hook、消息与公开事件源码定位补充在插件 README。

## 4. 权限与取消的真实边界

**插件不是操作系统沙箱，当前不能兑现原任务书 `task_files_v1`。** DSH 的 read-only/workspace-write 提供会话控制；NEXT 内置 Windows ACL 后端仍明确不限制任务外读取、网络或进程可见性，且存在硬链接边界及常驻 ACL/Low 标签副作用。工作目录、关闭 Web 工具或解析任意 shell 命令都不能证明文件读取和网络强隔离。证据：`dsh-sandbox-windows-acl/README.zh.md:127–134`。

预设自带 `approval: ask`，获批的单次 `request.mode` 优先于会话常设模式。后续桥若要求禁止临时升级，应对专属会话显式设 `approval=never` 并读回，不修改其他会话或全局默认；此时预设显示 custom 可能是正常结果。`approval.setPolicy(agent, policy)` 及有效策略查询存在，本轮派工桥已在专属会话 setup 使用这些控制，真实效果仍待人工验收。证据：`dsh-user-approval/lib/index.js:63–65,98–108,152–154`、`dsh-sandbox-policy/lib/index.js:141–145`。

agent cancel 默认清 inbox；Host 的 sessionController.cancel 则保留 inbox，不能混用。`whenIdle` 是 agent 层协作式静止，释放句柄不直接等于整个操作系统进程树已回收。NEXT 的 handle.dispose 会依次尝试取消/等待、scope.dispose、handle.close 及登记清理，并汇总失败；工具不响应、后台任务继续或超时仍须记未知。Windows 子进程后端仍存在弱 fallback 和 Job 外 ConPTY，不能凭代理 idle 宣称 `termination_confirmed`。证据：`dsh-agent-loop/lib/index.js:815–820,870–874,1714–1745`、`dsh-api-session-controller/lib/index.js:1060–1065`、`dsh-jobs-local/lib/index.js:811–865`、`dsh-subprocess-local/lib/index.js:1603–1606`。

插件与共享 Host 同生共灭，升级、卸载和资源争用会影响可用性；不改其他会话权限不代表共享进程有强隔离。本轮提供独立的 GUI 原生权限授权及实际会话读回，默认关闭。手动更改日常会话不自动授予 Alice 派工权；即使选择原生授权，也不能表述为原强隔离契约已满足。

## 5. 已核实的手动工作模式指引

手动指引供用户直接在 DSH NEXT 中工作，不表示 Alice 已设置、已读取或已确认模式。本节已对 NEXT 2.0.21-next / Harness 0.2.1-rc.4 重新核验；未观察用户当前界面或读取其配置。

- 当前会话输入框旁的“访问模式”，或在该会话输入不带参数的 `/permission`，可打开选择器。
- 只读分析/规划选择“仅可查看”（`read-only`）；生成或修改工作区文件选择“工作区内修改”（`workspace-write`）。须核对当前会话模式与工作区；工作目录不是读取隔离。
- 通用设置 →“权限”只更改当前 profile 以后新会话的默认值，不能替代当前会话设置。
- 另有“完全权限”（`danger-full-access`）。可选实验“自动审查”（EXP，旧版中文界面显示 Auto review）无沙箱、由同一模型逐工具审核并产生额外 token；不能替代原强隔离要求。

安装默认的“仅可查看”和“工作区内修改”都搭配 `approval: ask`，不代表每次读取或联网都会询问；获批升级后权限可能改变。当前会话设置只作用于当前会话；未来桥须对新建专属会话设置并读回，不能推定继承用户刚设置的另一会话模式。

| 安装定位 | 事实 |
|---|---|
| `dsh-client-ui-permission-presets/lib/client.js:160–161,190–206,455–457,769,796–823` | 包仍存在；三种中文模式、“自动审查”实验说明、/permission、通用设置及输入框入口；该入口不是 NEXT 的系统 microphone/screen 权限模块 |
| `dsh-base/cordis.patch.yml:246,259–276` | 三种预设及默认 `workspace-write + ask`；部署 profile/环境可覆盖 |
| `dsh-permission-presets/lib/index.js:370–388` | 新会话取默认，已建会话保留其有效权限 |
| `dsh-settings/README.zh.md:12,33–37` | 当前 profile patch 的持久化及优先级 |

Alice 存在事实与 GUI 连接指引已采用这些入口。安装未核实或版本变化时，不展示本版本精确步骤；用户自述已设置不能写成宿主核验通过。提示遵循和界面显示仍待人工验收。

## 6. 旧安装与 SDK 对照背景（不再实施该接入路线）

旧目标是 `D:/dsh/DSH Desktop` 的 `dsh-desktop@0.11.1`，内置 Harness `0.2.0-rc.2` 和 Cordis `4.0.4`，归档 SHA256 为 `C6AD1D6A90B2F52154BE97210C0E32CBBEDC471822D3FE2F3F74B21C619B1007`。这些仅是前期核验记录，不是当前 adapter 目标或本节以外源码行号的来源；本轮未修改旧安装。

旧归档的 SDK 只分发 initialize、session/prompt、shutdown，是前期选择插件控制面的背景。**这一限制不能照抄成 NEXT 的现状**：新归档 `dsh-sdk-jsonrpc-server/lib/index.js:275–299` 还分发 session/resume、session/wait、session/working-directory/get 和 set。此差异不改变用户已确定的插件主方向，也不恢复独占 SDK 接入工作。

新旧接口都不能把消息准入等同于任务完成，或把 cwd 当作权限边界。当前插件桥自行登记任务身份、关联消息轮次、处理未知状态，并以自己的窄接口和专用令牌连接；不读取桌面原认证，不把 Host 管理 RPC 暴露给 Alice 网页。

适配阶段只读核对 NEXT 安装代码并更新项目声明与文档；用户自行安装后，曾观察插件页面、被动查询默认端口并定向读取安装元数据。后续用户真实派工证据与各项待验状态，以实施报告为准。

## 7. 会话整理接口（0.4.0）

在同一 NEXT 安装包中核实，公开 `workspaceRegistry.archiveSession(id, {stopActivity:false})` 与 `unarchiveSession(id)` 支持归档和恢复可见性。没有已核实的会话永久删除接口；前端“删除工作区”明确保留文件和会话，不能代用。插件 `hello.management_capabilities` 独立报告 `{archive,restore,delete:false}`，不会因删除能力缺失阻断原有派工握手。

| 安装包位置（`node_modules/@deepseek-ai/` 下） | 核验事实 |
|---|---|
| `dsh-workspace/lib/index.js:506`、`:526`、`:553` | 归档集合、归档与取消归档；归档默认拒绝活跃会话 |
| `dsh-agent/lib/index.js:27`、`:596` | 活动状态提供者与精确 live agent 查询；不需要恢复 agent |
| `dsh-session-persistence-jsonl/lib/index.js:2564` | `stat` 仅读取目标会话头和revision，不读取日志事件 |
| `dsh-session/lib/index.js:1818` | 创建meta为白名单，任意自定义Alice标记不会持久化 |
| `dsh-api-workspace-controller/lib/index.js:297`、`:925` | 公开归档/恢复；原生结果包含集合，桥只投影选定目标的布尔值 |
| `dsh-client-ui-workspace/lib/client.js:4137`、`:4801` | 会话菜单归档/取消归档；删除工作区不删除会话记录 |

历史任务维护由核心账本给出精确session、原实例、原目录和首次执行时间窗；新任务另外保存原生header创建时间。插件通过 `stat` 校验根会话、`alice-UUIDv4`、目录和时间，不以标题或UUID前缀单独认领日常会话。归档服务在目标不在缓存时可能内部读取header索引；插件不枚举输出或读取其他会话正文。

当前闲置专属句柄在同步整理前释放，恢复只恢复可见性，不能据此恢复执行；核心记录 `session_closed`，防止无效追问占用执行次数。开始释放句柄或原生写入后的错误均保留为结果不确定；再次确认必须是用户显式操作，不会在后台自动重试或运行模型。
