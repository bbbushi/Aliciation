# R5b · 布局审查 · 大页面组（P2 发现 / P3 思想 / P4 设置）· 布局专审员 B

2026-11-18。输入：`layout-snapshot-20261117.json`（真机 1366x850 运行时几何）、`web/theme-orbit.css` §2、`web/style.css` + `web/discovery.css` + `web/thoughts.css`（面板原样式）、`03b-样式设计-大页面组.md` P2/P3/P4 规格段、`web/orbit.js` 落位段（783–879）。只读审查，未改任何代码。

## ① 总评

三页的骨架几何大体兑现 03b：页容器 1080px/padding 32/--den-gap 32 全部实测吻合；P2 双列 `300px 678px`、P3 抽屉栈 `340px`、P4 hub 卡 499x72/卡距 16/min-height 72 逐项对上，dock 让位（页底 756 vs dock 顶 782，26px）达标。但 P2 存在一处结构性破口：`#discovery` 自带的 `.discovery-panel` 旧皮（`flex:1` + 2px 边框 + `--bg` 实底）在 orbit 域没有任何重置，页高被 `flex:1` 锁进 638px，右列 708px 内容溢出面板盒约 300px、卡片穿出 2px 边框渲染，首屏结果卡只露出约 85px——这是三页中最重的问题。其次，P2 行动区（overview 309px）被 orbit.js 有意落进右主列（orbit.js:804–810），与 03b §2.1「左列 300px = 行动区」相悖，左右列密度严重失衡（左列 ~173px vs 右列 708px）；P3 则缺统一页头（orbit.js 未建 `orbit-page-head`，抽屉栈从页面内容顶 y85 起格），03b 3.1 的清醒睡眠徽标 chip 只有 CSS 没有 JS 接线。P4 几何最干净，仅页尾 240px 留白属预算内的稀疏。遗留原样式（`discovery.css` 的 2px 边/卡底/margin 体系、`thoughts.css` 的 details/form margin）在 orbit 包装重排后普遍产生双重间距与旧皮残留，需要在 orbit 域补一批域内重置。

## ② 问题清单

### P2 内容发现

