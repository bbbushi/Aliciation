# 003 — 保留 Endfield 悬停设计并补齐输入与偏好门禁

- **Status**: TODO
- **Commit**: 49accd4（含工作区）
- **Severity**: MEDIUM / P2
- **Category**: Accessibility / Purpose & frequency
- **Estimated scope**: web/style.css，1 文件

## Problem

`web/style.css:757` 没有指针门禁：

```css
html[data-theme=endfield] .msg:hover {
  transform: translateX(6px);
  box-shadow: inset 3px 0 0 var(--accent);
}
```

`:594–595` 只删除过渡，未删除位移终态。触摸伪hover或motion off/reduce仍可能使整行偏移。

## Target

保留设计文档明确的Endfield 6px悬停及色条，仅在精细指针、hover能力、`data-motion=on`、系统no-preference及forced-colors:none时发生位移。其他情况下可保留静态色条，transform:none。保留原 `.18s var(--ef-ease, cubic-bezier(.2,.7,.1,1))`，不借无障碍修复改变既定主题风格。

## Repo conventions to follow

使用 `web/style.css:578` 的媒体查询模式；动效偏好由app.js:3262维护。

## Steps

1. 将translateX从无条件:hover规则移到带上述门禁的规则。
2. 将色条与位移分离，确认motion关闭和reduce规则不被后置主题选择器覆盖。
3. 保持非Endfield主题样式不变。

## Boundaries

不删除用户已选择的主题交互；不改消息DOM、正文或模型。禁止全局!important覆写transform影响其他组件。

## Verification

- **Mechanical**: `git diff --check -- web/style.css`；静态检查选择器门禁。
- **Feel check**: 真人鼠标悬停仍180ms移动6px；触屏点击不粘住偏移；关闭动效和reduce下不位移，文字和链接可读可点。开发工具10%核对仅该规则运动。
- **Done when**: 鼠标设计与触屏/偏好边界均真人确认；当前未实施。
