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
- `data/` 是 Alice 的记忆（对话/记忆/人格），不入 git；动过就在「当前」写一句。
- `config.toml` 不入 git；服务只监听回环地址。
- 不碰 `.zcodeignore`、`~/.zcode/`。
- 四工作区（核心 `alice/`+`web/` / 测试 `tests/` / 附加 `addons/` / 资料 `docs/`、`tools/`）依赖单向指向核心；测试与 addons 只走公共面（`service.ChatService`、HTTP API、CLI），不 import `alice` 内部。详见 `docs/工作区约定.md`。
- 提交只暂存自己碰的路径，不用 `git add -A`（工作区常有另一棒的半成品）。

## 当前

- 2026-10-28 ZCode：**v0.2 全部实施并入库（b40ac06 已推送）**：P0 思想层/宪法v2、P1 表达层（言语行为标记+情绪词表+GUI 渲染）、P2 修订门控（无据拒绝/曾以为史）与压力卫生（pressure 标记/晋升闸门/思辨演化隔离）。验收挂 P4/P7/P5，等 DSH 人工压力轮。
- 工作区余量（未入库，归属见名）：`人格提示词/`（用户私人物料）、`docs/harness研究/插件参考/`（外部项目 clone，含非 Cortico 两个，未 gitignore）、`docs/harness研究/待动工模块/设计-监测仪v2.md`。
- 新：**GUI 三件需求已立项**（[docs/需求文档-GUI功能三件.md](./需求文档-GUI功能三件.md)：退出出口/移除检测仪/GUI 改接口配置），功能侧开发者接手——动的是核心 `alice/`+`web/`，全局约束写在需求文档第 0 节，实现疑问找 ZCode。
- data/：主库今晨 09:06 重置（旧 22 轮/14 记忆在 data/archive/20261028-090645/），现装有标签种子人格（**无情绪词表节——重新种底色或重新导入才会带上**）、0 轮对话；Yuki 截图对话不在主库也不在 dist（dist 库为空）。
- 雷：config.toml API key 仍过期（chat 401）——P1 的真机表现（P5）待 key 恢复后人工验；本机无 `timeout` 命令、无 Chrome；机上另有多个 alice serve 进程（8877/8878/8800 等），归属未查。
