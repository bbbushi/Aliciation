# 论文导读 · ReAct:推理与行动的交织

> 原文:[ReAct: Synergizing Reasoning and Acting in Language Models](https://arxiv.org/abs/2210.03629)(Yao et al., 2022,Princeton & Google Brain,ICLR 2023)
> 量级:ICLR 2023 口头报告,agent 领域引用最高的范式论文之一

## 一句话

让模型在「想」(Thought)和「做」(Action)之间交替:每做一步、看到环境反馈(Observation),再想下一步——把链式思考(CoT)和工具调用统一进一个循环。

## 它要解决的问题

2022 年的两条路线各有硬伤:

- **纯推理(CoT)**:模型只在脑内推演,不接触外部信息,推得越远幻觉越大——在开放域问答上经常编造事实
- **纯行动(act-only)**:模型直接调 API,但不展示中间推理,无法追踪它为什么这么做,错了也不知道错在哪

ReAct 的假设:推理帮行动规划(该查什么、下一步做什么),行动帮推理接地(查回来的事实喂进下一轮思考)。二者交织,幻觉更少、过程可审计。

## 核心机制

一个 ReAct 轨迹长这样(Wikipedia 环境示例):

```
Thought 1: 我需要搜索 Colorado's Trail Ridge Road,找到它连接哪两个公园
Action 1:  Search[Trail Ridge Road]
Observation 1: Trail Ridge Road 是美国最高的连续铺装公路……连接了 Rocky Mountain 国家公园……
Thought 2: 我需要搜索 Rocky Mountain 国家公园,看它在哪个州
Action 2:  Search[Rocky Mountain National Park]
Observation 2: (科罗拉多州……)
Thought 3: 问题问的是 Trail Ridge Road 位于哪个州,科罗拉多州
Action 3:  Finish[Colorado]
```

工程要点:

1. **少样本提示驱动**,不训练模型——把几条人工写的「思考-行动-观察」轨迹作为 few-shot 示例放进 prompt
2. **动作空间极小**:搜索场景里只有 `Search[关键词]`、`Lookup[词]`(翻结果页)、`Finish[答案]` 三个动作
3. **思考是自由文本**,不参与执行,只为下一行动提供依据——这让整条轨迹可以回放调试
4. **停止条件**:模型自己发出 `Finish`,或步数/上下文超限强制截断

## 关键结果

- **HotpotQA / FEVER(知识问答)**:ReAct 单独跑时准确率与 CoT 互有胜负,但**幻觉率显著更低**(推错事实链的比例大幅下降);两者结合(ReAct 失败时回退 CoT,或用 CoT 自一致性校验 ReAct 答案)是最优
- **ALFWorld( embodied 家务任务)**:成功率约 71%,大幅超过此前模仿学习基线(约 45%)
- **WebShop(网购任务)**:超过专门训练的 IL/RL 方法,而 ReAct 完全没有训练

最重要的定性结论:**接地(grounding)换来忠实,推理换来自主性,缺一不可**。

## 局限

- 上下文长度受限——长任务里早期观察会被截断,需要外部记忆(这正是后续 MemGPT 等工作的入口)
- 强依赖少样本示例质量;任务分布偏移时轨迹质量骤降
- 单线程:一条道走到黑,错了靠步数耗尽才发现(ToT 和 Reflexion 分别从搜索和复盘两个方向补救)

## 对 Alice 的启示

- **Alice 的 `loop.py` 就是 ReAct 循环的陪伴化改写**:收消息 → 组上下文 → 调模型 → (必要时)调工具 → 回消息。思考不可见的场景下,Alice 把"Thought"内化成了模型的输出风格与记忆操作
- **观察接地原则**适用于记忆工具:recall 返回的必须是**真实存储的内容**,模型只能基于返回值说话——这是"真实性原则"的工程对应物
- **动作空间克制**:ReAct 只用 3 个动作就跑通全部任务,与 SWE-agent「接口少而精」相互印证,Alice 的记忆工具也应保持最小集
- **可回放性**:Thought 轨迹可审计 → Alice 的记忆操作日志(data/ 中的记录)同样应该可回放,这支撑成长实验室的漂移诊断

## 精读指引

精读 §3(方法,含 prompt 模板)+ 图 1(ReAct vs CoT 轨迹对比,一图看懂);§4.3 的误差分析(幻觉 vs 推理错误的分解)值得细看;其余可略。
