# Alice ↔ DSH NEXT 本机任务桥

本目录实现受认证的握手、专属会话创建、派工、同会话追问、公开消息与工具事件、状态、取消和关闭。包名继续使用 `alice-dsh-bridge-probe`，以兼容此前安装的本地 link；代码版本为 **0.4.0**，新增专属历史会话的归档/恢复维护接口。沿用0.3版凭据门控（`enabled:true`、`allow_execution:true`）：没有有效托管/人工令牌时不监听，Alice 未显式配对和授权目录时不派工。本机用户已选的启用值保留；安装、加载、生成凭据和打开侧栏均不会自动发起模型任务。

当前目标为 `D:/dshNEXT/DSH NEXT`：Desktop 2.0.21-next、Harness 0.2.1-rc.4、Cordis 4.0.5。用户真实派工已确认连接、工具开始/结束事件和主聊天回报；写文件任务因创建时遗漏默认agent preset未完成。本版已补原生预设加载，仍需重新加载插件后用新会话人工验证。本轮只读诊断，不代启停 DSH、运行模型或重发任务。

**这是 DSH 原生权限下的任务桥，不是操作系统沙箱。** `boundary_enforced` 始终为 `false`，不提供任务文件强隔离或读取/网络隔离。执行仅使用自建且保有句柄的会话；历史整理只接受 Alice 账本绑定的精确专属身份，并核对原生头信息，不恢复 agent、读取事件正文或修改全局权限/模型设置。DSH 自身按正常生命周期保存任务会话；插件不绕过宿主存储机制。

## 任务整理（0.4.0）

Alice 的“设置 → DSH”分别保存归档/恢复、删除的同步选择，默认均关闭，独立于派工授权和次数。归档同步偏好可在未连接时预先保存；实际同步操作仍须连接支持该功能的插件，保存本身不会操作 DSH 会话。右侧任务详情提供归档、恢复与删除确认；归档任务在“已归档”列表查看。删除只清 Alice 正文及其派生内容，保留无正文的状态、额度和幂等记录。运行中或状态未知的任务不能整理。

此版本 DSH NEXT 公开支持 `workspaceRegistry.archiveSession` 与 `unarchiveSession`，没有会话永久删除接口。“同步删除到 DSH”因此不可启用；Alice 删除不会移除 DSH 历史或工作目录产物。归档使用 `stopActivity:false`，活跃会话会拒绝；恢复只改变列表可见性，不恢复执行句柄或发起模型工作。

认证后的 `tasks/maintain` 只接受固定操作、task/session/原实例、原目录、创建时间窗和可选精确创建时间。浏览器不能提交这些远端身份字段，由核心从不可变账本和历史授权回执提取。插件核对 `alice-UUIDv4`、根会话、原生 header 身份/目录/时间；原实例字段本身不是密码学证明。历史定位使用 `sessionPersistence.stat`，不调用 `resume` 或读取事件。原生归档服务在缓存缺失时可能内部加载 header 索引，插件不返回其他会话身份。

归档/恢复响应丢失时，Alice 保留待确认操作，不自动重发；用户显式“重试确认”沿用原目标，插件先读取目标状态。DSH 确认后才移动 Alice 记录；失败不会宣称同步完成。重载插件后重新配对才能取得新管理能力，旧执行任务不会自动重建。

## 在 Alice 中设置与连接

1. 确认 DSH NEXT 当前 profile 仍选中本 bundle，用户已安装的本地 link 指向本目录。本机启用值已保留，无需再抄配置或设置环境变量。升级到0.4.0后由用户正常重载 DSH 和 Alice；不要以重启掩盖尚未确认停止的旧任务。
2. 在 Alice「设置 → DSH」选择本机托管令牌的创建/连接动作。Alice 服务端生成独立随机令牌，以当前 Windows 用户的 DPAPI 加密保存；令牌不返回浏览器、不进入聊天，也不借用 DSH 登录凭据或模型密钥。目录、权限和执行上限也在这一页设置，右侧「协作会话」只管理任务记录。
3. 插件等待专用凭据文件出现，约每2秒检查变化，仅在变化时读取和解密，随后启动固定回环监听。Alice 的显式连接动作只尝试 hello；若提示凭据已准备但插件未就绪，先确认 DSH 已加载本版，再在 Alice 中重试连接。插件没有运行、解密失败或端口占用都不能当作已连接。
4. 连接成功后，在 Alice 明确授权工作目录、原生权限模式、执行次数与单轮时限。后续派工/追问才会发起模型工作。重启后仍需重新连接和授权；已有托管凭据可复用，旧任务不会自动恢复。