**B-01 · P1 · 旧面板皮未重置 + flex:1 锁高，右列内容溢出面板盒穿框**
- 证据：`index.html:364` `<section id="discovery" class="discovery-panel">`；`discovery.css:3` `.discovery-panel { flex:1; border:2px solid var(--line); background:var(--bg); }`；`theme-orbit.css` §2 无任何 `.discovery-panel`/`#discovery` 盒模型重置。快照：page [143,52,1080,704]（max-height 命中）→ panel(#discovery) [176,85,1014,638]（被 flex:1 压进页内容区）→ body [178,329,1010,392]（min-height:0 再被压）→ 右列内容 overview 309 + gap 12 + results 387 ≈ 708px，results 盒 [510,638,678,387] 底达 y1025，越过 panel 底缘 723 约 302px。2px 边框只画到 638px 处，结果卡从边框中部穿出；页面虽可滚动（overflow 链到 .orbit-page），但面板框线与内容底完全脱节。
- 修法（CSS）：orbit 域补 `html[data-theme=orbit] #discovery { flex: 0 1 auto; height: auto; min-height: 0; border: 0; background: transparent; }`，让面板盒随内容生长、皮让位给 `.orbit-page`；同时复核 `subFlag(discovery,"hidden",false)` 后的显隐通道不受影响。

**B-02 · P2 · 行动区落列偏离 03b §2.1，左右列密度失衡、首屏结果卡不可见**
- 证据：`orbit.js:804–810` 注释「右主列：行动区 + 结果区 + 偏好」并 `moveNode(overview, mainCol)`；快照 overview [510,329,678,309]（x=510 = 右列）而非左列 [178,…]；左列 360px 高只装 learn(≈27px)+drawer 46px+task，右列 708px。03b §2.1 明文「左列 300px = 行动区（范围摘要+开关+主按钮）→ 寻找策略抽屉 → S10 入口排；右列 1fr = 结果区」。后果：右列行动区把结果流压到 y=638，首屏（可视至 ~724）内结果卡仅露 ~85px，建立不了「这页有什么」的首屏印象；左列行动位近乎空置。
- 修法（JS 为主）：`orbit.js` 将 `overview` 移入 `side` 首位（drawer、entry-row 依次其后），`mainCol` 只留 `results`；若这是有意的新排布，须回写 03b §2.1 与 02b 登记表并重算首屏（当前快照不支持「有意」结论——左列留白与结果不可见均为负面信号）。

**B-03 · P3 · overview 列向化后 `align-items:center` 残留，行动区子件水平居中**
- 证据：快照 overview style `flexDirection: "column"`（orbit 覆盖，theme-orbit.css:346）+ `alignItems: "center"`（原 discovery.css:14 行向布局的垂直居中残留）。列向布局下 cross轴=水平 → 摘要文本/开关/按钮在 678px 列内居中散排，与全页左对齐语言不符。
- 修法（CSS）：`html[data-theme=orbit] .discovery-overview { align-items: stretch; }`（或 flex-start）。

**B-04 · P2 · 页头水平 28px padding 残留，页头文本与双列左缘错位**
- 证据：`discovery.css:4` `.discovery-header { padding:18px 28px 14px }`；orbit 仅覆盖 `padding-bottom`（theme-orbit.css:330–333），水平 28px 级联保留 → 页头 h1 左缘 ≈ x204，而双列/横贯带左缘 x176–178，错位约 28px。按级联推定，建议人工复核截图确认。
- 修法（CSS）：orbit 域 `html[data-theme=orbit] .discovery-header { padding: 0 0 var(--sp-5); }`，左缘归零对齐 body。

**B-05 · P3 · 结果卡双重间距：卡 margin-bottom 14px + 结果流 gap 12px = 26px**
- 证据：`discovery.css:92` `.discovery-card { margin-bottom:14px }`（orbit 无覆盖规则）；`theme-orbit.css:351` `.discovery-results { gap: var(--sp-3) }`（12px）。卡间实际 ~26px，wide 档区块距 32 之内的节奏被破坏。
- 修法（CSS）：orbit 域 `html[data-theme=orbit] .discovery-results .orbit-card { margin: 0; }`（.orbit-card 为 orbit.js 容器级挂类，登记在案 03b §0.5）。

**B-06 · P3 · 状态带/引导卡旧皮未接管：2px 边框 + card 底 + 旧 margin**
- 证据：`discovery.css:11` `.discovery-status { border:2px solid var(--line); background:var(--card); margin-block:0 12px; }` — orbit 只改了字号（theme-orbit.css:355）；`discovery.css:22` `.discovery-next { border-left:3px solid var(--accent); margin-bottom:18px; }` — orbit §2 无 `.discovery-next` 规则（03b 2.4 亦漏）。orbit 页语言是 1px faint 线 + 透明底 + gap 32，这两条横贯带保持旧卡皮并自带额外 margin，与 B-05 同族的旧皮/双距残留。
- 修法（CSS）：`html[data-theme=orbit] #discovery .discovery-status { border-width:1px; border-left-width:2px; background:transparent; margin:0 0 var(--sp-3); }`；补 `html[data-theme=orbit] #discovery .discovery-next { border-width:1px; border-left-width:2px; margin-bottom:0; }`（此条同时补 03b 2.4 漏项）。

**B-07 · P3 · `.orbit-body` 的 `grid-template-rows: auto minmax(0,1fr)` 是幽灵第二行**
- 证据：`theme-orbit.css:340/391`；P2/P3 的 orbit-body 都只有 side+main 两个子项，自动排布全落 row1，row2 恒为 0 高空行（快照 body 高 392 = 360+32gap+0）。无害但属陷阱结构：任何后续 append 的第三子项会掉进 1fr 空行。
- 修法（CSS）：两处删去 `grid-template-rows` 声明（03b 2.4/3.4 同步修订）。

### P3 思想与探索

**B-08 · P2 · P3 无统一页头：orbit-page-head 未建，抽屉栈顶格起于页内容顶**
- 证据：快照 settings 页有 `pageKids = [orbit-page-head:[176,85,1014,63], …]`，thoughts 页 panel 直起 y=85（页内容顶），drawerStack [850,85,340,929] 与主列内容同格起于 85；`orbit.js` 中 `orbit-page-head` 仅在 P4 hub 段创建（:935），P3 段（:850–879）无页头构建。03b §3.1 规定页头 = h2「思想与探索」+ 刷新 + 右端清醒睡眠徽标 chip + 切换状态按钮，下缘 faint 线、标题下 24px——现状标题/状态/lifecycle 散落主列（lifecycle [176,186,162,39]），页头体系在 P3 整体缺位，双列也失去了统一的页头下基线。
- 修法（JS+CSS）：orbit.js P3 段补建 `.orbit-page-head`（收纳 thought-title 文案 + chip 挂点 + 刷新/切换按钮位），插在 orbit-body 之前；主列首段相应下移。若维持现状，须在 03b §3.1 登记偏离并给抽屉栈一个与主列标题行的对齐基线。

**B-09 · P2 · 清醒睡眠徽标 chip 未接线：`.orbit-state-chip` 只有 CSS，JS 无创建**
- 证据：`theme-orbit.css:378–387` 完整 chip 皮（含 awake/sleeping 档）在库；`grep orbit.js` 无 `orbit-state-chip` 命中；快照中原 `#thought-lifecycle` 以裸文本 [176,186,162,39] 在主列可见（padding 6px 0，thoughts.css:15）。03b §3.1/0.5 登记的「chip + data-state 镜像」未落地，睡眠/清醒四档状态在页内不可读。
- 修法（JS）：orbit.js P3 段创建 `.orbit-state-chip` 挂入页头（依赖 B-08），只读 MutationObserver 从 `#thought-lifecycle` 镜像 data-state（03b §0.5 已登记该节点与还原路径）；原 lifecycle 元素按登记收纳（隐藏 + 还原恢复）。

**B-10 · P3 · 抽屉双重间距：details 原 margin 14px + 栈 gap 8px = 22px**
- 证据：`thoughts.css:12` `#thought-settings details { margin:14px 0 }`；快照 opsDrawer [850,225,340,46] 带 `margin:"14px 0px"`；`theme-orbit.css:394` `.orbit-drawer-stack { gap: var(--sp-2) }`（8px）。栈内抽屉间距实际 22px，且与 P2 策略抽屉（无此 margin）节奏不一致。
- 修法（CSS）：`html[data-theme=orbit] #orbit-page-thoughts .orbit-drawer { margin: 0; }` 与 `html[data-theme=orbit] #orbit-page-thoughts .orbit-drawer details { margin: 0; }`（域内限定，不动 thoughts.css 本体）。

**B-11 · P3 · 主列内部仍走旧 margin 体系，与 wide 档间距双轨**
- 证据：`thoughts.css:3` form `margin:16px 0`、`:9` article `padding:12px 0` + 1px `--line` 分隔线、`:12` details `margin:14px 0`；orbit 对主列（statuses/questions/stances 所在列）无间距接管，主列纵向节奏是 16/14/12 旧值叠 settings-section gap 9px（style.css:201），与页级 32px gap 与抽屉栈 8px gap 并存，三套间距档同屏。
- 修法（CSS）：orbit 域对档：`#orbit-page-thoughts form { margin: var(--sp-3) 0; }`、`#orbit-page-thoughts article { border-bottom-color: var(--orbit-line-faint); }`；或经主代理裁定登记「主列内部旧距豁免」，二选一，避免三档并存。

**B-12 · P3 · drawerStack 929px 溢出页可视域 + 页/栈双层滚动**
- 证据：快照 drawerStack [850,85,340,929]（底 y1014）vs 页可视底 ~756；stack `overflowY: "auto"` 自带滚动，页容器也滚动（panel 961 > 640）。抽屉折叠态本应只占三行 summary（~46x3+gap），929px 说明栈内有展开内容长期占位；叠加 B-08（无页头）后右列从页顶直贯到页外，850 视口内右列没有任何收束。
- 修法（CSS+JS）：随 B-08 页头建立后复核；给 `.orbit-drawer-stack` 加 `max-height: 100%` 并确认展开态滚动发生在栈内而非页级；展开态持久化策略交 orbit.js/宿主复核（不属本审改cfg）。

### P4 设置 hub

**B-13 · P3 · 页尾 240px 留白：内容 85–516，可视至 ~756**
- 证据：快照 hubCards 末卡底 y=516，页可视底 756（dock 顶 782）。首屏填充率约 67%（431/640）。03b 预算 41/90 本就是全组最轻页，留白可读作「hub 的呼吸位」，不判缺陷；但「设置页首屏只有 7 张卡+页头」与 03b 4.1 的分区健康态可读性目标之间，缺一个次级锚（如最近变更/待办连接态摘要）的话，页尾空旷感偏强。
- 修法：登记为设计取舍项；若补次级区块须走丙组 02c 时刻表（本审不扩字数预算）。

### 待人工/静态核对（快照未覆盖，不入问题计数）

1. P2 S10 入口排：`orbit.js:1054` 建 `.orbit-entry-row`，但快照 discovery 节未采到该节点 rect 与落列（应随 B-02 落位一并核对）。
2. P4 hub 卡方向符（::after ↗/→）与徽标挂点（.orbit-dot/.orbit-badge 右上 10px）：伪元素/绝对定位不入几何快照，需真机或 DOM 核对。
3. P3 `runBtn`/`questions` 快照均为 display:none：采集时未运行可解释 runBtn；但 03b §3.2 要求疑问输入区「保持全亮」为常驻主位，若 questions 常态隐藏须核 thoughts.js 显隐条件。
4. B-04 的 28px 错位是级联推定值，建议对页头截图复核。

## ③ 修法清单（按文件与优先序；全部需同步 03b §0.5 还原表登记）

**CSS（web/theme-orbit.css §2 追加，域内限定，不动宿主文件）**

| 序 | 针对 | 追加规则 |
| --- | --- | --- |
| C1 | B-01 | `html[data-theme=orbit] #discovery { flex:0 1 auto; height:auto; min-height:0; border:0; background:transparent; }` |
| C2 | B-03 | `html[data-theme=orbit] .discovery-overview { align-items: stretch; }` |
| C3 | B-04 | `html[data-theme=orbit] .discovery-header { padding: 0 0 var(--sp-5); }` |
| C4 | B-05 | `html[data-theme=orbit] .discovery-results .orbit-card { margin: 0; }` |
| C5 | B-06 | `html[data-theme=orbit] #discovery .discovery-status { border-width:1px; border-left-width:2px; background:transparent; margin:0 0 var(--sp-3); }` + `html[data-theme=orbit] #discovery .discovery-next { border-width:1px; border-left-width:2px; margin-bottom:0; }` |
| C6 | B-07 | P2/P3 两处 `.orbit-body` 删 `grid-template-rows` |
| C7 | B-10 | `html[data-theme=orbit] #orbit-page-thoughts .orbit-drawer, html[data-theme=orbit] #orbit-page-thoughts .orbit-drawer details { margin:0; }` |
| C8 | B-11 | `html[data-theme=orbit] #orbit-page-thoughts form { margin: var(--sp-3) 0; }` + article 分隔线换 `--orbit-line-faint`（或登记豁免） |
| C9 | B-12 | `html[data-theme=orbit] .orbit-drawer-stack { max-height:100%; }`（随 B-08 复核） |

**JS（web/orbit.js，P2/P3 落位段 783–879）**

| 序 | 针对 | 动作 |
| --- | --- | --- |
| J1 | B-02 | `moveNode(overview, side)` 置于 side 首位（drawer/task/entry-row 依次其后），mainCol 仅留 results——与 03b §2.1 对齐；若产品侧确认保留现排布，改为回写 03b/02b 登记 |
| J2 | B-08 | P3 段补建 `.orbit-page-head`（thought-title 文案 + chip/按钮挂点），插于 orbit-body 之前 |
| J3 | B-09 | 创建 `.orbit-state-chip` 入页头 + 只读 observer 镜像 `#thought-lifecycle` 的 data-state；原 lifecycle 按 03b §0.5 登记收纳与还原 |

**文档同步**

- 03b：§2.1（若 J1 改排布则左列描述已对齐；若不改则登记偏离）、§2.4 补 `.discovery-next` 规则（B-06）、§2.4/§3.4 删幽灵行声明（C6）。
- 还原表（03b §0.5 / 02b 还原表）：C1–C9 全部为 orbit 域新增选择器，须逐条登记还原路径；J2/J3 对应 0.5 表 `.orbit-state-chip` 行的落地与 `.orbit-page-head`（P3）新行。

**兑现项对账（免复核）**：P2 side 300px / main 678px / gap 32、P3 drawerStack 340px / main 642px / gap 32、两页页容器 1080px+padding 32、P4 hub 双列 499x72 卡距 16 min-height 72、页头→内容 gap 32、dock 让位 26px——均与 03b/03a wide 档吻合；`--den-gap/--den-pad/--den-ctl`（theme-orbit.css:44）兑现。
