# 论文导读索引

> 对应 [重要论文.md](../重要论文.md) 清单中全部 13 篇论文的详细导读。每篇导读按统一结构:**要解决的问题 → 核心机制 → 关键结果 → 局限 → 对 Alice 的启示 → 精读指引**。
> 2026-10-28 新增**哲学侧**(14~23,`./哲学侧/`):对应 [参考-AI哲学与自我意识.md](../参考-AI哲学与自我意识.md)——思想层(v0.2)的理论坐标系。
> 阅读顺序无需按编号;给 Alice 设计者的推荐路线见文末。

| 编号 | 导读 | 论文 | 主题 | 对 Alice 的价值 |
|---|---|---|---|---|
| 01 | [ReAct](./架构侧/01-ReAct.md) | ReAct (2022) | 基础范式 | 对话循环的范式原点 |
| 02 | [Toolformer](./架构侧/02-Toolformer.md) | Toolformer (2023) | 基础范式 | 工具描述即接口 |
| 03 | [Reflexion](./架构侧/03-Reflexion.md) | Reflexion (2023) | 基础范式 | 失败复盘与 changelog |
| 04 | [Tree of Thoughts](./架构侧/04-Tree-of-Thoughts.md) | Tree of Thoughts (2023) | 基础范式 | 分支与规划的理论 |
| 05 | [Generative Agents](./架构侧/05-Generative-Agents.md) ★ | Generative Agents (2023) | 记忆 | **记忆系统直系源头** |
| 06 | [MemGPT](./架构侧/06-MemGPT.md) ★ | MemGPT (2023, 含 Letta 后续) | 记忆 | **仓库层换页设计直系源头** |
| 07 | [Voyager](./架构侧/07-Voyager.md) ★ | Voyager (2023) | 技能 | **技能库直系源头** |
| 08 | [Constitutional AI](./架构侧/08-Constitutional-AI.md) ★ | Constitutional AI (2022) | 对齐 | 宪法形态依据 |
| 09 | [Sycophancy](./架构侧/09-Sycophancy.md) | Towards Understanding Sycophancy (2023) | 对齐 | 诚实度测试理论支撑 |
| 10 | [SWE-agent](./测试侧/10-SWE-agent.md) | SWE-agent (2024) | 评测/接口 | ACI 接口设计观 |
| 11 | [SWE-bench](./测试侧/11-SWE-bench.md) | SWE-bench (2024) | 评测 | 评估驱动迭代范式 |
| 12 | [Terminal-Bench](./测试侧/12-Terminal-Bench.md) | Terminal-Bench (2026) | 评测 | 当代基准形态 |
| 13 | [Agent 综述](./13-LLM-Agent综述.md) | A Survey on LLM-based Autonomous Agents (2023) | 综述 | 四模块坐标系 |

## 哲学侧(14~23)

| 编号 | 导读 | 论文/项目 | 主题 | 对 Alice 的价值 |
|---|---|---|---|---|
| 14 | [意识指标报告](./哲学侧/14-意识指标报告.md) ★ | Butlin et al. (2023/2025) | 意识理论×AI | **功能心智的检查清单** |
| 15 | [Metzinger 自我模型](./哲学侧/15-Metzinger自我模型.md) ★ | Being No One (2003)+伦理篇 | 哲学背景 | **思想层的哲学名分:人格=外化 PSM** |
| 16 | [Looking Inward](./哲学侧/16-Looking-Inward.md) ★ | 内省实验 (2024) | LLM 自我 | **自我报告须有据的实证地基** |
| 17 | [内省觉察](./哲学侧/17-内省觉察.md) ★ | Anthropic (2025-10) | LLM 自我 | 判据=准确性+接地;内省被底座抑制→离线思辨 |
| 18 | [LIDA](./哲学侧/18-LIDA.md) ★ | Franklin & Baars 认知架构 | 工程实践 | **"意识被编程"最早完整实现;广播=注入** |
| 19 | [机器人自我模型](./哲学侧/19-机器人自我模型.md) ★ | Bongard/Lipson 谱系 (2006-2025) | 工程实践 | **失配→停→有据重学:P2 的工程原型** |
| 20 | [Chalmers LLM 意识](./哲学侧/20-Chalmers-LLM意识.md) | Could a LLM be Conscious (2023) | 意识理论×AI | 缺口清单=harness 设计任务书 |
| 21 | [GWT 与 LLM](./哲学侧/21-GWT与LLM.md) | VanRullen&Kanai(2020)+Anthropic(2026) | 意识理论×AI | 外置与内置工作区的对齐问题 |
| 22 | [Project Sid/PIANO](./哲学侧/22-Project-Sid-PIANO.md) | Altera (2024) | 工程实践 | 身份维持=架构模块;其空白(修订)正是思想层 |
| 23 | [自我识别四联](./哲学侧/23-自我识别四联.md) | 自我识别/偏袒/失能/Gnosis | LLM 自我 | 自我知识:有,偏,缺,可补 |

## 推荐阅读路线

**给 Alice 的设计者(你)**:

1. [05 Generative Agents](./05-Generative-Agents.md) —— 先读,Alice 记忆系统的学术坐标系,三层记忆/重要度/检索加权/反思全部出于此
2. [06 MemGPT](./06-MemGPT.md) —— 上下文管理的操作系统隐喻,仓库层索引+换页的设计依据
3. [07 Voyager](./07-Voyager.md) —— 技能库三件套,补齐"经历→技能"的抽象
4. [10 SWE-agent](./10-SWE-agent.md) —— 换视角:接口设计本身决定 agent 能力,适用于 recall/remember 的返回格式
5. [08 Constitutional AI](./08-Constitutional-AI.md) + [09 Sycophancy](./09-Sycophancy.md) —— 诚实体系的两翼:正向立法 + 反向病理

**按项目模块对照**:

| Alice 模块 | 对应导读 |
|---|---|
| `alice/loop.py` 对话循环 | 01 ReAct |
| `alice/retrieval.py` 检索 | 05 Generative Agents、06 MemGPT |
| `alice/store.py` 三层记忆 | 05、06 |
| `alice/extraction.py` 记忆提取 | 05(反思机制) |
| `alice/persona.py` 人格演化 | 05、08、09 |
| `alice/skills.py` 技能库 | 07 Voyager |
| `alice/consolidation.py` 固化/遗忘 | 05、06 |
| `tests/growth/` 成长实验室 | 11 SWE-bench、12 Terminal-Bench |
| 工具返回格式设计 | 02 Toolformer、10 SWE-agent |
| `alice/thought.py` 思辨(思想层) | 15 Metzinger、16 Looking Inward、19 机器人自我模型 |
| 宪法 v2 认识论四分 | 14 指标报告、16、17 内省觉察 |
| P2 压力卫生/修订门控 | 19 机器人自我模型(失配→停→有据重学)、23 自我识别四联 |
| 检索装配(每轮注入) | 18 LIDA(广播=注入)、21 GWT 与 LLM |
| 压力测试规范探针 | 23 自我识别四联、22 Project Sid |

> 三篇 ★ 是 Alice 的"学术血脉",建议全文精读;其余导读读完即可,需要时再回原文。
> 哲学侧六篇 ★(14~19)是思想层的理论血脉;**优先读 15 Metzinger——思想层在哲学语言里就是"可读写的自我模型+定期去透明化审视"**。哲学侧推荐路线:15 → 14(指标表自查)→ 16+17(她的自我报告几斤几两)→ 18/19(工程先声)→ 20~23 按需。