托管方式仅支持 Windows，而且 Alice 和 DSH 必须运行于同一 Windows 用户。它不修改 DSH 私有 profile、登录信息或进程环境。DPAPI 保护磁盘上的静态秘密，同一用户下有相应权限的进程仍可能解密；它不是进程隔离或额外的任务沙箱。

配置仍只接受 `enabled`、`allow_execution`、`port` 三项，端口固定18765。`enabled` 控制监听，`allow_execution` 控制新建/发送；GUI 授权独立保留。0.3发布配置两项为true，以免新用户手改YAML；可以手动设为false或通过DSH关闭整个bundle。没有有效凭据时仍不监听，插件没有自动生成的设置表单。

## 专用凭据格式与轮换

固定文件为 `%LOCALAPPDATA%\Alice\dsh-bridge\credential.json`，只接受本地磁盘路径，不接受浏览器或请求提供的路径。Alice 仅在用户明确操作时创建目录/文件；插件只读，不创建或修复凭据，不读取 DSH 个人文件。外层严格包含四个字符串字段：

```json
{
  "format": "alice-dsh-bridge-credential-v1",
  "protection": "dpapi-current-user",
  "credential_id": "<64位小写hex身份摘要>",
  "ciphertext": "<DPAPI密文的标准base64>"
}
```

`credential_id` 是 Alice 对本次会话 epoch、NUL 和请求 ID 的 SHA-256，用于轮换幂等，不是令牌。被加密的内容是严格ASCII JSON，含 `format:"alice-dsh-bridge-secret-v1"`、同一 `credential_id` 和43字符 `token`。DPAPI使用 `CurrentUser`，固定附加entropy为 UTF-8 `Alice.DSH.Bridge.Credential.v1`。插件必须核对内外身份一致，再接受令牌；不能单改外层ID来伪装已完成轮换。

文件最大8192字节，解码密文最大4096字节；拒绝多余/重复字段、非规范base64、符号链接/目录跳转、多硬链接及路径偏移。打开前后、解密后复核文件指纹。插件通过固定系统 PowerShell、`-NoProfile -NonInteractive` 隐藏子进程调用 `ProtectedData.Unprotect`：密文仅通过stdin，解密JSON只通过受限stdout进入内存；令牌不出现在参数、子进程环境、临时文件、异常或日志。子进程5秒超时、stdout最多1024字节，失败只记录固定诊断。同一指纹失败不会循环启动解密进程。一次PowerShell瞬时失败后，可由用户重新加载插件重新尝试；不执行无界自动重试，也不因此发送任务。

凭据变化或删除时，插件先关闭旧监听，取消并释放自有会话。只有确认旧句柄已释放才允许新令牌启动新实例；15秒清理仍不明确会保持关闭并要求人工处理，不自动强杀共享DSH。同一DSH进程内的生命周期租约保留旧bridge，跨模块热重载也不能绕过未确认清理；新实例须核对旧句柄已释放，否则只在人工处理并重新启动DSH进程后恢复，仍不宣称外部进程树全部停止。新实例ID改变后，Alice重新握手，旧实例任务不重建。检测存在最多有一个轮询间隔，不能把删除操作宣称为瞬时终止。

托管文件优先于 `ALICE_DSH_BRIDGE_TOKEN`。本DSH进程一旦观察到托管文件或非法托管路径，会保留进程级标记，正常清理或插件热重载不清除；之后不会因删除/损坏回退到旧环境令牌。仅本进程从未看到托管文件且文件缺失（或非Windows）时，才保留原先人工环境令牌兼容方式；格式仍为 `^[A-Za-z0-9_-]{32,256}$`，不得复用模型密钥。重启整个DSH进程后，若用户仍显式设置旧环境令牌，且新进程从未观察到托管文件，则属于人工兼容入口；本版不写磁盘tombstone。托管主流程不需要命令行或复制令牌，也不将明文提供给旧终端握手工具。

真实任务验收按[实施与人工验收](../../../docs/DSH接入与协作会话-实施与人工验收.md)进行。本轮没有生成真实托管令牌、调用DPAPI/PowerShell解密、访问实际凭据文件或加载插件；只做实现和静态检查。

## 权限、执行范围与预算

创建专属会话先通过 `agentPresets.resolve()` 选择 DSH 当前默认预设，并把其身份放入会话元数据；在异步 `setup` 中调用 `agentPresets.mount(agentCtx, id)`，以取得该预设的原生工具和提示。专属权限及工具guard先安装，挂载后核对 `composedPreset`、原生模式、never审批及工作目录，发布后再次核对。解析和创建共用10秒准备时限；预设不可用时明确拒绝，不回退到只有工作目录/依赖路径工具的空能力会话。此步骤不写全局预设，不修改日常会话。

