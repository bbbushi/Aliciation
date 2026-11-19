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

- 2026-11-19 基于main c43a764（PR40/23d6568）重新适配UI；旧PR39已关闭未合入，重放a40d902时6个文件冲突已逐项解决，并经主会话复核。按用户授权更新trial/windows-v0.2-20261028并以PR交main，保留HTTPS/trial约定。备份.git/main-sync/20261119-214709-ui-adapt与codex/before-ui-adapt-20261119-214709保存旧提交、补丁和86项私有资料引用；恢复按manifest.stored_path，不单删引用快照。
- PR40 入库桌面 0.1.37、独立 Alice Lab 六模块、全网搜索许可、联系等待与计划摘要、分段回复和 Orbit 收纳 Dock／球面设置页。设置的「外部观察」启动已注册的 Lab，签发或撤销当前实例 15 分钟只读授权，正文须单独勾选。完整上下文和诊断在 Lab 查看，主界面沿用新版对话与思想投影。TEST_CONTROLS_ONLY 源码默认 False，上游运行策略原样保留。
- 本轮恢复共用设置窗与 DSH／Claude／QQ 分组，联网五类、六额度与保存清钥接线。新版 Lab 授权页适用四主题，Orbit 保留球面入口、退场过渡、设置页返回与原配色，单独思想页保留休息、联系等待及最近活动，折叠校验和焦点归还补齐。主题往返恢复原节点并清理监听和计时器。修复待确认徽标重进与 Dock 收纳贴底。
- 适配限web/{app.js,discovery.js,index.html,orbit.js,style.css,theme-orbit.css,thoughts.css,thoughts.js}和交接。Python编译、5 addon JS/4修改前端JS语法、7 JSON/TOML/Cargo.lock解析及差异空白通过；25原HTML输入/选择控件契约一致无重复ID，29相关控件/39联网字段及33离线静态布局见D:/alice_tmp/PR40设置适配-20261119/20261119-215827。两路源码复核与文件hash核对完成；86私有资料/旧stash/worktree保留，不纳入提交。真实Lab注册启动/授权到期撤销、保存清钥、首次引导、往返切主题及分段回复待人工；未读写user/、起停服务、调用业务/模型、迁移或机器行为/冒烟测试。
- legacy recall 动态余额／重复 source_block／先暴露后准入及 PR33 延迟队列边界待修验，诊断 D:/alice_tmp/会话上下文溢出排查-20261114/诊断报告.md。人格导入 material/token、Astra、人格 QQ 与 QQ 主动投递等前序待办仍在。私有 UI 预览、旧 stash 和 worktree 保留。
