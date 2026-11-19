# ASSEMBLY-NOTES — sample.html 组装记录（2026-11-17）

> 组装对象：`sample.html`（P01/P02/P03/P04/P05/P06/P07/P08，方向 B 舱壁 Bulkhead）。
> 契约来源：`parts-impl/0X/FRAGMENT.md` ×8；工艺宪法 `PRINCIPLES.md`；编排 `choreograph`（页级入场）。
> 本次新增部件：`parts-impl/04/`（逐句揭示流）、`parts-impl/08/`（演示控制器）——其 FRAGMENT 即契约，此处只记**拼装时对契约的改动**与全链路验证结果。

## 1. 信号路由表（谁发 → 谁收；装载后均经 `bus {on,emit}`）

| 信号 | 发 | 收 | 载荷 | 备注 |
| --- | --- | --- | --- | --- |
| `submit:text` | 05 | 08 | 已 trim 原文 | 08 消费：loading/失败面期缓存（§A14），failOnce 置位走 error 剧本 |
| `demo:dataset(name)` / `demo:replay` / `demo:reset` | 08 | 04 | 档名/无 | 04 → stopAll（切档/重播/清空取消揭示，无残余信号） |
| `turn:append(Turn)` | 08 | 02 → 05 → 04 | Turn | 注册序即契约：02.inject 先（节点在场，04 同帧 correction 依赖它）→ 05 回执释放在途锁 → 04（assistant+state='revealing' → begin） |
| `turn:update(id,patch)` | 08 | 02 → 04 | `{id,patch}` | 02.update 先（原位 fill + 状态剥离守卫 A4）→ 04.align |
| `reveal:progress(id,shownText)` | **04（唯一写者）** | 02 | 前缀 | 02 守卫：连续前缀 + 单调不回退；每句一次，离底 unread+1 |
| `reveal:done(id)` | **04（唯一写者）** | 02 → 08 | id | 02 守卫：前缀未完拒收（不整段倒出）；08 收它解除 busy（busyOwner 对齐） |
| `busy:change(bool)` | 08 | 05 | bool | 05 提示行 busy 态 + 锁发送（输入保留） |
| `link:change('up'\|'closing')` | 08 | 01 + 05 | string | 01 状态章（呈现）与 05 提示行+锁定（消费）各自归属 |
| `send:failed(text)` | 08 | 05 | 已 trim 原文 | 05 逐字回填草稿 + 失败提示；failed Turn 经 turn:append 留列表（03 重试） |
| `retry:send(id)` | 03 | 08 | `{id}` | 08：sending → 900ms → ok → busy → assistant 揭示（重发走 send 剧本） |
| `retry:load` | 02 | 08 | 无 | 08 重新装载当前档 |
| `follow:change({atLatest,unread})` | 02 | 07 | 对象 | 07 显隐与计数的唯一真源（滞回 140/48px 归 02） |
| `jump:latest` | 07 | 02 → 04 | 无 | 注册序：02.jumpLatest（回底+清零）先，04.skipAll（用户主动回底=跳过）后 |
| `focus:composer` | 06 | 05 | 无 | 05 聚焦输入区（行为非动效） |
| （非总线，host 适配层）`beginLoad/loadDone/loadFail/state` | 08 | 02 | — | 02 载入生命周期无对应总线信号，按 02 FRAGMENT 对外 API 直调（§A11） |
| （非总线）`P02.onChange → P07.render(ctx)` | 02 | 07 | `{turns,listState}` | 07 只认 follow:change 一个信号（其 FRAGMENT），turns/listState 上下文由装配方注入（§A7） |

## 2. 拼装时对部件契约的改动（逐条：改动 + 原因）

**A1 · 切句统一**：02 的本地 `splitSent` 改为委托 `P03.splitSent`（对外 `P02.sentences` 同源）。原因：02/03/04 三处 FRAGMENT 都要求"03 公共实现为准，防两份漂移"。

