# 001 — 让键盘触发的聊天与协作者窗口即时呈现

- **Status**: TODO
- **Commit**: 49accd4（审计含当前未提交工作区）
- **Severity**: HIGH / P1
- **Category**: Purpose & frequency
- **Estimated scope**: web/app.js、web/worker-tasks.js，2 文件

## Problem

`web/app.js:3067–3070` 的 Enter 调用 `send()`，`:2996–2998` 仍传 `animate: !retry`，`:317` 添加入场类；`web/style.css:572` 定义 220ms 入场。

```js
if (!userNode?.isConnected) userNode = bubble("user", text, { animate: !retry });
if (!node?.isConnected) node = bubble("assistant", "", { animate: !retry });
```

`web/worker-tasks.js:391` 键盘 click 调用 activate，`:1709` 无区别调用 `windowFrame.open(origin)`，窗口播放420ms动画。

## Target

键盘触发不播放位置或缩放动画。指针触发沿用现有动画，不修改消息内容和句级揭示，不改变窗口打开/关闭的业务逻辑。无需引入新的曲线；若新增按压反馈，其值限定 `160ms cubic-bezier(0.23,1,0.32,1)`，且仅指针启用。

## Repo conventions to follow

保留 `web/app.js:3250` 的动效偏好实现、`:317` 的动效门禁。协作者窗口已有现态捕获与动画取消（worker-tasks.js:658），不可丢失。

## Steps

1. 给 send 的 UI 调用入口显式传输入来源；重试、自动恢复等调用继续使用原有语义。Enter 入口将 animate 设为 false；在 await 前捕获来源，不用延迟返回时的全局键盘状态推断来源。
2. 协作者键盘激活将 `animate:false` 沿 activate/open/windowFrame.open 传递；指针默认不变。键盘关闭同样即时执行正常收尾。
3. 检查所有 send/open 调用签名，保留默认参数以兼容原入口。

## Boundaries

不改逐句缓冲170–520ms、桌面气泡420–1600ms，不改模型、API、记忆、配置。不得给高频列表切换补动画。若位置漂移先重读调用链，不覆盖他人改动。

## Verification

- **Mechanical**: `node --check web/app.js` 与 `node --check web/worker-tasks.js`；不运行机器行为测试。
- **Feel check**: 用户真实对话中分别回车发送/鼠标发送；回车无整行滑入，逐句回复仍正常。Tab+Enter/Space打开/关闭协作者窗口应即时，指针仍沿头像展开；快速反转窗口保留现态接续。开发工具10%播放核对无额外键盘位移。
- **Done when**: 以上真人操作通过；当前未执行。
