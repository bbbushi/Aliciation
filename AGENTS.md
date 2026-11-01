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
- **能力增减须同步宪法「存在事实」清单**（`alice/retrieval.py`：我有什么/没有什么）——自模型与实际能力的漂移本身就是认知错误源（2026-10-31 烟味事件的教训）。
- 提交只暂存自己碰的路径，不用 `git add -A`（工作区常有另一棒的半成品）。
- **分支流程（2026-10-30 用户纠正）**：当前用户的改动推 `trial/windows-v0.2-20261028`；`ytq` 属于其他协作者，不作为当前用户的推送目标。验收后走 **PR 合入 main**（不再本地 merge 推 main）；**main 不接受直接 push**。合并前跑共存检查：`compileall` + 起服验证 + grep 对方分支可能触碰的路径（语义冲突 git 报不出来，三处旧路径 bug 的教训）。当前 origin 使用 HTTPS。

## 当前

- 2026-11-02 Codex：DSH功能更新0bd2770已推trial/windows-v0.2-20261028，PR19 https://github.com/bbbushi/Aliciation/pull/19 向main待审核；基线为最新main 48e175b（PR17/18已合），未直接推main。功能提交含22个代码/说明路径，本交接同步至同PR；历史任务书、含真实聊天的参考截图、重复v2评审原件和QQ群聊方案留本地未提交，开发方案已改链至仓库现有评审。上轮rebase回退备份仍保留codex/backup-before-main-rebase-20261102-004707、stash 3ff107c及.git/rebase-backups/20261102-004707。
- 架构沿用用户裁决f983fa7：trial 3732ffe为正典、DSH插件桥为P1唯一路径；另一套D1已撤回（f3acba0历史留作P3本地CLI参考），worker_artifacts.py/testbed保留但未接线。开发方案/总纲/契约/testbed说明已对齐，不再误指失效注册入口；契约§六+astra§5仍为P1人工验收标尺，P2来源契约待设计；文件ID导入、产物下载、跨任务来源转发仍未开放。
- PR19包含可拖头像/720px浮窗/右侧固定、左右气泡/安全Markdown/折叠工具/紧凑菜单；Alice单次无工具总结/持久领取/依赖隔离，原文仅GUI；单任务引用保留草稿、worker_target绑定请求与epoch、工具仅限目标。重启/断开保留授权版本/有效正文/已用次数，同参恢复不撤销，明确关授权/改参仍清正文且GUI确认；旧通知降纯状态，追问先只读核原会话，历史入口与执行资格分离。已读按站点/epoch/协作者/task保存数字游标、跨标签单调同步/删除标记防迟到，只有前台实际读到末尾才推进，正文/标题/凭据不入浏览器存储。
- QQ私聊检索6cb3113/PR17、安装PR15/16已合main，qq-owner仍需内容访问+聊天查找+QQ资料许可，平台/额度共用、缓存来源分渠道、GUI历史双范围有效才展示QQ正文。QQ群聊e8aa55d继续留D:/alice-qq-group-20261101，等私聊稳定后再调整，未合入/推送。GUI知乎candidate/url旧问题、P1/观察器/见闻/深海UI/GUI与QQ共存、A02/A05及旧SDK路径适配仍待后续或人工；插件0.4.0本地link，NEXT2.0.21-next/Harness0.2.1-rc.4/Cordis4.0.5，原生权限非OS沙箱、1执行+4待办，NEXT无永久删除接口。
- 本轮compileall（alice）、2个修改JS语法、暂存diff检查与前后端独立静态审查通过，未发现发布阻塞；未启停应用、读写user/、调用模型、发QQ消息或机器自测。完整更新需重启Alice并刷新网页，已用0.4.0插件无需重载；旧流程已清正文不恢复，旧版读态需首次重看，插件15分钟空闲回收可能使旧会话不可续。DSH A01~A12、重启/恢复/撤权/引用重试/多标签已读及QQ真实搜索/撤权/超时仍待人工。
