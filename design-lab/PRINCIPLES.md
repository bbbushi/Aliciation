# Alice 聊天消息流 Demo · 全局工艺标准（宪法）

**地位**：本文件是 demo 所有部分（双向消息气泡、composer、空态、"回到最新" pill、演示模式开关）共同的工艺宪法。只定标准——该不该动、动多久、什么曲线、如何中断与退化；**不定视觉方向**（配色、版式、字体、具体形态属下游设计）。

**数值纪律**：所有曲线、时长、形态值取自团队 skill 标准文件（emil-design-eng、review-animations/STANDARDS.md、animate、choreograph），本文件不自造数值；**表外值一律违规**，review 时直接 flag。

**硬前提（本 demo 特有）**：零依赖单文件 HTML、离线、无框架 ⇒
- 工具白名单：CSS transition → `@starting-style` → CSS animation（仅一次性编排）→ WAAPI，**停在第一个够用的**；不引动画库，不手写 rAF 补间。
- 本 demo **没有 modal / drawer / sheet / toast** ⇒ **300ms 是全站硬上限，不存在任何例外档**。

---

## 1. 交互频率分级与动效预算

三档定义（对应 skill 频率门）：

| 档 | 判据 | 动效预算 |
| --- | --- | --- |
| **高频** | 键盘触发，或 ≥100 次/天 | **零**。任何入场/转场动效都是缺陷，不是风格选择 |
| **中频** | 几十次/天 ～ 偶发 | **近不可感知 ～ 标准**：按压档～小浮层档时长、小幅度（fade + 初始 transform） |
| **罕见** | 首次 / 每会话 ≤1 次 | **可以做得明显**：允许一次性编排与 delight，但总长 ≤300ms、一容器一入场、不阻塞交互 |

> 反馈态（press / hover / disabled / focus）**不是动效，是底线**，全档必做（见 §2.1）；频率预算管的是入场、转场与编排。

### 逐交互分级表（本 demo 全部交互）

| # | 交互 | 部件 | 档 | 预算与边界 |
| --- | --- | --- | --- | --- |
| 1 | 键盘输入字符 | composer | 高频 | 零。光标闪烁是系统行为，不加工 |
| 2 | Enter 发送 | composer | 高频 | 零。键盘触发是资格否决项，不是判断题 |
| 3 | 点击发送按钮 | composer | 高频 | 仅底线反馈：press `scale(0.97)`、按压档 100–160ms；提交动作本身零动效 |
| 4 | 发送按钮 disabled↔enabled | composer | 高频 | 即时变化，或至多按压档（100–160ms）内的颜色过渡；状态不得迟到 |
| 5 | 用户消息气泡入场 | 气泡 | 中频 | 显式二选一并记录：不动画（直接出现），或按压档内 fade + 起始 `scale(0.95–0.97)`、无位移。不许默认随手加 |
| 6 | Alice 回复气泡入场 | 气泡 | 中频 | 小浮层档 125–200ms，fade + `translateY(≤8px)` 沿内容流位移，`--ease-out`；必须有初始 transform，禁纯 fade |
| 7 | 打字指示器出现/消失 | 气泡 | 中频 | 出现/消失走小浮层档 125–200ms；点的跳动=状态指示，允许循环、`linear`；reduced-motion 下点静止；有界——不得无期限空转 |
| 8 | "回到最新" pill 出现/消失 | pill | 中频 | 小浮层档 125–200ms；从其指向的方向（底部）入场，起始 `scale(≥0.95)` + opacity；**exit 快于 enter** |
| 9 | 点击 pill 滚回最新 | pill | 中频 | 单次受控滚动，下拉档 150–250ms、`--ease-in-out`（屏内移动），或瞬时跳转——二选一；禁止劫持/改写手动滚动 |
| 10 | hover 气泡/按钮 | 全部 | 中频 | 仅颜色/透明度类属性，`ease` 曲线，按压档～小浮层档（100–200ms）；必须 `(hover:hover) and (pointer:fine)` 门控 |
| 11 | composer 聚焦/失焦 | composer | 中频 | 颜色/边框类 transition，`ease`、≤200ms（小浮层档上限）；位移 ≤2px 或无 |
| 12 | 手动滚动 / 选中文本 / 复制 | 消息流 | 高频 | 系统行为：不拦截、不加工、不挂过渡 |
| 13 | 窗口 resize | 全部 | 中频 | 零动效；布局瞬变可接受，transition 一律不挂布局属性 |
| 14 | 空态入场 | 空态 | 罕见 | 可以明显：一次性编排，总长 ≤300ms；stagger 30–80ms 且非均匀；一容器一入场；播放中全部可点 |
| 15 | 空态 → 首条消息 | 空态 | 罕见 | 空态退场快于其入场；退场与入场同轴反向（空间一致性） |
| 16 | 演示模式开关扳动 | 开关 | 罕见 | 可以明显：滑块走下拉档 150–250ms（屏内移动用 `--ease-in-out`）；状态立即生效可读；可中断——快速反复扳动不重放、不叠加 |
| 17 | 页面首次加载 | 全部 | 罕见 | 轻编排，总长 ≤300ms（产品 UI 预算，营销预算不适用）；后续任何重渲染/模式切换**不重放** |