**A2 · 节点内部归 03**：02 的 `nodeTurn` 改为 `article.p02-msg.p03-msg` + `P03.fill`；02 的骨架 CSS（对齐/宽度/组距/日期行边距）保留，02 的占位气泡 CSS（.p02-bub/.p02-meta 等）删除，03 的骨架代演行删除（其注释明示"装配时由 02 提供"）。article 双类使两侧 CSS 契约逐字成立、零冲突（规则同值）。入场类 `p03-enter` 仅实时尾插路径（`inject` 尾插快路）添加，重建/回填/更新一律 false——同内容只入场一次。

**A3 · update 原位化**：02.update 由 `replaceWith(fresh)` 改为对既有 article `P03.fill`（原地、保焦点、不重放入场）。原因：03 FRAGMENT §7 明示建议；乱序/时间变更仍走 structural 重建（instant）。

**A4 · 04/02 联合守卫（04 FRAGMENT §4，装配 02 逐字实现）**：
- `revealProgress`：shownText 必须 text 连续前缀且长度不回退（不变量 1/洪流不回退）；
- `revealDone`：midReveal（text 有实内容 ∧ shown 为真前缀 ∧ 未满）→ 拒绝（不变量 2，"done 曾整段倒出"真实产品教训的守卫面）；重写兜底（shown 非前缀）与沉默（text 无实内容）放行；无节点（让位窗口期）只落数据；
- `update`：揭示轮的 `patch.state` 非 revealing → 剥离该键（done 单一写者）。
原因：不变量的载体在两侧——04 引擎保证"done 只在全部句单元可见后发出"，02 守卫拒绝一切越权收尾。

**A5 · 空态面交 06 + 让位窗口**：02 `faceEmpty` 改为 `P06.mount(userName)`（挂载点归 02、内容归 06）；`inject` 的 0→1 边界实现"先消失后上屏"——`p06.leave()` 后等 `LEAVE_MS`(130ms) 再 renderList，期间 `st.emptyExit` 缓冲后续注入，gen 失配/节点摘除则让位作废。`P02.mount(el,bus,{userName})` 增加 userName 注入点（06 文案字段，PARTS §2.2）。原因：06 FRAGMENT §6 的宿主义务。

**A6 · 入场与 reduce 收口**：删除 02/03/05/06/07 各 part 页的 `.app/.lab/.p02-root` 页壳入场（页面壳 `.app` 唯一入场：220ms fade+6px rise，@starting-style）；各部件 reduce 块合并为**一条全局 reduce 块**（PRINCIPLES §4"一条全局 reduce 块"）；04 部件无 reduce 面（唯一样式是 cursor 标记）。原因：一容器一入场（choreograph 硬规则 1）+ cohesion。

**A7 · 07 的上下文注入**：07 的 `p07Mount` 改为 `P07.mount({pill,count,bus})` + `P07.render(ctx)`——显隐条件 `!atLatest && turns>0 && listState==='ready'` 中的 turns/listState 经 `P02.onChange` 注入。原因：follow:change 载荷只有 {atLatest,unread}，07 FRAGMENT 又限定它"只认 follow:change 一个信号"；由装配方补上下文是唯一不新增信号的解。

**A8 · pill 定位**：`absolute;bottom:76px`（07 演示舞台）→ `fixed;bottom:122px`（B 基线，悬于 composer 上方）。07 FRAGMENT §CSS 注释既定。

**A9 · 06 的总线**：06 part 页用页级全局 `bus`；装配封装为 `P06` IIFE，`init(bus)` 注入（唯一交互 `focus:composer` 经它发出）；其余逐字。

**A10 · 揭示交互的 DOM 标记（跨部件新增，属 DOM 标记非信号）**：02 在 `nodeTurn/update/revealDone` 维护 `data-p04-live` 属性与 `title="点击跳过逐句显示"`；04 提供唯一样式 `.p03-msg[data-p04-live]{cursor:pointer}`；点击跳过由 04 的 `skipHost`（=02 列表元素）事件委托实现。原因：03 明示"揭示中的点击归 04，03 不实现"；04 是逻辑部件不持节点——标记由宿主写、样式与交互归 04。按 PARTS §5 规则此改动在两份 FRAGMENT 成文（04 FRAGMENT §5/§7），未新增总线信号。

**A11 · 02 载入生命周期走适配层**：`P08.init(bus,{beginLoad,loadDone,loadFail,state})`。原因：PARTS §2.3 无 loading 生命周期信号；02 FRAGMENT 的对外 API 就是装配面。其余 08 一切交互走总线。

