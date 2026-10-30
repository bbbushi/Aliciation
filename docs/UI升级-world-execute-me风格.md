# Alice UI：world.execute(me); 美术风格适配

日期：2026-10-30。按用户最新指示，旧 Codewhale 参考不再作为视觉方向；本轮参考所提供的视频截图与 `MisakaZentai/world-execute-me-dsh-pv` 仓库，**只吸收美术风格，布局以 Alice 为重心，不追求动画效果**。

单一永久会话、主聊天、按需打开的观察器/P1、设置与 QQ 入口继续沿用已完成的界面结构。[上一轮实施说明](UI升级-Codewhale参考与实施.md)中的入口映射、业务契约与人工验收仍适用；其中暖白深青配色、浅色视觉和旧截图仅作历史记录，由本文的深海终端方向替代。

## 参考核实与范围

观察版本为默认分支 `main` 的 [a598092a1b1db91fa43961a81269353d13419f6f](https://github.com/MisakaZentai/world-execute-me-dsh-pv/commit/a598092a1b1db91fa43961a81269353d13419f6f)，提交时间为 2026-10-06 00:13:03 UTC。以下源码链接固定此版本。

用户提供的视频为 [B 站 BV1xCai6aE9g](https://www.bilibili.com/video/BV1xCai6aE9g/)。本次访问该页返回 HTTP 412，未观看完整视频；实际视觉依据是用户附图，制作方法、配色与许可依据是已读取的仓库资料。没有安装依赖、执行出片脚本或运行参考项目。

| 已读取资料 | 核实结果 |
| --- | --- |
| [README](https://github.com/MisakaZentai/world-execute-me-dsh-pv/blob/a598092a1b1db91fa43961a81269353d13419f6f/README.md)、[制作原理](https://github.com/MisakaZentai/world-execute-me-dsh-pv/blob/a598092a1b1db91fa43961a81269353d13419f6f/docs/HOW_IT_WORKS.md) | 这是代码逐帧生成的非官方同人 PV。左侧用 dsh CSS 拼静态 HTML 并截图，右侧由 PIL 绘制终端风格模型意象；没有真实 dsh 进程、聊天服务或模型调用。视频中的训练阶段、检查点和歌词镜头不能当作可用产品功能。 |
| [build_frame.py](https://github.com/MisakaZentai/world-execute-me-dsh-pv/blob/a598092a1b1db91fa43961a81269353d13419f6f/film/pv_dsh_frontend_20260927/build_frame.py#L137) | 聊天页仍使用系统界面字体，存在 16px 正文字号版本；头像、姓名、状态、消息和输入区分层。其深海主题在同文件 [L169–172](https://github.com/MisakaZentai/world-execute-me-dsh-pv/blob/a598092a1b1db91fa43961a81269353d13419f6f/film/pv_dsh_frontend_20260927/build_frame.py#L169)：背景 `#05080f`、用户气泡 `#18233d`、输入区 `#0d1528`、选择器 `#1a2440`、边框 `#1b2540` / `#223052`。 |
| [tuikit.py 配色](https://github.com/MisakaZentai/world-execute-me-dsh-pv/blob/a598092a1b1db91fa43961a81269353d13419f6f/film/tui_pv_world_execute_20260926/tuikit.py#L30) | `deepsea` 色板为近黑蓝底 `#04070f`、冷白系统色 `#c8d6ea`、蓝色 `#4d6bfe` / `#7894ff`，错误 `#ff3b30`，警告 `#ffcc00`。这些是参考色，不要求 Alice 逐值照搬；操作状态须保留可读文字。 |
| [tuikit.py 窗格](https://github.com/MisakaZentai/world-execute-me-dsh-pv/blob/a598092a1b1db91fa43961a81269353d13419f6f/film/tui_pv_world_execute_20260926/tuikit.py#L333) | `box()` 用细线轮廓、四角约 7px 长的 2px 亮角，以及被标题打断的顶边制造终端窗格感。可用原创 CSS 边框与角标表达，不需引入 PIL。 |
| [后期源码](https://github.com/MisakaZentai/world-execute-me-dsh-pv/blob/a598092a1b1db91fa43961a81269353d13419f6f/film/tui_pv_world_execute_20260926/tuikit.py#L442)、[合成源码](https://github.com/MisakaZentai/world-execute-me-dsh-pv/blob/a598092a1b1db91fa43961a81269353d13419f6f/film/pv_dsh_frontend_20260927/dsh_her.py) | 扫描线、辉光、残影及镜头明暗变化服务于音乐影像。本轮只保留低强度静态纹理和少量蓝光，不照搬闪烁、故障抖动、逐帧运动或音乐时间线。 |
| [字体说明](https://github.com/MisakaZentai/world-execute-me-dsh-pv/blob/a598092a1b1db91fa43961a81269353d13419f6f/docs/FONTS.md) | 终端使用本机 Consolas，中文使用微软雅黑；标题使用 Space Mono / Anton。聊天页仍为系统无衬线。Alice 不下载或打包这些字体，英文标签可用本机等宽字体，中文正文保持正常系统字体。 |

## Alice 的适配决定

| 参考中的美术特征 | Alice 实现方向 |
| --- | --- |
| 近黑蓝底、冷白字、蓝色高光 | 统一欢迎页、聊天、导航、设置、QQ、观察器、P1、人格弹窗和退出结果的暗色变量；错误/警告各自保留颜色和文案。 |
| 窗格细线、亮角、终端标题 | 用在 Alice 自身界面的分区与边界；不新增终端、训练控制台、项目树或多会话。 |
| 字符图案 | 原创静态小图案作为导航点缀，不占据大型右侧展示区，不声称呈现模型内部或人格状态。 |
| 聊天之外的模型世界 | 不复制视频左右窄聊天/大可视化的比例。保留大聊天区；观察器和 P1 只有用户主动打开才占用辅助空间。 |
| 扫描纹理与微光 | 只做静态氛围，保持细字、中文和输入光标清晰；不要求动画、音频、GPU 渲染或新的运行依赖。 |
| 深色输入与气泡 | 输入、发送、用户消息和 Alice 正文保持清晰对比；长文、思考、工具摘要、渠道/投递说明延续原渲染与滚动语义。 |

中文正文不改成密集等宽小字，不用发光滤镜模糊消息。装饰不接管真实状态：不添加假训练进度、检查点编号、token 计数、心跳波形或伪记忆日志。现有连接状态、忙碌、未知结果和观察器数据仍来自原接口；未提供数据时保留缺失/未知提示。

QQ 二维码保留黑白编码与白色底板，不套反色、辉光或扫描纹理，不用虚构二维码作为真实可扫码状态。扫码/配对、本人确认并保存、开始 QQ 聊天仍为独立步骤。

## 实现边界

- 改动位于 `web/style.css` 和 `web/index.html` 的美术与装饰层；`web/app.js` 与本轮开始前逐字节一致，不改业务许可或接口。
- 保留原 DOM ID 与事件接线、原生 HTML/CSS/JS、无构建链/框架/外部字体/CDN/动画库的约束。核心服务、标准库边界、回环监听与同源校验不变。
- 不改聊天请求身份、防重复发送、不确定结果恢复、草稿、多行输入/IME、历史与跟随滚动；不改 QQ 自然分条算法、账本、投递恢复或已有文案。
- P1 日额度/单轮授权、输入区许可提示、人工证据/标签/校准、删除确认等保持原含义。变更配色不会启用模型请求，也不会替用户授权。
- 沿用手机导航、独立滚动区、短屏处理、弹窗焦点隔离和关闭返回焦点。纯文本聊天与受限人格渲染的安全边界不变。
- 已运行的 Alice 只需重新加载静态资源，不因美术修改要求重启。交付时发现先前服务已停止，沿用用户此前启动授权，以原配置重新启动并打开新页面；没有主动终止旧进程。刷新已有页面前应完成在途回复、保存草稿并处理本页 P1 待绑定状态。

## 许可与素材

仓库原创代码按 [MIT](https://github.com/MisakaZentai/world-execute-me-dsh-pv/blob/a598092a1b1db91fa43961a81269353d13419f6f/LICENSE) 发布，版权为 `Copyright (c) 2026 MisakaZentai`。复制实质代码须保留版权与许可。其 [NOTICE](https://github.com/MisakaZentai/world-execute-me-dsh-pv/blob/a598092a1b1db91fa43961a81269353d13419f6f/NOTICE.md) 另外区分：dsh 前端 MIT/DeepSeek 与 React 上游声明；鲸鱼娘、表情、头像及衍生字符画为 CC BY-NC-SA 4.0；随附字体为 OFL；音乐、歌词及原片部分舞者材料不由代码 MIT 授权。

本轮采用原创 Alice HTML/CSS 和字符点缀，仅参考美术原则。未复制鲸鱼娘、头像、舞者、音乐、歌词、dsh bundle、商标或字体资源；不替换 Alice 名称与身份。因此不把参考仓库素材许可扩展到本项目，也不向本项目引入其非商业素材限制。

## 检查记录与人工验收

| 检查层级 | 状态 | 限制 |
| --- | --- | --- |
| 参考资料核实 | 已完成：用户截图、固定版本 README/NOTICE/源码/字体说明；B 站页 412 | 不声称观看完整视频或运行参考产品。 |
| 最终差异与 DOM 契约审查 | UI 差异空白检查通过；原 ID 无删除、无重复；app.js 与本轮开始前逐字节一致 | 本轮未改 JS，不新增机器自测；静态检查不等于功能验收。 |
| 新暗色版合成视觉检查 | 已查看 1366×900 桌面聊天、1440×960 观察器/P1/设置、390×844 手机聊天/QQ/欢迎页/观察器；修正手机分区间距与底栏留白，未见这些视图整页横向溢出 | 仅版式视觉查看；未执行真实扫码、发送、配置保存、付费核查或自动交互验收。 |
| 真实环境人工功能验收 | **未执行** | 不创建/运行自动交互测试、机器自测或冒烟测试。 |

本轮合成文件：[独立静态预览](C:/Users/ylinz/.codex/visualizations/2026/10/30/01a18c7f-af1e-71f2-8d6f-23e7d62f2887/alice-world-preview/preview.html)、[桌面截图](C:/Users/ylinz/.codex/visualizations/2026/10/30/01a18c7f-af1e-71f2-8d6f-23e7d62f2887/alice-world-preview/desktop.jpg)、[手机截图](C:/Users/ylinz/.codex/visualizations/2026/10/30/01a18c7f-af1e-71f2-8d6f-23e7d62f2887/alice-world-preview/mobile.jpg)。同目录保存 `observer.jpg`、`mobile-observer.jpg`、`p1.jpg`、`settings.jpg`、`mobile-qq.jpg`、`mobile-welcome.jpg`。均为合成内容，无真实记忆/凭据；临时回环静态预览服务已停止，HTML 可本地打开。

此前完整业务剧本继续见[上一轮人工验收清单](UI升级-Codewhale参考与实施.md#真实环境人工验收清单)。本轮额外检查以下项目，每项记录通过/未通过/未执行、窗口尺寸与脱敏证据：

- [ ] 欢迎、空态、聊天、人格预览/导入、设置三分区、QQ、观察器全部视图、P1 与退出屏的背景、文字、边框一致，无浅底残片或暗字落在暗底。
- [ ] 普通/悬停/选中/禁用/焦点状态可辨；正文、次要状态、提示、危险按钮和链接具有足够对比，状态不只靠颜色表达。
- [ ] 字符点缀和扫描纹理不遮文字、不截获鼠标、不成为屏幕阅读器噪声；没有伪造的训练、token、连接或记忆状态。
- [ ] QQ 在真实扫码等待时二维码仍为清晰黑白、白底和足够留白；手机可扫码。过期/失败/配对/本人确认/保存未启用/运行/暂停等状态保持原操作顺序，关闭窗口不改变桥运行状态。
- [ ] 1366/1440px、1024px、900px 附近、390/320px 宽度及手机软键盘/短屏：聊天始终是主体，导航可达，设置与开发面板不横向溢出，输入与发送不被底部遮挡。
- [ ] 200% 缩放、减少动画和高对比度环境下仍可读；Tab、Shift+Tab、Escape、弹窗返回焦点与 IME 候选确认继续正常。
- [ ] 长中文、代码、URL、表格、人格长正文和分段详情可读；静态纹理不造成文字抖动或重影，滚动旧消息时不被流式更新抢回底部。
- [ ] 发送防重、busy 保留草稿、断线未知结果、原请求查询/显式新一轮流程与上轮一致；不通过美术更新触发实际消息或模型探测。
- [ ] P1 单轮授权/未知提示在折叠开发者导航后仍显示于输入区；额度、勾选、取消/删除确认、证据审阅与人工标签保持原规则，暗色不削弱费用和破坏性动作提示。

截图仅使用隔离合成数据，不读取/上传真实 `user/` 会话、人格或密钥。真实扫码、消息发送、配置保存、付费核查与退出流程由人工测试者在专用环境明确执行。

## 回滚

此工作树还含其他未提交改动。回滚本轮风格时，先保存当前差异并核对并行编辑，只按块反向移除本轮 HTML/CSS/必要界面适配，使其返回本轮开始前的界面；不要整体恢复到 git HEAD，否则会丢失上一轮已经实现的导航、滚动、输入与可访问性改进。

不得使用整树 reset/restore，也不得覆盖 `web/app.js` 中原有 QQ 自然分条文案或别人的后续改动。QQ 实现、账本、联网计划、后端和 `user/` 均不属于此次风格回滚范围。提交仍按用户指示，先 `ytq` 验收，再 PR 合入 `main`，不直接推送 `main`。
