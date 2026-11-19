# R5c 布局审查·窗与浮层（审查员 C：召唤窗与浮层体系）

2026-11-17。审查眼睛：`layout-snapshot-20261117.json` 末节 m2win（1366×850 真机 M2 打开态）；代码面：`web/theme-orbit.css` §1a/§1c/§3/§4、`web/style.css` 的 modal/settings 叠加面、`web/orbit.js` 壳机制（makeWindow 530–576、installWindows 578–655、installSettingsWindow 664–749）；规格面：03a §8.1/§五、03c §一/§五。只读审查，未改任何代码。

## ① 总评

三层容器令牌与 S 族落位基本忠实：宽度三档（窗 860 基类/640 --md/420 --sm、浮层 320、tip 240/long 320）与 03a §8.1 逐项对上（theme-orbit.css 66/499/500/76/168/178）；亮度递增（page e6→window f2→float 实色）、影锐独占（44/80/20）、变暗独占（::backdrop 仅 .orbit-window:71 + S1 `--orbit-backdrop-2`:901）、dock-clear 96 及其派生（toast 84、#app 94、S5 96）全部成立；自建四窗宽度修饰类已挂（orbit.js:584 S2→sm、645 M9→md、621/651 M8/M10 默认 860）且 tools wrapper 正确（543–555）；S5 right24/bottom96/z300/左缘 warning 与 03c §5.4 权威值一致（宽 360 系已登记例外），S8 bottom84/z70/tone 竖条、S1 320+tight+40dvh 滚动兜底、S7 240+pointer-events:none 均合规。**但 M2–M7 复用 `#settings-modal` 这条权威口径留下三处 legacy 类未接管的叠加面**：窗头保留 `.modal-head` 的 `justify-content:space-between` 且 orbit.js 未包 `orbit-win-tools`（03c 表行 52 明文要求），快照实测关闭钮落在 x=696 窗中央、说明钮孤立右缘——本批唯一 P1；步点条 legacy `margin:0 24px` 与 orbit `padding:0 24px` 叠成 48px 双重缩进、分区线内缩不通栏；`.settings-body` 的 legacy gap 22 加上 `#model-settings-anchor` 空盒子占一个 gap 槽，分区间隙实测 44px（规格 mid 档 20）。另有 ::backdrop 对 div 窗全链失效、步点指示条有 CSS 无生产者两处死区。快照各几何量与代码推断完全互证（窗 640 居中、窗头 52、body=`modal-body settings-body` 双类 4/24/24 内边距），无一处需要动壳机制，修法全部落在 orbit.js 设置窗增强段与 theme-orbit.css §3/§4 的接管补齐。合计 12 项：**P1×1、P2×5、P3×6**。

## ② 问题清单

### A. 窗体几何

- **A1｜P2｜::backdrop 对 M2–M7 全链失效（死样式 + 压暗档偏差 + 开关动画不播）**
  证据：index.html:381 `#settings-modal` 是 `<div class="modal hidden" role="dialog">`，非 `<dialog>`；orbit.js:668–669 仅给 `.modal-card` 追加 `orbit-window orbit-window--md`，div 的 ::backdrop 永不渲染 → theme-orbit.css:71 `#05080fd9`（85% 压暗）与 §4:893/898 的 backdrop 入退场动画对最常用的设置窗全部是死代码；实际压暗来自 legacy `.modal` 底 `#01040ac7`（style.css:178，78%、色相 #01040a），且无入退场过渡。自建四窗（真 dialog showModal，top layer）与 M2–M7（z-40 普通层叠）呈现两套压暗亮度与两套开关节奏。
  （居中与 max-height 本身核对通过：快照窗卡 x≈363–1003=640 含 1px 边、垂直 43–807 居中，max-height 100dvh−48=802 未触发，长内容时卡自滚 + 窗头 sticky 可达。）

### B. 窗头

