# FRAGMENT · 部件 08 演示控制器（p08-）

> 源实现：`parts-impl/08/part.html`（`window.P08`，IIFE 导出；`p08-panel` 为部件本体，lab 剧本台/替身读出/信号台为演示脚手架，组装时丢弃）。
> 规格：`parts/08-演示控制器.md`（档位/剧本/内容清单/状态矩阵/文案表）；工艺 `PRINCIPLES.md`。
> 08 是**收口部件**：唯一数据源与剧本引擎。它不含聊天界面本体，只合成总线信号与驱动 02 的载入生命周期。

## 0. 定位与信息分区

- 演示控制区（p08-panel）与聊天区并列：读屏经 `aria-label="演示控制器"` 区分"演示装置"与"被演示的界面"。
- **假数据声明常驻一行**，全部 5 档 + 空态 + 失败态下在场；"这是假数据"的一切声明归 08，其他部件不得复述。
- 离线硬约束：档位/重播/清空/开关的任何操作不发任何网络请求（纯 setTimeout + 本地数据）。

## 1. 对外 API（`window.P08`）

```js
P08.init(bus, host)      // host=02 装配适配层：{beginLoad():gen, loadDone(gen,arr), loadFail(gen), state?():snapshot}
P08.mount(el)            // 构建面板（档位组/动作/开关/声明）并绑定事件
P08.setDataset(name)     // 'normal'|'worst'|'single'|'empty'|'flood'
P08.replay()             // 重播当前档（含 loading）；装填的载入失败在此生效
P08.clear()              // 清空会话（等价切 empty + 停一切剧本；不清草稿——草稿属于用户）
P08.toggleFailOnce()     // 模拟发送失败：置位后下一次 submit 走 error 剧本，成功一次自动复位；返回置位态
P08.toggleClosing()      // 模拟服务关闭：置位 → linkState=closing；复位 → up；返回 closing 态
P08.armLoadFail()        // 装填一次载入失败（重播或下一次档位装载生效；规格 §5"重播或首载模拟触发一次"）
P08.injectProactive()    // 主动消息剧本：无 submit 触发，channel=qq 的 assistant 轮走揭示
P08.state()              // => {dataset,playing,busy,link,failOnce,closing,gen,loadFailArmed,queued,userName}
P08.DATA / P08.NAMES     // 数据集与档位名（测试钩子）
```

## 2. 信号（08 合成全部总线信号）

**发出**：

| 信号 | 载荷 | 时机 |
| --- | --- | --- |
| `demo:dataset(name)` | 档位名 | 点档位：停旧剧本 → 指示先行更新 → beginLoad → 420ms → 注入 |
| `demo:replay` / `demo:reset` | 当前档名 / 无 | 重播 / 清空（04 收三者 → stopAll） |
| `turn:append(Turn)` | 完整 Turn | 数据集注入（loadDone 由 02 排序渲染）、洪流分批、send/error/retry/主动消息剧本 |
| `turn:update(id,patch)` | `{id,patch}` | 用户轮回执 sending→ok；retry:send 的 sending→ok |
| `busy:change(bool)` | bool | send/retry 剧本 true；**以对应轮的 reveal:done 为 false 的对齐点**；切档/清空兜底复位 |
| `link:change('up'\|'closing')` | string | 服务关闭开关（01 呈现、05 锁定，两处文案各自归属） |
| `send:failed(text)` | 已 trim 原文 | error 剧本：05 逐字回填草稿 + 失败提示；failed Turn 留列表（03 重试） |

**消费**：

| 信号 | 处理 |
| --- | --- |
| `submit:text`（05） | link=up 且非 busy 才执行；loading/失败面期到达 → **缓存**（草稿已被 05 清空，不可丢弃），注入完成后依序执行；failOnce 置位 → error 剧本（一次性自动复位） |
| `retry:send(id)`（03） | turn:update sending → 900ms → ok → busy=true → assistant 揭示（重发走 send 剧本） |
| `retry:load`（02 失败面） | loadDataset(当前档) |
| `reveal:done(id)`（04） | id===busyOwner → busy=false（busy 解除对齐点）；顺带 flush 缓存的 submit |

