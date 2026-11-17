# R5d · 布局审查：规格对位与令牌执行（审查员 D）

2026-11-18。输入：`layout-snapshot-20261117.json`（真机 1366×850 快照）× `03a`（令牌/密度/容器/dock）× `03b/03c`（页面规格）× `web/theme-orbit.css` 全文 × `06-集成施工记录.md`（勘误终值）× `web/orbit.js` 关键段核对（分组/密度挂载）。三方对照口径：**稿（设计文档）→ 落码（CSS/orbit.js）→ 实测（快照 rect）**。只读审查，未改任何代码。

---

## 一、总评

四页骨架的"大语言"是同一套且兑现良好：页容器 1080px 四页一致、左右留白 143/143 均衡、wide 密度 32/32 在 P2/P3/P4 实测兑现、dock 56px 高与 26px 页底让位四页全过、`--orbit-dock-clear` 派生纪律全稿零裸写（80/94/96 均经 calc 引用）、R3 P1-5 的 .70 功能档与 .55 装饰档在落码全部就位、M2 窗 640/窗头 52/步点 36/头 padding 24 与稿逐项吻合、P4 hub 卡 72/16/双列与稿一致、P1 的 92px 身分行 + 98px 消息列契约 + composer 880 对齐（06 八补.3 修复后）三方吻合。**但密度三档的"挂载机制"存在系统性缺口：`--den-pad` 只在带 `data-density` 的元素上定义且 CSS 无回退值，orbit.js 只给页容器（wide）与设置卡/自建窗 body（mid）挂档——导致三个自建窗窗头、S1 确认框、S5 权限卡 padding 归零、S7 卡误继承 wide 32px（P1-2）；同时 P2 双列分组与 02b 0.5 登记表左右颠倒并引发结果区溢出主列（P1-1）。** 这两条是结构性断裂，修复成本低（挂档 + 回退 + 重排 moveNode），但波及面广：前者波及除 M2–M7 外所有自建窗与 S 族浮层，后者动摇 P2 页的整页信息架构。跨页页头语言在 P2/P4 横贯兑现、P3 缺席横贯模板（P2-1），M2 窗头工具簇出现 36×32 异位钮与 40×40 dock 类图标混用（P2-2）。令牌纪律整体执行认真，残留问题集中在"稿自身即裸写"的一批一次性效果色与 dock 底色 f0/e6 双值（P3 组）。

### 通过项核对表（三方吻合，不再立项）

| 维度 | 稿值 | 落码 | 快照实测 | 结论 |
| --- | --- | --- | --- | --- |
| dock-clear 派生 | 96 令牌 + calc 派生（03a 1.3/R3 P1-3） | `#app`=clear−2、OB=clear、toast=clear−12、M10 窗高=…clear+8，无裸写 80/94/96 | 页底 756 → dock 顶 782，让位 26px 四页一致 | PASS |
| R3 .70 档 | 功能低档 .70 / 装饰 .55 / coarse .75 | `#chat-latest` .7、`.orbit-source-icon` .7、`[data-idle=low]` .7、coarse .75、sigil .55 | 快照未测透明度（代码层核对） | PASS |
| wide 密度 | pad 32/gap 32（03a §二） | `[data-density=wide]` + orbit.js 770/780/847/933 挂档 | P2/P3/P4 页 padding 32px、gap 32px 实测 | PASS |
| 页容器 | max-width 1080 居中 | `min(1080px,100%)` | 四页均 [143,52,1080,704]，左右 143 均衡 | PASS |
| P1 身分行 | min-height 92、padding 20/28/16（03b 1.1） | 92 在 style.css 现状沿用，orbit 只换 border 色 | header [145,54,1076,92]，padding 20/28/16；动作区 88=40+8+40、右缘 28 对齐 | PASS |
| msgs 契约 | 30/98/28 红线不动 | 不碰 | msgsPadGap 实测一致；composer 243/880 与消息列同轴（八补.3 生效） | PASS |
| M2 窗 | md 640 / 头 52 / 步点 36 / mid pad 24 | 基类+`--orbit-win-head-h`+设置卡挂 mid | 卡 638(+2 边)、头 52、title x=卡+24、dots 36×36、底部余 24 | PASS |
| P4 hub | 卡 min-h 72 / gap 16 / 双列（03b 4.1） | 同 | 7 卡 499×72、行距 88、列距 16、页头 63 | PASS |
| P3 右列 | 340px 抽屉栈 / 栈内 gap 8（03b 3.1） | `minmax(0,1fr) 340px`、`.orbit-drawer-stack` gap sp-2 | 右列 340@x850、列距 32、栈 gap 8px 实测 | PASS |
| 三层容器宽度 | 窗 640/860、浮层 ≤320 | 基类照抄；S5 360 为登记豁免 | M2 638 ✓；S 族未开窗未测（见 P3-3） | PASS（帽内） |
| P1 直角例外 | radius 0 随 chat 形状契约（03b 0.1） | `#orbit-main` 无圆角、`.chat` 2px 边不动 | 与 03b 十.1 一致，例外设计自洽 | PASS |

