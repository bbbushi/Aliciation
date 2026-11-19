# FRAGMENT · 部件 07 回到最新 pill（p07-）

> 方向 B 舱壁 Bulkhead。装配源码：`parts-impl/07/part.html`（演示页内联，`bus` 为页面级 {on, emit}）。
> 规格：`parts/07-回到最新pill.md`；工艺：`PRINCIPLES.md`。

## 信号

**订阅**：`follow:change` `{atLatest, unread}`（来自 02）——pill 显隐与未读计数的唯一真源。
**发出**：`jump:latest`（→ 02.jumpLatest 链路；用户主动回底 = 04 揭示跳过的触发器）。

## 状态表（S1–S5，demo 可逐态驱动）

| 态 | 条件 | 表现 |
|---|---|---|
| S1 | 在底 / 空档 / 加载 | pill 不在场（Tab 仍可达，Enter=回到底部兜底） |
| S2 | 离底 · unread=0 | 仅固定文案「回到最新」 |
| S3 | 离底 · 揭示中 | 计数随句递增（+n 句） |
| S4 | unread ≥ 100 | 显示封顶 `99+`，内部继续累加 |
| S5 | unread 非法（-1/NaN/1e15） | 计数段隐藏，仅固定文案，不崩溃 |

## 滞回（防抖动实测）

- 离底 >**140px** → pill 出现；回底 ≤**48px** → pill 消失（双阈值带，48–140px 区间保持现状）。
- 快速反复滚动（6 次越界往返）：显隐可中断、不叠加、不跳变（transition 重定向，非重放）。
- 48–140px 内判定点收敛 ≤140ms 内定案，pill 不闪烁。

## CSS 契约（前缀 p07-）

- `p07-pill`：切角块（`clip-path` 7px 右下），位于列表容器底部悬浮层；显隐 `opacity/transform` 中频标准档（近不可感知~200ms，禁弹跳）。
- `p07-count`：等宽数字计数段；封顶 `99+`；非法值隐藏该段。
- 键盘：pill 为真 `<button>`，显隐期间始终 Tab 可达（S1 下 Enter 走回底兜底）；读屏在 S1 用 `aria-hidden` 隐藏装饰计数。
- 最坏攻击已内建可验证：计数暴涨洪流 50 条（一次到位封顶）、快速反复滚动、回底后再离底（unread 重新累加）、切空档（即使离底也消失，atLatest 复位、unread 清零）。

## 装配注意事项

1. pill 必须挂在 02 的滚动容器**外层**（悬浮定位），滚动监听由 02 的 `follow:change` 驱动——07 自身不挂 scroll 监听（装配时删掉 demo 的 mock 监听）。
2. `jump:latest` 到达后 02 会发 `follow:change(atLatest=true, unread=0)`——07 无需自行清零。
3. 与 04 的联动：揭示期间离底 → `unread` 按 +n 句递增（04 发 `reveal:progress` 时由 02 折算进 follow:change，07 只认 follow:change 一个信号）。
