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

- 2026-10-29 ZCode：**护栏正式退役（用户决定，未提交）**——宪法删「观点与被冲击时」整节（先停一拍/守住或有据修订/权重只给论据/讨好不是回应，A/B 证明会表演化）；保留语义契约（三分法+四分定义+记忆区读法）与 P1 表达标记。依据设计哲学·机制优先于条款；退位复测（v0.3 验收第 7 条）待真思辨跑通后做。
- **合并遗留的旧配置路径已修**：service.py:72 / server.py:441 / cli.py:105 三处默认值改指 `USER_DIR/config.toml`（原后果：GUI 在线改配置写错文件、CLI 无参启动失败）；旧路径引用全库清零，起服验证通过。
- AgentMerge 已并入 origin/main 的 GUI 三件+观察器+复盘文档（docs/项目问题清单与分层整改建议.md）；ytq 与 main 内容已汇合（本地 main = 合并结果）。
- 待人工验收：观察器、GUI 三件（docs 两份验收单）；DSH 启动器待适配 user/ 布局（launch.py:69、deploy.py:146/221/237/250）。
- 雷：服务当前停着；主库 11 轮在 user/data/；本机无 timeout/Chrome。
