# FRAGMENT · 部件 02 消息列表容器（p02-）

> 方向 B 舱壁 Bulkhead。装配源码：`parts-impl/02/part.html`（`window.P02`，IIFE 导出）。
> 规格：`parts/02-消息列表容器.md`；工艺：`PRINCIPLES.md`。

## 对外 API（`window.P02`）

```js
P02.mount(el, bus)        // el=挂载元素；bus=装配级信号总线（见下）
P02.inject(raw)           // raw={id, role:'user'|'assistant', text, time}
                          // 消毒规则：非法 role 单条丢弃；同 id 视为更新（节点数不变）；
                          // 按 time 值排序插入（乱序中插安全，日期行重算）
P02.update(id, patch)     // 原位更新（不重放入场）
P02.revealProgress(id)    // 逐句揭示中（04 驱动；元信息挂"正在回复"）
P02.revealDone(id)        // 揭示完成收尾
P02.jumpLatest()          // 滚动到底（真实链路：置底+follow(true) 清零+04 跳过）
P02.beginLoad()           // 载入面（loading 宿主）
P02.loadDone()            // 载入完成→正常渲染
P02.loadFail()            // 载入失败面（含 retry:load 入口）
P02.state()               // => {listState, fail, atLatest, unread, turns, gen}
P02.onChange(cb)          // 状态变化钩子（08 观测用）
P02.sentences             // 分句器引用（装配时以 03 的公共实现为准）
```

## 总线信号

**订阅**：`turn:append`（raw 同 inject）、`turn:update`、`jump:latest`（07）、`retry:load`（失败面按钮）、`reveal:progress` / `reveal:done`（04，转发到对应消息节点）。

**发出**：`follow:change` `{atLatest, unread}`（→ 07；滞回：离底 >140px 出现、回底 ≤48px 消失，实测无抖动）、`retry:load`（→ 08）。

## CSS 契约（前缀 p02-）

- `p02-root`：`height:100dvh; display:flex; flex-direction:column`，进出 `opacity/transform 220ms var(--ease-out)`（过 `@starting-style`）。
- `p02-list`：**唯一滚动面**，`flex:1; min-height:0; position:relative`（offsetTop 基准）；滚动本身零加工（PRINCIPLES §1 #12）。
- `p02-col`：内容列 `max-width:780px; margin:0 auto; padding:16px 20px 30px`。
- `p02-fail` / loading / empty 面：`56px auto` 居中，切角按钮（`clip-path` 7px），`color 140ms ease + transform 160ms var(--ease-out)`。
- 消息节点内部（p02-msg 占位）由 03 的实现替换——02 只负责顺序/分组/滚动语义。

## 行为要点

- **防跳变**：`anchorInsert/anchorGrowth`——顶部/中部插入时用滚动位置锚点补偿，底部插入且 atLatest 时自动跟随。
- **重排不重放**：`renderList()` 全量重建仅用于切档/乱序重排（同一内容只入场一次）；在线更新走原位路径。
- **空/载宿主**：`faceEmpty()` 留 06 挂载点（装配时由 06 渲染真实空态，02 只判宿主态）。
- **分组**：`recomputeGroups()` 相邻同侧连续分组 + 日期行（时间差跨日插入）。

## 装配注意事项

1. bus 需实现 `on(sig,fn)` / `emit(sig,detail)` 两方法（demo 内有参考实现）。
2. `sentences` 装配时统一替换为 03 导出的公共分句器，避免两份实现漂移。
3. 长列表：当前全量 DOM（无虚拟化）；>200 条时优先走 08 的分页加载剧本（retry:load），不引虚拟滚动库。
4. 视口窄档（<560px）下 `p02-col` padding 降为 `12px 14px 24px`（demo 已含三档视口测试）。
