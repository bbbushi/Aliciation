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

- 2026-11-04 Codex按用户“最新修复审核后推送个人分支”指示完成P2复审，审核通过、无新增高置信阻挡问题；交付目标origin/trial/windows-v0.2-20261028，父提交b1ffbd74be2e6407f975edfe59296b50bb44e6ca，fetch后本远端0/0无分叉。提交包含llm.py/loop.py修复及对应文档证据；提交号与推送完成状态以git log和远端核对为准，不建PR、不合并/直推main。
- 复审报告docs/设计文档/整体设计迭代概览/Alice架构完善-P2修复推送前复审-20261104.md；独立复跑25项全过并补前情生成/审查3场景4轨迹全过，合计28/28。原P2两例分别精确30/12与20/8且incomplete=true、无双计；前情子usage与总量/落库同步，后续成功不清未知。原95项93/2及修复轮25项历史均保留。
- 新证据P2用量聚合-推送复审证据-20261104/；70文件当前源码、source与三套实际workspace全SHA一致，快照b7255c901ccf4202035095cc0228d5affc0de5035c6fc156440375359f35e18a；原修复93份归档哈希一致。改动2Python AST/内存编译及diff检查通过，guard为空；未重跑无关Windows/崩溃套件。模型/人格装载/等待为替身，前情回答审查分支与字典alias仅静态核对，不夸大动态覆盖。
- 本轮仅审查、临时隔离诊断、报告/当前与Git交付，未另外改业务代码，未读写真实user/、启停Alice、调用真实模型/Claude/DSH/QQ或迁移安装。真实部署共存/模型回执/工人许可/GUI/长期体验仍待人工，W5未改通过；W6—W9/前情退休为设计，D1—D5/蓝图待裁决、P1b默认关。原私有资料/头像素材不纳入提交，备份/回退分支/stash/其他worktree保留，仍HTTPS、trial经PR合main。
- 并行头像：V14 C2仍为当前首选（face-variants-v14/c2-clear-taper-v14.png，preferred.json已记录）；新附件0cf0c346与C2字节一致，用户要求在其基础上再收下巴。pixel_avatar_design以内置image_gen单次编辑C2＋原画，生成轻收底部圆宽的V15：docs/assets/avatar-concepts/orange-flower-20261104/chin-trial-v15/c2-chin-taper-v15.png及prompts.txt，1254×1254透明RGBA，已看图/核alpha/无损保存。底弧略集中、变化轻，脸长/表情/头发/抱书总体保持，细纹有生成漂移；V15待用户确认，未覆盖首选。后续始终带原画作结构/气质参照，保留已认可头发/眼眉/嘴倾角，小图v5冻结，V13双基线与V14四候选保留。未改运行时头像/源码/UI/user/、运行应用测试或提交推送。
