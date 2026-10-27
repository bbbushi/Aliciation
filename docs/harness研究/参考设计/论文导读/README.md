# 论文导读索引

> 对应 [重要论文.md](../重要论文.md) 清单中全部 13 篇论文的详细导读。每篇导读按统一结构:**要解决的问题 → 核心机制 → 关键结果 → 局限 → 对 Alice 的启示 → 精读指引**。
> 阅读顺序无需按编号;给 Alice 设计者的推荐路线见文末。

| 编号 | 导读 | 论文 | 主题 | 对 Alice 的价值 |
|---|---|---|---|---|
| 01 | [ReAct](./01-ReAct.md) | ReAct (2022) | 基础范式 | 对话循环的范式原点 |
| 02 | [Toolformer](./02-Toolformer.md) | Toolformer (2023) | 基础范式 | 工具描述即接口 |
| 03 | [Reflexion](./03-Reflexion.md) | Reflexion (2023) | 基础范式 | 失败复盘与 changelog |
| 04 | [Tree of Thoughts](./04-Tree-of-Thoughts.md) | Tree of Thoughts (2023) | 基础范式 | 分支与规划的理论 |
| 05 | [Generative Agents](./05-Generative-Agents.md) ★ | Generative Agents (2023) | 记忆 | **记忆系统直系源头** |
| 06 | [MemGPT](./06-MemGPT.md) ★ | MemGPT (2023, 含 Letta 后续) | 记忆 | **仓库层换页设计直系源头** |
| 07 | [Voyager](./07-Voyager.md) ★ | Voyager (2023) | 技能 | **技能库直系源头** |
| 08 | [Constitutional AI](./08-Constitutional-AI.md) ★ | Constitutional AI (2022) | 对齐 | 宪法形态依据 |
| 09 | [Sycophancy](./09-Sycophancy.md) | Towards Understanding Sycophancy (2023) | 对齐 | 诚实度测试理论支撑 |
| 10 | [SWE-agent](./10-SWE-agent.md) | SWE-agent (2024) | 评测/接口 | ACI 接口设计观 |
| 11 | [SWE-bench](./11-SWE-bench.md) | SWE-bench (2024) | 评测 | 评估驱动迭代范式 |
| 12 | [Terminal-Bench](./12-Terminal-Bench.md) | Terminal-Bench (2026) | 评测 | 当代基准形态 |
| 13 | [Agent 综述](./13-LLM-Agent综述.md) | A Survey on LLM-based Autonomous Agents (2023) | 综述 | 四模块坐标系 |

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

> 三篇 ★ 是 Alice 的"学术血脉",建议全文精读;其余导读读完即可,需要时再回原文。
