# 参考:Cortico 的 harness 机制(Core 层深析)

> 姊妹篇:[参考-Cortico架构.md](./参考-Cortico架构.md) 讲四层与哲学,本文深入 **harness 机制本身**——承载模型的机器。依据:源码 `src/core/`(loop.ts 2211 行 / types.ts 1250 行 / core.ts 714 行等)与 `docs/sessions.md`、`docs/worlds.md` 契约。
> 架构图:[`架构图/cortico.png`](../架构图/cortico.png)(由 `tools/make_cortico_diagram.py` 生成)。

## 一、主循环:RunPhase 状态机 + 轮次边界

MainLoop 只跑 `receivesEvents=true` 的常驻 session,显式状态机:`idle → delivering → model → tools → backoff/handoff`,每次相位变化记录时间与执行中工具(onRunPhase 同步给 World)。

**核心纪律:新事件只在轮次边界进入上下文**——本轮工具全部返回之后,或 preempt/interrupt 取消模型轮之后。这保证模型看到的上下文在轮内是冻结的,不会出现"回复到一半世界变了"的撕裂。

两种取消的精细区分是整个设计里最值得学的:

| | preempt | interrupt |
|---|---|---|
| 取消条件 | 仅当输出尚未外化(outputTap 未报告) | 无条件 |
| 已外化输出 | (不适用) | **保留** |
| 模型输出 | 丢弃,同批带新事件重来 | 取消,本轮终止 |
| 执行中工具 | 不打断 | 可中断工具收 signal,回执注明"执行期间被打断";未开始的调用跳过 |

语义上:preempt 是"**你还没说出口的话作废重来**",interrupt 是"**用户叫你停下**"。对陪伴场景,前者对应"用户在你打字时又发了一条",后者对应"用户说别说了"。

## 二、事件管道:WorldHost → EventStore → WakeBus

- **WorldHost.pushEvent**:落库分配游标(cursor)后按 trigger 投递;`pushDeferred` 投递时才渲染正文(读当前状态,配合 piggyback 搭车,不为查状态多发一轮);`withdraw` 撤回未投递事件(留痕,重启不补投);`promote` 把排队事件提升为立即触发。
- **EventStore**:事件是 `EventEnvelope{cursor, run, type, ts, source, origin, tags, text, senderKey, meta, blobs, ephemeral}`——**Core 不给事件添加任何语义内容**;`origin: internal` 只允许来源可验证的内部通知。
- **WakeBus**:合批、闸门、`schedule_wake` 持久闹钟(载荷含 blockDelivery / 唤醒关键词 / 溢出上限)——**主动行为的时机被机制化为持久定时器+投递闸门**,而不是散落在线程里 sleep。

## 三、上下文装配与交接(最精细的部分)

- 装配:`assembleSystem` = Persona 有序段(systemSegments)+ 各 World 环境提示 + 记忆注入;`sessionHead()` 在 system 之后、历史之前合成开头,**每次请求现取、不落盘**。
- 容量:`hardTokens = max(0, 生效窗口 − maxTokens)`,生效窗口取服务探测与配置的较小者;上游未报计数时用 Provider 估算函数或字数比例**本地估算**。
- 交接:超限或服务拒绝时,在**稳定回合边界取快照**,调 `Persona.onHandoff(snapshot, {hardTokens})`——**Persona 决定保留什么**(返回 tail/trim),Core 负责重建前缀、修复工具调用配对(fixPairing)、截断保留内容并重置 session。**交接在事务里并发共享**,期间不投递事件。
- 熔断:交接后若计数仍越限(窗口≤最大输出等病态配置),**不再按计数强交**,记 error,直到计数回到限内或端点变更才恢复——防交接风暴的机制化处理。

对照 Alice:Alice 无 session 状态可交接(每轮从 store 全量重组,等于把交接做成了默认),这是架构差异带来的天然简化;但"**Persona 决定保留什么**"这个职责划分值得记住——容量策略是语义决策,该归人格层而非机械层。

## 四、工具系统:执行纪律

- **仅 `status=completed` 的调用才执行**,其余回执为未执行+warn,同轮后续调用也不执行;
- `endsTurn` 工具结束本轮;`barrierAfter` 控制流式提前派发的边界;`interruptible` 声明可中断;
- 回执 `ToolOutcome{text, blobs?, failed?}`,handler 抛错/返回类型不对都转失败回执——**回执必须可还原为可验证事实**(诚实认识论的落地);
- 未执行原因被编码成 12 种 marker(NOT_EXECUTED_INCOMPLETE / SHUTDOWN_INTERRUPTED / TOOL_FAILED_BAD_ARGS…)进 transcript——**审计粒度到"为什么没执行"**;
- 工具名 bot 内全局唯一,冲突时装配层拒绝挂载;声明(`<PREFIX>_TOOL_DECLS`)与自描述(describe)同源生成,有测试核对一致。

## 五、Session 家族与持久化

- 主 session:`session-main.jsonl` **只追加**;交接重置先写 `.tmp` 再 rename(原子);启动时末行无换行视为中断:解析不出的截掉、完整的补换行;孤儿回执(前面无对应调用)删除并记 error 入 transcript;缺回执的调用补 `[result missing]`——**崩溃恢复处理到了半个 JSON 行的粒度**;
- Fork:临时 session 独立工具循环(梦/潜意识/World 认知),`stopWhen/wrapUpHint/capNote` 控制,并发数簿记,最近 8 个保留查看;
- 统计(调用数/prompt/completion/缓存命中/推理 token)内存态,重启清零——诚实地区分了"账本"与"档案"。

## 六、可靠性工程清单(值得抄的杂项)

单实例锁(防双开)、structured transcript + `pnpm logq` 查询、用量/成本表、错误隔离(World 抛错不倒 Core)、配置四层级联+热改(`x-hot: false` 标记重启生效项)、shutdownVerification(关机前核对外部状态)。

## 七、与 Alice 的机制对照

| Cortico 机制 | Alice 现状 | 判定 |
|---|---|---|
| RunPhase + 轮次边界冻结 | GUI 单锁串行轮 | 已有等价物(更简单);多事件并发时需要升级 |
| preempt/interrupt 二分 | 无(单用户单渠道) | **二代半渠道时必引入**——"用户又发了一条"vs"用户叫停" |
| 事件五级触发 | 无事件概念 | 二代半/三代词汇表,至少要 debounce/preempt/interrupt 三档 |
| onHandoff:人格层决定保留 | 每轮全量重组 | Alice 天生无交接;容量策略归人格层的思想适用于 context 配置演进 |
| 工具 completed-only + marker 审计 | dispatch_tool 容错吞错 | 可借鉴:工具失败应结构化记录而非仅打印 |
| jsonl 追加 + 半行恢复 | SQLite(天然事务) | Alice 已更强,无需动 |
| 持久闹钟 + 投递闸门 | 无 | 活动周期系统的机制化蓝本 |
| Fork(梦/认知) | 离线 extract/evolve(同步) | 三代社交观察时的结构参考:观察流 fork 消化,不污染主对话 |

**一句话总结**:Cortico 的 harness 把"事件如何进入模型的上下文"做成了第一等公民——五级触发、轮次边界、两种取消、交接快照,全部服务于"持续运行的个体面对混合实时输入";Alice 的单用户轮对话是它的静态特例,**蓝图推进到二代半时,这张机制清单就是 Alice loop 的升级路线图**。