---

## 二、问题清单

### P1-1 · P2 双列分组与 02b 0.5 登记表左右颠倒，结果区溢出主列 336px

**三方对照**
- **稿**：02b 0.5 登记表——左列 `.orbit-body-side` 收纳 `.discovery-overview`（行动区）、`#orbit-strategy-drawer`、`.orbit-entry-row`（S10 入口排）；右列 `.orbit-body-main` 收纳 `.discovery-results`；2.1 补充右列底部含「活动记录与用量」折叠。02b 2.4 行定义 `grid-template-rows: auto minmax(0,1fr)`。
- **落码**：orbit.js 804–831 实际分组——**右主列**收 `.discovery-overview`+`.discovery-results`+`#discovery-preferences`（注释自书"右主列：行动区 + 结果区 + 偏好"，808–810）；**左列**收见闻维护+活动用量 details+策略抽屉（824–826）；S10 入口排未建（`.orbit-entry-row` 类实际用在了 M9 窗内三钮排，1053–1056）；偏好被包进 `.orbit-float` 浮层（831）——与登记表三项全部错位。CSS 行定义照抄稿，但两项自动放置全部落入第 1 行，`minmax(0,1fr)` 第 2 行空置。
- **快照**：`orbit-body`[178,329,1010,392]＝行1 360+gap32+行2 0；`.orbit-body-main` 实测高 360，其内 overview 309px + results 387px ≈ 696px 内容溢出至 y≈1025，超出页面可视底缘 723 约 **300px**；主列无内滚，可达性全靠页级滚动兜底；左列 360px 中可见内容仅 drawer 46px + 上方 59px，大量空置。

**修法**：①orbit.js 按 02b 0.5 重排 moveNode 分组（overview/入口排回左列，activity/usage 折叠回右列底部），或经主代理裁定修订 02b 0.5 并同步登记——**不可两者都不做**；②行定义与分组二选一收敛：主列独占 `minmax(0,1fr)` 行 + `overflow-y:auto; min-height:0`（卡流内滚），或 rows 改单行 `auto` 让页级滚动完整接管；③`.discovery-overview`（快照中 309px 块）在 02b 结构清单中未登记，归属（左列首块 or 右列保留）提请裁定后补登记。

### P1-2 · `var(--den-pad)` 无定义回退 + 密度挂载缺口：三窗头/S1/S5 padding 归零，S7 误档 32px

**三方对照**
- **稿**：03a §二 tight 档 pad 16px（S1/S5/S7 走 tight）；03c 1.3 窗体 mid pad 24px、窗头 `padding: 0 var(--den-pad)`。
- **落码**：CSS 中 `--den-pad` 仅由 `[data-density=…]` 三条规则定义，**无回退值**；引用处：`.orbit-win-head`（:505）、`[data-orbit-surface=confirm/gate/avatar-card]`（:743/:770/:779）。orbit.js 挂档全集＝{自建窗 `.orbit-window-body`→mid（562），设置卡→mid（670），五页容器→wide（770/780/847/933/995）}；**makeWindow（529–563）把 `.orbit-win-head` 挂在 dialog 直下、density body 之外**，dialog 本身不挂档；S1 `#action-confirm` 为 body 级 dialog（index.html:15，#app 外）、gate 由 claude-gate.js 注入 #app 外——均无 density 祖先；S7 卡容器在 #chat 内（index.html:161）→ 误继承 P1 的 wide 32px。
- **快照**：M2 设置卡（卡身挂 mid）窗头 title x＝卡+24 ✓ 正常——反证未挂档者归零；自建窗与 S 族未开窗未测，但 IACVT 语义（padding 归 initial 0）为确定性行为。

