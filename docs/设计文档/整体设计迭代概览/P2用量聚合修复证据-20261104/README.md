# P2 用量聚合修复：定向隔离证据（2026-11-04）

本包按用户对既有隔离诊断的修复/复跑授权执行，不扩为CI或常规机器自测。业务代码由主代理修改，本包只含诊断脚本及隔离结果。原`隔离检测证据-20261104`的95项/93通过/2失败报告保持不变。

**最终新增定向证据：25个独立case通过，0失败，保护违规0。** 其中`final-run`为继承observability 10项＋新增14项；`delta-run`只执行新增的零/非法字段矩阵1项（5变体），没有再跑已经通过的24项。初次`initial-run`为23通过1失败，失败来自诊断脚本误以为预算拒绝抛异常；实际既有行为返回并持久化受控提示。修正后的断言仍要求0 provider调用、0逐调用事件、aggregate与context_basis.usage均为`{}`，没有删减业务完整性要求。

## 结果与来源

- `summary.json`：25个独立case汇总、历史初次运行解释、已归档子脚本SHA核对。
- `initial-run/`：初次输出和实际执行脚本，保留harness误假设失败，不能算生产缺陷或重复计入最终数量。
- `final-run/`：两套最终24项的运行前源码/脚本manifest、实际执行脚本、逐项结果、facts、简要stdout/stderr。
- `delta-run/`：仅`zero_invalid_usage_matrix`定向补验及5变体；同样保留实际执行脚本/manifest和结果。
- 每个现场的`source-verification.json`：运行后对生产白名单、复制source及实际运行workspace重新核验每组70文件，全部无差异。没有归档这些源码副本或临时SQLite。

三个现场的源码快照相同：`b7255c901ccf4202035095cc0228d5affc0de5035c6fc156440375359f35e18a`。与历史70文件manifest相比仅有两个业务文件变化：

| 文件 | 本次SHA256 |
|---|---|
| alice/llm.py | a8eb80d01430a2ccebe777e005e85e282a6ac0663f4202b3e52d2f083b2196a5 |
| alice/loop.py | c48c4283e4b051b66bc9b6f97b2f3e61ca05b3b72b44fbf87eeda4d6b4316abb |

原两例的复跑及独立精确对照结果：

| 路径 | aggregate与持久化context_basis.usage |
|---|---|
| 已报流式10/4后中断，再非流20/8成功 | `{"prompt":30,"completion":12,"incomplete":true}` |
| 两次unknown失败attempt后非流20/8成功 | `{"prompt":20,"completion":8,"incomplete":true}` |
| 完整成功0/0 | `{"prompt":0,"completion":0}`，不带incomplete |
| bool或负数单字段 | 只累计另一个合法整数字段；`incomplete=true` |

新增检查还覆盖：完整非流和流式正控、流式多次累计回执只计末次、流式partial后fallback自身重试、非流单字段及完全未知、成功流末尾没有最终usage、全失败和取消的外部aggregate保留未知、retry=False改写审查失败后whole/子usage持久化、调用前预算拒绝、on_attempt_usage回调错误不改原调用数/取消，以及真实临时SQLite观测INSERT失败恢复不重发模型。

## 隔离与保护

`run_observability.py`仅按历史manifest中的70个明确路径复制`alice/`、`web/`、`addons/claude_agent/`及`config.example.toml`，拒绝任何user路径、绝对/上级路径、目录或文件symlink/reparse以及规范路径不一致；不会复制真实配置、对话或凭据。相较旧replay，这还阻止白名单文件被链接到仓库内部真实user目录后先被复制。

全部应用导入从新的系统临时副本发生。子检测在导入应用前安装继承的Python审计保护：禁止活仓库读写/遍历、probe外写入/SQLite、socket与子进程；新临时guard把rename/link/symlink源也按写入保护，避免移动外部文件或硬链接后写穿。只调整临时guard，不改历史包。保护是诊断运行约束，并非面向不可信恶意Python的OS沙箱。检测仅使用固定受控Python助手，不启动真实Alice服务/模型/CLI工人/QQ，不安装依赖。

所有provider回执/错误和人格loader均为合成；真实执行的是复制源码中的调用控制流、SQLite账本/上下文记录和公开观察投影。它证明用量聚合与保护控制流，不证明供应商实际计费、真实流式协议、Windows Job/工人回收或人工体验。未触碰真实user/。

## 重跑入口

Windows及本机已有Python 3.11+；默认仅准备新的隔离副本，不执行任何case：

```powershell
python -I -S -B 'D:/alice/docs/设计文档/整体设计迭代概览/P2用量聚合修复证据-20261104/run_observability.py' --repo D:/alice --allow-changed-source --prepare-only
```

后续明确授权的完整定向重跑（当前脚本是10＋15＝25项；本次实际最终证据来自24项和1项delta两次，不伪称完整25项单次执行）：

```powershell
python -I -S -B 'D:/alice/docs/设计文档/整体设计迭代概览/P2用量聚合修复证据-20261104/run_observability.py' --repo D:/alice --allow-changed-source --run
```

仅零/非法字段矩阵的固定定向入口：

```powershell
python -I -S -B 'D:/alice/docs/设计文档/整体设计迭代概览/P2用量聚合修复证据-20261104/run_observability.py' --repo D:/alice --allow-changed-source --run --only-case zero_invalid_usage_matrix
```

每次生成全新系统临时目录，输出实际路径和源码差异。`--allow-changed-source`是有意检查新版本的显式标记，不能省略差异记录。历史包内总replay默认会运行全95项及进程中断场景，本次没有调用它，也未重复运行已绿范围以扩大证明。
