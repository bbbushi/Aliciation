# AGENTS.md

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
- **能力增减须同步能力层JSON注册表与必要自我事实**（`alice/capabilities/commands.json`、`alice/constitution.json`）；注册表与SDK工具同源，宪法不重复工具目录或用户操作教程——自模型与实际能力须一致（2026-11-11用户确认的新组织方式）。
- 提交只暂存自己碰的路径，不用 `git add -A`（工作区常有另一棒的半成品）。
- **分支流程（2026-11-22 用户裁定）**：AGENTS.md 不写具体分支名——改动**推当前所在分支**（分支归属随协作轮换，以工作区实际检出为准）。验收后走 **PR 合入 main**（不再本地 merge 推 main）；**main 不接受直接 push**。合并前跑共存检查：`compileall` + 起服验证 + grep 对方分支可能触碰的路径（语义冲突 git 报不出来，三处旧路径 bug 的教训）。当前 origin 使用 HTTPS。

## 当前

- 2026-11-22 合并 main 139f2e7：Theresa 的 27 项源码修复、7 项复审补修与 Orbit 回归修正入线（结论见 docs/代码审查/Alice源码修复总体报告-20261121.md）；TUI Editor 加固（SS3/PgUp/PgDn）与可滚动来源到期历史视图并入视觉二稿。同期 62e1109 落线 TUI 视觉二稿与独立启动两阶段（按需起宿主、alice stop、桌面附接共享宿主）；79a71b2 代入库 0.1.39 弹层与版本。
- 11-22 桌面 0.1.39 隔离构建已安装 /Applications/Alice.app（旧 0.1.37 备份于 /Applications/.alice-backup-3w85s50v）；0.1.38 修复导入"分类失败"假警报（SSE 流式+心跳）。待真人验收：导入全程阶段文字、弹层解锁、桌面真实交互。
- 遗留：updates.rs 的 shared 拦截被隔离构建还原需重打；旧 Brave 密钥须重录；F05 旧往返补提取、F06 历史遮罩原话恢复待裁定；恢复备份 .git/pr-prep/20261122-184055（按 manifest.stored_path）。
- 运行时：源码档案宿主运行中（`alice` 按需启动链路实测通过）；SIGINT 优雅退出挂起待复测（stop 与 Ctrl+C 同排空链）。待真人：TUI §3.6 视觉、独立启动 §7 场景表（两入口并发、桌面关窗留存与「停止 Alice」、共享模式维护指引）、向导、投影环、自主调度与来源过期联动、QQ 与 macOS/Linux 终端。F01–F19 未修。
- 11-22 ZCode：删除「简洁·专注对话」主题（用户裁定）——外观选项、style.css 整块（含 ui-window 弹窗皮肤）、app.js Progressive Disclosure 模块（AlicePanels，仅 minimal 挂载）与 normalize 候选移除；主题剩 room/legacy/orbit，已存 minimal 偏好经 normalize 自动回落 room；thoughts.js 原有 else 内联展开兜底不受影响。真机回归：选项三枚、三主题往返、偏好迁移、零报错。
- 11-22 ZCode：思想与探索改版（用户裁定）——去掉设为清醒/进入睡眠按钮改为主开关（映射 wake_state，关=睡眠待机仍回应消息）；探索与联系设置改 6 步向导（按旧表单顺序：探索权限/额度时段/活动工具/主动联系/联系次数时段/休息推演）；首启判定=autonomy.py 快照新增 settings_archived（autonomy_settings 键首次保存前不存在即无留档），无留档拨开关走向导、有留档直接按档案开启、更改设置入口重开编辑态向导。旧单页表单与脏草稿机制移除；desktop_host/tests/lab/autonomy_v2/TUI 零改动兼容。验证：隔离实例（user 副本 8819，删留档键）走完首启/直启/编辑/放弃/409/重载/环宿主全流程零报错，真实服务只读冒烟过；过程中修两处自伤（向导骨架未注入致模块初始化中断、快照派生键回传被校验拒绝）。重启前 user/ 备份 /private/tmp/alice-before-thought-wizard-20261122。见 docs/设计文档/自主探索与主动交流/思想与探索-主开关与分步设置向导-实施与人工验收-20261122.md。同日追加：主开关改滑动 toggle（checkbox appearance:none 即轨道 + ::after 滑块，纯 CSS 无 JS 改动；选择器升为双 id 前缀压过 style.css 的 #settings-modal #thought-settings checkbox 皮肤）。再追加排版重构：主开关区改独立状态卡（开关+休息/更改设置一行、lifecycle 独立小字行，thoughts.js 去掉轮询对状态行的周期性覆盖，status 空时隐藏）。
