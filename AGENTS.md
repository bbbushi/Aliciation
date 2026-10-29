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
- 四工作区（核心 `alice/`+`web/` / 测试 `tests/` / 附加 `addons/` / 资料 `docs/`、`tools/`）依赖单向指向核心；测试与 addons 只走公共面（`service.ChatService`、HTTP API、CLI），不 import `alice` 内部。详见 `docs/工作区约定.md`。
- 提交只暂存自己碰的路径，不用 `git add -A`（工作区常有另一棒的半成品）。

## 当前

- 2026-10-29 ZCode：**user/ 迁移（未提交）**——config.toml 与 data/ 全部挪进 `user/`（整目录 gitignored）；`alice/config.py`（USER_DIR/默认加载）与 cli 提示、README、config.example、压力规范已同步。**DSH 适配点（未代改，tests 是你的区）**：`tests/pressure/launch.py:69` 读 `PROJECT/config.toml`；`deploy.py:146` 复制 config 到会话根、`221/237/250` 读会话 `data/`——新会话布局应为 `user/config.toml`+`user/data/`。
- v0.3 已提交（`6bbb3c5`）；文档树已重组（用户）：`docs/`＝工作约束/测试文档/设计文档三分类，研究材料与历史版本进 `wiki/`——旧路径的增删未提交，.gitignore 的 Cortico 规则已跟着改到 wiki 路径。
- **护栏测试态（勿提交）**：`alice/retrieval.py` 工作区宪法暂回 v0.1 供基线 A/B；恢复＝`git restore alice/retrieval.py`＋重启。基线结论：两向失真（表演性反抗/平静），根因空脊柱；退位复测前提＝真思辨先跑通一次（v0.3 验收第 7 条）。
- 环境：ytq 分支（main 已分叉：PR#1 合并+复盘文档 `修改建议.md`，合并时注意）；主库 11 轮对话在 `user/data/`；服务 8800 以新路径重启；API key 有效。
- 雷：本机无 `timeout`、无 Chrome；外部项目 clone 在 `wiki/harness研究/插件参考/`、`人格提示词/`、`待动工模块/` 均未入库。
