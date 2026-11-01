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

- 2026-11-02 ZCode：**撞车裁决落定（用户裁定按 ZCode 建议执行）**——采纳 trial 3732ffe 为正典（桥路径为 P1 唯一路径，跑对方 A01~A12 人工验收）；本地撞车实现已 revert（代码存 git 历史 f3acba0，为 P3 本地 CLI 干将轨现成起点），保留 worker_artifacts.py 与 testbed worker（对方无对应件、无冲突互补）；契约 v1 转 P1 验收标尺（§六 + astra §5 门槛），架构侧下一步=对照审计对方实现（初探：其 loop 按 scope 取 worker_refs，9.1 方向正确，深审待做），P2 来源契约为下一块设计；设计稿 v2.3、astra 评审与开发方案已入库。
- 2026-11-01 Codex（PR#17 已合 main）：**QQ 本人私聊搜索修复**——原 GUI 限定改为受许可 qq-owner（内容访问+聊天查找+QQ 资料许可），平台/额度共用、缓存/来源按渠道，GUI 历史双范围有效才展示 QQ 正文；仅 10 个搜索路径，群聊/DSH 不入 PR（群聊 6cb3113 前身 e8aa55d 另行保留）。真实搜索/撤权/超时待人工。
- 2026-11-01 Codex（同批 3732ffe）：**DSH 插件 0.4.0 协作会话**——专属历史会话 archive/restore/status（无永久删除），Alice「设置→DSH」归档/恢复/删除同步选项默认关闭；Windows DPAPI 独立令牌、**原生权限非 OS 沙箱**（如实标注，契约 5.2 对应）、1 执行+4 待办/五工具；7 处核心接线同一提交，不将截图自报产物当宿主验收，真实操作待人工。
- 待人工验收/待修：QQ 真实安装/扫码/私聊搜索与撤权超时、DSH 协作会话（A01~A12）、对话内检索（**GUI 知乎 candidate/url 静态不匹配 bug 待修**，评审 E11）、P1、观察器、深海 UI、见闻通道、身体层复测；A05/A02、tests 旧路径适配 user/。
- 雷：本机无 `timeout`、无 Chrome；**起服一律 `.venv/bin/python -m alice serve`（系统 python3 无 openai，serve 启动即需要；macOS 下 ps 显示框架路径，不代表没用 venv）**；`dist/` 与 `user/` 不入库；PR 走 compare 页手动建；推送等用户明确指示；**活库元先验待下次人格重装种入**。
