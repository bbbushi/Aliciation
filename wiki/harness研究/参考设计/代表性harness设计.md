# 代表性 Harness 设计

> 四个代表性样本 + 2026 格局速览。选择标准：每个样本代表一种设计立场，且立场内部自洽（见《通用设计理念》的四对张力）。每个条目附架构图（`架构图/` 目录）。

## 0. 2026 格局速览

第一方 harness（模型厂自建：Claude Code、Codex CLI、Gemini CLI）走**深度模型集成 + OS 级沙箱**路线；开源阵营分裂为两派——**极简自扩展派**（pi、OpenClaw：「模型擅长写代码并运行它，那就拥抱这件事」）和 **Claude Code 复刻派**（OpenCode 等）。安全侧已收敛于 pre-tool hook 的 allow/ask/deny 三态门。实证：同一模型跨 8 个 harness 通过率差 20 个点——harness 是第一等设计对象。

## 1. pi —— 极简主义的完全体

**立场**：工具与提示最少化（4 个工具、<1000 token），行为可预测性最大化。代表命题：RL 训练的前沿模型不需要精心调教的提示，harness 越薄越好。

**架构**（详见[参考-pi架构.md](./参考-pi架构.md)，图：`架构图/pi.png`）：
- 四层组件：pi-ai（LLM 抽象，四种 API 归一）→ pi-agent-core（循环/队列/可中断）→ pi-tui（终端 UI）→ pi-coding-agent（成品）
- **会话是可分支的树**（干净 JSON）：支线任务不污染主上下文
- **TypeScript 扩展 + 自扩展**（热重载），拒绝 MCP——外部能力用「CLI 工具 + README」渐进披露
- 定位是 SDK：「一组搭你自己 agent 的小组件」——OpenClaw 即由此而生

**对 Alice 的启示**：分层纪律（循环不感知 provider）、会话树（v2 的回溯设计）、评估驱动（Terminal-Bench 验证而非拍脑袋）。

## 2. Claude Code —— 生产级功能主义

**立场**：给前沿模型充分但受控的能力面。与 pi 相反的每一极：丰富工具集、多层安全门、庞大扩展生态。

**架构**（图：`架构图/claude-code.png`）：
- 核心循环四态：理解 → 计划（plan mode：先出方案经人确认再动手）→ 执行 → 验证
- 工具面：文件读写/编辑/bash + grep/glob（渐进披露：不预载代码库，靠搜索按需取）+ MCP 外接生态
- **Hooks**：pre/post-tool 钩子，allow/ask/deny 三态门（行业安全范式的来源）
- **Subagents**：隔离上下文的子代理（搜索型/审查型），主上下文不被淹没——这是「上下文预算」张力的解法之一
- **Skills**：按需加载的能力包（SKILL.md + 脚本），模型自判何时读取
- 状态外置：CLAUDE.md（项目记忆）、PLAN.md/TODO.md（任务状态），会话内 compact 压缩

**对 Alice 的启示**：plan mode 对应「重大决定先说方案」（已出现在 tech 人生的人格里）；hooks 模式是陪伴型安全边界（危机干预规则）的工程形态参考；状态外置 ≈ 人格文件的自审。

## 3. SWE-agent —— 研究驱动的接口设计论

**立场**：harness 的本质是 **Agent-Computer Interface（ACI）**——为 LM 设计的人机接口。接口设计本身（命令集、反馈格式、护栏）直接决定 agent 能力，这个概念是它最大的学术贡献（NeurIPS 2024，引用 3300+）。

**架构**（图：`架构图/swe-agent.png`）：
- LM ⇄ **ACI 瓶颈层**（全部交互收窄于此）：精简文件查看/编辑命令（防呆的编辑器：改完自动 lint，出错立即反馈）、受限 shell、结构化错误信息
- 执行侧：隔离沙箱（repo + 测试环境），issue 进 → patch 出
- 与 SWE-bench 配套：接口改动 → 基准回归 → 数据说话
- 关键发现：**同样的模型，仅改 ACI 设计，解决率显著变化**——「harness 设计是第一等对象」的学术定论来源

**对 Alice 的启示**：把 recall/remember 的工具描述与返回格式当作 ACI 来设计（「未列出=不记得」的显式标注就是陪伴版的「防呆编辑器」）；实验室即 Alice 的 SWE-bench。

## 4. OpenClaw —— 陪伴方向的最近亲

**立场**：通用个人助理 harness，pi 当 SDK 用。与 Alice 同走「关系连续性」路线，但出发点是生产力+生活助理，不是情感陪伴。

**架构**（图：`架构图/openclaw.png`）：
- 底座：pi 组件（pi-ai / pi-agent-core）
- **常驻网关**：7×24 进程，多渠道接入（Telegram/WhatsApp/…），消息归一化
- 会话与状态：pi 会话文件 + 外置记忆/计划文件
- 工具生态：cron 定时任务（主动性）、浏览器、消息收发、外部 MCP 桥接
- 安全：权限门 + 用户确认机制（执行型操作需放行）

**对 Alice 的启示**：这是二代半（渠道 + 主动行为）最成熟的参考实现——常驻网关、渠道归一化、cron 型主动性都可以对标设计；但注意分寸感（OpenClaw 的主动性是生产力语义，Alice 的必须是陪伴语义）。

## 5. Cortico —— 陪伴/主播方向的事件流 harness

