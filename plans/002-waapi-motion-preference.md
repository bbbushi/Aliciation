# 002 — 让应用动效开关覆盖协作者 WAAPI

- **Status**: TODO
- **Commit**: 49accd4（含工作区）
- **Severity**: MEDIUM / P1
- **Category**: Accessibility
- **Estimated scope**: web/worker-tasks.js，1 文件

## Problem

`web/app.js:3262` 将应用关闭/系统减少动态效果/forced-colors统一成 `root.dataset.motion`。`web/style.css:594` 的CSS覆盖无法停止WAAPI。`web/worker-tasks.js:247,653,674` 仅判断系统reduce、几何与可见性：

```js
if (!origin?.width || !origin?.height || reducedMotion.matches || typeof panel.animate !== "function" || document.hidden) return;
```

因此应用内关闭仍可播放头像240ms和窗口420ms动画。

## Target

WAAPI启动前同时要求 `document.documentElement.dataset.motion === 'on'`；偏好切成off时立即结束现有WAAPI并提交正常业务终态。不改变现有240/420ms曲线、头像几何和反向接续；本计划不重做既定视觉。

## Repo conventions to follow

worker-tasks.js:232 的 stopSizeMotion、:490 的 stopExpansion 已管理cancel与collapseDone；系统reduce change已有监听(:423/:637)。扩展现有清理路径，不另造并行生命周期。

## Steps

1. 在头像缩放与窗口动画入口共享 motionAllowed 判定，包含root data-motion、系统reduce与可见性。
2. 通过 MutationObserver 监听root data-motion；关闭时调用当前控制器清理函数，确保collapseDone只执行一次。
3. 在模块停止/卸载中disconnect新增observer；重新开启不重播旧动画。

## Boundaries

只改worker-tasks.js，不改全局偏好存储和默认值，不用CSS animation:none假装控制WAAPI，不改窗口布局或模型接口。

## Verification

- **Mechanical**: `node --check web/worker-tasks.js`；不机器行为测试。
- **Feel check**: 真人关闭应用动效后点击头像缩放/窗口开关均即时；运动中关闭开关无悬空遮罩、无被卡住的窗口。系统reduce/forced-colors也即时。重新开启后仅未来操作播放。10%慢放核对取消无跳回旧尺寸。
- **Done when**: 开关与系统偏好下的真实操作一致；尚待实施及真人验收。