每个 `create` 使用当前 DSH 默认模型选择和随机的 `alice-<UUID>` 会话身份，**不调用 resume**。在发布前通过专属 session 设置 `read-only` 或 `workspace-write`，随后将该 session 审批策略设为 `never`。设置后及每次发起模型/工具调用前，读回核对模式、审批与工作目录；不符合则拒绝。权限选择加 `never` 后 DSH 可能显示自定义模式，这是审批策略覆盖的结果。日常会话的默认模式和全局设置不变。

任务工具采用该 DSH agent 的工具环境，并阻止已核实的常见子代理、团队调度及插件管理工具名。此名字防护不是任意第三方插件或 shell 的隔离证明；原生权限和用户授权仍是必要前提。工作目录检查不把目录当作安全沙箱，不能据此宣称读取、网络、进程或系统文件强隔离。

每次 `send` 有三项限额：模型请求次数 1–8、每请求输出 `maxTokens` 256–8192、协作取消时限 30–1800 秒。Alice 默认采用 8 次、4096 输出 token；每次追问单独计次。`agent/request` hook 包含根 agent 的重试，调用前计数并夹紧输出上限；到时请求取消，停止后等待 `whenIdle`。这些限制**不是总 token 或金额硬帽**：附加模型活动、工具费用及宿主插件行为不能据此承诺全覆盖。

`usage` 只累计本次发送中 DSH 公布的请求用量，注明 `source:dsh_reported`、`coverage:owned_agent_only`。没有样本时 token 为 `null`，存在缺报时状态为 `partial`；金额始终 `null` / `unknown`，不能把未知当作免费。取消是协作式；`agent_idle:true` 或 `dispose_confirmed:true` 都不证明外部子进程树已退出，`termination_confirmed` 始终为 `false`。15 秒内未确认停止会保留 unknown 和插件执行槽，不杀共享宿主。

## 固定 HTTP 契约

所有端点只接受 HTTP/1.1，唯一且精确的 `Host: 127.0.0.1:18765` 与 `Authorization: Bearer <token>`。拒绝 query、Origin、Referer、Sec-Fetch-*、Transfer-Encoding、Expect、Trailer；没有 CORS、重定向或任意目标。比较令牌使用 `timingSafeEqual`。请求头上限4096字节、32项，连接上限8，请求/套接字时限15秒；每个响应关闭连接。POST 必须唯一 `Content-Type: application/json` 和精确 `Content-Length`，体积不超过128KiB。

`GET /alice-bridge/v1/hello` 没有请求体或 Content-Type/Content-Length，返回：

```json
{
  "protocol": "alice-dsh-plugin-bridge-v1",
  "instance_id": "<本次插件实例UUID>",
  "execution_enabled": true,
  "boundary_enforced": false,
  "status": "ready",
  "permission_profiles": ["dsh_native_v1"],
  "permission_modes": ["read-only", "workspace-write"],
  "budget_mode": "request_count_and_cooperative_timeout",
  "cost_hard_cap": false,
  "capabilities": {"agents_create": true, "sessions_get": true, "permission_set": true,
    "permission_current": true, "sandbox_resolve": true, "approval_effective": true,
    "default_model": true, "tools_guard": true, "events_subscribe": true}
}
```

实际能力布尔值由当前 ctx 的函数存在性决定；没有创建 AgentHandle 来探测。未启用业务时 `execution_enabled:false`、`status:execution_disabled`。hello 成功只证明当时连接和方法存在。

POST 路径为 `/alice-bridge/v1/tasks/<操作>`，所有请求都带 `instance_id`、`task_id`，操作字段如下；其余字段拒绝：

| 操作 | 额外字段 | 结果 |
| --- | --- | --- |
| `create` | `request_id,cwd,permission_profile:"dsh_native_v1",native_permission_ack:true,permission_mode,max_output_tokens,max_turns,timeout_seconds` | 新建空闲的专属会话及状态；无任务正文 |
| `send` | `request_id,attempt_id,message_id,text` | `accepted,dsh_message_id,state,event_cursor`；调用官方 `createUserMessage` 后进入同一 agent 的 followup |
| `status` | 无 | `state,session_id,active_attempt_id,last_attempt_id,agent_idle,event_cursor,floor_cursor,usage,permission_mode,approval_policy,cancel_requested,close_requested,dispose_confirmed,termination_confirmed,last_error` |
| `events` | `after_cursor`，可选 `limit` 1–100 | `events,next_cursor,event_cursor,floor_cursor,truncated` |
| `cancel` | `request_id` | 受理取消和当前状态；随后查询确认空闲 |
| `close` | `request_id` | 受理关闭和当前状态；随后查询 `dispose_confirmed` |