**后果**：S2/M8/M9 三个自建窗窗头标题贴窗缘（M10 无 makeWindow 头、其宿主 worker-head 自带 padding 不受累）；S1 确认框与 S5 权限卡文字按钮贴边；S7 卡 32px 而非 tight 16px。
**修法**：①makeWindow 给 dialog 挂 `data-density=mid`（一行，连同窗头一并修复）；②orbit.js 给 `#action-confirm`、gate 注入根、`.collaborator-avatar` 挂 `data-density=tight`（或 CSS 侧三处写 `padding: var(--den-pad, 16px)` 双保险）；③S7 若确需 16px，挂档位置需脱离 #orbit-main 子树或直接以 tight 属性覆盖。

### P2-1 · P3 页头缺席 `.orbit-page-head` 横贯模板，页头语言三页不同构

**三方对照**
- **稿**：03b 0.3 "页头统一语言（**P2/P3/P4 共用**）"；3.1 P3 页头＝h2+刷新+右端清醒睡眠 chip+切换按钮。
- **落码**：CSS `.orbit-page-head` 已定义；P4 由 orbit.js 建头（933–935 `head.className="orbit-page-head"`）；P2 走宿主 `.discovery-header`（横贯 #discovery 1014，值与模板同）；**P3 组装段（842–879）无任何 head 构建**，宿主 `#thought-settings` 的原生标题随"其余全部子节点"落入主列。
- **快照**：P3 `panel`[176,85,1014,961] 唯一子节点 `orbit-body` 顶齐 y85——P3 页头只能在 body 左列（642 宽）内，不横贯；P4 页头实测 [176,85,1014,63] 横贯。

**修法**：orbit.js 给 P3 补建 `.orbit-page-head` 页级 kid（h2「思想与探索」+刷新+chip+切换按钮上提横贯），或在 06 勘误表登记"P3 页头随宿主结构留主列"为终态裁定——当前是 silent deviation。

### P2-2 · M2 窗头工具簇混规格：36×32 异位钮 + 40×40 dock 类图标

**三方对照**
- **稿**：03c 1.2——右侧工具簇（说明/同步/关闭×）统一 `.orbit-win-btn` 32×32、gap 8、右对齐；关闭×沿用宿主节点换皮 32×32。
- **落码**：`.orbit-win-btn` 32×32 已落（:514）；`.orbit-win-tools` margin-left:auto 已落。
- **快照**：M2 head kids 实测——`card-x` [696,52,**36**,32]（36 宽、**不在右缘**，距右缘工具 206px）与 `orbit-icon` [938,48,**40**,40]（**dock 图标语言 40×40**）同簇混用；右缘 24px 对齐的是那枚 40×40 图标。

**修法**：核对 orbit.js 对 `#settings-x`/「说明」钮的 subClass——两枚统一 `orbit-win-btn`（32×32）并收进 `.orbit-win-tools`；`card-x` 的 36px 宽与 mid-head 异位疑宿主 `.modal-head` 布局残留，需在 orbit 域重置；40×40 的 `.orbit-icon` 类误用（dock 语言不该出现在窗头）。

### P2-3 · 三页页头 align-items 三值并存（flex-end / center / flex-start）

**三方对照**
- **稿**：03b 0.3 `.orbit-page-head` `align-items:flex-end`，自称"P2/P3/P4 共用统一语言"；03b 2.4 P2 `.discovery-header` 却写 `align-items:flex-start`；03b 3.4 P3 覆盖 `align-items:center`。
- **落码**：三值照单全收（theme-orbit.css :198/:331/:376）。
- **快照**：P4 头 63px（flex-end 形态）；P2/P3 头未单独捕获，无法实测对照——差异停留在代码层即已成立。