**永不发出**：`reveal:progress` / `reveal:done`（04 唯一写者）、`follow:change`（02）、`jump:latest`（07）、`focus:composer`（06）、`retry:send`（03）。

## 3. 剧本时序（字段级）

| 剧本 | 序列 |
| --- | --- |
| send（默认） | `submit:text` → busy:change(true) → turn:append(user, sending) → 350ms → turn:update(user ok) → turn:append(assistant, state='revealing', shownText='') →【04：等待首句→逐句 progress→done】→ busy:change(false) |
| error（开关触发） | `submit:text` → turn:append(user, **failed**) → send:failed(原文本)；无 busy |
| closing（开关触发） | link:change('closing')；复位 → 'up'。连接类归 01、处理类归 05，各自文案 |
| retry:send | turn:update(sending) → 900ms → turn:update(ok) → busy=true → assistant 揭示 |
| 主动消息（随 normal 档就绪 +1.2s） | 无 submit 触发：turn:append(assistant, channel='qq', state='revealing')——验证用户未发消息时列表也会增长。**不置 busy**（无人请求处理） |
| 载入失败（一次性） | beginLoad → 420ms → loadFail（02 失败面）→ 用户重试 → retry:load → loadDataset 当前档。仅由"重播（装填后）"或"首载模拟"触发一次 |
| flood 注入 | loadDone(前 10 条) → 每 280ms 一批 10 条 turn:append；gen 令牌守卫，切档/清空中途可取消 |

## 4. 数据集目录（与 02/03 dev 同源收口）

| 档位 | 内容 | 覆盖的验收 |
| --- | --- | --- |
| normal 日常对话 | 5 轮双向（1–3 句/轮）；含 channel=qq 轮；n4/n5 跨自然日（11-15→11-16 日期行）；就绪 +1.2s 注入主动消息（揭示演示） | 日期行 / 渠道徽标 / 分组 / 揭示全链 |
| worst 最坏数据 | 8 轮：URL 204 字（无句界）/ 2000 字长文（1900+ 字含句界）/ 纯 emoji（含 👨‍👩‍👧‍👦 ZWJ）/ 单字"嗯" / 空白（沉默）/ failed 用户轮 / time=null 轮 | 03 状态矩阵与 02 滚动语义全部极端 |
| single 单条 | 1 条 assistant | 0/1/N 边界 |
| empty 空会话 | 0 条 → 02 faceEmpty → 06 挂载 | 空态互斥（loading/失败/首条） |
| flood 洪流 50 条 | 首批 10 条 + 4 批流式；超长与单字混杂、time 有同值有 null、乱序中插 | 02 跟随/未读、08 可取消性 |

**契约取舍（规格 §2 vs 01 FRAGMENT §4）**：worst 档的"超长标题注入给 01"不实现——01 FRAGMENT 明确 sessionTitle 恒为缺省 "Alice"，2000 字注入只是 01 部件实验台能力，不是产品态；超长标题攻留 01 部件演示页。

## 5. 状态机与竞态守卫

- **gen 令牌**：每次 loadDataset/clear `++S.gen`；420ms 后的注入、洪流批次、ACK/RETRY/主动消息定时器全部以 gen 失配取消——**晚到的旧档数据不得覆盖新档**（连点三档竞态：最终列表=最后选中档）。
- **剧本唯一性**：loadDataset/clear 先 stopScript（四个定时器清空）+ setBusy(false)——同一时刻至多一份剧本在发信号；切档后无"正在回复…"残尸（04 stopAll）+ 无半截正文（02 beginLoad 清空）。
- **busy 归属**：busyOwner=触发揭示的 assistant 轮 id；该轮 reveal:done 才解除——并发揭示（主动消息 + 回复）互不误清。
- **一次性开关**：failOnce 消费即复位；loadFailArmed 消费即复位。
- **loading 期 submit 缓存**：05 已清草稿发信号，08 不得丢弃——注入完成后依序执行（flushQueue：deliver 后与每次 reveal:done 后各试一次）。