**立场**：与 Alice 同赛道的常驻 agent 框架（人格 Bot / AI 主播 / 角色扮演 / 陪伴），TypeScript 实现。定位差异：Cortico 是"一类 bot 的框架"，Alice 是"一个个体"。四层严格分离：Core 零语义（session/事件流/模型调用）、Persona 定义认知、Memory 是唯一权威状态、World 是对外唯一边界（与渠道约束同构但更彻底）。

**架构**（图：`架构图/cortico.png`，深析见 [参考-Cortico-harness机制.md](./参考-Cortico-harness机制.md)、总览 [参考-Cortico架构.md](./参考-Cortico架构.md)）：
- **五级事件投递**（preempt/interrupt/flush/debounce/piggyback）+ 轮次边界：外部事件如何打断/汇入正在进行的模型轮，混合实时输入的系统性答案
- **preempt 与 interrupt 二分**：未外化的输出作废重来 vs 已外化的保留停轮——"用户又发了一条"与"用户叫停"的精确区分
- **梦机制**：上下文交接后从快照 fork 整理线程，修订宪法；清醒态对宪法只读（高压下人格不被改写的结构保证）
- **Memory 即人格**：主体性完全由记忆数据定义，模型/运行时/世界皆可替换；记忆注入"地图+下钻"
- **诚实认识论工程化**：Core 不加语义、事件/回执可还原为可验证事实、origin 显式标记

**对 Alice 的启示**：殊途同归的强验证（诚实认识论≈不变量 2、Memory 即人格≈三库+生长人格）；五级触发与 preempt/interrupt 二分是二代半（渠道+活动周期）的现成词汇表；梦式"醒改只读"是高压溃败问题的护栏强化版。学词汇表与护栏思想，不学框架体量。

## 6. Nascence 辉夜 —— 涌现人格的常驻意识体

**立场**：与 Alice 同赛道的另一极端：人格零硬编码锚点（冷启动把设定写进第一批**记忆**而非提示词），永续认知循环（无输入也想，3~10s 一跳），半衰期+图扩散的动力学式记忆遗忘，学得的生物钟（作息从自身经历长出）。表达被刻意压制（20 字内、禁神态）——与 Alice 的宪法+表达层恰成对照实验。

**架构**（深析见 [参考-Nascence辉夜.md](./参考-Nascence辉夜.md)）：
- **念头/发言分离**：每轮行为思考输出 `{say, text}`，say=false 的念头只入她自己的短期记忆（4~6 条"我说/我想"）——内心独白是一等数据结构
- **反刍抑制**：关键词/种子/边连续复用即抑制 N 轮——常驻循环不被同一块记忆嚼死，同类未见
- **软件即注意力**：平台是她可开关的"软件"，未开只见未读数，本地门控决定开关，语言层不知平台存在

**对 Alice 的启示**：活动周期系统（蓝图）的四个现成组件——念头流、学得生物钟、反刍抑制、虚拟时钟倍速（人工测试时间膨胀）。

## 7. 同型但未展开

- **Codex CLI**（OpenAI）：OS 沙箱硬边界路线的代表
- **Gemini CLI**（Google）：开源 + 免费额度大，沙箱路线
- **OpenCode**：Claude Code 开源复刻派代表（换模型不换交互）
- **Aider / Goose**：git 深度集成型 / 可组合工具型
- **dsh-bili-humanism**（DSH 插件）：非 harness 而是插件样本——"用人类世界喂人格"的**经历供给线**（取稿→认识论手账→日摘一句→人格文件），深析见 [参考-dsh-bili-humanism.md](./参考-dsh-bili-humanism.md)；活动周期"带她看世界"的完整参考
- **dsh-im**（DSH 插件）：十余个 IM 平台的**渠道接入层**完整工程样本（协议适配层/共享语义层/双通道 HarnessClient/排队插话/稳定寻址主动投递），深析见 [参考-dsh-im.md](./参考-dsh-im.md)；Alice 渠道方向的分层参考
- **dsh-bili-scroll**（DSH 插件）：**无人值守值日生**样本——最小权限 headless 轮次跑章程、判重表、风格闸门、谷时定时与省钱纪律，深析见 [参考-dsh-bili-scroll.md](./参考-dsh-bili-scroll.md)；活动周期"自主时段"的纪律参考
- **dsh-heartbeat**（DSH 插件）：**活动周期的先行实现**——七相心跳循环、零 token 分寸闸门、心事匣（没说完的话带过下一拍）、素材池双仓四淘汰、画像 journal，深析见 [参考-dsh-heartbeat.md](./参考-dsh-heartbeat.md)；与蓝图重合度最高的样本

## 来源

- [Pi: The Minimal Agent Within OpenClaw — Ronacher](https://lucumr.pocoo.org/2026/1/31/pi)
- [Zechner：构建极简编码 agent 的经验](https://mariozechner.at/posts/2025-11-30-pi-coding-agent)
- [SWE-agent 论文](https://arxiv.org/abs/2405.15793) / [仓库](https://github.com/swe-agent/swe-agent)
- [Agent Harness Landscape（社区指南）](https://github.com/FlorianBruniaux/claude-code-ultimate-guide/blob/main/guide/ecosystem/agent-harness-landscape.md)
- [2026 Q2 CLI 编码 agent 对比](https://wal.sh/research/2026-q2-cli-coding-agents)
- [Harness token 成本研究](https://thenewstack.io/agent-harness-token-costs)
- [OpenClaw vs Claude Code](https://parallel.ai/articles/openclaw-vs-claude-code-which-ai-agent-should-you-actually-use)
