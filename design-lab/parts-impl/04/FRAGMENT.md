# FRAGMENT · 部件 04 逐句揭示流（p04-）

> 源实现：`parts-impl/04/part.html`（`window.P04`，IIFE 导出；页面内宿主替身/lab/自测表为演示脚手架，组装时丢弃）。
> 规格：`parts/04-逐句揭示流.md`（三态、四不变量、状态矩阵、交互清单、文案）；工艺 `PRINCIPLES.md`。
> 04 是**逻辑部件**：自身仅一条 CSS 交互标记（揭示中指针），其余全是时序与信号。demo 页的宿主替身内置三条守卫，与装配后 02 的实现逐字同源，自测对「引擎+守卫」的整链成立。

## 0. 定位

- **页面唯一揭示写者**：一切 `reveal:progress` / `reveal:done` 仅由 P04 发出；02/03/05/08 都不发（08 以 `reveal:done` 为 busy 解除对齐点，只收不发）。
- **内容节拍非动效**：句间隔 240–380ms、等待首句 600–1000ms（方向 B 基线带宽，取自真实产品 170–520ms/句 带；PRINCIPLES 不约束内容节奏）。**每句出现零过渡**（内容义务，非入场动画——规避"纯 fade 入场"禁令，DIRECTIONS 共同决定）。
- 三态：`idle` / `revealing` / `done`。done 的唯一到达条件：全部句单元可见（不变量 2）。

## 1. 对外 API（`window.P04`）

```js
P04.init(bus, opts)     // opts:{skipHost?, sentences?, cadence?, think?}
                        // skipHost=02 列表元素（事件委托实现"点击揭示中消息跳过"，04 不持有节点引用）
                        // sentences=P03.splitSent（公共切句，防漂移）；cadence/think 仅供自测提速
P04.begin(turn, opts)   // 手动启动；常规路径=监听 turn:append 自动触发（assistant + state='revealing'）
P04.skip(id)            // 跳过一条：先补全 progress(全文) 再 done
P04.skipAll()           // jump:latest（用户主动回底）触发；返回跳过条数
P04.stopAll()           // demo:dataset / demo:replay / demo:reset 触发；静默取消，不发残余信号
P04.align(id, patch)    // turn:update 对齐；常规路径=监听自动触发
P04.active()            // => [id,…] 揭示中
P04.state(id)           // => 'revealing' | 'idle'
```

## 2. 信号

**订阅**：

| 信号 | 触发 |
| --- | --- |
| `turn:append` | detail 为 assistant 且 `state==='revealing'` → `begin`（等待首句 → 逐句 progress → done） |
| `turn:update` | id 在揭示中 → `align`（不变量 4） |
| `jump:latest` | **用户主动回底**（仅 07 点击产生；02 的自动跟随不发任何信号）→ `skipAll` |
| `demo:dataset` / `demo:replay` / `demo:reset` | `stopAll`：切档/重播/清空打断，随旧档清除，无"正在回复…"残尸 |

**发出**：

| 信号 | 载荷 | 语义 |
| --- | --- | --- |
| `reveal:progress` | `{id, shownText}` | 每句一次；shownText 恒为 text 的连续前缀且单调增长（不变量 1）。跳过路径一次补全到全文 |
| `reveal:done` | `{id}` | 自然完成 / 跳过 / 重写兜底 / 沉默捷径——**四条路径都必发** |

## 3. 引擎语义（四不变量的实现）

1. **按序**：`shownText = units[0..ui]` 连接；每步 ui+1，不跳序不重复；宿主守卫二次校验前缀。
2. **完整**：done 只在 `shown===text` 时由引擎发出。**早期 done 对齐**（真实产品教训：done 曾"丢弃缓冲+整段重渲"一次性倒出，改为 revealSync 对齐进缓冲按原节奏放完）——外部 `reveal:done` 被 02 的 midReveal 守卫拒绝，引擎不受影响，按原节奏补全前缀后到达完整；外部 `turn:update` 的 `state` 迁移被 02 剥离（状态迁移唯一入口是 reveal:done）。
3. **可跳过**：两条触发——点击揭示中 assistant 消息本体（`skipHost` 事件委托，命中 `[data-role="assistant"][data-id]` 且 id 在揭示中）；`jump:latest`（用户主动回底）。**跳过=立即完整，不可撤销**；跳过协议：先发一次 `progress(全文)` 再发 `done`——02 的 done 守卫要求"全部句单元可见"，补全 progress 是跳过的必要半步。**自动跟随（02 在底跟随滚动）不构成跳过触发**。
4. **可对齐**：`turn:update` 新文本 → 前缀一致（新文本以已显示前缀开头）→ 已显示前缀保持，按新文本重新切句并重定位 ui，继续按序；**前缀不一致（重写）→ 立即 done 兜底**：整段显示新全文并置 done（宁可一次性，也要页面与数据一致；02 守卫对该路径放行）。

