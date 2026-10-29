# pi（Mario Zechner）架构参考

> 用途：Alice 的工程骨架参考。pi 是编码型 harness，能学的是分层与循环等工程决策；陪伴内核（人格宪法、记忆管道）恰恰是它没有、也不能省的部分。
> 来源：2026-10 调研，基于 Zechner 本人博客与 Armin Ronacher 评测（文末链接）。

## 1. 一句话概括

四层组件化的 TypeScript monorepo，核心刻意做小（4 个工具、<1000 token 提示），把"变大"交给扩展系统和会话结构消化。自我定位是"一组可以搭你自己的 agent 的小组件"，而非成品——OpenClaw 就是把它当 SDK 用的。

## 2. 分层结构（pi-mono monorepo）

```
pi-coding-agent   ← CLI 成品：会话、主题、斜杠命令、项目上下文
      ↑
pi-agent-core     ← Agent 类：状态管理、消息队列、传输抽象
      ↑
pi-ai             ← LLM 统一抽象：四家 API 风格归一
      +
pi-tui            ← 终端 UI 框架（与核心并列，可单独用）
```

| 包 | 职责 | 关键细节 |
|---|---|---|
| **pi-ai** | LLM 抽象层 | 归一 4 种 API 风格：OpenAI Completions / OpenAI Responses / Anthropic Messages / Google GenAI。覆盖 Anthropic、OpenAI、Google、xAI、Groq 及任何 OpenAI 兼容端点（Ollama、vLLM 等）。流式输出、工具调用校验（TypeBox/AJV）、thinking、token/成本统计、**跨 provider 上下文交接**（会话中途换模型） |
| **pi-agent-core** | agent 循环 | 循环体：模型输出 → 有工具调用就执行 → 结果回喂 → 直到无工具调用。全程可中断且返回部分结果；turn 之间消息排队；事件流式吐给 UI。**刻意不设 max-steps** |
| **pi-tui** | 终端 UI | 保留模式组件、差分渲染、同步输出（CSI ?2026），近乎无闪烁 |
| **pi-coding-agent** | 成品 CLI | 会话、斜杠命令、主题、项目上下文文件 |

模型注册表从 OpenRouter / models.dev 数据生成（类型、成本、能力），自托管端点可内联定义自定义模型。

## 3. 四个关键设计决策

### 3.1 只有 4 个工具：read / write / edit / bash

系统提示 + 工具定义总计 <1000 token。依据：harness 功能越堆、agent 行为越不稳定（对 Claude Code 的观察）；RL 训练出的前沿模型不需要精心调教的提示。工具少 → 行为可预测。grep/find/ls 仅作可选只读工具。

**对 Alice**：Alice 已对齐此分层——`llm.py`≈pi-ai、`loop.py`≈pi-agent-core、`cli.py`≈pi-coding-agent。循环不感知 provider 的拆分方向正确。

### 3.2 会话是可分支的树，不是线性日志

- 会话文件为干净 JSON：可序列化、可恢复、可导出 HTML
- **支持分支**：在分支上处理支线任务，回来合并不污染主上下文（也是 /review 工作流的基础）
- 单个会话可混用多个 provider 的消息
- 允许存放非 LLM 的自定义消息

**对 Alice**：目前是单表线性 `turns`，MVP 够用。未来做回忆核查 / 对话回溯时，分支结构比线性日志灵活得多，是值得偷的设计。

### 3.3 扩展系统：TypeScript，拒绝 MCP

- 扩展可注册工具、渲染 TUI 组件、把状态持久化进会话
- 外部能力 = "CLI 工具 + README"：agent 需要时自己读 README、经 bash 调用（渐进披露）
- **自扩展**：agent 现场给自己写扩展 → 热重载 → 迭代到能用；Ronacher 的扩展几乎都是 agent 自己写的而非下载
- 明确不做：MCP（可经 mcporter 间接用）、子 agent、后台 bash（用 tmux）、计划模式（用 PLAN.md 文件）

### 3.4 默认 YOLO + 完全可观测

无权限确认直接执行；发给模型的一切内容用户可见；质量用 Terminal-Bench 2.0 验证（每任务 5 次）而非拍脑袋。

**对 Alice 的警示**：极简提示哲学建立在编码模型已被 RL 打磨成熟的前提上。陪伴场景相反——人格宪法与记忆检索管道不能省，诚实行为要靠管道显式实现。pi 教工程骨架，不教陪伴内核。

## 4. 与 OpenClaw 的关系

OpenClaw 不是"用了 pi"，而是把 pi 当 SDK / 库用。Ronacher 的 Telegram bot、Zechner 给母亲做的陪伴项目，都以 pi 组件为基座搭建；两个 agent 之间还能互相指认、互相生成连接。Ronacher 评价其"像黏土一样可塑"，并以 OpenClaw 的增长作为"agent 自建软件"路线成立的证据。

## 5. Alice 可借鉴清单

| 借鉴点 | 优先级 | 说明 |
|---|---|---|
| llm / loop / cli 分层 | ✅ 已对齐 | 保持循环层不感知 provider |
| 会话树（分支结构） | v2 | 回忆核查、对话回溯时引入 |
| 完全可观测 | v1 内可做 | Alice 已有记忆审计（memory 命令、墓碑），可补"本轮注入了什么"的调试开关 |
| 事件流 / 可中断 | v2 | 接 Telegram 等长渠道时再要 |
| 极简提示 | ❌ 不采纳 | 陪伴场景的宪法与记忆区不可省 |
| 自扩展 / YOLO | ❌ 不采纳 | 与陪伴型安全边界冲突 |

## 6. 来源

- [What I learned building an opinionated and minimal coding agent — Mario Zechner](https://mariozechner.at/posts/2025-11-30-pi-coding-agent)
- [Pi: The Minimal Agent Within OpenClaw — Armin Ronacher](https://lucumr.pocoo.org/2026/1/31/pi)
- [Building Pi, and what makes self-modifying software so interesting — Pragmatic Engineer](https://newsletter.pragmaticengineer.com/p/building-pi-and-what-makes-self-modifying)
- [pi.dev 官网](https://pi.dev)
