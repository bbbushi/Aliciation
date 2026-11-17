# R5a · 布局审查 · P1 对话主页（布局专审员·A）

2026-11-17。输入：`layout-snapshot-20261117.json`（真机 IAB 1366x850 运行时几何）× `web/theme-orbit.css` §1/§2 P1 段 × `web/style.css` 82–137 行现状契约 × `03b-样式设计-大页面组.md` §一 P1 规格。只读审查，未改任何代码。审者无浏览器，快照 rect=[x,y,w,h] 为唯一实测来源；推导过程随证据给出，可复核。

## ① 总评

P1 的主体几何是**合格且精确的**：页框 [143,52,1080,704] 与 03b「1080 居中 / 高 100dvh−52−94」逐位吻合；顶栏身分行实测恰 92px、padding 20/28/16 与规格一字不差；垂直预算精确闭合（92 + msgs 478 + composerWrap 130 = 700 = 704 − chat 上下边框 4，零溢出零残隙）；消息列与 composer 双双落在 880 同轴（左缘同 243、右缘同 1123），sync-bar 隐藏态 msgs 顶=header 底 146 无残留空隙，notice/composerEval/composerFoot 隐藏态确认，协作卡右下双卡 16px 垂距不重叠且距消息文本列 4px 净空。**但 dock 有一个真 P1**：§1b 的 `#orbit-dock > *` 定位提升规则以更高特异性覆盖了扫描线内衬的 `position:absolute`，内衬变 0 宽 in-flow 子项吃掉一个 16px flex gap 槽——dock 盒实测 697px（应为 681），可见图标行中心 691.5 偏离视口中心 683 约 8px，且内衬纹理整体失效。另有簇距 33px 对「16px 簇距」的规格内部双重计数、composer 880 对齐依赖 JS 挂类等 P2 三项。快照采集面偏窄（sync-bar/头像/空态/回底/站点光点均无条目），P1 规格约三分之一的参数只能 CSS 侧静态证实，建议下一轮补采。

## ② 问题清单（按严重度排序）

### P1-1 dock 扫描线内衬被定位提升规则击穿：内衬失效 + 可见图标行右偏约 8px

- **证据（快照）**：dock rect [335,782,697,56]。按 CSS 应有宽 = 簇盒和 607（172+57+101+277）+ 簇间 flex gap 3×16=48 + padding 24 + 边框 2 = **681**，实测 697 = 681+**16**（多出一个 gap 槽）。dockIcons 首图标 x=364，而 dock 内容左缘 = 335+1+12=348——首簇前有且仅有 16px 未解释位移；末图标右缘 1019 恰=内容右缘 1032−1−12。可见图标行跨度 364..1019，视觉中心 **691.5**，视口中心 **683** → 右偏 ~8.5px。簇盒位置（364/552/625/742）与 4 次 gap（3 簇间+1 前置）完全自洽。
- **证据（代码）**：`theme-orbit.css:99` `html[data-theme=orbit] #orbit-dock > * { position: relative; z-index: 1; }`（特异性 1,1,1）压过 `:100–101` `.orbit-dock-texture { position:absolute; inset:0 }`（0,1,0）→ 纹理层变 relative 的 0×0 in-flow 子项：不再覆盖 dock（内衬失效），且作为首个 flex 子项占用一个 16px gap。
- **修法（CSS）**：`html[data-theme=orbit] #orbit-dock > *:not(.orbit-dock-texture) { position: relative; z-index: 1; }`；或给 `.orbit-dock-texture` 补 `html[data-theme=orbit]` 前缀提权。改后 dock 应为 681px、图标行回中（偏差 ≤0.5px）、扫描线恢复覆盖。

### P2-2 跨簇图标距实测 33px，对 03a「簇距 16px」成倍偏离（规格内部双重计数）

- **证据（快照）**：三处簇界一致 33px——c1 末图标右缘 536→c2 首图标 569；c2 末 609→c3 首 642；c3 末 726→c4 首 759。
- **证据（代码/规格）**：`theme-orbit.css:85` `#orbit-dock{gap:16px}`（源自 03a:352）+ `:106` 相邻簇 `padding-left:16px`（源自 03a:373）+ 1px 分隔线 = 33px。两规则在 03a 草稿内同存，实现忠实转录——是**规格内部双重计数**，不是实现走样；但与任务口径「03a §3.2 簇距 16px」矛盾，观感上簇间距放大一倍。
- **修法（需先裁定，CSS 或文档）**：若以 16px 图标距为准 → CSS `#orbit-dock{gap:0}`（得 16 padding+1 线=17px，≈规格）；若认定「分隔线两侧各 16」是有意设计 → 改 03a §3.2 文字为「簇距 33px（线两侧各 16）」。不建议未裁定直接改码。