**A12 · 首载失败一次**：装配页启动 `P08.armLoadFail()+P08.setDataset('normal')`——首载模拟载入失败一次（08 规格 §5"载入失败仅由'重播'或首载模拟触发一次"），失败面点「重试」进入 normal；armed 标志一次性消费，`armLoadFail` 亦可程序化重填（覆盖"重播触发"分支）。样机 rig 不入成品，故运行期用户无重复触发入口——规格两分支的机制均已实现。

**A13 · worst 档的"超长标题注入给 01"不实现**：01 FRAGMENT §4 明确 sessionTitle 恒为缺省 "Alice"（2000 字注入只是 01 部件实验台能力，不是产品态）。两规格冲突取更后出的部件契约；该攻击面留在 01 部件演示页。

**A14 · loading 期 submit 缓存**：08 收 submit 时若 02.listState==='loading'（含失败面）→ 缓存，deliver 完成后与每次 reveal:done 后 flushQueue。原因：05 已清草稿发出信号，不可丢弃；02 loading 是整面替换期不接增量（A3 语义）。样机页实测：失败面提交 → 重试 → 缓存剧本依序执行。

**A15 · 滚动判定同步化**：02 滚动监听由"rAF 合并"改为直调 `checkFollow()`。原因：后台标签页 rAF 暂停会漏判离底（浏览器实测复现）；scroll 事件天然逐帧合并，直调等价且更快。passive 只读不拦不变。

**A16 · 03 冗余行**：`window.P03=P03;`（对未声明标识符的二次赋值）删除；模块定义已含 `window.P03=`。

## 3. 页级入场编排（choreograph 记录）

**Timeline**：

| Element | Role | Starts | Duration | Motion | Relationship |
| --- | --- | --- | --- | --- | --- |
| `.app`（页壳：08 面板+01 头栏+02 列表+05 composer） | Lead | 0ms | 220ms | fade + translateY(6px→0)，`--ease-out` | —（唯一动作者） |
| 其余一切（pill、消息、空态） | Static | — | — | 随容器在场（pill 本态隐藏；06 只在真实 empty 挂载时按其罕见档编排入场） | Carry |

**What doesn't move, and why**：01 头栏（部件零动画）、08 面板与按钮（装置）、02 列表内容（历史回填禁 stagger、同内容只入场一次）、05 composer（高频输入零动效）、07 pill（常态隐藏）。06 的四元素 275ms 编排是"空态真实挂载"的内容编排（其罕见档预算），不参与页级首载——二者不同事件、不叠加。

**Feel check**：整页单次 fade-up 读作"一块面板落位"；无二级入场可看。总长 220ms ≤ 300ms；reduce 折叠为 160ms 纯 opacity。

## 4. 全链路验证结果（浏览器实测，Chromium IAB，1280×720）

