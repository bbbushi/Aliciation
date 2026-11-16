# FRAGMENT · 06 空状态（B 舱壁 Bulkhead）

> 源实现：`parts-impl/06/part.html`（其中 `p06` 命名空间的 CSS/JS 块为部件本体，其余为演示脚手架，组装时丢弃）。
> 上游契约：`../PARTS.md` §2.2/2.3。本部件**只发出一个信号** `focus:composer()`，不新增跨部分字段/信号。

## 0. 定位与动效决策

- 档位：罕见（PRINCIPLES §1 #14/#15）——全页唯一允许"最明显"的一次性入场编排，总跨度 275ms ≤ 300ms。
- 目的一句话：**罕见档 delight + 引路**。精密伺服语感：铭牌落位 → 主句点亮 → 说明随行 → 切角开关入座（顺序即装配因果，也是阅读顺序）。
- 退场 = 空间一致性：单容器一次，130ms，入场同轴反向（内容自下来，退场回下去），不逆序 stagger。

## 1. 挂载与互斥（消费：`listState`、`userName`）

| 条件 | 行为 |
| --- | --- |
| `listState==='empty'`（loading 结束且 Turn 数 0 且无揭示中轮次） | 02 宿主调 `p06.mount(userName)` 挂载，入场随挂载自动播放 |
| `loading` / 载入失败 / Turn 数 ≥1 | 本部件不存在；"用户已上屏、Alice 未回"不是空，不得挂载 |
| 空态在场 | 02 保证 `atLatest=true`、unread=0 ⇒ 07 必然隐藏（本部件无动作） |

## 2. HTML 块（前缀 `p06-`）

```html
<div class="p06" role="group" aria-label="空会话引导">
  <div class="p06-glyph" aria-hidden="true">A</div>
  <h2 class="p06-title"><!-- 有称呼：<span class="p06-name">{userName}</span>，这里还很安静 --></h2>
  <p class="p06-desc">说第一句话，Alice 会一句一句地回你。</p>
  <span class="p06-cta-slot"><button class="p06-cta" type="button">写下第一句</button></span>
</div>
```

文案逐字出自 06 规格 §5；超长称呼只在 `.p06-name` 视觉截断（DOM 保留全文），"，这里还很安静"恒在场；`userName===''` 时标题整句替换为"这里还很安静"（无残留逗号）。

## 3. CSS 块

依赖全页 `:root` 共享 token（`--ease-out` 与 B 配色变量），部件不自造曲线/配色/平行 token。要点：

```css
.p06{margin:14vh auto 0;max-width:430px;padding:0 20px;text-align:center;
  transition:opacity 130ms var(--ease-out),transform 130ms var(--ease-out)} /* 仅服务退场；容器不入场 */
.p06.is-leaving{opacity:0;transform:translateY(8px)}
.p06-glyph{…切角铭牌…;animation:p06-rise 190ms var(--ease-out) both}
.p06-title{…;animation:p06-rise-lead 200ms var(--ease-out) 40ms both}
.p06-desc{…;animation:p06-rise 190ms var(--ease-out) 75ms both}
.p06-cta-slot{display:block;margin:24px auto 0;animation:p06-seat 150ms var(--ease-out) 125ms both}
.p06-cta{…切角按钮…;transition:color 140ms ease,background-color 140ms ease,border-color 140ms ease,transform 160ms var(--ease-out)}
.p06-cta:active{transform:scale(.97)}
@keyframes p06-rise{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}
@keyframes p06-rise-lead{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}
@keyframes p06-seat{from{opacity:0;transform:scale(.96)}to{opacity:1;transform:none}}
@media (hover:hover) and (pointer:fine){.p06-cta:hover{…仅颜色…}}
```

关键工程决定：**入场 transform 挂 `p06-cta-slot`、按压反馈挂内部 `p06-cta`**——`animation-fill-mode:both` 会在动画结束后持续压住元素自身的 transform 声明，`:active` 的 scale 将失效；双层分离让入场与底线反馈互不侵占。keyframes 仅用于本一次性编排（§2.5 允许项），fill-mode 均 `both`。

## 4. JS 块（`p06` 命名空间 + 总线）

```js
const p06={
  LEAVE_MS:130,
  mount(userName){ /* 构建上节 HTML；cta.click → bus.emit('focus:composer')；返回节点 */ },
  setName(node,userName){ /* 原地改 .p06-title 文案：不重挂载、不重放入场 */ },
  leave(node){ node.classList.add('is-leaving'); } /* 宿主在 LEAVE_MS 后移除节点 */
};
```