### P2-3 composer 880 对齐修复依赖 JS 挂类，回退路径静默且三主题分叉

- **证据（快照）**：composerWrap padding `14px 98px 15px`、composer 宽 880——orbit 域 880 对齐**运行时已生效**（与 msgs 880 同轴 ✓）。
- **证据（代码）**：`style.css:125` base 仍为 760 公式；880 修复在 `theme-orbit.css:1106–1107` `#chat > footer.orbit-composer-wrap { padding: 14px max(28px, calc((100% - 880px)/2)) 15px; }`，类 `orbit-composer-wrap` 由 orbit.js 挂——**漏挂类时 orbit 静默回退 760，失去与消息列同轴且无报错**；room/默认主题 composer 仍 760（属主题域差异，但需登记）。
- **修法（JS+文档）**：03b/还原表登记该类依赖；orbit.js 启动自检 footer 类存在性（缺类时 console.warn 即可）。（可选 CSS：把 footer 侧距抽成三主题共用自定义属性，动公共层需另行立项，不强制。）

### P2-4 快照采集缺口：P1 规格多项参数无法运行时闭环

- **证据（快照）**：无 `chat-sync-bar` 条目（92px 信号行的 min-height 38/padding 4/28/右对齐线只能由 `style.css:91` + `theme-orbit.css:277` 静态证实——padding-right 28 → 内容右缘 1193 恰=headerActions 右缘 1105+88，构造上共线 ✓ 但无实测）；无 header 头像节点（03b「头像 36×40」未验）；无空态/回底 pill 条目；无 aria-current 图标（「站点光点 bottom −6px」未验）。
- **修法（JS）**：快照采集脚本补采 `#chat-sync-bar`、header 左簇（头像/who）、`#chat-empty`、`#chat-latest`、`[aria-current=page]` 图标；光点为伪元素可采主图标 rect 推算。

### P3-5 dock 让位实测 26px，与 03a「缓冲 28」叙述差 2px

- **证据（快照+代码）**：页底 756（52+704）/ dock 顶 782 → 26px。`theme-orbit.css:186` #app 底 padding = 96−2 = 94（R3 P1-3 为对齐旧算式 80+14 的有意 −2），而 03a §1.3 叙述「96 = dock 顶 68 + 缓冲 28」未同步。
- **修法（文档）**：03a/03b 注记统一为「#app 底 94 / 实际缓冲 26」口径；或回改 padding 96（会破坏与 03c 的核对统一，不建议）。

### P3-6 orbit 域 composer footer 底 padding 丢失 safe-area 兜底

- **证据（代码）**：base `style.css:125` 底 padding 为 `max(15px, env(safe-area-inset-bottom))`；orbit `theme-orbit.css:1107` 固定 `15px`。快照本机 env=0（实测 15px）无感，但带安全区环境会让位不足。
- **修法（CSS）**：1107 行改回 `max(15px, env(safe-area-inset-bottom))`。

### P3-7 header 左轴（173px）与消息列/composer 左轴（243px）双轴并存——规格内有意，登记防误修

- **证据（快照）**：header/信号行内容左缘 145+28=173；msgs 与 composer 内容左缘均 243（98px 侧距）。03b §1.1 明文顶栏 padding 20/28/16「沿用」，属有意双轴（顶栏 28px 内衬 vs 880 内容列居中）。
- **修法**：不改码，登记口径。若未来要三轴合一，改法为 header/sync-bar 侧距换 `max(28px, calc((100% - 880px)/2))`——动 03b 规格，需另议。

### P3-8 user 气泡右缘与 composer 外缘严格对齐（✓），与发送按钮差 14px——契约产物，登记口径

- **证据（快照）**：user txtRect 右缘 1077+46=1123 = composer 外缘 243+880=1123 ✓；发送按钮右缘 = composer 内衬 12+边框 2 → 1109，差 14px。composer 内部契约（03b：不入 --den-ctl）所致，非缺陷。
- **修法**：不改。追齐需动 composer 内衬，得不偿失。

### P3-9 ::before 说话人标签到首句气泡实际间距 18px（8px margin 与 10px flex gap 叠加）

- **证据（快照）**：msg rect 顶到 txtRect 顶 Δ=37–38px（样本 1：−212→−174；样本 2/3：128→165、285→322）≈ 标签行高 19 + 8(margin) + 10(gap)；assistant/user 一致。
- **修法**：base 契约（style.css:95/97，03b 红线「消息流契约不动」）内，仅登记。若嫌标签悬浮过高，改法是 `margin-bottom:0` 留 gap 10——动公共层，orbit 域无权单独改。