- **首载失败→重试**：载入失败面在场 → 重试 → normal 5 轮注入，aria-current/数据/生成序一致。
- **主动消息**：normal 就绪 +1.2s 注入 channel=qq 揭示轮——"…"等待 + 正在回复… → 逐句 progress → 全文 53 字/3 单元，live/wait 零残留，busy 不误置。
- **发送全链**：发送 → busy 提示"Alice 正在处理消息；输入会保留" → 用户轮 entrance（scale 0.96）→ 350ms 回执 ok → assistant 揭示 → done → busy 解除；draft 清空、在途锁经 turn:append 回执释放。
- **离底/未读/跳过**：离底 >140px pill 出现；揭示中离底 unread 逐句累加（＋1→＋8 句，洪流并发同权）；点 pill → jump:latest → 回底清零 + 揭示一次补全（"…"态 1 字 → 77 字全文一跳）、pill 消失、无重复无丢失。**自动跟随（在底）不发任何信号，不构成跳过**（结构性验证：progress 时 atLatest=true 只跟随）。
- **失败回填重试**：拨"模拟发送失败" → 发送 → failed Turn 留列表（红框、可聚焦 role=button）+ 草稿逐字回填 + 失败提示 + 开关自动复位；点失败消息 → sending → ok → Alice 回复揭示完成。
- **服务关闭**：拨开关 → 01 状态章切"服务正在关闭"、05 提示"服务正在关闭，暂停接收新消息"、发送禁用；关闭期提交被 05 拒（草稿保留）；复位恢复。
- **空态 0↔1**：empty 档 → 06 挂载（"阿澈，这里还很安静"+说明+CTA，pill 不在场，头栏在场）；「写下第一句」→ 输入区实际聚焦；空态发送 → 发送瞬间 p06 与消息绝不同屏 → 130ms 让位后用户轮上屏 → 揭示照常。
- **最坏档不破版**：8 轮全在场——1900 字长文、URL 204 字（无横向溢出，scrollWidth≤clientWidth）、ZWJ emoji 完整、单字"嗯"、（沉默）、failed 轮、time=null（meta 该字段隐藏）、跨年日期行 1 条；无"正在回复…"残尸。
- **洪流**：50 条全部入序、无重复 id；清空 → 干净 empty（0 轮、06 在、pill 无、busy false、无进行中信号）。
- **连点三档竞态**：normal→worst→single 连点 → 最终列表=单条档（1 轮 s1）、08.gen=02.gen=9、指示=最后选中档、无旧档混入。
- **04 自测套件**（parts-impl/04/part.html）：T1–T8 全过（按序/完整含 done 补全攻击/双路跳过/对齐增长/重写兜底/沉默/无句界/洪流并发）。
- **08 独立页**：首载失败 → S1 提交被缓存（文案在案）→ retry:load → 缓存剧本依序执行，trace 完整呈现 send 剧本信号序（busy true→user append→ok→assistant 揭示→done→busy false）+ 主动消息；就绪后 8 轮、busy 解除对齐正确。
- **静态纪律扫描**：三份新文件零外部引用（离线）、无 `transition:all`、无 `ease-in`（字面值）误用、CSS 无 >300ms 时长、keyframes 仅 06 一次性编排、reduce 块各 1 处（sample 为合并后的全局块）。
- **环境限制**：IAB 后台标签页对 setTimeout 有 1–2s 节流（页面 >5min 后更重），揭示节奏实测被拉长；此为测试环境伪影，前台浏览器不受影响（节奏常量 240–380ms/句不变）。`prefers-reduced-motion` 无法在该环境模拟——reduce CSS 为静态核验（块存在、选择器与部件 reduce 契约一一对应），真机降级待人工确认。

## 5. 遗留问题

1. **越权 progress 攻击不设防**：契约外直接 `bus.emit('reveal:progress',{id,shownText:全文})` 会经 02 守卫（前缀+单调）上屏为一次性倒出。已防：越权 done（midReveal 拒收）、越权 state patch（剥离）。按 PARTS §2.3 progress 唯一写者是 04，故视为出契约攻击；如需字面防御可在 02 加"每步恰好一个句单元"校验（代价：跳过协议需改逐句补发），本期未做。
2. **让位窗口内的揭示降级**：首条注入后的 130ms 让位窗口里到达的揭示轮，progress 无节点可渲染（被守卫拒），done 落数据层，让位结束后 renderList 直接呈现完成态——该极窄竞态下无逐句观感、数据一致。当前剧本时序（ACK 350ms > 窗口 130ms）不会触达。
3. **reduced-motion 真机人工确认**：见 §4 环境限制；CSS 已按各部件 FRAGMENT 的 reduce 契约合并为全局块。
4. **IME 真机**：composition 门控在 demo 页已实现（05 部件逻辑逐字），真实中文输入法验证待人工（05 FRAGMENT 装配注意 3）。本环境合成 Enter 不入 textarea 监听，Enter 路径以发送按钮等效路径实测。
5. **clip-path 合成成本**（B 方向自评风险）与 **>200 条虚拟化**（02 FRAGMENT 注意 3：优先分页剧本）均未压测/未启用，样机 flood 上限 50 条。
6. **02.update 时间补丁触发全量重建**（resort 路径）会丢文本选择/焦点——演示数据不含该路径，运行剧本仅改 status；如后续需要补丁 time，应改走原位重排。
7. **sessionTitle 最坏注入**未接（A13，属规格冲突的有意取舍，非缺陷）。