**修法**：统一为一种（建议 flex-end：动作区与导语基线对齐，kicker 眉标在 P4 是唯一变体可注记）；若有意差异（P2 的 × 顶对齐、P3 的 chip 居中），在 03b 注记理由，消除"同名模板三值"。

### P3-1 · P1 信号行设计塌缩：已登记施工偏离，但 03b 未回改闭环

**三方对照**：稿 03b 1.1 信号行 min-height 38px、与身分行"两行合读"→ 落码 orbit.js 1024–1038 将 `#chat-refresh` 移入顶栏动作区、sync-bar 无可见内容时 `display:none`（06 八补.2 登记）→ 快照 chat 键集无 sync-bar（已隐藏）。**修法**：按 06 勘误表惯例补一条"03b 1.1 信号行——同步图标移顶栏后信号行空壳隐藏为终态"，03b 加残注即可，不必回改全文。

### P3-2 · M2 窗体区间距 44px ≠ mid 档 20px（宿主残留待核对）

**三方对照**：稿 mid 区间距 20px（03a §二）→ 落码 theme-orbit.css 未对 `.settings-section` 间距做任何收敛（依赖宿主）→ 快照 section1[151..222] 与 section2[266..] 间距 **44px**（而 dots→section1 的 20px 是对的）。**修法**：核对 style.css `.settings-section` margin 残留，orbit 域按 `--den-gap` 收敛；若两 section 间存在未捕获节点则销案。

### P3-3 · 浮层纪律的机检钩子空挂：`data-orbit-scroll` 零实例，S5 360 豁免未收编 03a

**三方对照**：稿 03a §五规则 4 例外"须挂 `data-orbit-scroll` 作为机检钩子"→ 落码 CSS 基类+钩子已落（:74），但**全 CSS 唯一 `.orbit-float` 实例是 P2 偏好包装**（orbit.js 831，未挂钩子）；实际带内滚的浮层（S1 40dvh、S8 data-scroll、M10 popover 320px）全部经 `data-orbit-surface`/专用选择器复制实现，无一挂 `.orbit-float` 或钩子；S5 宽 360 超帽的豁免只登记在 03c 5.4 提案 v，03a §五的例外规则口径（只讲 data-orbit-scroll）覆盖不到宽度帽豁免。**修法**：二选一闭环——①S1/S5/S7/toast/popover 统一改挂 `.orbit-float`（滚动者加 `data-orbit-scroll`）；或②06 勘误表登记"浮层语言允许 data-orbit-surface 复制实现，钩子仅约束 .orbit-float 实例；S5 360 豁免并入 03a §五注记"。

### P3-4 · dock 底色 `#0d1624f0` 裸写 4 处，与 `--orbit-surface-page(e6)` 形成双值

**三方对照**：稿 03a 3.2 写"底色 `--orbit-surface-page`（`#0d1624f0`）"——**稿自身即矛盾**（令牌值是 e6，括号值是 f0）→ 落码 dock 底、badge 开孔描边、dot 描边三处裸写 f0（:87/:138/:145）→ 快照 dock 实测形态正常（差异仅在透出率）。**修法**：增设 `--orbit-surface-dock: #0d1624f0` 令牌（三处引用），03a 3.2 措辞同步；或接受 f0 为"比页容器实一档"的独立值并登记豁免。

### P3-5 · 效果色/渐变裸写一组（稿源同，低优先）

轨道延伸线渐变裸写 `#3d5178`（:93，可 `var(--orbit-line-strong)`）；站点光点 `#91b8ff88`（:122）、dot-breathe `#e4c08040`（:858）、toast-danger `#ffa3a34d/#ffa3a333`（:851–852）、badge warn 字 `#3a2c10`（:140）。全部源自 03a 8.2/3.4 草稿原文。**修法**：高值处（渐变）改 var 引用；效果色若保留，03a 令牌表加"一次性效果色豁免"注记，避免后续审查反复立案。

