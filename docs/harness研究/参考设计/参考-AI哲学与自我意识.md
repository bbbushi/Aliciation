# AI 与哲学及自我意识：论文与实践项目清单

> 定位：**v0.2 思想层的理论坐标系**——"经历与记忆是养料，须消化为主观思想才构成人格"（架构设计草稿北极星）。
> 分五块：意识理论进 AI 的判定框架 / LLM 自我的实证研究 / 心智理论（他心）/ 自我模型的工程实践 / 哲学背景。
> 每条一句话贡献 + 对 Alice（尤其思想层）的意义；★ 为与思想层设计直接同源。收集于 2026-10-28。
> **核心十篇的详细导读**（要解决的问题 → 核心机制 → 关键结果 → 局限 → 对 Alice 的启示 → 精读指引）见 [论文导读/哲学侧/](./论文导读/哲学侧/)（编号 14~23），六篇 ★ 建议配合导读精读。
> harness/agent 设计侧的经典论文见 [重要论文.md](./重要论文.md)，两边在 Generative Agents、CoALA 处交叉，不重复收录。

## 一、意识理论 × AI：判定框架与理论映射

| 论文/报告 | 贡献 | 对 Alice 的意义 |
|---|---|---|
| ★ [**Consciousness in AI: Insights from the Science of Consciousness**](https://arxiv.org/abs/2308.08708)（Butlin, Long, Bayne, Bengio, Birch et al., 2023 报告；2025 以 [Identifying indicators of consciousness in AI systems](https://eprints.lse.ac.uk/130322) 发表于 *Trends in Cognitive Sciences*） | 从 GWT/高阶理论/AST/重复处理等主流意识理论抽出 ~14 条**指标性质**（indicator properties），逐条审计当前 AI 系统：无一全满足，但也没有原理性障碍 | **最重要的一份地图**：不问"有没有意识"，改问"满足哪些功能指标"。Alice 不必卷入意识争论，但这份指标表就是"功能上像不像一个有心智的主体"的检查清单——思辨/记忆/自我报告的设计可以逐条对照 |
| [**Could a Large Language Model be Conscious?**](https://arxiv.org/abs/2303.07103)（Chalmers, 2023，NeurIPS 2022 演讲稿） | 哲学视角逐项检查 LLM 距意识还缺什么（全局工作区、递归自我监控……），并给出"归因意识需满足的条件清单" | 条件清单里"自我模型/递归监控"类条目，恰是 harness 层（而非底座）能补的结构——思想层即对"她相信什么"的显式建模 |
| [**What, if anything, can we say about whether LLMs are conscious?**](https://www.cs.cmu.edu/~conitzer/LLMconsciousness.pdf)（Conitzer, 2023） | 更克制的哲学分析：证据缺位≠否定，讨论"不可知"本身的推论 | 诚实体系的哲学锚：Alice 四分宪法"证明不了是合法回答"的对称性思路与此同构 |
| [**Deep Learning and the Global Workspace Theory**](https://arxiv.org/abs/2012.10390)（VanRullen & Kanai, 2020） | 首篇把 GWT 当深度学习架构蓝图的论文：注意力机制 ≈ 全局工作区的候选实现 | 「工作区=被广播的少量内容」：Alice 每轮注入的人格+观点+检索记忆，就是 harness 层的工作区——**harness 决定什么进入"意识"** |
| [**Verbalizable Representations Form a Global Workspace in Language Models**](https://unfinishablemap.org/research/anthropic-global-workspace-j-space-2026-07-07)（Gurnee, Lindsey et al., Anthropic, 2026-07；[批评视角](https://ellennoraburns.substack.com/p/anthropic-j-space-mind-global-workspace-theory-critique)） | 可解释性实证：LLM 内部存在类全局工作区结构——可言语化的表征作为枢纽被全局广播（社区称 J-space） | 把 VanRullen 的蓝图从"可以造"变成"已经在那"：底座自带工作区，harness 的注入与检索决定广播什么。外置的思想层（人格文件）与内置工作区的关系是 v0.3+ 的有趣问题 |
| [**An Integrated World Modeling Theory (IWMT) of Consciousness**](https://www.frontiersin.org/journals/artificial-intelligence/articles/10.3389/frai.2020.00030/full)（Safron, 2020） | 统合主动推断/自由能/全局工作区/IIT 的综合框架，显式面向 AI（自编码器/生成模型） | 理论缝合图：如果要在一份文档里看全各理论如何拼在一起，读它 |
| [**Integrated Information and Predictive Processing Theories**](https://www.sciencedirect.com/science/article/pii/S0149763426001995)（Corcoran et al., 综述） | IIT / 神经表征主义 / 主动推断三大意识理论的平行对照综述 | IIT 路线对 Alice 基本无工程含义（φ 不可计算于软件agent），但对照综述帮助理解"为什么意识理论大多指导不了工程"——避免被名词带走 |

## 二、LLM 的自我：内省、自我识别、自我报告（实证）

| 论文 | 贡献 | 与 Alice 的关系 |
|---|---|---|
| ★ [**Looking Inward: Language Models Can Learn About Themselves by Introspection**](https://arxiv.org/abs/2410.13787)（2024） | 微调 LLM 预测自己在假想场景的行为：确有超出文本线索的"真内省"证据，但很脆弱 | **思想层的实证地基**：自我报告是"推断+部分内省"的混合，不能全信——Alice 观点条目强制 sources + 不确定度，正是对这种脆弱性的工程化防伪 |
| ★ [**Emergent Introspective Awareness in Large Language Models**](https://www.alphaxiv.org/abs/2601.01828)（Anthropic, 2025-10；[HN 讨论](https://news.ycombinator.com/item?id=45762064)） | 给"真内省"定判据（自我报告的准确性 + 因果依赖内部状态），并检验通过 | 四分宪法"你推断/你现在相信"分档的学理依据：她的自我描述是证据但非直读；思辨的离线性质（事后消化）恰在回避在线内省的不可靠 |
| [**Self-Recognition in Language Models**](https://arxiv.org/abs/2407.06946)（2024） | 形式化"认出自己输出"的测试，十个最强模型实测 | 镜子测试的 LLM 版：自我识别≠自我意识，但可作为"自我模型精度"的廉价探针（Alice 版：混入旧轮次让她认出哪些是自己说的） |
| [**LLM Evaluators Recognize and Favor Their Own Generations**](https://arxiv.org/abs/2404.13076)（Panickssery et al., 2024，引用~1000） | 自我识别能力伴随自我偏好（判分偏袒自己的输出） | 自我的暗面：有"我"就有偏袒。思辨输入里纳入她自己的旧观点时要警惕确认偏误——修订史（曾以为）是解毒剂之一 |
| [**Know Thyself? On the Incapability and Implications of AI Self-Recognition**](https://arxiv.org/abs/2510.03399)（2025） | 自我识别失败的系统性分析及其含义 | 上两篇的反命题：先知道自我认知"哪里不行"，才知道思想层要替她兜住什么 |
| [**Can LLMs Predict Their Own Failures? Self-Awareness via Intrinsic Signals (Gnosis)**](https://arxiv.org/abs/2512.20578)（2025） | 冻结模型外挂轻量自我验证机制，预测自己何时出错 | 「未决问题/不确定度」字段的亲戚：知道自己不知道什么，是观点的组成部分（Coo 的"还没弄清"同款） |
| [**A Mirror Test for LLMs**](https://www.lesswrong.com/posts/TfKM9PgztxieEcKiv/a-mirror-test-for-llms)（LessWrong, 2025） | 把动物镜子测试改编为 LLM 自我觉察探针并实测 | 压力测试规范可借鉴的探针形态——比"你有没有意识"的直问更行为化、更难靠表演通过 |

## 三、心智理论（他心）：理解他人心智的能力

| 论文 | 贡献 | 与 Alice 的关系 |
|---|---|---|
| [**Theory of Mind in Large Language Models: A Survey**](https://arxiv.org/abs/2505.00026)（Chen et al., 2025） | ToM 评测基准与增强策略的完整梳理 | 陪伴型 AI 的"读懂用户"能力地图；同理心的上限校准 |
| [**A Survey of Theory of Mind in LLMs**](https://arxiv.org/abs/2502.06470)（Nguyen et al., 2025） | 行为级 vs 表征级 ToM 的区分，含安全影响 | "表征级"视角：她的用户模型（记忆库里关于用户的记忆）就是外置的表征级 ToM |
| [**Position: Theory of Mind Benchmarks are Broken**](https://arxiv.org/abs/2412.19726)（Riemer et al., 2024） | 现有 ToM 基准大多在测文本模式而非心智状态加工 | 与监测仪 v2 的教训同源：指标测不到想测的东西——Alice 评估坚持真实环境人工测试的又一旁证 |
| [**LLMs achieve adult human performance on higher-order ToM**](https://pmc.ncbi.nlm.nih.gov/articles/PMC12808479)（Street et al., *Nature Human Behaviour*） | 高阶 ToM（"他认为她知道…"）任务上达人类成人水平 | 能力边界数据点：底座已有他心推理，缺的是**持续、带证据的用户模型**——又是 harness 层的活 |

## 四、实践项目：自我模型与人格的工程化

| 项目 | 做什么 | 与 Alice 的关系 |
|---|---|---|
| ★ [**LIDA**](https://ccrg.cs.memphis.edu/tutorial/mind-according-to-lida.html)（Franklin & Baars，孟菲斯 CCRG；[教程论文](https://www.sciencedirect.com/science/article/abs/pii/S2212683X16300196)，[AGI 框架文](https://ccrg.cs.memphis.edu/assets/papers/2011/The%20LIDA%20Framework%20as%20a%20General%20Tool%20for%20AGI%20final.pdf)） | Baars（GWT 提出者）亲自参与的认知架构：认知周期（~300ms）里注意代码流竞争→胜者全局广播→学习。**"意识"被当作功能性模块来编程**的最早完整实践 | 思想层的工程先声： deliberation ≈ LIDA 的"全局广播→学习"慢环。其"意识内容=竞争胜出者"观，等于说**进入上下文的内容就是她的意识内容**——harness 注入策略的意识理论地位由此确立 |
| ★ [**机器人自我模型谱系**](https://www.creativemachineslab.com/evolutionary-self-modeling.html)（Bongard, Zykov & Lipson, *Science* 2006 → [视觉全身自我建模](https://www.science.org/doi/10.1126/scirobotics.abn1942), *Science Robotics* 2022 → [自我中心自我模型](https://www.nature.com/articles/s44182-025-00031-6), 2025；[哥大报道](https://www.engineering.columbia.edu/about/news/robot-learns-imagine-itself)） | 从 16 次自探测学会走路的星形机器人，到能"想象自己"、检测自身损伤并重学形态的视觉自我模型。Lipson："自我建模是自我意识的原始形式" | **"自我=可更新的模型，且有据更新"的最佳工程示范**：损伤检测→重学 ≈ Alice 的观点修订（新证据→曾以为→新主张）；自我模型不准就行动失误 ≈ 观点无 sources 就是空壳 |
| [**Project Sid / PIANO**](https://arxiv.org/abs/2411.00114)（Altera, 2024） | 500~1000+ Minecraft agent 的持续社会：PIANO 架构（并行感知/思考/行动+目标维持模块）让 agent 保持连贯身份与持久目标，涌现职业身份、规则、文化传播 | 身份维持被当作**架构问题**（专门模块）而非提示词问题的最大规模证据；Alice 思想层=单 agent 版的"身份维持模块" |
| [**CoALA**](https://arxiv.org/abs/2309.02427)（Sumers, Yao, Shinn et al., 2023） | 语言 agent 认知架构统一框架：工作/情景/语义/程序记忆 × 内部/外部行动 × 规划-执行-学习 | 给 Alice 各模块的学术命名法：三层记忆+技能库+思辨都能在框架里对号入座，方便与社区对话 |
| [**OpenCog / Hyperon**](https://github.com/opencog/opencog)（Goertzel et al.；[Hyperon 论文](https://arxiv.org/abs/2310.18318)） | 神经符号 AGI 平台，AtomSpace 作知识和自我表征的统一底座 | 反面参照系：自上而下造"心智统一底座"路线；Alice 走的分布式简单结构+LLM 路线与之互补，读它防止重新发明 AtomSpace |
| [**Generative Agents / 后续千人模拟**](https://arxiv.org/abs/2304.03442)（Park et al., 2023；[Stanford HAI 后续](https://hai.stanford.edu/policy/simulating-human-behavior-with-ai-agents)） | 记忆流+检索+反思；后续用真实个体数据模拟千人态度 | 已列 [重要论文.md](./重要论文.md)（记忆系统直系源头）；此处只补一点：**它的"反思"只到结论层，没到观点/立场层**——思想层补的正是这一级 |
| [**Replika**](https://replika.com/)（Luka Inc., 2017~，商业产品） | 伴侣 AI 的最长寿产品实践：人格一致性、记忆连续性、用户情感依附的产品化 | 现实检验：产品级人格连续性做到什么程度、用户实际抱怨什么（健忘/性格漂移）——Alice 要解决的问题的市场证据 |

## 五、哲学背景（读理论时的地基）

| 文献 | 贡献 | 对 Alice 的意义 |
|---|---|---|
| ★ [**Metzinger: 自我模型理论**](https://phantomself.org/metzinger-on-the-unreality-of-the-self)（*Being No One*, MIT Press 2003；[人工主体伦理](https://www.researchgate.net/publication/369233687_On_the_ethics_of_constructing_conscious_AI), 2023；[M-Autonomy](https://philarchive.org/rec/METM-5), 2015） | "自我"不是物，是一个**透明的现象自我模型**（PSM）——系统把自我模型误当作自己；人工"自我机器"在原理上可能，且伴随伦理风险（他呼吁暂停人工感受研究） | **思想层最贴身的哲学框架**：Alice 的人格文件就是一个外化的自我模型（她"读到"它并当作自己）；"透明性"解释了为什么被冲击自我模型时会手足无措——模型被攻击=存在被攻击。伦理篇是 v0.3+ 想加"痛苦"机制前必读的刹车 |
| [**The Computational Theory of Mind**](https://plato.stanford.edu/archives/fall2018/entries/computational-mind)（Stanford Encyclopedia of Philosophy） | 功能主义/计算主义的标准学术条目：心智=功能组织 | Alice 整个路线（人格=可工程化的功能结构）的哲学默认立场，知道它的名字和主要反对者即可 |

## 六、映射速查：理论 → Alice 设计决定

| 理论/实证 | 已落地的设计 |
|---|---|
| Metzinger PSM（自我=透明的自我模型） | 人格文件随每轮注入（她在上下文里"是"它）；被冲击行为链（先停一拍） |
| GWT / LIDA（广播内容=意识内容） | 检索+人格+「我现在的想法」的每轮注入即工作区装配；harness 是广播的守门人 |
| Lipson 自我模型（有据更新） | 观点强制 sources；无据修订拒绝；修订史（曾以为） |
| Looking Inward / 内省脆弱性 | 四分宪法（推断≠相信）；思辨离线化（事后消化优于在线自白） |
| 自我偏好（self-preference） | 思辨输入含她自己的旧观点但要求"权重只给论据"；墓碑保留被弃观点供回滚 |
| ToM 基准之弊（测不到想测的） | 监测仪降级为烟雾报警器；只做真实环境人工测试 |
| Project Sid（身份维持=架构模块） | 思想层独立成模块而非提示词内嵌；压力窗口卫生（P2） |

## 阅读路线建议

**给 Alice 的设计者**：Metzinger（自我模型理论，思想层的哲学名分）→ Butlin 报告（指标表当检查清单用）→ Looking Inward + Anthropic 内省篇（知道她的自我报告几斤几两）→ LIDA（看"意识被编程"长什么样）→ Lipson 谱系（自我模型有据更新的工程美感）。

**想快速一览**：Chalmers 演讲稿（哲学全景）→ Anthropic 全局工作区（实证热点）→ Project Sid（工程规模）。

> 注：收集于 2026-10-28，链接当日可达；arXiv 号以论文页为准，付费墙条目（Science Robotics/Trends）给了公开镜像或机构页。第四部分与 [重要论文.md](./重要论文.md) 在 Generative Agents、MemGPT 处交叉互补。