- **B1｜P1｜M2–M7 窗头工具簇解体：关闭钮落在窗中央，说明钮孤立右缘**
  证据：快照 winHead [364,43,638,52] 的 kids 实测——title[388,54]、`card-x orbit-win-btn`[696,52,36,32]、`orbit-icon`[938,48,40,40]，间距 205/206 呈 space-between 均布。根因两行：①orbit.js:664–679 只 subClass 了 head/title/x，未按 03c 表行 52（"外包新建 orbit-win-tools wrapper，还原表登记解包"）包 wrapper——tools wrapper 只在 makeWindow 自建路径（543–555）存在；②head 仍带 `.modal-head` 类，style.css:180 的 `justify-content:space-between` 未被 orbit-win-head（theme-orbit.css:502–507 未声明 justify-content）覆盖。规格 1.2 的右聚工具簇（说明+关闭同簇、margin-left:auto）落空；关闭钮悬在窗正中，既有视觉误导也有误触风险（快照即证）。
- **B2｜P2｜`.orbit-win-back` 返回钮缺失（03c 1.2 明列 M2–M7 必备）**
  证据：快照 head kids 仅 3 个（title/card-x/info），无 back；orbit.js 全文无 orbit-win-back 创建（grep 零命中）。指针用户失去 Esc 同源的返回/关闭辅钮（关闭本身见 B1 已错位；本条指规格元素缺位）。待核对 05 轮次是否已另案降级。
- 核对通过：窗头高 52 实测吻合（--orbit-win-head-h: 52px，theme-orbit.css:39/505）；sticky 成立（503 `position:sticky;top:0;z-index:2;background:inherit`，父级 .orbit-window overflow:auto:69 为滚动容器，head 是其直接子级）；标题 20px/600/ellipsis（508–509）；标题/分区起点对齐 24px（--den-pad，实测 x=388）。

### C. 步点条

