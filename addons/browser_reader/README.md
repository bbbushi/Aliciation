# 独立浏览器读取组件

此组件为 B站、知乎提供独立 Edge 会话。`alice/` 仍只依赖标准库；Playwright 安装在本目录的 `.venv/`。不读取日常浏览器账号、导出 Cookie、保存密码到 Alice 配置或向模型提供登录资料。

## 安装与使用

Windows 需已有 Microsoft Edge。在仓库目录运行：

```powershell
python addons/browser_reader/install.py
```

安装器只从 PyPI 安装固定版本 `playwright==1.64.1` 及其依赖，不下载额外浏览器。安装成功标记放在 `.venv/`，不随仓库提交。

重启 Alice 后，在 **内容发现 → 偏好设置 → 去哪里找** 的 B站或知乎卡上选择 **使用独立浏览器**。平台开关仍独立控制，不因换通道自动打开。需要登录时点 **打开独立浏览器 / 登录**，由用户在窗口中完成登录或验证，随后关闭该独立窗口再寻找。也可手动打开：

```powershell
python -m alice.browser_transport open zhihu
python -m alice.browser_transport open bilibili
```

每个平台分别使用当前配置文件所在目录下 `browser_sessions/<平台>/`。默认是 `user/browser_sessions/`，由 Edge 自己保存会话；不使用现有个人浏览器目录，不开放远程调试端口。独立窗口属于用户的正常浏览器操作，不受后台寻找的网络额度计账；自动读取走下面的监督协议。账号退出可在该独立窗口中亲自操作，关闭平台则立即停止后续后台使用。

## 读取范围

- **B站**：浏览器搜索首页，提取规范 BV 链接和视频标题；宿主再调用已有固定 `view` 只读入口核对 BV 号并读取实际视频简介。不会播放或下载音视频、读取字幕或评论。浏览器直接读视频页的 CLI 仅用于研究，不用于当前自动流程。
- **知乎**：先打开首页，由独立会话发出同源 `search_v3` GET，再读取指定回答的 `answers/<id>` 正文。问题编号、回答编号以 Python 整数解析，避免 19 位编号被 JavaScript 舍入；正文须同时核对这两个编号。只接入已验证的回答文字，文章、问题描述和付费内容暂不开放；不采纳搜索结果里的作者资料或其他回答。已在用户登录后实际取得搜索候选和对应回答正文。
- 两平台当前均只取搜索首页，不自动翻页。知乎按至少一页的结果量请求，过滤不支持的模块后再应用用户设置的候选数量，避免较小的数量恰好只取得不可读模块；这不改变日额度或用户设定的候选上限。
- 搜索结果和摘要只用于发现；无法确认正文就失败，不改用全页文字、登录页面或第一个无关回答。
- 验证页面、登录要求、拒绝和限流停止当次读取；不自动完成验证、轮换身份或改端点重试。未使用隐身补丁、指纹伪造或第三方项目代码。

## 宿主协议与控制

固定 Python 子进程读取初始 JSON，输出 `ready` 后等待 `go`。CDP Fetch 在请求及重定向发出前暂停，每个获准资源输出 `request`，宿主重复检查平台、固定 HTTPS 主机/读取路径、GET/HEAD 和资源类型，先计账再发 `grant`。自动页面另加 CSP，禁止弹窗、表单提交、媒体、框架和工作线程；图片、字体及不在范围内的请求被阻止，WebSocket 关闭。人工登录窗口不加这些限制。

只允许当前平台网页、限定读取 API 及其脚本/样式 CDN；页面、模型、用户网页请求不能提供任意程序、脚本、个人目录或任意网络目标。DOM 提取脚本由组件固定，不执行模型生成代码。核心不 import addon，addon 不 import 核心。

每个资源计入共享网络次数；搜索首次资源另外计一次查询。单资源预留量由 `browser_resource_bytes` 控制（初值 8 MiB，可在高级任务设置调整），完成后以浏览器报告的传输字节结算；中断或未知的传输保留预留量。它是资源计账与采纳边界，不是操作系统级流量限速器；未知长度传输超额须在结束事件才能确认。登录窗口的人工操作单独于后台计账。

取消、前台对话、版本变更、期限和日额度沿用发现监督器；Windows Job Object 绑定 Python、Playwright 和 Edge 子进程，宿主关闭或取消时整体回收。登录窗口不加入后台任务的 Job，不会被后台取消操作关闭。浏览器已被用户打开时后台不抢占配置目录，而提示关闭独立窗口再试。

## 真实环境诊断 CLI

以下操作真实联网，但不打开 Alice 对话数据库、不调用模型。仅在人工核查时使用，不是自动测试套件，也不写后台业务账本。

```powershell
python -m alice.browser_transport search bilibili --query 科技 --limit 5
python -m alice.browser_transport search zhihu --query 科技 --limit 5
python -m alice.browser_transport read zhihu --url https://www.zhihu.com/question/问题编号/answer/回答编号
```

`--requests`、`--response-bytes`、`--timeout` 为本次诊断额度，可自行设置；`--show-window` 可在人工诊断时改用普通窗口模式，自动任务默认无窗口。输出只含规范候选、范围/文字长度、筛选计数、错误代码和用量，不输出正文、Cookies 或浏览器日志。实际结果、尚未完成的验收见[接入报告](../../docs/B站与知乎-浏览器接入与人工验收.md)。

## 参考

- [dsh-bili-scroll 使用流程](https://github.com/c1overzz/dsh-bili-scroll#首次使用两步)：实际配置包含用户扫码登录，不把其可用性等同于匿名接口可用。
- [OpenCLI 的知乎浏览器适配说明](https://opencli.info/docs/adapters/browser/zhihu.html)及[B站说明](https://opencli.info/docs/adapters/browser/bilibili.html)：参考浏览器会话作为访问通道的划分，没有安装、运行或复制其插件。另核对了 [search.js](https://github.com/jackwener/OpenCLI/blob/main/clis/zhihu/search.js)（读取时 blob `a58a5e4d04ea52e6ac07b3f75773c571f269541b`）和 [answer-detail.js](https://github.com/jackwener/OpenCLI/blob/main/clis/zhihu/answer-detail.js) 的站内读取端点，本组件独立实现、只取一页、不沿用写操作或账号导出。
- [zhihu-mcp 的 DOM 读取实现](https://github.com/Douyh123/zhihu-mcp/blob/master/zhihu/actions.py)：仅参考页面元素定位；本组件独立实现，不沿用其整页猜测、自动滚动或发布行为。
- [Playwright 持久上下文](https://playwright.dev/python/docs/api/class-browsertype#browser-type-launch-persistent-context)及[CDP Fetch](https://chromedevtools.github.io/devtools-protocol/tot/Fetch/)：组件依赖的浏览器控制接口。
