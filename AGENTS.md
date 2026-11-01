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

- 2026-11-01 Codex：**QQ 组件首次安装**——网页 QQ 首次安装/重新检测入口，缺组件显式安装、失败重试、成功后直接扫码，缺 Node.js 给官网指引；固定锁定依赖、安装超时/进程回收、网页与终端运行租约互斥（connector_setup.py 新增）；安装只由点击发起，不自动扫码；**加载新版需受控重启，升级前先停旧 QQ 连接**。既有 GUI 自主检索/正式见闻/browser_reader/QQ 桥账本 v2 保留。
- 2026-11-01 ZCode：**协作者调度设计稿 v2.1（管家架构总纲）+ 架构图**（docs/设计文档/协作者调度-设计.md、docs/架构图-协作者调度.png；当日用户裁定本期只动设计文档）——纯协作者模型：名册主角=本机已装完整 agent（注册即授权：调用档案+权限档+预算帽+试跑），并行异步派工为核心，artifact/information 双回传契约、干将报告过膜；检索协作者=discovery_worker 加 manifest；astra 出具架构评审与开发方案 v1（P0 核实→P1 单 agent 闭环→P2 内容整合→P3 并行，实施待授权）；最关键待决=权限档档位标准。
- 待人工验收/待修：QQ 真实安装与扫码收发、对话内检索（**GUI 知乎 candidate/url 静态不匹配 bug 待修**，astra 评审 E11）、P1、观察器、深海 UI、见闻通道、v0.4+分寸首思辨、身体层复测；A05/A02、tests 旧路径适配 user/。
- 雷：本机无 `timeout`、无 Chrome；**起服一律 `.venv/bin/python -m alice serve`（系统 python3 无 openai，serve 启动即需要；macOS 下 ps 显示框架路径，不代表没用 venv）**；`dist/` 与 `user/` 不入库；PR 走 compare 页手动建；推送等用户明确指示；**活库元先验待下次人格重装种入**。