- **C1｜P2｜`.orbit-step-indicator` 是死代码：CSS 有、全库无生产者**
  证据：theme-orbit.css:927–934 定义了指示条（absolute bottom:−1 高 2 accent、transform/width 过渡、`:not([data-ready])` 免动画守卫），但 grep 全部 web/*.js 无任何 `step-indicator` 创建、无任何 `data-ready` 写入（orbit.js 亦无）。05 轮次 7 裁定 5 的"元素化指示条"只落了皮没有落引擎：当前分区指示实际只剩 `[aria-pressed=true]` 的 accent 底色（539，静态可见），滑动指示条永不出现，§4 的 M-09 动效规格整体悬空。
- **C2｜P2｜步点条双重缩进 + 分区线不通栏（与 D3 同根，几何归此）**
  证据：快照 dots rect [388,95,590,36]——nav 自身 x=388/宽 590，说明 legacy `.settings-nav { margin:0 24px 16px }`（style.css:198）仍生效；而 orbit `.orbit-step-dots` 又叠加 `padding:0 var(--den-pad)`（theme-orbit.css:527）→ 首格实际起于 x≈412，比窗头标题与分区的 388 多缩 24px；border-bottom 分区线只画在 388–978（590 宽），窗头底边线却通栏 364–1002（638），两条横线长短不一，"与窗体之间即是分区线"的语义破损。间距侧：nav margin-bottom 16 + body 顶 padding 4 = 20 恰好凑到 den-gap 值，属巧合对齐（见 D2）。
- 核对通过：36×36 格/18px svg/gap 4/圆角 8（529–536）；三态（idle .8 faint、hover soft、aria-pressed accent+accent-soft）齐全；密度无虞（6 静态 + claude 动态 1 = 7 格 ×40≈280 ≪ 590）；触屏 `pointer:coarse` 下 settings-nav button min-height 44（style.css:481）命中区可用。

### D. 分区内容

- **D1｜P2｜分区间隙 44px：legacy gap 22 × 2 个 gap 槽（anchor 空盒子）**
  证据：快照两 visibleSections [388,151,590,71] 与 [388,266,590,518] 间隙 44。DOM 链：index.html:526/535 两个 `data-settings-section="general"` 分区是 body 的相邻 flex 项，中间夹着 534 行 `<div id="model-settings-anchor"></div>`（0 高 flex 项，占一个 gap 槽）；`.settings-body { gap:22px }`（style.css:200）未被 orbit 覆盖（theme-orbit.css grep 零命中）→ 22+0+22=44，与快照精确吻合。规格 mid 档区块距 --den-gap 20（03a/03c 1.3），双倍超额。
- **D2｜P3｜窗体节奏未令牌化：mid 档密度只接管了控件，没接管容器**
  证据：窗体 side/top/bottom padding 全部来自 legacy `.modal-body { padding:4px 24px 24px }`（body 是 `modal-body settings-body` 双类，index.html:388；theme-orbit.css 无 .modal-body/.settings-body 覆盖）——24px 横向值与 --den-pad(mid)=24 巧合相等，但若密度/令牌变动不会跟随；顶部 4px 是 legacy 杂值（dots→首分区 20 = 16+4 的拼凑）；行距侧 `.settings-hint` line-height 1.75（style.css:206）≠ den-lead 1.7。密度令牌实际只作用到控件高 --den-ctl 38（快照 select 行高吻合）。
- **D3｜P2｜（并入 C2，见上）**
- **D4｜P3｜关闭钮 36×32：`.card-x` 的 flex-basis 残留**
  证据：快照 card-x rect 36×32。`.card-x { width:36px; height:36px; flex:0 0 36px }`（style.css:35），orbit-win-btn 只覆盖 width/height=32（theme-orbit.css:514–515）未重置 flex → flex 主轴上 flex-basis 36px 压过 width 32，成 36×32 的非规格钮（spec 32×32）。
- 核对通过：label/select/hint 纵向节奏本身正常（.settings-section gap 9、label 13/600、hint 13 muted；快照分区行高与 --den-ctl 38 吻合）；两个 general 分区并显是设计使然（同 data-settings-section 值成组切换），非双显 bug。

### E. 浮层体系一致性

- **E2｜P3｜S9 启动条宽度 480 未对齐浮层纪律、换行态 radius 未自适应**
  证据：theme-orbit.css:812 `max-width: min(480px, calc(100vw - 48px))` 超 03a §五.4"浮层 ≤320"帽（S5 的 360 已在 03c §5.4 登记 8.3 提案 v，S9 无登记）；且 03c §5.7"radius 999（换行时自适应为 10px）"只落了 999（812），长错误两行换行时仍保持胶囊形。
- 核对通过（逐项）：窗 640/420/860 ✓；浮层 .orbit-float max-width 320 ✓（76）；tip 240/long 320 ✓（168/178）；亮度递增 page #0d1624e6→window #121d2ff2→float #182540 实色 ✓（17–19 = 03a:277–279）；影锐独占 blur 44/80/20 ✓（44–46）；变暗独占 ::backdrop 仅窗层 + S1 第二层 #05080f66 ✓（71/901，M2–M7 例外见 A1）；dock-clear 96 派生一致（40–41 注记自证）✓；S1 确认框 320+tight+完整正文 40dvh 兜底 ✓（739–748）；S5 360/边界音调/z300 = 03c §5.4 权威值（已登记例外）✓；S7 240/tight/pointer-events:none ✓（778–786）；S8 320/bottom84/z70/竖条+色双图形/clamp3+滚动 ✓（788–807 = §5.6 表逐项）；M10「更多」popover 320+内滚（浮层例外登记在案）✓（703–715）。

### F. S 族落位

- **F6｜P3｜触屏命中区：自建窗关闭钮 32×32 无 coarse 放大**
  证据：style.css:481 的 coarse 媒体只覆盖 `.btn/.tab/.settings-nav button/.card-x`；自建窗的 `.orbit-win-btn`（makeWindow 产出）不在列 → S2/M8/M9/M10 关闭钮触屏 32×32（<44 指南）；步点条 coarse 下仅高度放大（min-height 44），宽仍 36。
- **F7｜P3｜M10 矮视口溢出：min-height 480 无视口钳制**
  证据：theme-orbit.css:664 `#orbit-win-workers { height: min(720px, calc(100dvh - 48px - var(--orbit-dock-clear) + 8px)) }` 与 :666 `min-height:480px` 并存——视口高 <616 时 height 被 min-height 顶到 480，超过可用空间，窗底（含操作区）越出视口不可达（dialog 本体无滚动）。
- 核对通过：S2 记忆概览窗 orbit-window--sm 420 + 「记忆概览」窗头 + #memory 整节点搬移 ✓（orbit.js:584–593）；M9 --md ✓（645）；M8/M10 默认 860 ✓（621/651）；S5 right24/bottom96=--orbit-dock-clear/z300/左缘 2px warning ✓（765–771）；S8 bottom=dock-clear−12 ✓（790）；S9 顶部居中 top16/window 底+float 影/z 20（dock 之下页面之上，符合 §5.7"同带"）✓（809–820）；窄窗宽度链 --md/--sm 均带 calc(100vw−…) 钳制 ✓。范围注记：S3/S4/S6 注入小窗的 orbit-window--sm 壳由 chat-search.js 等注入方落地，03c §5.3 本就登记"注入根非 dialog 则转 06"——留 06 核对项，不计本批问题。

### 补充疑点（跨 C/F）

- **C3｜P3｜thoughts 步点"隐藏"条件两头不接：8 号点可能常驻**
  证据：03c 1.6 与 orbit.js:695 注记均为"thoughts 按钮隐藏（归 P3，轮次 3 裁定 1）"，但 orbit.js:696 的 thBtn 是死变量（查询后未用），:697–699 的隐藏循环条件是"无 data-settings 属性"，而 thoughts.js:8 注入的按钮恰恰带 `dataset.settings="thoughts"` → 不会被隐藏，步点条实际可达 8 格（6 静态+claude 动态+thoughts），且经 :686–694 的循环获得图标与 data-tip。若轮次 3 裁定 1 是无条件隐藏则此为实现漏洞；若语义是"随 P3 可见性状态显隐"（syncThoughtsVisible 机制）则 03c/orbit.js 两处措辞需对齐并在 07 清单登记第 8 点的存在条件与窄窗换行形态（8 格 ×40=320，视口 <452px 时 legacy flex-wrap 换行成两行）。

## ③ 修法清单

1. **（修 B1，P1）**orbit.js installSettingsWindow（664–679 段）：新建 `div.orbit-win-tools`，把 `#settings-x` 与说明 info 钮按规格顺序（info 前、关闭后）收进 wrapper 后 append 到 head；还原表登记"解包"。同时给 head 一行 CSS 兜底 `html[data-theme=orbit] .orbit-win-head { justify-content: flex-start; }` 防其他复用窗再被 space-between 撕开。验收锚点：关闭钮右缘回到 x≈966–998（info 40 在其左侧同簇）。
2. **（修 A1，P2）**不动 DOM 的对齐案：`html[data-theme=orbit] .modal { background:#05080fd9; }` 把压暗色/透明度对齐令牌档；并在 §4 给 `.modal` 补 data-opening 同款入退场淡入淡出（或声明 M2–M7 无 backdrop 动画为已知偏差，登记 06 勘误）。若日后追求完全等价再评估升级 dialog（还原表面宽，不推荐本批做）。
3. **（修 C2/D3，P2）**步点条接管 legacy 边距：`html[data-theme=orbit] .orbit-step-dots { margin:0 0 var(--den-gap); padding:0; }`（特异度同为 (0,2,1)，须置于文件序其后或并写 `.settings-nav.orbit-step-dots` 确保压过 style.css:198）——去掉 orbit 自身 24px padding、清掉 legacy 24px margin 与 16px bottom：分区线通栏 364–1002，首格 x=388 与标题/分区对齐，条下距明确为 den-gap 20。
4. **（修 D1，P2）**`html[data-theme=orbit] .settings-body { gap: var(--den-gap); }` + `html[data-theme=orbit] .settings-body > :empty { display:none; }` 吃掉 anchor 的 0 高 gap 槽——两个 general 分区间隙 44→20。验算锚点：快照复测 266−222=20。
5. **（修 D2，P3）**容器节奏令牌化：`html[data-theme=orbit] .modal-body { padding:0 var(--den-pad) var(--den-pad); }` 去 legacy 4px 顶值（段前距由修法 3 的 margin-bottom 承担），并可选 `html[data-theme=orbit] .settings-hint { line-height: var(--den-lead); }`。此后窗体几何全部由密度令牌驱动，不再依赖巧合的 24px。
6. **（修 C1，P2）**给指示条装引擎：orbit.js 在 :684 挂类处同步 `span.orbit-step-indicator` append 进 nav，click 捕获回调（701–708）内按当前 `[aria-pressed=true]` 按钮的 offsetLeft/offsetWidth 写 transform/width 并 `nav.dataset.ready="1"` 解锁 §4 过渡（reduce/off 分支 CSS 已备）；若裁定 5 已另案降级，则删 927–934 死样式、回写 03c 1.6 的 ::after 方案——二选一，不留两端都不接的空档。
7. **（修 B2，P2）**先查 05 轮次记录确认 back 钮未被另案裁掉；未裁则 orbit.js 在 head 前插 `.orbit-win-back`（orbit-win-btn 皮 32×32、data-tip「返回」、click 复用关闭链路与 Esc 同源），03c 1.2 若有"仅切换分区后显示"的条件渲染需求一并注记。
8. **（修 D4，P3）**`html[data-theme=orbit] .orbit-win-btn { flex:0 0 auto; }` 压掉 .card-x 的 flex-basis 36px，恢复 32×32。
9. **（修 C3，P3）**定语义后二选一：无条件隐藏 → orbit.js:696 死变量落实为 `subStyle(thBtn,"display:none")`（或循环条件排除 thoughts）；随 P3 显隐 → 对齐 03c 1.6/orbit.js 注释措辞，07 清单登记 8 号点存在条件与 <452px 视口两行换行形态。
10. **（修 E2，P3）**S9：宽帽改 `min(320px, calc(100vw - 48px))` 或在 03c §5.7/8.3 正式登记 480 例外（与 S5 同法）；补换行态 `#boot-status[data-wrap] { border-radius:10px; }`，wrap 判定由 orbit.js/app.js 在 scrollHeight>clientHeight 时挂（规格 §5.7 既定）。
11. **（修 F6，P3）**`@media (pointer:coarse) { html[data-theme=orbit] .orbit-win-btn { width:40px; height:40px; } html[data-theme=orbit] .orbit-step { width:40px; height:40px; } }`——自建窗关闭钮与步点触屏命中 ≥40（与 03c 2.1 交互式步点 40×40 对齐）。
12. **（修 F7，P3）**`#orbit-win-workers .worker-workspace { min-height: min(480px, calc(100dvh - 48px - var(--orbit-dock-clear))); }`（或矮视口媒体查询取消 min-height），防 100dvh<616 时窗底越界不可达。

验收锚点（转 07 清单复测）：M2 打开态快照复测——关闭钮右缘 x≈966–998；步点首格 x=388、分区线通栏 364–1002；两 general 分区间 20px；压暗色 #05080fd9；S2 窗宽 420；S5 卡底距 96；toast 底距 84。
