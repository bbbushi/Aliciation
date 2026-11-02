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

- 2026-11-02 Codex：PR19已合main 90093ea，本地trial/windows-v0.2-20261028已rebase到该版本并逐字节保留4个增量文件；新UI功能e17164b已推trial，PR20 https://github.com/bbbushi/Aliciation/pull/20 向main待审核，本交接同步同PR。仅3前端+交接路径，不直接推main；历史任务书、真实聊天截图、重复v2评审、QQ群聊方案仍留本地；原rebase备份分支/stash 3ff107c保留，本轮快照在.git/pr-delivery/20261102-143012。
- 架构沿用用户裁决f983fa7：trial 3732ffe为正典、DSH插件桥为P1唯一路径；另一套D1已撤回（f3acba0历史留作P3本地CLI参考），worker_artifacts.py/testbed保留未接线；契约§六+astra§5仍为P1人工验收标尺，P2来源契约待设计，文件ID导入/产物下载/跨任务来源转发未开放。PR19既有Alice单次无工具总结、任务引用绑定、GUI/QQ/记忆隔离、重启/断开保留授权与次数、同参恢复及多标签已读游标保留。
- PR20：左侧头像+常驻会话列表、右侧消息与40px拖动栏、底部同步状态，浮窗最大960×680并支持停靠；旧任务引用保留列表与归档定位。大/窗口头像152px（窗口框162px）、小64px，断线/未知灰显；窗口展开收起420ms，关闭立即inert/代际隔离，异步收尾恢复焦点；头像装饰框与图片共用240ms动画，外框不先跳终态、线宽圆角不拉伸，连续反转续接当前画面，确认拖动后接管位置，减少动态效果与隐藏/退出清理保留。
- QQ私聊检索PR17/安装PR15/16已合main；qq-owner仍需内容访问+聊天查找+QQ资料许可，缓存来源分渠道/GUI双范围显示。QQ群聊e8aa55d留D:/alice-qq-group-20261101待私聊稳定后调整，未合入/推送。GUI知乎candidate/url旧问题、P1/观察器/见闻/深海UI/GUI与QQ共存、A02/A05及旧SDK适配仍待；DSH插件0.4.0本地link、NEXT2.0.21-next/Harness0.2.1-rc.4/Cordis4.0.5，原生权限非OS沙箱、1执行+4待办，NEXT无永久删除接口。
- 本轮JS语法/暂存diff/298唯一HTML ID及70个协作JS字面量引用、独立静态审查通过，无发布阻塞；discovery-title ARIA缺失为main既有问题未改。未启停应用/机器自测/读写user/调用模型/发QQ，本轮刷新网页加载；上次已启Alice 127.0.0.1:8800（当时监听PID19960，日志.git/runtime-logs），该次正常打开维护user/，本轮未重验进程。窄屏/停靠/展开收起/连续反转/拖动/焦点与DSH/QQ真实场景待人工，B张嘴笑未制作。
