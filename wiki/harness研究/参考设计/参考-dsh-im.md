# 参考 · dsh-im：IM 渠道接入的完整工程样本

> 来源：[插件参考/dsh-im/](../插件参考/dsh-im/)（本地 clone，MIT；DeepSeek Harness 官方 Token 赞助的社区插件，GitHub Trending JS 日榜前十）
> 深析基于源码与文档通读（2026-10-29，ZCode）。问题意识：**"一个 harness 怎么接入十余个 IM 平台"**。

## 一句话

把 DeepSeek Harness 接到微信/飞书/钉钉/企微/QQ/Slack/Telegram/Discord/WhatsApp/iMessage/Matrix/邮件十几个 IM 渠道的插件：每渠道一套协议适配器，全部收敛到一个共享的 HarnessClient，双向消息走**会话映射 + 排队/插话**，另有独立寻址（`botId+targetId`）的主动投递服务。

## 接入是怎么实现的（核心问题的答案）

### 总体四层

```
IM 平台（微信 ilink / 飞书开放平台 / TG Bot API / …）
   │  轮询·WebSocket·回调（每平台原生方式）
   ▼
渠道适配层  src/channels/<platform>/          ← 每平台一个目录，唯一差异点
   api.mjs（协议） controller.mjs（状态机） runtime.mjs（连接监督+退避重试） bridge.mjs（消息语义）
   │  统一后的"文本/图片/文件/引用回复"事件
   ▼
共享语义层  src/channels/shared/              ← 所有渠道共用
   HarnessClient · busy-dispatcher(排队/插话) · deferred-delivery(超时补发) · 审批队列 · 分块 · 群聊历史 · 访问策略
   │
   ▼
宿主插件层  plugin-src/host/                  ← 以 DSH 插件形式装进宿主
   每渠道一个 host 插件（inject: connection/credentials/typertGateway）+ management-rpc + delivery-service
```

外加 `plugin-src/client/`（React 设置面板：扫码、渠道管理、i18n 中英跟随）。

### 关键机制一：Harness 接入是双通道的（`shared/harness-client.mjs`）

- **进程内**：插件跑在 DSH 宿主进程里，直接拿 `apiProxy` 调 RPC（`session.create`、提交 prompt、订阅 `events.mux` 事件流拿助手输出）——零网络开销；
- **进程外**：填 `baseUrl` 则走 HTTP+WebSocket 连独立 DSH 实例，还支持 `autostart` 直接 `spawn('dsh')` 拉起本地二进制。
- 一个 `HarnessClient` 类把两种传输抹平（`transport: 'host-api' | 'http'`），渠道层无感。

### 关键机制二：微信走的是官方 ilink 机器人接口（`weixin/weixin-api.mjs`）

不是野路子 web 微信：`ilinkai.weixin.qq.com` 的 AI bot 协议（版本 2.4.6）——二维码登录状态机（wait/scaned/confirmed/expired）、**35 秒长轮询**收消息、CDN 分片上传文件（64KB 块、3 次重试）、消息 ID 时间戳校验。飞书/钉钉/企微走开放平台 App Manifest 与事件回调；Telegram/Discord 走官方 Bot 网关；iMessage 是本机 Messages.app 的例外。

### 关键机制三：会话映射与并发（多人共用一个机器人）

- **每机器人独立的工作区/模型/会话绑定**（`BotWorkspaceStore`），渠道间互不干扰；
- **会话目录隔离**（可选）：每个对话在自己的目录里工作——建 Session 前按会话标识切工作目录，多人并行不互踩文件；
- **排队与插话**两模式：忙时新消息默认排队（回复排队提示）；插话模式把纯文本新消息作为 `steer` 补进当前任务——这是 Cortico 五级投递的插件级简化版（两模式 + steer）。

### 关键机制四：主动投递的稳定寻址（`delivery-service`）

调用方只存 `botId + targetId`（稳定的别名），**不绑定 sessionId/消息 ID/webhook**——会话可以换、目标路由可改，调用参数不变。三个入口：HTTP POST、同宿主 `ctx.dshIm.send()`、Connection RPC `message.send`。

### 关键机制五：凭据单向流

浏览器/设置页只拿二维码、Manifest、脱敏状态；Secret/Token 只单向提交给本机 Host，**任何 RPC 响应都不回传凭据**。管理 RPC 默认 `trusted-host` 权限。

## 与既有样本的对照

| | Cortico World | dsh-im 渠道层 | Alice 现状 |
|---|---|---|---|
| 形态 | 框架级事件边界（五级投递） | 插件级适配器（排队/插话两模式） | 仅本机 GUI（stdlib HTTP+SSE） |
| 平台差异收敛在 | World 接口（事件+工具契约） | `channels/<platform>/api.mjs` 一层 | — |
| 对宿主 | 语义化边界，Core 零语义 | HarnessClient 双通道（进程内/HTTP+WS） | service 单写者 |
| 主动触达 | 事件驱动 | botId+targetId 稳定寻址 | 蓝图活动周期 |

## 评价

**做得好的**：渠道差异被死死压在一层目录里，shared 层的语义归一（排队/补发/审批/分块/访问策略）是十几平台趟出来的通用词汇；HarnessClient 双通道设计让同一插件既能在宿主进程内零开销跑、也能拉起独立实例；主动投递的稳定寻址是对"会话生命周期与投递目标解耦"的干净答案；微信 ilink 官方协议的登录态机与长轮询实现可直接当文档读。

**局限**：插件形态受宿主 API 面约束（inject 服务、typertGateway 依赖）；排队/插话两模式比 Cortico 的五级粗，长任务高并发下的体验会退化；iMessage 依赖本机 Messages.app，跨平台不齐。

## 给 Alice 的启示（按价值）

1. **渠道适配的分层纪律**：若 Alice 未来接 IM（蓝图的渠道方向），"平台协议差异压在适配层、语义层全共享"是必抄结构——Alice 的 `service.ChatService` 单写者天然就是那个共享层，缺的只是每平台 api.mjs；
2. **排队与插话的词汇**：Alice 单用户单会话暂用不上，但多人/多渠道时代这就是 Cortico 五级的起步子集；
3. **主动投递稳定寻址**：活动周期系统"她主动开口"的寻址设计可参考（目标别名而非会话绑定）；
4. **会话目录隔离**：若 Alice 做多用户/多她实例，per-conversation 工作目录是现成答案；
5. 微信 ilink 是官方 AI bot 通道的存在证明——**将来接微信不必走野路子**，这个文件就是协议参考实现。
