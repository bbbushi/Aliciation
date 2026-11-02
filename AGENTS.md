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

- 2026-11-02 ZCode：**claude 直调转正式完成（用户裁定 ytq 分支限定，不合 main）**——设计 v1（530bf2c）+ S1~S5 实施：claude_policy 独立表（DSH 零接触）、ClaudeLocalTransport、权限闸门三层（结构禁区硬挡/任务内缓存/质询文件协议 10 分钟超时）、/api/worker-permissions + GUI 质询卡。**C02/C03/C04/C06/C07 自检全通**（allow 落盘/deny 零产物不绕路/`~/.zcode` 诱导被硬挡/取消回收/精确计费 $0.13 级）；C01/C05/C08~C10 待 GUI 人工。下一步=工具枚举名单化接线（dispatch 分派 claude-agent）+ GUI 全链路验收；实施与验收记录 docs/设计文档/claude直调-实施与验收-ytq分支.md。
- 2026-11-02 Codex：DSH协作、头像交互与本轮切换动画统一通过PR19交付（可拖头像/720px浮窗/左右气泡/安全Markdown/折叠菜单 + 240ms头像切换缓动；JS语法与独立静态审查过，实际动画及PR19其余待人工；未加载前批PNG路由者先重启Alice）。历史任务书/真实截图/重复v2评审/QQ群聊方案留本地未提交；上轮rebase备份与stash保留。
- 架构沿用用户裁决f983fa7：trial 3732ffe为正典、DSH插件桥为P1唯一路径；另一套D1已撤回（f3acba0留作P3本地CLI参考）。**数据层撞车已清**：昨夜本地D1起服把自建worker表形写入user/data/alice.db，今日对方schema报no such column:sequence；已备份（before-worker-drop-*.bak）后删7张空表、对方10表重建，服务200（ZCode动过user/在此记录）。教训：**平行施工裁决要赶在起服前——起服即写库**。worker_artifacts.py/testbed保留未接线；契约§六+astra§5为P1人工验收标尺，P2来源契约待设计；文件ID导入/产物下载/跨任务来源转发未开放。
- PR19细节：Alice单次无工具总结/持久领取/依赖隔离（原文仅GUI）、单任务引用保留草稿、worker_target绑定请求与epoch；重启/断开保留授权版本与有效正文，同参恢复不撤销，明确关授权/改参清正文且GUI确认；已读游标按站点/epoch/协作者保存、跨标签单调同步防迟到，正文/标题/凭据不入浏览器存储。
- QQ私聊检索（PR#17）/安装（PR#15/16）已合main，qq-owner需内容访问+聊天查找+QQ资料许可，平台/额度共用、缓存来源分渠道；群聊方案另留未合。GUI知乎candidate/url、P1/观察器/见闻/深海UI/GUI与QQ共存、A02/A05及旧SDK适配待办；插件0.4.0本地link，原生权限非OS沙箱、1执行+4待办，NEXT无永久删除接口。
- 雷：本机无`timeout`、无Chrome；**起服一律`.venv/bin/python -m alice serve`（系统python3无openai；ps显示框架路径不代表没用venv）**；`dist/`与`user/`不入库；PR走compare页手动建；推送等用户明确指示；**活库元先验待下次人格重装种入**。