---

## 2. 全局工艺底线

### 2.1 反馈态（全档必做，与频率无关）

- 任何可按压元素（发送、pill、开关、气泡内可点元素）：`:active { transform: scale(0.97) }`，`transition: transform 160ms var(--ease-out)`；幅度保持 0.95–0.98。
- hover / 颜色变化一律 `ease` 曲线，且必须门控（§2.6）。
- 状态（disabled、聚焦、发送中、演示中）必须**即时可读**：动效不得承担"表达状态"的延迟职责。

### 2.2 曲线表（唯一定义，全页共享 token）

```css
:root {
  --ease-out: cubic-bezier(0.23, 1, 0.32, 1);     /* 入场/退场与 UI 反馈 */
  --ease-in-out: cubic-bezier(0.77, 0, 0.175, 1); /* 屏内移动/形变 */
}
/* hover/颜色变化 → ease；常速循环（指示器点、进度）→ linear。
   --ease-drawer: cubic-bezier(0.32, 0.72, 0, 1) 本 demo 无 drawer：不定义、不使用（不造死 token）。 */
```

- 禁 `ease-in` 于任何 UI（它延迟用户盯得最紧的那一瞬）。
- 禁内置弱曲线（`ease-out`/`ease-in-out` 字面值）用于 deliberate 动画；禁自造 cubic-bezier。
- 两条 token + `ease`/`linear` 已覆盖本 demo 全部场景，**没有新增曲线的理由**。

### 2.3 时长表（与 skill 标准文件一致）

| 元素类 | 预算 |
| --- | --- |
| 按压反馈 | 100–160ms |
| 小浮层 / pill / 提示 | 125–200ms |
| 开关 / 下拉类 | 150–250ms |
| 一次性编排（首载 / 空态） | 总长 ≤300ms |
| **全站硬上限** | **300ms（本 demo 无 modal/drawer/toast，无例外档）** |

### 2.4 物理性与属性

- **永不从 `scale(0)` 入场**：起始 `scale(0.9–0.97)` + `opacity: 0`；非 reduce 情况下禁无初始 transform 的纯 fade 入场。
- origin 必须物理正确：pill 从底部/其指向侧、开关滑块沿滑轨、空态居中；`transform-origin: center` 仅限真正居中呈现的元素。
- 动画属性白名单：**`transform`、`opacity`**（`clip-path` 为白名单例外）；禁 `width`/`height`/`margin`/`padding`/`top`/`left`（本 demo 无 accordion，无 height 例外）。
- `translate` 优先用元素自身百分比（如 `translateY(100%)`），不硬编码 px。
- 禁止在父元素上改 CSS 变量驱动子元素 transform（样式重算风暴）；transform 直接写在元素上。

### 2.5 可中断与一次性

- 反复触发的元素（气泡入场、pill、开关、指示器）一律 CSS transition（或 `@starting-style` 入场），**禁 keyframes**；keyframes 仅允许用于一次性入场编排，且 `animation-fill-mode: both`（防延迟项先闪终态）。
- 连续触发场景（连点发送、快速扳开关、pill 连点、快速 hover 进出）：不叠加、不从零重放、不跳变。
- **同一内容只入场一次**：重渲染、演示模式切换、历史回填不得重放入场动画。

### 2.6 无障碍（与动效同 ship，不许后补）

```css
@media (prefers-reduced-motion: reduce) {
  /* 更少更温和，不是零：保留 opacity/颜色过渡，删位移与循环 */
}
@media (hover: hover) and (pointer: fine) {
  /* 所有 :hover 动效必须门控——触屏 tap 会触发假 hover */
}
```

- reduce 下的退化策略：编排折叠为同时 fade（删 stagger 与位移）、指示器点静止、保留有助理解的颜色/透明度过渡。

### 2.7 编排（多元素同动时）

- **一容器一入场**：容器动了，内容就在场；禁两层入场。
- stagger 仅限罕见档一次性编排：**30–80ms/项、非均匀、总量 <300ms**；中频/高频面禁 stagger——**历史消息/列表回填一律同场出现或不动画**。
- 方向随内容流与因果（新内容自下、作用点向外）；**退场同路反向、更快、更简单**（不逆序 stagger 退场）。
- 编排永不阻塞交互：stagger 播放中一切可点。

---

## 3. 验收清单（每个部分必须全过；break-ui / review-animations 按此打）

逐项判定"过 / 不过"，任一"不过"即该部分不达标：

