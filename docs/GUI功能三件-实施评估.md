# GUI 功能三件实施评估

日期：2026-10-29；基线：`a99a145`，分支 `trial/windows-v0.2-20261028`。本评估依据[需求文档](./需求文档-GUI功能三件.md)、[工作区约定](./工作区约定.md)与当前源码；[项目审查报告](./项目问题清单与分层整改建议.md)仅作为风险线索。开始评估时工作区干净，原有 `127.0.0.1:8800` 服务仍在监听；未读取、重启或修改其会话、配置与数据。

## 决策边界与结论

三项均可在现有分层内局部实施，无需先裁决架构变更。`server.run()`继续拥有 HTTP 服务的启动/收尾，`ChatService`继续拥有 Store、模型客户端和单会话锁。新增的 HTTP 排空门闩仅决定服务退出时哪些请求已经准入；没有引入任务队列、改动跨模块事务协议、数据库结构或公共 API 兼容策略。配置发布在 `ChatService._lock` 内一次完成，不转移客户端所有权；现有 `ChatService(cfg, db_path, client)` 注入方式保留。

本期不处理审查报告 A05/A06/A07 的完整轮次恢复、跨进程独占、归档补偿或全站来源保护。若实现中发现上述机制是满足本需求的必要条件，应停止受影响部分、更新本文并提交架构裁决报告，不能以局部补丁替代决策。

## 一、GUI 优雅退出

现状：`web/`没有关闭入口；`server.run()`的 `serve_forever()` 退出后立即 `server_close()`、`svc.close()`，且 `ThreadingHTTPServer.daemon_threads=True`；`ChatService.close()`未持锁，可能与 `send()`重叠。`send()` 持 `_lock` 执行 `loop.chat_turn()`、提取、watchers 和原监测；Store 逐项提交。`loop.chat_turn()`先提交用户轮、回复后提交助手轮，`close()`强制提取待处理记忆再关闭 Store。SSE 写回异常会落入 `loop` 的宽泛流式回退，可能额外发起模型调用（审查 A05/A06）。

拟改 `alice/server.py`、`alice/service.py`、`web/index.html`、`web/app.js`、`web/style.css`：页面按钮二次确认；`POST /api/shutdown`在同源校验后设置 HTTP 关闭门闩，拒绝随后请求，另线程/请求处理线程调用 `httpd.shutdown()`；`run()`等待已准入 API 请求结束，再调用持锁的 `svc.close()`，最后关闭服务器。关闭请求等待收尾结果再返回成功/失败，页面在此期间显示等待，不用端口消失推断成功。`daemon_threads=False`，确保请求线程不遗留。断线的 SSE 输出回调停止写网络但允许已开始的轮次走到落盘。已准入的轮次用原配置完整结束；门闩关闭后的新请求明确失败。

资源/异常：HTTP 监听器归 `run()`，Store/客户端归 `ChatService`；退出前提取按现有语义执行，不进行新迁移。提取失败需要向页面报告“关闭完成但记忆提取未完成，可重启继续处理”，不能暴露模型异常原文；已提交对话仍保留。若关闭请求本身断线，主线程仍负责收尾，页面只应显示状态未知。Windows 重启命令由服务端根据本次项目目录、Python 解释器、配置文件绝对路径和实际端口生成，保留自定义 `--config`，路径按 PowerShell 字面量转义；不包含 Key。关标签页不代表退出服务。

## 二、整体移除检测仪

现状：`ChatService.send()`在同一锁内调用 `_collect_metrics()`，其中 `metrics.judge_turn()`用 `cheap_model` 做人格偏移、诚实度、人机感评审；SSE `done.metrics`、`GET /api/metrics`、`POST /api/metrics/toggle`和 GUI 面板/卡片绑定。`metrics.enabled()`还会把显式空列表误读为默认全开，因此只关闭开关无法保证省去评审。思考深度独立走 `POST /api/settings`→`ChatService.set_thinking()`→Store meta，必须迁至新的设置入口。CLI 不调用检测仪；`tests/pressure` 的 `metrics` 是其独立人工判定记录，不依赖 Alice 检测仪；addons 无调用。

拟改 `alice/service.py`、`alice/server.py` 与 `web/`：从对话路径移除采集与评审调用，不再在 SSE 返回检测数据；删除 GUI 面板、卡片、入口和请求；把 thinking on/off 放进设置。保留已列为公共面的旧 `GET /api/metrics`及既有 toggle 作为惰性兼容接口，返回历史数据但不触发任何评审；旧数据、表、`alice/metrics.py`均不删除、不迁移。兼容查询不在 GUI 暴露，新轮次不再产生监测值。若未来要求删除旧公共接口，应另行决定兼容策略。

资源/异常：只撤掉对话路径上的评审调用，不改 Store 写锁、历史数据、CLI 或测试 SDK。思考开关继续持锁并存 Store meta；刷新、重启后仍有效。

## 三、GUI 修改模型配置并立即生效

现状：`config.load()`在启动时读取 `config.toml`；`ChatService.__init__()`创建 `self.client`，后续 `send()`及后台 watcher 持同一 `_lock`使用 `self.cfg/self.client`。GUI 现有 `/api/settings`仅管思考深度。`tomllib`只能读取；直接重写 TOML 会丢未知字段、注释和格式。`config.toml`可能含真实密钥，本评估没有读取其正文。

