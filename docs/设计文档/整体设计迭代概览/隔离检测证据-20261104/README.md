# 2026-11-04 隔离检测证据包

本包保存用户本轮明确授权的临时诊断脚本和实际输出，不安装测试框架，不加入 CI，不修改应用代码。最终结果为 **95 场景：93 通过，2 失败**。两项失败属于一个 P2：重试／流式中断的未知或部分用量没有传播到聚合完整性。

主报告：[隔离自动检测与人工交接](D:/alice/docs/设计文档/整体设计迭代概览/Alice架构完善-隔离自动检测与人工交接-20261104.md)。计数与源码复核见同目录 `summary.json`。

## 文件

- `source-manifest.json`：原检测复制的70个文件及SHA256。不包含真实配置或数据。
- `check_support.py`：复制工作区、临时目录写入限制及Python审计保护。
- `permissions/`、`windows/`、`recovery/`、`persona/`、`history/`、`observability/`：实际运行脚本和最终`results.json`；recovery另有10次真实进程中断的`crash_results.json`。
- `fixture-evidence/`：56份合成现场的细节JSON。未打包临时数据库或完整复制源码。
- `static-results.json`、`javascript-results.json`：67个Python语法／内存编译、TOML及2个JS的语法结果。
- `observability/agent-valid-results.json`：独立复跑前的一次有效结果；最新`results.json`是主代理复跑结果，均8通过2失败。
- `windows/harness-initial-*.json`：已排除的检测保护规则问题记录；`first-valid-results-with-duplicate-cleanup.json`是改进重复清理前的有效运行，不重复计数。
- `permissions/before-cross-task-coverage-results.json`：补上实际跨任务轮询前的输出；以最新`results.json`及修正后脚本为准。

JSON内原始临时绝对路径用于追溯首次现场，可能随系统清理失效。归档结果及本包重跑入口不依赖那个临时目录仍存在。

## 重跑

Windows、Python 3.11+；JS语法检查另需本机已有Node。运行从明确清单复制源码，拒绝复制真实`user/`；在新的系统临时目录运行，受控助手最长40秒，不启动真实Alice服务、模型或CLI工人。不会安装包、修改业务代码或清理旧数据。

```powershell
python -I -S -B 'D:/alice/docs/设计文档/整体设计迭代概览/隔离检测证据-20261104/replay.py' --repo D:/alice
```

默认要求源码与本次70文件快照一致。当前版本应在两个已知缺陷处失败，并以退出码1结束，不能为了全绿删掉断言。修复后有意检查新版本时使用：

```powershell
python -I -S -B 'D:/alice/docs/设计文档/整体设计迭代概览/隔离检测证据-20261104/replay.py' --repo D:/alice --allow-changed-source
```

新目录中的`source-differences.json`保留快照差异，`source-manifest.json`记录实际新版本，`replay-commands.json`记录各阶段退出码。`--prepare-only`只创建隔离副本和脚本，不运行场景。报告交付时已验证该准备流程；各检测脚本已按报告分别实际运行，归档后的完整总入口没有再重复跑全部95项。

`recovery/run_crashes.ps1`保留首次执行方式及解释器路径；总入口使用当前Python解释器运行同一crash helper，不依赖首次机器上的Anaconda路径。每次运行都产生全新现场；已存在的某些单套件fixture目录会拒绝复用，因此优先用总入口准备新目录。

这些脚本含明确的内部契约探针和边界注入，作用于源码副本。它们不是应用公共接口、人工测试SDK或真实供应商兼容性证明；不会改变`tests/`与`addons/`的依赖约定。
