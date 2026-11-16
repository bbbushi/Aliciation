# 动效审计与实施计划

[全库审计](AUDIT-20261116.md) · 基线49accd4及当前工作区。只读审计后先在当前统一样例落地组件打磨，其他区域保持计划状态。

| 编号 | 计划 | 优先级 / 严重度 | 状态 |
| --- | --- | --- | --- |
| 001 | [键盘操作即时呈现](001-keyboard-motion.md) | P1 / HIGH | TODO |
| 002 | [WAAPI遵循应用偏好](002-waapi-motion-preference.md) | P1 / MEDIUM | TODO |
| 003 | [Endfield悬停门禁](003-hover-accessibility.md) | P2 / MEDIUM | TODO |
| 004 | [统一样例组件打磨](004-sample-component-polish.md) | P2 / MEDIUM | CODE COMPLETE，待真人 |

正式界面建议顺序002→001→003（先统一偏好，再输入来源），可独立交付；本轮样例004独立于三项，以便先体验交互工艺。每项计划含自身完整定位、值、边界和人工体验方法。

Backlog：设计实验reduce覆盖、人工剧本工具toast减少位移、进度条布局动画、旧样例键盘开关。详见审计F5–F8，暂未选入实施。

状态严格区分：TODO / CODE COMPLETE（代码完成、待真人）/ DONE（真人验收通过）。不以语法或资源HTTP200代替视觉验收。不运行机器行为测试。
