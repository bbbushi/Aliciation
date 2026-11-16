# 004 — 打磨统一样例的窗口、开关与反馈

- **Status**: CODE COMPLETE（代码已完成，待真人视觉与交互验收）
- **Commit**: 49accd4；addons/ui-study-v2 为未提交样例
- **Severity**: MEDIUM / P2
- **Category**: Purpose & frequency / Interruptibility / Cohesion；反馈淡入为可选增量
- **Estimated scope**: addons/ui-study-v2/app.js、style.css、必要index.html语义

## Problem

style.css:151 的switch滑块 `transition: transform 140ms var(--ease-out)` 不区分键盘；:53 所有button过渡也不区分键盘。:135 的dialog入场220ms keyframe可在用户按键时被class改变取消，app.js:184/188直接dialog.close无退出视觉接续。

```css
body:not(.keyboard-input) dialog[open] { animation: dialog-enter 220ms var(--ease-out); }
```

app.js:31–53每次更新重建feedback及按钮，重复渲染会丢失已聚焦的按钮。反馈只瞬间出现，缺少统一token。不要因此给高频列表/阅读切换加运动。

## Target

只打磨同一浅色主从页：键盘触发控件即时；指针按压160ms/scale(.97)；窗口enter 220ms、exit 160ms，`cubic-bezier(0.23,1,0.32,1)`，transform-origin:center，起始scale(.985)/translateY(7px)。采用可中断transition或可取消WAAPI，不能用一个固定延迟阻止输入。输入来源在触发时捕获，Esc立即关闭。reduce仅保留温和opacity/color，不做位移。新反馈只在内容变化时120ms淡入，错误不自动消失；相同反馈保留节点与焦点。增加必要触控命中面积，不改页面结构风格。

## Repo conventions to follow

沿用 `--ease-out`、body.keyboard-input、body.mobile-detail及原生dialog焦点约束。state/history/thoughts继续只存在内存。不使用外部依赖。

## Steps

1. 改进输入来源处理及switch/button门禁；指针反馈transition可反向接续，键盘/系统reduce位移立即归零。
2. 将dialog动画从CSS keyframe换成明确生命周期；保留showModal原生语义、背景/Esc/关闭按钮和焦点恢复。close过程中取消或重开时不留旧计时器，不延迟已提交业务。
3. feedback只在实际内容或操作目标变化时更新，稳定按钮节点/焦点；新增提示用120ms opacity-only，错误持久可重试，reduce下保留可读状态。
4. 增大图标/开关/展开入口触控命中区，保持布局和中文密度；提交组件Before/After表记录。

## Boundaries

不改web、desktop、design-lab或旧ui-sample。不得添加列表入场、逐项错峰、搜索等待或人造延迟。模拟探索2200ms为演示等待，保持可停止，不新增真实模型或数据写入。

## Verification

- **Mechanical**: `node --check addons/ui-study-v2/app.js`；静态差异检查；不机器行为自测。
- **Feel check**: 真人鼠标/键盘分别切开关、开关窗口；Esc即时无闪回，快速开关可中断；窗口中Tab/Esc焦点正确；窄屏触控无伪hover、展开项可点、长标题无溢出；失败重试/完成查看在重渲染后不丢焦点。10%播放观察窗口没有重新从头入场；reduce无位移但有可读反馈。
- **Done when**: 代码完成与真人验收分开标记；未真人确认不得写DONE。

## 本轮组件打磨记录

| Before | After | Why |
| --- | --- | --- |
| 键盘开关仍滑动、按钮边框仍过渡 | keyboard-input下开关/按钮即时，指针保留160ms按压 | 区分输入方式，不拖慢高频键盘操作 |
| dialog只有CSS keyframe入场，关闭立即消失 | 指针WAAPI 220ms进/160ms出，现态捕获后可取消；键盘/Esc即时，reduce仅opacity | 明确窗口生命周期，避免重播与旧回调干扰 |
| 相同反馈反复拆建按钮 | 按反馈文本、类型及目标签名复用；变化时120ms淡入并恢复必要焦点 | 更新不中断正在进行的交互，错误持续可见 |
| 窄屏入口33–38px、长串约束不足 | 44px触控命中区，图标尺寸保留；长串断行及safe-area | 提高实际可操作性和窄屏稳定性 |

核查：JS语法通过、源码复核完成；没有执行机器行为测试。浏览器控制工具连接超时，尚无截图、慢放或真实设备体验证据，未将状态标为DONE。
