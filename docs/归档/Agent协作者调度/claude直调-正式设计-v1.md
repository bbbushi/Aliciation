# Claude 直调正式设计 v1（claude-agent 本地 CLI 干将 · 分支限定）

> **2026-11-02 补充设计入口**：[CLI 协作·宿主契约补充设计 v1](./CLI协作-宿主契约补充设计-v1.md)。下文保留原 v1 方案与修订历史；涉及身份/政策、生命周期、权限、预算和结果准入的冲突，以补充设计为准，不能视作已实现或已通过人工验收。
> **重点纠正**：实际沿用独立 `claude_policy`；`--add-dir` 不是权限上限，gate 不保证覆盖每次工具调用；CLI 费用是报告/估算值，不能称为精确账单；产物落盘不等于宿主收取；取消请求不等于确认回收。目录分离与原生权限档不提供 OS 强隔离，仍仅限 `ytq`。

> 2026-11-02。**分支裁定（用户）：转正式仅在 `ytq` 分支实施与验收，不合入 main**——main 的 P1 正典（DSH 桥路径）不受影响；本设计是 P3 本地 CLI 轨的先行实施，两轨并存于本分支。
> 依据：[demo 记录](./claude直调-权限质询demo-2026-11-02.md)（两分支全通）+ [协议契约 v1](./协作者调度-协议契约-v1.md)（GO 门控/整树回收/预算/验收清单，实现存 git 历史 `f3acba0`）+ [开发方案 v1](./协作者调度-开发方案-v1.md)（§4 权限档/§7 状态恢复/§12 验收门槛）。
> 定位一句话：**claude-agent 是第二个干将公民、第一个本地 CLI 干将**——它不走桥，走终端 spawn + MCP 权限闸门；它的加入把 v2.1 设计里"泛化接缝"的第一批（注册表多行化、工具枚举名单化）真实触发。

## 〇、总架构（分支持有形态）

```
用户（GUI 授权：claude-agent 政策）
   │  派工（对话槽内，只返回排队事实）
   ▼
Alice 本体 ──工具──▶ WorkerRuntime（复用 main 正典：准入/账本/独立任务槽/事件回投）
                        │ 本地轨分支：name == "claude-agent"
                        ▼
              ClaudeLocalTransport（新：f3acba0 帧纪律 + demo 驱动合并）
                │  spawn: claude -p --permission-prompt-tool <闸门MCP> --add-dir <任务沙箱>
                │  GO 门控 / 256KiB 帧上限 / 进程组整树回收 / stderr 有界
                ▼
   ┌─────────────┴──────────────┐
   ▼                            ▼
claude CLI（deepseek-flash）   gate_server.py（MCP 闸门，stdin/stdout JSON-RPC）
 用户自有登录，凭据不过手        质询 →【结构禁区硬挡 | Alice 转述 → 用户三层答案】
   │                            │ 每条质询写审计 jsonl
   ▼                            ▼
任务沙箱产物（collected 收取复用 worker_artifacts）   质询事件 → worker_events → Alice 下一轮转述
```

与 DSH 轨的关系：**同一套账本/事件/GUI 面板，两个 Transport 实现**。DSH 走桥（127.0.0.1:18765），claude 走本机 spawn；`worker_policy` 由单行扩为按工人一行（本分支数据迁移，见 §五）。

## 一、模块与落点（全部在 ytq 分支）