**捷径与退化**：

| 场景 | 行为 |
| --- | --- |
| 沉默（text 空白） | 不进逐句流程：begin 即发 done（不留"…"不留状态行，宿主渲染（沉默）） |
| 无句界 2000 字 | 切句=1 单元：一段即一句，1 次 progress 即 done，不伪装节奏 |
| 400+ 单元 | 切句上限收尾（宁可单元超长不可丢字），照常走完 |
| 揭示中洪流 | 其他 Turn 并发注入：引擎按 id 独立，不中断、不丢进度、前缀不回退 |
| 切档/重播/清空 | stopAll 静默取消：无残尸、无进行中的 reveal 信号 |

## 4. 与 02 的联合守卫（不变量的载体在两侧，装配 02 逐字实现）

| 守卫（位于 02） | 规则 | 保护的不变量 |
| --- | --- | --- |
| `revealProgress` | shownText 必须是 text 的连续前缀，且长度不小于当前 shownText（不回退） | 1 / 洪流不回退 |
| `revealDone` | midReveal（text 有实内容 ∧ shownText 是 text 真前缀 ∧ 未满）→ **拒绝**；重写兜底（shown 非前缀）与沉默（text 无实内容）放行 | 2（不整段倒出） |
| `update` | 揭示轮的 `patch.state` 非 revealing → 剥离该键 | 2（done 单一写者） |

跳过竞争：跳过（skip）置 done 并 clearTimeout，与进行中的句显示不会叠加；终态完整全文，无重复句无丢失句（自测 T3）。

## 5. 与 03（句单元）

- 单元钩子：`span.p03-sent[data-si]`（0 起连续）；等待占位「…」是存在的句单元（`p03-sent--wait`，data-si=0）——对齐逻辑按 id 寻址，占位不等于节点缺席。
- 「正在回复…」挂 meta（`p03-st-live`），出现/消失时序归 04（02 收 progress/done 后调 `P03.fill` 即生效）。
- 03 不给句单元加任何动效、不实现揭示点击；**揭示中光标与点击命中归 04**：02 在 nodeTurn/revealDone 维护 `data-p04-live` 标记与 `title="点击跳过逐句显示"`，04 提供唯一样式。

## 6. 与 07 / 08 的耦合

- **07**：`jump:latest` 只来自 07 的用户点击（Enter/点击等效）；02 的滚动跟随、滞回判定不发该信号——"自动跟随不跳过"由信号所有权结构性保证。
- **08**：send 剧本 `busy=true → turn:append(assistant revealing) → … → reveal:done → busy=false`，**reveal:done 是 busy 解除的对齐点**；跳过/兜底/沉默路径同样发 done，busy 不滞留。08 的生命周期信号（demo:dataset/replay/reset）→ stopAll；08 永不发 reveal:*。

## 7. 装配

1. **接线次序**：02 的总线订阅（含 reveal:progress/reveal:done）必须先于 `P04.init(bus,…)注册`——同帧沉默/重写兜底的 correction 依赖监听注册序（02 先渲染占位，04 的 done 同帧收敛）。
2. `P04.init(bus,{skipHost:listEl, sentences:P03.splitSent})`；listEl=02 唯一滚动面。
3. 04 的 CSS 仅一条：`.p03-msg[data-p04-live]{cursor:pointer}`。
4. 节奏常量在 P04 模块头（CAD/THINK），与 02 的 dev 数据、DIRECTIONS 的 B 基线一致；自测可用 `P04.begin(turn,{cadence,think})` 提速。

## 8. 文案（经 03/02 呈现，本部件不持有 DOM 文案）

正在回复…（03 meta）/ …（03 等待占位）/ （沉默）（03 沉默占位）/ 点击跳过逐句显示（title 提示，02 写入）。

## 9. 验收对照（规格 §7 攻击面 → 自测）

| 攻击面 | 自测 |
| --- | --- |
| done 补全攻击（外部置 done 不整段倒出） | T2：守卫拒绝、可见前缀一字不动、引擎按原节奏补全至全文 |
| 对齐攻击（一致增长不重头 / 重写立即一致） | T4 / T5 |
| 跳过竞争（点击与进行中句显示同时） | T3：终态完整全文，无重复无丢失 |
| 全空白回复 | T6：直达（沉默），无「…」/状态行滞留 |
| 无句界长文 | T7：一段即一句，1 次 progress 即 done |
| 并发洪流 | T8：进度单调、unread 正确累加 |
| 切档中断 | demo:reset → stopAll，日志无残余信号（重置按钮演示） |
