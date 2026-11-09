# Alice QQ 接入 · 实现架构说明

> 整理：ZCode，2026-11-02。基于源码通读（`addons/qq/` 1726 行 Node.js + `alice/channels.py` 539 行 Python + `alice/server.py` 渠道路由）。
> 功能面：**官方 QQ 机器人 · 单一已确认所有者 · C2C 纯文字 · 被动回复**。群聊/陌生人/附件/主动消息/管理操作全部关闭。

## 一、总览：三方架构

```
┌─────────────────────┐   WebSocket(官方)   ┌──────────────────────────┐
│  腾讯 QQ 官方服务     │◄──────────────────►│  QQ 桥（addons/qq，Node） │
│  api.bot.qq.com     │   收C2C事件/发消息   │  独立进程 · 不监听公网      │
└─────────────────────┘                    └───────────┬──────────────┘
                                                        │ 本机 HTTP（127.0.0.1）
                                                        │ /api/channels/v1/*（machine token 鉴权）
                                            ┌───────────▼──────────────┐
                                            │  Alice 服务（Python）      │
                                            │  server.py 路由            │
                                            │  → ChannelCoordinator      │
                                            │  → ChatService（单写者锁）   │
                                            │  → 三层记忆/思想层/人格      │
                                            └──────────────────────────┘
```

**核心设计决策：QQ 桥不是 Alice 的一部分，是独立进程**。它以 `addons/qq/` 独立包存在（Node ≥22.19，官方 SDK `@tencent-connect/qqbot-*`），通过本机回环 HTTP + 机器令牌与 Alice 通信。**同一个 Alice**：QQ 消息走渠道协议进入 ChatService，与 GUI 共用会话、记忆、人格——渠道只是嘴。

## 二、两侧职责

### Alice 侧（Python，`alice/channels.py`）

- **ChannelCoordinator**：渠道请求的准入与状态机——
  - `session_epoch`（会话纪元）：重置/归档时更换，桥上所有请求必须携带当前纪元，旧纪元请求 409 隔离；
  - `reserve/release`：**单槽预订**——同一时刻只处理一个渠道请求（与 GUI 的 `_lock` 互斥），GUI 请求也走 `gui_request()` 归一到同一协议；
  - `normalize/submit`：严苛入站校验（来源六字段、仅本人 C2C、时间窗 ±300s、payload 哈希、**source_key 幂等**——同一条 QQ 消息重复投递只返回已有结果，绝不重复生成）；
  - 三张表：`channel_requests`（请求状态机）/ `channel_turns`（轮次关联）/ `channel_deliveries`（分段回执账本）；
  - `delivery()`：**发送回执状态机**——ready→sending→platform_confirmed/failed/send_unknown，状态不可回退，platform_confirmed 必须带真实消息 ID，sending 时重查 `_reply_sendable_locked`（来源过期则拒绝授权发送）。

### 桥侧（Node，`addons/qq/src/`）

| 模块 | 职责 |
|---|---|
| `main.mjs` | 终端入口：`scan/pair/config/run`；扫码状态机（ilink 风格 5 分钟时限）、机器凭据生成（0600 私有目录）、单例锁 |
| `web-control.mjs` | **网页控制进程**（推荐路径）：stdin/stdout JSONL 协议与 Alice 服务端通信；安装组件（uv 式自举）、扫码、配对、启停 QQ 连接；进程持有 runtime-lease 防双开 |
| `bridge.mjs` | **核心状态机**：收消息（去重/时效/绑定/媒体拒绝/长度闸）→ 提交 Alice → 轮询结果 → 冻结回复计划（分段）→ 逐段发送（频控/退避/回执上报） |
| `reply-plan.mjs` | 回复分段：QQ 消息长度限制切分、被动窗口预算 |
| `delivery-plan.mjs` | 发送计划冻结（`freezePlan`）：计划一经冻结不可改写，结果查询重复到达也不重算 |
| `storage.mjs` | Ledger（JSON 账本：请求/分段/频控时间戳，原子写）、SafeLog（脱敏日志）、机器令牌私有目录 |
| `alice-client.mjs` | Alice HTTP 客户端（26 行，machine token） |
| `runtime-lease.mjs` | 运行时租约（防双桥） |

## 三、一条消息的完整旅程

```
1. 收入    QQ C2C_MESSAGE_CREATE → bridge.receive()
           校验：本人 OpenID / 非机器人 / 事件时效（±5min）/ payload 哈希 / 幂等键
           媒体/超长 → 直接回"仅纯文字"提示（不走 Alice）
           /status /cancel 内建命令；// 转义字面斜杠
2. 排队    记 Ledger（status=queued，deadline=消息时间+被动窗口）
3. 提交    POST /api/channels/v1/qq-owner/submit（machine token）
           → normalize（六字段源/纪元/幂等）→ reserve 单槽 → accept 入库
           → 后台线程跑 ChatService 完整轮次（记忆提取/思辨检查全走）
4. 生成    202 accepted → 桥轮询 GET /result（先删 text 防泄露）
5. 冻结    回复生成 → buildReplyPlan 分段（QQ 上限）→ freezePlan（计划+哈希冻结，text 删除）
           delivery_allowed=false（来源撤销/归档）→ 停止未发分段
6. 逐段发  每段：report(ready) 注册 → report(sending) 授权 → QQ API POST（msg_seq 频控、
           429 全局退避、401 令牌刷新一次）→ platform_confirmed/failed/send_unknown
           每个终态回执上报 Alice 入 channel_deliveries（事实账本）
7. 异常态  被动窗口耗尽 → expired（完整回复留网页）；epoch 变化 → quarantined；
           结局不明 → send_unknown（不自动重发）；时钟回拨 → 停机
```

**发送纪律**（bridge 的精华）：所有 QQ 发送前三重检查（时钟未回拨 / 窗口预算足 / 纪元与绑定仍有效）；频控 `not_before` 全局共享（429 惩罚无法通过改记录绕过）；**结果不明的发送绝不自动重试**——宁可让用户在网页看完整回复。

## 四、安全模型

- **凭据分离**：AppSecret 只在桥侧私有目录（0600）；Alice 只持机器令牌的 SHA256——两侧泄露互不放大；
- **单向信任**：桥→Alice 用 machine token（HMAC 比对）；Alice→桥零信任（所有回执可独立审计）；
- **最小面**：仅本人 C2C 纯文字；管理操作一律在本机网页；模型不可见凭据；网页不回显密钥；
- **账本可审计**：每次 QQ 实际发送前 `save()` 落盘（中断保守计为 send_unknown）；terminus 回执只记事实，不授予权限。

## 五、与既有架构的咬合

| 既有件 | 在 QQ 链路中的角色 |
|---|---|
| `ChatService._lock` | 渠道请求与 GUI 互斥的单写者核心（`reserve` 持有时才动库） |
| 记忆提取/思辨/演化 watcher | QQ 轮次照常触发（同一个她） |
| 压力卫生 / A01 校验 | QQ 文本与 GUI 文本一视同仁 |
| 渠道协议 v1 | `protocol_version=1`——未来飞书/TG 等渠道复用同一准入框架 |

## 六、架构图

见 [QQ接入-架构图](../../wiki/harness研究/架构图/qq-bridge.png)（`tools/make_qq_bridge_diagram.py` 生成）。

---

*本文为实现说明；功能边界与使用步骤见 `addons/qq/README.md`。人工验收状态以 AGENTS「当前」为准（QQ 真实扫码/收发尚未验收）。*
