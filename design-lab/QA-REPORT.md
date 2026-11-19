# QA-REPORT · sample.html 四项质检

> 质检人：ZCode（质检代理超时中止后由主代理接手）。对象：`design-lab/sample.html`（8 部件组装件）。
> 方法：静态 grep 扫描 + Chromium 实测（127.0.0.1:8891/sample.html，含最坏数据三连与全链路走查）。

## 1. break-ui（对抗性测试）

实测三档最坏数据（08 控制器驱动）+ 程序化溢出扫描：

| 攻击 | 结果 |
|---|---|
| 最坏数据档（emoji 混 CJK、单字符"嗯"、（沉默）占位、发送失败态、time=null 隐藏元信息） | ✅ 无破版；失败态"点击重试"链接可点 |
| 空会话 | ✅ 空态接管（阿澈，这里还很安静 / 写下第一句），0↔1 消息无同屏帧 |
| 洪流 50 条 | ✅ 全量渲染、连续分组、无重 id、无横向滚动 |
| 程序化溢出扫描（scrollWidth>clientWidth 且 overflowX visible） | ✅ brokenCount=0，pageHScroll=false |

备注（非 bug）：发送失败态的重试链接用暖红——B 变体设计语言选择（PRINCIPLES 只约束动效不约束配色，红色语义若要收紧属方向层决定）。

## 2. animation-performance（帧预算）

- 全部 transition/animation 仅涉 `transform/opacity/color/background/border`；4 个 @keyframes（p06-*）只动 opacity+transform，且属空态罕见档一次性入场。
- 滚动路径零加工（02 的铁律），pill 显隐走 transition 重定向非重放。
- 未见 blur/filter/布局属性动画；未见 will-change 滥用（0 处）。
- 结论：帧预算内，无需真机录制项——长列表 >200 条的虚拟化已在 ASSEMBLY-NOTES 列为后续项（08 分页剧本承接），非本 demo 阻塞。

## 3. review-animations（十条不可协商）

逐条：正当性 ✓（17 交互频率分级表驱动）、频率匹配 ✓（高频零动效）、ease-out 家族 ✓（`--ease-out`/`--ease-mech` 两 token + color 用 ease）、时长 ✓（130–220ms，禁忌扫描 [3-9]xx ms 命中 0）、origin ✓（p03 入场 origin 右下/自下）、可中断 ✓（transition 重定向，无重放式 keyframes 除空态一次性）、GPU 属性 ✓、reduced-motion+hover 门 ✓（7 处门控）、不对称时序 ✓（入场 160-180ms / 退场 130ms 同轴反向）、cohesion ✓（press 形态/曲线/门控写法全页统一）。

verdict：**Approve**（无 Block 项）。一次审查意见：p06 入场 keyframes 与 @starting-style 两种机制并存（前者空态一次性、后者常规进出）——机制按场景分工明确，判 cohesion 通过。

## 4. mobile-native（composer 输入密集）

✓ `enterkeyhint="send"`、✓ input 16px（防 iOS 聚焦缩放）、✓ `touch-action: manipulation`、✓ `viewport-fit=cover, interactive-widget=resizes-content`、✓ 100dvh（p02-root）。
待真机（环境无法模拟）：iOS 键盘弹出 resize 行为、中文 IME 组合期 Enter、safe-area 实机观感。

## 必须修清单

无必须修项。三项待人工真机（IME/键盘 resize/safe-area）+ 一项方向层可议（失败态红色语义）+ 一项后续工程（>200 条虚拟化策略）。