## 6. UI 契约（p08-panel）

- 结构：行 1=档位组（5 个 + 重播 + 清空）；行 2=两开关；行 3=假数据声明（常驻）。
- 档位指示：当前项 `aria-current="true"`，**指示先行更新**（点击即变，内容以注入完成为准，二者最终一致）。
- 开关：`aria-pressed`；滑块 200ms `--ease-in-out`（罕见档 #16 屏内移动）；快速反复扳动不重放不叠加（transition 重定向）。
- 按钮 press：`scale(0.97)` 160ms `var(--ease-out)` 底线；hover 颜色 140ms ease，`(hover:hover) and (pointer:fine)` 门控。
- 文案表（规格 §7 逐字）：演示数据 / 日常对话 / 最坏数据 / 单条 / 空会话 / 洪流 50 条 / 重播当前档位 / 清空会话 / 模拟发送失败 / 模拟服务关闭 / 本页为离线演示，所有消息与状态均为虚构数据。

## 7. 装配

1. `P08.mount(panelEl)` → `P08.init(bus,{beginLoad:()=>P02.beginLoad(), loadDone:(g,a)=>P02.loadDone(g,a), loadFail:g=>P02.loadFail(g), state:()=>P02.state()})`——02 生命周期走适配层直调（无对应总线信号），其余全走总线。
2. 启动：`P08.armLoadFail(); P08.setDataset('normal');`——首载模拟失败一次（规格 §5"首载模拟触发一次"），失败面点重试进入 normal 并随主动消息演示揭示全链。
3. 接线次序（同页注册序）：02/05/07 订阅 → P03.init → P04.init → P08.init。08 收 submit:text 在 02/05 之后无碍（其剧本异步发 turn:append）。
4. 面板置于页面最上（B 基线），01 头栏在面板之后、02 列表之前。

## 8. 与各部件的耦合

- **02**：唯一 host 直调面（beginLoad/loadDone/loadFail）；数据经 turn:append/turn:update 注入；retry:load 回环。
- **03**：failed Turn 经 turn:append 渲染（w7 / error 剧本）；retry:send 由此回环。
- **04**：assistant 轮以 state='revealing'+shownText='' 建立，04 自动接手；08 收 reveal:done 解 busy；生命周期信号喂 stopAll。
- **05**：turn:append(user) 是在途锁回执；send:failed 回填；busy:change/link:change 控提示与锁定。
- **06**：empty 档即其数据源（loadDone 空数组 → 02 faceEmpty）；userName 恒 '阿澈'（PARTS §2.2 字段）。
- **07**：洪流/主动消息离底时的 unread 累加由 02 折算，08 不直碰。
- **01**：link:change 唯一写入者；sessionTitle 恒缺省。

## 9. 验收对照（规格 §8）

| 攻击面 | 实现/演示 |
| --- | --- |
| 连点三档竞态 | gen 令牌 + stopScript；part.html「连点三档竞态」剧本：最终列表=最后选中档，无旧档混入 |
| flood 注入中途清空 | clear → gen 失配批次作废；列表空、pill 无、空态在、无进行中信号 |
| 揭示中切档 | demo:dataset → 04 stopAll + 02 beginLoad：无残尸无半截 |
| 剧本中重播 | stopScript 先行：无两份剧本并发发信号（busy 复位、定时器清空） |
| 假数据声明常驻 | p08-note 恒在场 |
| 离线硬约束 | 纯本地数据 + setTimeout，零 fetch/XHR |
| 指示与内容最终一致 | aria-current 先行；注入完成由 loadDone/gen 守卫保证不矛盾 |
