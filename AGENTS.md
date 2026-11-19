# AGENTS.md

ZCode 与 DSH 轮流在这个工作区干活：开工先读「当前」，收工把「当前」整节替换（≤5 行），历史留在 git log，
不要往这里追加段落——这个文件每轮都会进上下文，长了就是负担。
分工（2026-10-27 起）：**ZCode 负责架构优化设计，DSH 负责测试环境搭建（面向人工测试的 SDK：部署/剧本执行/数据回收/记录辅助）**。
测试策略（同日定）：**取消冒烟测试与机器自测，只保留真实环境人工测试**；蓝图实验室验收维度的口子未收。

## 项目

Alice：陪伴型 harness，单一永久会话 + 三层记忆 + 可生长人格。Python 3.11+（用 `tomllib`）。

## 不变量

- `alice/` 只用标准库；`openai` 在 `alice/llm.py:13` 惰性导入（离线测试不装也能跑）。
- GUI 同样零依赖：`alice/server.py` 纯标准库，`web/` 原生 JS，不引入构建链/框架。
- `matplotlib` 只用于 `tools/` 出图，不进运行时。
- `user/` 是本机专属目录：`user/config.toml`（接口配置）+ `user/data/`（Alice 的记忆：对话/人格/归档），整目录已 gitignore；动过就在「当前」写一句。
- 服务只监听回环地址。
- 不碰 `.zcodeignore`、`~/.zcode/`。
- 四工作区（核心 `alice/`+`web/` / 测试 `tests/` / 附加 `addons/` / 资料 `docs/`、`tools/`）依赖单向指向核心；测试与 addons 只走公共面（`service.ChatService`、HTTP API、CLI），不 import `alice` 内部。详见 `docs/工作约束/工作区约定.md`。
- **能力增减须同步能力层JSON注册表与必要自我事实**（`alice/capabilities/commands.json`、`alice/constitution.json`）；注册表与SDK工具同源，宪法不重复工具目录或用户操作教程——自模型与实际能力须一致（2026-11-11用户确认的新组织方式）。
- 提交只暂存自己碰的路径，不用 `git add -A`（工作区常有另一棒的半成品）。
- **分支流程（2026-10-30 用户纠正）**：当前用户的改动推 `trial/windows-v0.2-20261028`；`ytq` 属于其他协作者，不作为当前用户的推送目标。验收后走 **PR 合入 main**（不再本地 merge 推 main）；**main 不接受直接 push**。合并前跑共存检查：`compileall` + 起服验证 + grep 对方分支可能触碰的路径（语义冲突 git 报不出来，三处旧路径 bug 的教训）。当前 origin 使用 HTTPS。

## 当前

- 2026-11-19 基于main 8041e5e完成PR38设置回退修复，由Alice UI细调会话施工、主会话独立复核；用户已授权提交trial/windows-v0.2-20261028并向main发PR。提交前fetch确认main未变，保留本用户trial/HTTPS/PR约定。同步备份.git/main-sync/20261119-202332及codex/before-main-sync-20261119-202332共87项含86私有资料引用；恢复按manifest.stored_path，保留被引用快照。
- PR38 增加 Orbit 四页、召唤窗、居中球与房间背景，自主活动 v2 含清醒睡眠、休息和先邀请后展开。上下文查看入口和正文留存独立保存，默认元数据开、正文和查看入口关。桌面入库 0.1.18（macOS 开发包，Windows 气泡尚未启用）。TEST_CONTROLS_ONLY=True仍临时放宽自主后台额度/时段/间隔，保留相应总开关与工具权限，本轮UI修复未改该策略。新版 Store 启动会建 v2 表，旧联系和观点迁移须先准备再确认。
- 基于 8041e5e 修复 Orbit 设置回退。恢复共用设置窗的分区、联网五类与六额度、DSH 连接/授权/任务同步、Claude 授权及 QQ 提示。在内容发现保留联网设置入口，在独立思想页保留活动工具权限、状态和记录，折叠内校验可展开首项。上下文观测加入 Orbit 设置入口并统一表单布局，记忆观察器沿用双栏与原关闭清理。修复 Orbit 退出时的节点和监听还原顺序。
- 修改web/{app.js,discovery.js,index.html,orbit.js,style.css,theme-orbit.css,thoughts.css,thoughts.js}；备份/29静态布局样例D:/alice_tmp/PR38设置共存修复-20261119/20261119-204607。Python编译/4JS语法/diff空白通过，38原HTML输入选择控件契约不变、无重复ID，24 DSH/Claude控件与39联网字段/6额度及关键提交源码对照通过；最终两路源码复核未见阻塞。主会话交付核验在.git/pr-prep/20261119-204414-ui-repair，86项未跟踪资料hash/集合不变且不纳入提交，旧stash/worktree保留。真人保存/清钥、授权/扫码、反复切主题、首次引导、思想/观测/焦点仍待验；仅源码与无产品脚本的离线静态排版，未起停服务、访问user/、调用业务接口/模型、迁移或机器行为/冒烟测试。
- legacy recall 动态余额、重复 source_block 与先暴露后准入、PR33 延迟队列边界仍待修验，诊断 D:/alice_tmp/会话上下文溢出排查-20261114/诊断报告.md。人格导入预检 installation_started=false 已有兼容，material/token 链待补。Astra、人格 QQ、QQ 主动投递和原人工验收待推进。UI v2 与 A/B/C 预览、未跟踪资料、旧 stash、worktree 和清理候选保留。