拟改 `alice/config.py`及局部配置写入模块、`alice/service.py`、`alice/server.py`、`web/`：新增模型配置读取、候选连接探测、保存接口与设置界面。预设填充智谱、DeepSeek、自定义 OpenAI 兼容；切换预设不覆盖用户尚未确认的现有选择。智谱 URL 与项目示例模型 `glm-4.6`/`glm-4.5-flash`见[官方兼容文档](https://docs.bigmodel.cn/cn/guide/develop/openai/introduction)和[模型概览](https://docs.bigmodel.cn/cn/guide/start/model-overview)；DeepSeek URL 与现有 `deepseek-v4-pro`/`deepseek-flash`见[官方快速开始](https://api-docs.deepseek.com/quick_start/agent_integrations/)。预设只是可编辑建议，不擅自迁移现有模型。

密钥读取只返回“已配置/来源”，不回传密钥、尾号或可复用掩码；“保留现有密钥”和“替换密钥”是独立动作，空白输入不会写入掩码。候选探测用独立临时客户端对当前编辑的 URL/key/对话模型和廉价模型分别发起最小 Chat Completions 请求，不先改生产配置；它能验证当时的网络、凭据、这两个模型及普通非流式基础接口，不能保证工具/流式/思考协议或未来可用。探测可能产生少量供应商费用，只有用户点击“测试”或“验证并保存”才执行。失败按状态/异常类别给可理解文案，不回显供应商原文、URL 查询、密钥或响应体。

探测还校验返回的 `choices/message/content` 结构，普通网页或其他不兼容的 HTTP 200 响应不能作为保存依据；短输出预算产生的空正文/思考回复仍允许。

保存时先校验候选和构建客户端，再持 `_lock`对照 GUI 读取时的配置版本与磁盘现状；只定点更新 `[llm]` 的 `base_url/chat_model/cheap_model/api_key`，保留其他文本、注释、未知字段，遇歧义 TOML 形式保守拒绝。`tomllib`复读校验；同目录临时文件 `flush/fsync` 后 `os.replace` 原子替换，失败则不发布内存配置并清理临时文件。替换成功后在同一锁内发布新 `cfg/client`，保留并合并当前 thinking 设置。已进入锁的轮次用旧配置跑完；之后进入锁的轮次整体用新配置；一轮不会混用客户端/模型。磁盘替换到内存发布之间的异常需明确报告并恢复/停止，不能谎称保存成功。新敏感路由局部校验 Host、Origin、JSON Content-Type，现有路由的全局信任边界另案处理。

兼容性：保留 `ChatService`构造签名和旧 `/api/settings` thinking 语义，新增接口不改 DB。配置路径以 GUI 实际启动时的文件为准；若修复 `--config`传递，仅修复其被忽略的局部缺陷，不改变默认路径。外部直接编辑配置与 GUI 保存冲突时报错并要求重新载入，不覆盖他人编辑。

启动边界：本期是已配置服务的运行中换 Key/模型。现有 `ChatService`启动时必须创建可用客户端，因此配置文件至少要有完整 URL、两模型和非空 Key（可为占位值）才能进入 GUI；让空配置也启动需要引入“无客户端”服务状态并改变启动契约，超出本期问题。后台 8800 服务已有配置，不受此边界影响。

## 顺序、回退与人工验收

1. 先去掉对话评审调用和 GUI 检测入口，把思考设置迁出；源码检查所有调用点。回退只还原本轮代码，不动历史监测表。
2. 加配置候选探测、保真持久化、原子发布及 GUI；失败始终保持旧有效配置。回退可还原代码，并由用户从安全副本恢复 `config.toml`；开发过程不触碰真实文件。
3. 加退出排空与页面结果反馈；回退代码后仍可用终端 Ctrl+C，但人工验收只能在隔离副本执行。

人工验收只在独立的真实环境副本进行，不运行冒烟测试、机器自测或模拟模型验收：

- 退出：空闲与真实流式回复中点击退出，确认防误点、等待当前轮、终态、端口/进程无残留、重启后对话与记忆完整；断开聊天 SSE 后再退出；令退出前提取失败时核对页面报错和可恢复数据；关闭后新写请求返回拒绝。关页后服务仍在。
- 检测仪：界面所有入口、卡片和 token 曲线消失；供应商用量/请求记录核对一轮没有评审型额外调用；思考 on/off 各发真实一轮，刷新与重启后设置仍有效。廉价模型仍可能因正常记忆提取等功能被调用，须按用途区分。
- 模型配置：在副本保留自定义 TOML 注释/未知字段，故意填错 key 探测并确认旧配置继续可聊；有效 key 探测与保存后不重启即聊天；在途轮次切换核对旧/新模型边界；外部改文件造成版本冲突时不覆盖；编辑特殊字符 key 与路径后复读 TOML；模拟写入权限失败核对旧配置和临时文件清理。测试连接不代表全链路验收。

本轮仅做源码、差异和静态设计核对；未在原有 8800 服务调用真实模型或修改 `data/`、`config.toml`。人工验收结果须后补，不能将静态核对写成已通过。
