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

- 2026-11-08 Codex按用户要求交付本地上下文优化，共8个白名单文件（AGENTS、6个核心Python、1份评估报告），目标trial/windows-v0.2-20261028及PR→main；提交基线48cee7a已是最新origin/main，无需重放。本轮快照.git/pr-delivery/20261108-162946及codex/before-delivery-20261108-162946保留；仅走PR，不直接push/合并main。
- 最终CONSTITUTION按用户批准的存在事实稿精简，Alice名下7047→3052个LF字符，正文hash e27e04bd49d46aaf021fdc217bcc07d24e1bbe254f92d6001fd5275492abaef0；usage提示full/registry均v2，样本话题/关怀/收尾非每轮必做。memory_projection仅对严格原始捐赠先验九字段模板隐藏原候选聊天投影，接入legacy/recall/indexed/工作集；原数据、版本/权限/检索排名不改，未知或修订格式原样回退，非全局脱敏。context_persona改按实际返回text含自有条目/依赖/分隔符计账。
- 本轮全alice compileall与git diff --check通过，代码/文档两路只读复核无明确阻断项，凭据模式扫描无命中；仅GET现有首页/runtime均200，未启停服务/调用模型/业务机器自测。前开发会话真实请求正文19007→14366、输入usage12462→9655且新宪法已注入，但上下文不同且未取得该轮最终回复，不能当受控成本或人格效果验收；多场景人工验收及重新起服共存仍待用户。
- 源码哈希变化会使旧未完成导入检查点跨版本续跑被拒绝，已安装人格/记忆不被改写。legacy想法重复、registry目录近似预算/跨包依赖重复及最终metadata刷新仍待办；观点单正文/结构化审计、人称转换、样本/关系阶段修改及registry激活待裁决。QQ/联网/协作授权与停止边界保留，10请求正文采集自动停止仍未认证。
- 原82份私有资料和docs/assets候选全部排除交付并保留，旧stash/其他worktree及先前备份不动；本轮未读写user/或配置，先前context_inspector_enabled/observer_body_capture开关及用户自行重启安排不变。协议12/initial_plus_delta_v3、DSH/Claude双轨/浮窗、冻结头像、W6—W9/蓝图与既往Windows实测范围保留；远端提交及PR结果见本轮交付回执/git日志。