1. **正当性**：每个动效一句话说出目的（反馈 / 空间一致 / 状态指示 / 防突变 / 罕见档 delight）；说不出即删。
2. **频率匹配**：交互已列入 §1 表且未超档；键盘触发与高频档零入场动效。
3. **曲线**：只出现 §2.2 白名单曲线；无 `ease-in`、无内置弱曲线、无自造值。
4. **时长**：落在 §2.3 对应档内；全文件无 >300ms。
5. **物理正确**：无 `scale(0)`；无纯 fade 入场（reduce 变体除外）；origin 语义正确；translate 用百分比优先。
6. **可中断**：连续触发不叠加 / 不重放 / 不跳变；反复触发元素无 keyframes。
7. **属性白名单**：transition/animation 只挂 `transform`/`opacity`（+`clip-path` 例外）；无布局属性。
8. **无障碍**：reduce 下有温和退化（留 opacity/颜色，删位移/循环）；hover 动效全部有门控。
9. **不对称**：enter/exit 不同速（exit 更快）；退场与入场同路反向。
10. **一次性**：重渲染 / 模式切换 / 回填不重放入场；历史消息无逐条 stagger。
11. **数据压力**（break-ui 交界）：最坏数据不破预算——超长纯 emoji、不可断行长串、单字符、空串、连发十条、千条历史时：入场仍 ≤ 档内预算、无布局属性动画、无重放；空态显隐条件在 0 条/1 条边界正确；pill 在滚动边界不显隐抖动（需迟滞）。
12. **不阻塞**：编排 / stagger 播放中一切可点可用。

---

## 4. 部件间一致性规则（cohesion）

**必须全页统一**（一处违反即不一致缺陷）：

- 曲线 token：`:root` 唯一定义，各部分引用同名 var；禁部分私有曲线或平行 token 体系。
- 时长档位与频率分级：同类交互同档同预算（§1 / §2.3）。
- press 形态：所有可按压元素同 `scale(0.97)` + `transform 160ms var(--ease-out)`。
- hover 门控与 reduced-motion：写法、位置（一条全局 reduce 块、同一门控模式）与退化策略全局一致。
- 属性白名单与工具阶梯：全站同一套（`transform`/`opacity` + clip-path 例外；CSS 优先，不引库）。
- 方向语义：垂直内容流——新内容自下入场、退场原路反向且更快。
- 状态即时性：键盘与高频操作零延迟可见；状态不靠动效表达。
- 一次性入场原则：同内容只入场一次。

**允许部分自定**（护栏内，不构成不一致）：

- 档内具体取值：时长在档区间内、起始 scale 0.9–0.97、stagger 间隔在 30–80ms 内。
- 各部分的 origin 语义（pill 底部、开关沿轨、空态居中）——由部分设计定，但必须物理正确。
- 罕见档是否做一次性编排、编排的具体形态（§2.7 护栏内）；中频档不允许编排。
- 中频交互显式选择"用零"（如用户气泡不动画）——决定须写明，不是默认。
- 打字指示器的循环形态（在 `linear` 与有界约束内）。

---

## 5. 不做清单（本 demo 一律禁止）

1. `transition: all`——必须写明具体属性。
2. `ease-in` 用于任何 UI；内置 `ease-out`/`ease-in-out` 字面值用于 deliberate 动画（必须用 token）。
3. `scale(0)` 入场；非 reduce 的纯 fade 入场（无初始 transform）。
4. 布局属性动画：`width`/`height`/`margin`/`padding`/`top`/`left`（本 demo 无 accordion 例外）。
5. 键盘触发动作的任何动效（输入、Enter 发送）。
6. 历史消息/列表回填的逐条 stagger；一容器两层入场；等距 stagger（节拍器感）。
7. 常驻装饰循环：背景漂浮、呼吸发光、鼠标跟随、视差、扫描线——"looks cool" 不是目的。
8. bounce / 弹性超调 / spring 引入（本 demo 无拖拽场景；零依赖下无库）。
9. 3D 装饰：`rotateX`/`rotateY`/`preserve-3d`/orbit。
10. 任何 >300ms 的动效（本 demo 无例外档）。
11. 表外数值：自造曲线、随手圆整的时长。
12. 动画阻塞交互：stagger 播放中禁点、延迟浮现的按钮。
13. 重渲染重放入场动画。
14. 未门控的 `:hover` 动效；缺 reduced-motion；"以后再补"。
15. 劫持滚动（wheel/touchmove 拦截、常驻 smooth-scroll）；仅允许 §1 #9 的单次受控滚动。
16. blur 动画：仅允许 ≤2px 且仅用于桥接 crossfade 的两态重叠；禁止作为装饰或 >2px。
17. rAF 手写补间、引入动画库（零依赖铁律）。

---

*数值出处：emil-design-eng/SKILL.md；review-animations/SKILL.md 与 STANDARDS.md（曲线/时长/press/频率表/十条不可协商标准）；animate/SKILL.md（工具阶梯与 Never Ship 表）；choreograph/SKILL.md（一容器一入场、stagger 30–80ms 非均匀、方向与退场、产品 UI ≤300ms）。本文件未新增任何数值。*