### P3-6 · 小硬值散点

`worker-list-filter button` radius **4px**（:678，圆角档 6/8/10/14 之外）；`#chat-new-attempt` gap **6px**（:286）、`#discovery-toggle-enabled::before` margin-right **6px**（:1033）——非 4 倍数档。**修法**：radius 4→`--radius` 6 或登记例外；6px→`--sp-1` 4 或 `--sp-2` 8，或登记为图标-文字专用距。

### P3-7 · composer 对齐公式在 720–936px 区间与消息列失配（待核对）

**三方对照**：稿/06 八补.3——composer 侧距公式 `max(28px, (100%−880px)/2)` 与 #msgs 同源 → 快照 1366 下 243/880 精确对齐 → 但容器宽 <936px 时公式取 28px 而 `#msgs` 侧距契约仍是 98px（room 窄屏媒体查询行为未核对），720–936 区间两列不对齐。**修法**：核对 style.css 是否已有 #msgs 窄屏规则；若无，composer 公式与 #msgs 在同一断点同步收敛（保持"同源公式"承诺）。

---

## 三、落码补丁清单（汇总）

| # | 对应问题 | 补丁 | 落点 |
| --- | --- | --- | --- |
| 1 | P1-1 | 重排 P2 moveNode 分组对齐 02b 0.5（或裁定修订 02b 0.5 后按新表执行）；`.orbit-body-main` 加 `overflow-y:auto` 或 rows 收敛单行；`.discovery-overview` 归属裁定后补登记 | `web/orbit.js` 804–831、`web/theme-orbit.css` :337–343 |
| 2 | P1-2 | makeWindow 给 dialog 挂 `data-density="mid"`；orbit.js 给 `#action-confirm`/gate 注入根/`.collaborator-avatar` 挂 `data-density="tight"`；CSS 三处 `var(--den-pad)` 补回退 `16px` | `web/orbit.js` :530–563 及 S 族安装段；`web/theme-orbit.css` :505/:743/:770/:779 |
| 3 | P2-1 | orbit.js 为 P3 补建 `.orbit-page-head` 横贯页头（或 06 登记终态豁免） | `web/orbit.js` 842–879 |
| 4 | P2-2 | M2 工具簇两钮统一 `orbit-win-btn` 32×32 收进 `.orbit-win-tools`；重置 `card-x` 宿主残留位；摘除窗头 40×40 `.orbit-icon` 误用 | `web/orbit.js` 设置窗增强段 |
| 5 | P2-3 | 页头 align-items 统一 flex-end（或注记三值理由） | `web/theme-orbit.css` :198/:331/:376 |
| 6 | P3-1 | 06 勘误表补"03b 1.1 信号行空壳隐藏为终态"残注 | `06-集成施工记录.md` §三 |
| 7 | P3-2 | 核对 `.settings-section` margin 残留并按 `--den-gap` 收敛（或销案） | `web/theme-orbit.css` §3 |
| 8 | P3-3 | S 族统一挂 `.orbit-float`+`data-orbit-scroll`，或 06 登记"复制实现豁免+S5 360 并入 03a §五注记" | `web/theme-orbit.css` / `06` §三 |
| 9 | P3-4 | 增设 `--orbit-surface-dock:#0d1624f0`，dock/badge/dot 三处改引 | `web/theme-orbit.css` :16 区/:87/:138/:145 |
| 10 | P3-5 | 渐变改 `var(--orbit-line-strong)`；效果色豁免注记入 03a | `web/theme-orbit.css` :93 |
| 11 | P3-6 | radius 4→档内值；6px→sp 档或登记 | `web/theme-orbit.css` :286/:678/:1033 |
| 12 | P3-7 | 核对 room 窄屏 #msgs 行为，必要时 composer 公式同断点收敛 | `web/theme-orbit.css` :1106 |

**未改动任何代码**；本清单为 R5 汇总轮的输入，全部补丁待主代理裁定后施工。快照未覆盖面（OB1/OB2、S 族开窗态、M8/M9/M10 开窗态、.70 档实测透明度）已在对应条目标注，转 07 人工验收清单核对。