## 5. 信号

| 信号 | 方向 | 载荷 | 触发 |
| --- | --- | --- | --- |
| `focus:composer()` | 06 → 05 | 无 | 点击 `p06-cta`（本部件唯一交互；不可拖拽/关闭/无二级入口） |

消费为只读渲染参数（`listState`、`userName`，经宿主传入），不发信号。

## 6. 与 02 / 05 的耦合契约

**02（宿主）**
- 02 拥有 `listState` 与挂载点；仅 02 翻转到 empty 时挂载。
- **首条 `turn:append` 到达时**：02 先 `p06.leave(node)`，等 `p06.LEAVE_MS`(130ms) 后移除节点、再挂载该 Turn——"先消失后上屏"，0↔1 永不同帧；若期间发生 reset/切档（节点已被重渲染摘除），让位流程作废。
- `userName` 变更：02 调 `p06.setName` 原地更新，**禁止重挂载**（同内容只入场一次）。
- 08 reset / 切回空档使 `listState` 真实回到 empty：重新挂载并播放入场（真实状态变化，非重放违规），文案与首次一致。

**05（模拟目标 → 真实 composer）**
- 收 `focus:composer()`：把焦点移入输入区（行为，非动效）；聚焦后的边框样式归 05（§1 #11），与本部件无关。

## 7. 一次性时间线

入场（容器静止，四元素错峰 40/35/50ms，非均匀，均在 30–80 带；总跨度 275ms）：

| 元素 | 角色 | 开始 | 时长 | 动作 |
| --- | --- | --- | --- | --- |
| `p06-glyph` 铭牌 | Support · 锚 | 0ms | 190ms | fade + translateY(6px)，切角面板先落位 |
| `p06-title` 主句 | Lead | 40ms | 200ms | fade + translateY(8px)，行程最大 |
| `p06-desc` 说明 | Support | 75ms | 190ms | fade + translateY(6px) |
| `p06-cta-slot` 行动引导 | Support · 落点 | 125ms | 150ms | fade + scale(0.96→1)，center origin（居中元素） |

退场（单容器一次）：

| 元素 | 开始 | 时长 | 动作 |
| --- | --- | --- | --- |
| `p06` 容器 `.is-leaving` | 首条到达即触发 | 130ms | fade + translateY(8px)，入场同轴反向；快于入场（130 < 275） |

播放中全部可点：CSS animation 不阻断命中测试，cta 在错峰延迟期内已可点击（不阻塞交互）。

## 8. reduced-motion 退化（同 ship）

四元素折叠为**同拍** `p06-fade` 160ms（纯 opacity，删错峰/位移/scale——reduce 下允许纯 fade）；退场仅 opacity 130ms、`transform:none`；文案、结构、可达性不变，静态完整。

## 9. 验收对照（PRINCIPLES §3 / 06 规格 §6）

- 频率 #14/#15 罕见档：总长 275 ≤ 300；错峰 40/35/50 ∈ 30–80 非均匀；一容器一入场；不阻塞 ✓
- 曲线/时长：仅 `--ease-out`（动效）+ `ease`（hover 颜色 140ms）；按压 160ms；退场 130ms ✓
- 物理：无 scale(0)（入座起点 0.96）；非 reduce 无纯 fade 入场；origin 语义正确（居中元素用 center）✓
- 属性白名单：animation/transition 只挂 transform/opacity；clip-path 仅静态形态 ✓
- 可中断/一次性：节点替换 + `isConnected` 守卫，无叠加；setName 原地更新不重放；重放仅发生在真实 empty 重现 ✓
- 不对称：exit 130 < enter 275，同轴反向，不逆序错峰 ✓
- 数据压力：0↔1 先消失后上屏（绝不同屏）；userName 2000 字只截名不吞 CTA；空称呼退化通顺；reset→重现文案一致 ✓
- reduce/hover 门控齐备 ✓

## 10. 与 `variants/B.html` 占位实现的差异

B 内空态为单容器 240ms 入场 + 130ms 退场的**占位**；本部件按 PRINCIPLES #14 的完整预算升级为四元素一次性错峰编排（总跨度 275ms），退场保持 130ms 同轴反向、B 的切角/配色/边距 token 全部沿用。组装时以本 FRAGMENT 为准整体替换 B 的 `.empty/.e-g/.e-t/.e-d/.e-a` 块与 `renderFace` 空态分支。