### P3-10 消息 gap 28px 与大页 wide 档 --den-gap 32px 跨页不一致

- **证据（快照）**：msgsPadGap gap=28（契约值）；P2/P3/P4 页面级节奏 32。红线「消息流契约不动」保护，属可接受的跨页档位差。
- **修法**：登记即可，不改。

### P3-11 composer-foot 聚焦浮现规则落在 §2 P1 段之外（1096–1101 行）

- **证据（快照+代码）**：功能完整——实测 opacity 0 隐藏 ✓、`:1098–1099` focus-within 揭示 ✓、`:1101` reduce 关过渡 ✓；但 03b §1.4 草稿把该规则列在 P1 段，实施却散落在后段，P1 专属规则检索易漏。
- **修法（文档/注释）**：§2 P1 段加一行指针注释「composer-foot 浮现见 :1096」；或搬移归段（纯注释级整理）。

### P3-12 sync-bar 出现/消失为 39px 内容跳变（既有行为，静态快照不可验）

- **证据（快照）**：隐藏态无缝（msgs 顶=header 底 146，无残隙 ✓）；但信号行显隐时消息区整体位移 min-height 38+1px 边线，为 base 既有行为，快照无法捕捉动态。
- **修法（JS+CSS，可选）**：如需平滑，给 `.chat-sync-bar` 加 max-height/opacity 过渡——触及契约，仅登记不建议本轮动。

## ③ 修法清单（可执行，标注改动面）

| # | 对应问题 | 改动 | 具体动作 |
| --- | --- | --- | --- |
| 1 | P1-1 | CSS（theme-orbit.css:99） | `#orbit-dock > *` → `#orbit-dock > *:not(.orbit-dock-texture)`；改后自检：dock 盒宽应回 681、图标行中心≈视口中心 |
| 2 | P2-2 | CSS 或文档（待 03a §3.2 口径裁定） | 裁定 16px：`theme-orbit.css:85` `gap:16px→0`（得 17px 实距）；裁定 33px：改 03a 文字。**勿未裁定先改** |
| 3 | P2-3 | JS+文档 | 03b/还原表登记 `orbit-composer-wrap` 类依赖；orbit.js 启动自检 footer 挂类，缺类 warn |
| 4 | P2-4 | JS（采集脚本） | 快照补采 sync-bar / header 头像 / 空态 / 回底 / aria-current 图标 |
| 5 | P3-5 | 文档 | 03a/03b 让位叙述统一为「94/26」口径 |
| 6 | P3-6 | CSS（theme-orbit.css:1107） | 底 padding 改回 `max(15px, env(safe-area-inset-bottom))` |
| 7 | P3-7/8/9/10 | 无改码 | 总规口径登记：双轴 173/243、气泡对齐 composer 外缘（按钮差 14）、标签距 18px、消息 gap 28 |
| 8 | P3-11 | 文档/注释 | §2 P1 段补 composer-foot 规则位置指针（或归段搬移） |
| 9 | P3-12 | JS+CSS（可选，暂缓） | sync-bar 显隐平滑过渡——触契约，仅登记 |

## 附：通过项核对（快照实测 vs 03b 规格）

| 03b §1.1 参数 | 快照实测 | 判定 |
| --- | --- | --- |
| 页框 1080 居中 / 高 100dvh−52−94 / 直角 | [143,52,1080,704]；1338 可用宽下左右各 129 页边距 | ✓ |
| 顶栏身分行 92px / padding 20/28/16 | height 恰 92；padding 逐位一致 | ✓ |
| 下缘 1px --orbit-line-faint | theme-orbit.css:261 | ✓（色值未验） |
| 消息流契约 880/30/98/28/gap28 | msgs [145,146,1076,478] padding `30px 98px 28px` gap 28 | ✓ |
| composer 对齐 880（本轮修复） | composer [243,638,880,73] 左右缘 243/1123=消息列 | ✓（依赖挂类，见 P2-3） |
| 待确认提示行 margin 0 28px 8px | notice margin `0px 28px 8px`（display:none） | ✓ |
| 眉标隐藏 | theme-orbit.css:259 `content:none` | ✓ |
| 协作卡右下不重叠 | claude [1127,444,78,74] / dsh [1127,534,78,74]，垂距 16，右缘 1205 在 chat 右 16 内，左缘距文本列 4px | ✓ |
| composerFoot 隐藏/位置 | opacity 0，位于 composer 下 9px（margin 9px 3px 0），随 composerWrap 收纳 | ✓ |
| notice/composerEval 隐藏 | 均 display:none | ✓ |
| 空态/头像/信号行几何/回底/站点光点 | 快照无条目 | → P2-4 补采 |
