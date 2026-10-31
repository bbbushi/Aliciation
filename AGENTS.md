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

- 2026-10-31 Codex（经 trial PR#13 入 main）：**对话内检索 + browser_reader 交付**——GUI 主模型按需 `discovery_search(query/platform/limit/fresh)`（无独立表单/口令门槛/强制预取），后台/聊天独立开关、共享额度、单前台槽、锁外模型/网络与短存储步骤、进度/停止/原请求恢复及平台阻塞保留；QQ 不开放新搜索协议。
- 同批 Codex：**正式见闻**需真实来源与保存许可（salience=0、短证据/依赖 ID 和版本、普通遗忘；URL-only 拒写，旧 source_url 标 legacy_unverified；按渠道提供见闻观点，不写跨渠道人格正文）；首次迁移自动备份。**browser_reader** 独立 Playwright 环境读 JS 渲染页（B 站简介/知乎回答实测过），核心/GUI 零新增依赖，逐资源核准计账；用户独立 Edge 登录。
- 本轮 Python 编译、14 项 JS 语法、37 路径凭据模式检查及截图核对通过，8800 首页 200；自主检索→阅读→对话→记忆→撤销、多平台/GUI 与 QQ 共存/视觉待人工。
- 2026-10-31 ZCode（经 ytq PR#11/#12 入 main）：**见闻通道**（memories.source_url 列+remember source_url 参数+interp 枚举「见闻」+思辨引用计 use——网上检索的信息作为见闻进成长管线，与 Codex 的正式见闻衔接）、**存在事实清单**（宪法身份边界：烟味事件收口，复测待做）、**元观点**（对话终结权元先验+思辨叙事恒重建）、**分寸机制**（契约松绑+元先验+提取配对事件）、**v0.4 思想层认识论结构化 P0**（七字段 schema+先验→验证→修订生命周期）、架构图 v0.4、催睡观察、对话内检索设计稿（discovery_search 方案 A——与 PR#13 实现一致）。
- 待人工验收：QQ 真实扫码收发、P1、观察器、深海 UI、内容发现矩阵（贴吧增强含内）、对话内检索、见闻通道、v0.4+分寸首次真机思辨、身体层复测（烟味重发）。
- 待做：ZCode 侧 A05（remember 压力绕行）、A02（共享锚点阻止首次自动思辨）；`tests/pressure/launch.py:69`、`deploy.py:146/221/237/250` 旧 data/ 布局待适配 user/。
- 雷：本机无 `timeout`、无 Chrome；`dist/` 与 `user/` 不入库；PR 走 compare 页手动建；推送等用户明确指示；**活库元先验待下次人格重装种入**。
