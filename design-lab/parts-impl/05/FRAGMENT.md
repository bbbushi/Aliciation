# FRAGMENT · 部件 05 composer 输入区（p05-）

> 方向 B 舱壁 Bulkhead。装配源码：`parts-impl/05/part.html`（`window.P05`，工厂导出）。
> 规格：`parts/05-composer输入区.md`；工艺：`PRINCIPLES.md`。

## 对外 API（`window.P05`）

```js
P05.mount(host)           // host=挂载元素；内部构建 textarea(.p05-ta)+发送键(.p05-send)
P05.on(ev, fn)            // 订阅 P05 发出的信号（见下）
P05.handle(sig, arg)      // 外部 → P05 的信号入口（装配总线桥接用，见下）
P05.setDraft(text)        // 直写草稿（失败回填/演示注入走这里）
P05.state()               // => {draft, busy, link:'up'|'closing', inflight, failHint, ime}
```

## 信号

**发出**（经 `P05.on` 订阅）：
- `submit:text` —— 已 trim 的非空草稿（Enter 或点击发送；IME 组合期与空白-only 不发）
- `state:changed` —— snapshot 变化（busy/link/inflight/ime/failHint 任一）

**消费**（经 `P05.handle(sig, arg)`，装配时由总线桥入）：
- `busy:change` (bool) —— Alice 处理中：不可发送但**仍可输入**（草稿保留优先于锁定）
- `link:change` ('up'|'closing') —— 服务关闭面：提示行切换、发送禁用
- `send:failed` (原文) —— 失败回执：释放在途锁、原文逐字回填草稿、失败提示
- `turn:append` (role='user') —— 用户 Turn 上屏回执 → 释放在途锁（与 08 的 send 剧本对齐）
- `focus:composer` —— 06 空态引导信号 → 输入区实际聚焦（光标进入+舱壁聚焦态）

## 状态机（readout 实证）

`draft-empty → can-send → in-flight（在途锁，成功=turn:append 回执解锁 / 失败=send:failed 回填解锁）`；`busy-lock` / `closing-lock` 叠加在输入自由之上，只锁发送。

## CSS 契约（前缀 p05-）

- `p05-row`：composer 行容器；`p05-ta`：多行自增高 textarea，`≥16px` 字号（移动端防缩放），上限 `132px` 高后内部滚动、不截断。
- `p05-send`：切角发送键（`clip-path` 10px 右下），press `scale(0.97) 160ms var(--ease-out)`；禁用态直角降透明度。
- 提示行三态（失败/关闭/busy）：同一行内切换，不叠行不残留。
- 输入为高频操作：聚焦/输入本身零动效；只有 press 反馈与提示行切换属预算内。

## 装配注意事项

1. 移动端：`enterkeyhint="send"`、`touch-action: manipulation` 已内置；装配页 `<meta name=viewport>` 需含 `interactive-widget=resizes-content`（mobile-native 要点）。
2. 草稿终身保管：busy/closing/切换演示档都不清草稿；真实刷新场景在装配层用 sessionStorage 承接（demo 用内存模拟）。
3. IME：`compositionstart/end` 门控 Enter（组合中 Enter 是选字，不得触发 submit）——装配后必须真机中文输入法验证。
4. `send:failed` 的 arg 必须是**已 trim 的提交原文**（08 的失败剧本负责传入）。