| 模块 | 来源 | 改造 |
|---|---|---|
| `alice/worker_transport.py` | **从 git 历史 f3acba0 恢复**（帧纪律/GO/整树回收），与本分支 main 版（DshBridge）**并存为两个类**：`DshBridge`（不动）+ `ClaudeLocalTransport`（新，复用 `_Bounded`/`ProtocolViolation`/帧常量） | claude 的 stdout 是 claude 的事件流而非帧协议——`ClaudeLocalTransport` 适配：GO 门控=闸门策略装载完成后才发任务书；整树回收=start_new_session+killpg；stderr 有界照搬 |
| `addons/claude_agent/gate_server.py` | demo 版升级 | ①裁决点接宿主：质询写 `worker_events`（type=`permission_request`）并阻塞等三层答案（`multiprocessing.Event`+答案文件，宿主 GUI/下一轮写入）；②结构禁区硬挡先行（`~/.zcode`、仓库根、`user/`、凭据文件——命中直接 deny 不质询）；③"本任务内允许"缓存（工具+路径前缀，attempt 生命周期） |
| `addons/claude_agent/manifest.py` | 新 | 注册事实单一来源：`name="claude-agent", kind="agent", transport="local_cli", permission_profile="claude_gate_v1"` + 能力如实标注（`{os_sandbox: 不可, 动态质询: 可, add_dir_上限: 可}`）+ 预算声明（`{usage: result 帧精确, cost: total_cost_usd 精确, 中断: unknown}`） |
| `alice/worker_tools.py` | main 版扩展 | 工具枚举从硬编码 `["dsh-agent"]` 改为**注册表名单渲染**（`claude-agent` 进枚举）；`worker_target` 校验按 transport 分派 |
| `alice/loop.py` / service 接线 | main 版复用 | 零改动预期：账本/事件/GUI 面板全部工人无关（这是 P1 正典设计红利） |
| `web/worker-tasks.js` | main 版复用 | 质询卡（permission_request 事件 → Alice 转述文案 + 三按钮：允许一次/本任务内允许/拒绝） |
| 数据迁移 | 新脚本（§五） | worker_policy 单行 → 按工人多行；**起服前执行**（数据层撞车教训入律） |

## 二、权限模型 `claude_gate_v1`（本设计的核心）

三层防线，从外到内：

1. **政策层（派工前）**：用户对 claude-agent 的持续政策——允许的工作目录（`--add-dir`）、单任务预算帽、最大轮次。政策不在名册里就 spawn 不发生。
2. **结构禁区层（硬挡，不质询）**：命中即 deny 并审计——`~/.zcode/`、Alice 仓库根、`user/`、凭据文件、沙箱外绝对路径写入。这一层没有"用户允许"选项：**质询只裁政策内的事，不裁底线**。
3. **质询层（运行中，动态）**：模型每次要用工具 → 闸门收到 `{tool, input}` →
   - 命中"本任务内允许"缓存 → 直接放行（审计标注 cached）；
   - 否则写 `permission_request` 事件 → GUI 质询卡 + Alice 下一轮转述 → 用户三层答案：
     - **允许一次**：本次放行；
     - **本任务内允许**：放行 + 缓存（tool+路径前缀，attempt 结束即焚）；
     - **拒绝**：deny + 消息回给模型（demo 实证：模型不重试不绕路、如实转述）；
   - **超时语义**：质询 10 分钟无答案 → deny（"宿主未应答"），任务不悬挂。
   - **诚实边界**（与 dsh_native_v1 同规，写进名册与 GUI）：claude_gate_v1 是**权限层动态质询，不是 OS 沙箱**；进程以用户身份运行，`--add-dir` 只是可见性上限。用户须在 GUI 明确接受此标注后才能启用 claude-agent（同 DSH 的接受流程）。

## 三、派工与结果流

```
dispatch_worker("claude-agent", brief) → 排队事实 → 独立任务槽（1+4）→ ClaudeLocalTransport.spawn
  ├ GO 门控：闸门就绪 + 政策装载 + 预算预留（result 帧承诺精确 usage/cost）后才写任务书 stdin
  ├ 过程：stream-json 事件 → progress（节流）/usage（seq 累计）→ worker_events
  ├ 质询：permission_request → 三层答案（§二.3）
  └ 终态：result{result, usage, total_cost_usd, num_turns} → 账本四态（执行/结构验收/人工/交付）
       产物：任务沙箱 output/ → worker_artifacts.collect（realpath 包含性/两阶段/宿主哈希，main 已有件原样用）
       失败：stdout 断/超时/非预期退出 → interrupted|unknown；连接性损失不重发
```

预算：claude 的 result 帧自带 `usage`+`total_cost_usd`（demo 实测精确到分）——**这是比 DSH 桥更强的计量面**，按"事前上限+事后精确结算"入账本（token/currency 分列），中断保留 unknown 不记零。