所有成功响应带 `instance_id,task_id`；任务状态枚举为 `idle/running/succeeded/failed/cancelled/unknown/closed`。变更请求按 `request_id + 操作 + 规范化参数摘要` 保留收据；同 ID 同参数返回原收据，同 ID 改参数返回409。新实例拒绝旧 `instance_id`；同 task/message/attempt 身份不接受重新派工。响应丢失由 Alice 保留 unknown，不自动重发。错误只有固定代码，HTTP 接口不回传异常对象、堆栈或宿主错误文本。

插件全实例只有一个活跃发送槽，多个空闲会话不占执行槽。并发发送返回 `bridge_busy`；未确认停稳时不释放槽。单任务空闲15分钟自动 dispose，之后不能续接，需明确创建新任务。最多16个未确认释放的句柄、64条任务记录、4096个任务身份；收据上限8192，新建/发送在4096条时停止准入，为取消/关闭留空间。关闭记录可淘汰，但本实例任务身份保留为 tombstone；达到容量后须结束当前工作并由人工重新加载，不能靠清缓存重发未知工作。

事件是专属 Session 对象和该次 inbox claimed 轮次的投影，不读取历史、日常会话或原始事件包。每条含 `sequence,type,attempt_id,actor,observed_at`；正常完成的助手文本为 `agent_message_completed`，带稳定 `message_id,text`，只取公开 text 块。reasoning、system、工具参数/输出不投影；中断、错误或输出上限截断的正文不标 completed。工具仅发 `tool_started/tool_completed/tool_failed` 和名称。终态为 `run_completed/run_failed/run_stopped`；辅助事件包括 `task_created/run_started/alice_message_sent/usage_updated/cancel_requested/close_requested/task_closed`。

文本去除控制与方向字符，遮盖本桥令牌及常见密钥形态，UTF-8最多32KiB，超限标 `text_truncated:true`；这不是对任意秘密的完备识别。Alice 仍须按公开消息准入规则处理正文，不把文本变成宿主命令。每任务事件最多512条或512KiB，分页最多100条或约128KiB。`next_cursor` 是本页最后序号，无结果则保持请求游标；`event_cursor` 是当前末尾；`floor_cursor` 是仍可完整续读的最小前置游标。请求低于 floor 返回409 `events_expired`；正常分页 `truncated:false`。终态后仍需读到 `event_cursor`，不能把未拉完的事件当作完整结果。

## 安装兼容证据与生命周期

包采用官方 ESM `name/inject/apply` 和 `ctx.effect`；依赖 `agents/sessions/permissionPresets/sandboxPolicy/approval/agentDefaultModel/tools`。没有臆造 Config 依赖。`@deepseek-ai/dsh-llm` 和 `@deepseek-ai/dsh-user-approval` 是精确版本 peer，用于官方消息构造、流 usage/finish 读取及单 session 审批事件；不是复制上游实现。

NEXT 对本地 link 有明确的运行时依赖路由，因此此目录无需私装宿主包。以下行号均来自新目标的 `resources/app.asar/node_modules/@deepseek-ai/`，属于静态证据，不是已加载证明：

| 来源 | 已核实用途 |
| --- | --- |
| `dsh-app-boot/lib/index.js:664–691` | 当前 profile 外链注册真实路径 linkedRoots |
| 同文件 `1340–1348,1680–1694` | 外链 importer 按其 peerDependencies 路由到宿主安装包 |
| 同文件 `1850–1893,3506–3516` | ESM/CommonJS 解析拦截和 PluginPackages 生命周期 |
| `dsh-agent/lib/index.js:453,466`；`dsh-agent-loop/lib/index.js:1893–1933` | 官方 create/resume 面及创建前 setup；本桥只 create |
| `dsh-agent-loop/lib/index.js:806–820,870–874,1714–1745` | followup、清空 inbox 的 cancel、whenIdle、完整句柄 dispose |
| 同文件 `103–109,682–697,1029–1032,1156–1166,1191–1195` | claimed轮次、工具事件、回合结束、助手settlement、请求hook |
| `dsh-llm/lib/index.js:59–63,1411–1415` | createUserMessage、lastAssistantStreamChunk |
| `dsh-permission-presets/lib/index.js:344–360`；`dsh-user-approval/lib/index.js:63–65,152–154` | 单session权限设置、审批事件与读回 |
| `dsh-sandbox-policy/lib/index.js:141–146`；`dsh-agent-default-model/lib/index.js:38–44` | 生效模式/工作目录与默认模型选择 |
| `dsh-tools/lib/index.js:3017–3030` | agent作用域工具guard |

监听异常只发固定诊断并开始自有任务清理，不把原始错误或秘密写日志，不宣称启动成功。卸载关闭监听和连接，取消并 dispose 自有句柄，重复清理等待同一 promise；15秒后仍不确定时明确记录未确认，不终止共享 DSH 宿主。完整接口背景见[接口核验](../../../docs/DSH接入-接口核验.md)。
