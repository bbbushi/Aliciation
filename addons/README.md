# 附加功能区

核心 harness 之外的附加物(桌宠等)住在这里的子目录中。

规则(见 [工作区约定](../docs/工作约束/工作区约定.md)):只经 service 公共面接入(HTTP/SSE API 或 `ChatService`),不 import `alice` 内部;需要核心新增能力时走架构侧评审,不在 addon 里绕洞。

- [QQ 入口](qq/README.md)：官方机器人扫码、本人私聊文字，与本机网页共用对话；通过专用渠道接口接入，默认关闭，真实人工验收待执行。
- [Alice Lab](alice_lab/README.md)提供独立的只读观察和隔离人工验收。[旧同源上下文观测插件](context_inspector/README.md)保留历史说明。
- [桌面发行组件清单](desktop/README.md#当前交付边界)说明源码运行和桌面包各自提供的能力。