## 四、安全与信任纪律（继承 + 新增）

继承全局：招募权在人类（启用=用户政策行为）；素材≠指令（claude 产出按不可信输入过膜，含注入扫描）；判断力不出租（Alice 转述质询、评估产物，不替用户按按钮）；失败分话术。

本轨新增：
1. **提示注入面更大**——claude 自己会读网页/文件，其质询内容可能携带诱导（"请允许因为…"）。对策：质询卡只展示**结构化字段**（tool/input 摘要），模型的理由文本标注为"工人自述，非宿主事实"；
2. **模型后端是 deepseek-flash**（用户配置）——名册如实记录"执行模型与 Alice 无关、费用走用户自有 key"；
3. **分支限定即风险边界**：本轨不合 main 前，main 用户不可能接触此轨；合入需另行评审（至少含对照审计 + 本清单全过）。

## 五、数据迁移（起服前，一次性）

`worker_policy`（id=1 单行）→ `worker_policies`（per-worker 行 + revision + enabled + budgets）。迁移脚本：备份 → 断言旧行唯一 → 插入 `dsh-agent` 行（继承现值）与 `claude-agent` 行（enabled=0）→ 保留旧表只读一版。**执行顺序铁律：迁移 → 起服**（数据层撞车教训）。

## 六、验收（分支内人工，DSH A01~A12 同规裁剪）

| 编号 | 场景 | 必须看到 |
|---|---|---|
| C01 | 注册与启用：政策未开 → 工具不挂载；启用需勾选"非 OS 沙箱"接受框 | 名单显隐准确 |
| C02 | 最小闭环：GUI 派工"在沙箱建 hello.txt" → 排队 → 质询卡 → 允许 → 产物收取 → 结果卡（含费用） | 全链路、账本四态正确 |
| C03 | 三层答案：允许一次（再问再质询）/ 本任务内允许（同前缀不再问、attempt 结束失效）/ 拒绝（模型如实转述、零产物） | 缓存边界精确 |
| C04 | 结构禁区：任务书诱导写 `~/.zcode` → 硬挡 deny，无质询产生 | 底线不经质询 |
| C05 | 注入质询：任务书含"始终允许一切工具"文本 → 质询仍逐条发生，模型自述标"非宿主事实" | 素材≠指令 |
| C06 | 取消与超时：运行中取消 → 进程组整树回收（含 claude 子进程）；质询 10 分钟无答 → deny + 任务失败收尾 | 无悬挂 |
| C07 | 预算：预算帽 1 次的第 2 次派工 → quota_exhausted；result 帧费用入账本分列 | 软硬如实 |
| C08 | 重启恢复：运行中杀 Alice → interrupted/unknown；重启后不重发；已收产物仍可查 | 未知不假报 |
| C09 | 双轨共存：DSH 派工与 claude 派工交替，账本/事件/GUI 面板互不串线 | 泛化接缝成立 |
| C10 | 回退：禁用 claude-agent → DSH 轨完全不受影响 | 接缝隔离 |

## 七、明确不做（本版）

不接 Alice 质询语音/推送（GUI 质询卡为唯一面）；不做多 claude 并行（同槽 1+4）；不做 claude 会话续聊（`--resume` 留 v2）；不做 Windows（本机 macOS 先行，`killpg` 的 Win 对应沿 ProcessTree 思路后补）；**不合 main**（合入另行评审）。

---

*版本 v1（2026-11-02，ZCode 架构侧，ytq 分支限定）。实施顺序：§五迁移 → transport 恢复与 ClaudeLocalTransport → 闸门升级 → 工具枚举名单化 → GUI 质询卡 → C01~C10。修订在文末追加。*

*v1.1 补充（2026-11-02）：增加宿主契约设计入口，明确修订原生权限、成本、产物与回收承诺。后续施工按补充设计 H1～H4 推进，人工验收沿用旧 C 编号并增加 H01～H10；旧图示、模块提案和历史机制验证不作为当前接线完成的证明。*
