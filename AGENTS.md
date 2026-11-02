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
- **分支流程（2026-11-02 当前用户纠正）**：当前用户在 `ytq` 上工作，后续改动以 `ytq` 为工作分支及用户明确要求推送时的目标；此前 `trial/windows-v0.2-20261028` 的归属说明不适用于当前用户。Claude直调仍限定 `ytq`，不合 main；其他获准合入的改动验收后走 **PR 合入 main**（不再本地 merge 推 main）；**main 不接受直接 push**。合并前跑共存检查：`compileall` + 起服验证 + grep 对方分支可能触碰的路径（语义冲突 git 报不出来，三处旧路径 bug 的教训）。当前 origin 使用 HTTPS。

## 当前

- 2026-11-02 ZCode：**claude 直调外评五项全修+活体验收通过（f748e55，ytq 限定）**——F1 授权按任务工人身份（worker_context refs 逐ref对表+validate_chat_target 本地轨不要求桥）；F2 available_workers 构造参数化；F3 账本完整链（事务内登记 task+messages+attempts 含 originating/tool_call，完成回复写 completed 消息）；F4 取消接真句柄；F5 Row 下标。活体链全通；僵尸任务已收敛。**已知断点（Codex 接手诊断）：首页引用 claude-gate.js 但 GET 404（静态路由缺）→ 质询卡不显示；待修+GUI 人工（C01/C05/C08~C10）**。
- 2026-11-02 Codex：PR20 已交付（分屏工作区：左头像+会话列表/右消息/40px 拖动栏，浮窗 960×680 可停靠，420ms 展开/收起，头像动画细节保留）；trial 已 rebase 到 PR19 并推增量；历史任务书/截图/QQ群聊方案留本地。另出 claude 直调诊断文档（质询卡缺失：claude-gate.js GET 404、refs 版本、回执哈希等）待修清单 A~D。
- 正典沿用f983fa7裁决：trial 3732ffe、P1只走DSH插件桥；旧D1撤回（f3acba0作P3参考），worker_artifacts.py/testbed未接线，文件ID导入/产物下载/跨任务来源转发未开放，P2来源契约待设计，契约§六+astra§5为P1人工标尺。前轮ZCode动过user/：备份before-worker-drop-*.bak后删7张空worker表、重建对方10表，服务200；起服即写库，施工裁决须先于起服。
- PR19已交付DSH协作、可拖头像/720px浮窗/左右气泡/安全Markdown/折叠菜单及240ms切换缓动，实际动画与其余业务待人工；缺PNG路由须重启。单次无工具总结/持久领取/依赖隔离仅GUI；单任务引用保留草稿、worker_target绑定请求与epoch；重启/断开/同参恢复保留授权及有效正文，关授权/改参确认后清正文；已读游标按站点/epoch/协作者单调同步，正文/标题/凭据不入浏览器存储。历史任务书/截图/v2评审/QQ群聊方案留本地未提交。
- QQ私聊检索PR17及安装PR15/16已合main，qq-owner需内容访问+聊天查找+QQ资料许可，共享平台/额度、分渠道来源；群聊未合。GUI知乎candidate/url、P1/观察器/见闻/深海UI/GUI与QQ共存、A02/A05及旧SDK适配待办；DSH插件0.4.0本地link，原生权限非OS沙箱，1执行+4待办，NEXT无永久删除接口。活库元先验待下次人格重装种入；本机无timeout/Chrome，起服用`.venv/bin/python -m alice serve`；user/dist不入库，PR走compare页手动建，推送须用户明示且目标ytq，Claude直调不合main。
