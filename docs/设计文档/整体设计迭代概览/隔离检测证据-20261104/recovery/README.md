# 人格安装与 reset 隔离恢复检测

本次共 **34 个场景通过，0 个失败**：24 个真实 SQLite／临时文件与边界异常场景，10 个真正退出自有 Python 进程后重开恢复的场景。没有发现这些被执行场景中的产品缺陷。

所有产品模块来自 `../source` 复制出的独立工作区，解释器使用 `-I -S -B`，加载前安装 `check_support.protect()`；禁止网络、访问 `D:/alice` 和向检测根目录外写入。每场景使用独立新数据目录。进程中断仅由本脚本控制的 Python helper 执行 `os._exit(86)`，未启动 Alice 服务、任何模型／CLI 工人／QQ，未读取或修改真实 `user/`。检测未修改业务仓库。

## 可重跑入口

从检测根目录运行，并为每次执行提供未使用的后缀：

```powershell
D:/Anaconda/python.exe -I -S -B ./recovery/run_recovery.py run2
& ./recovery/run_crashes.ps1 -RunSuffix run2
```

脚本拒绝复用已有 fixture 目录。两个汇总文件会记录最新一轮，同一轮的各 fixture 保留具体证据文件。`run_crashes.ps1` 只启动本目录的 `crash_helper.py`，检查注入进程退出码必须为 86，再以全新解释器执行恢复核对。

## 已执行结果

### `results.json`：24／24

- 首次安装、替换安装、重复恢复及同 op_id 重放拒绝。
- 使用第二个真实 SQLite 连接，在写入 marker 后、commit 前观察：新先验和 marker 均不可见；当前事务内先验完整可见。提交后同时可见。
- 第一条先验 INSERT 后、marker 写入前异常：没有半批先验、旧文件和旧元信息保持、恢复幂等。
- 文件替换前、db_committed/file_replaced/completed 日志边界异常：前文件阶段回滚；已有新文件与同事务 marker 时按已提交收尾，不诱导重新安装。
- 留下真实中断现场后关闭并重新打开 Store：完成／回滚幂等，不覆盖后来人工编辑；跨 epoch 或已修改先验拒绝并保留现场。
- 外层事务存在时拒绝安装，不提交或回滚调用者自己的事务。
- reset 首文件已移的回滚、另一侧后来编辑的阻断、旧归档改变的阻断。
- reset 归档后前向安装、空重置、错误新库 epoch、已有失败安装禁止盲重放、已完成安装但未 reset 收尾的恢复。

### `crash_results.json`：10／10

下列阶段触发真正的 `os._exit(86)`，没有执行 `finally`／`Store.close()`／安装函数的异常恢复；随后由全新解释器打开同一 SQLite／文件现场并调用产品恢复函数：

1. prepared 文件日志写完，数据库事务尚未开始。
2. 第一条真实 INSERT 之后、事务尚未提交。
3. 同事务 marker 写入后、COMMIT 之前。
4. COMMIT 已成功、db_committed 文件日志尚未写入。
5. persona.md 已原子替换、file_replaced 日志尚未写入。
6. file_replaced 日志已写、completed 尚未写。
7. reset 仅移动第一个真实数据文件。
8. reset 完成旧数据归档，尚未打开新库。
9. reset 新库已创建，尚未写入 new_epoch。
10. reset 新人格安装已提交，reset 日志尚未完成。

`workspace_first/fixtures/*/evidence.json` 保存第一组场景证据；`hard_first_*/crash_evidence.json` 与 `verification.json` 保存第二组崩溃前／重开后证据。所有汇总及现场的 `blocked_attempts` 均为空。

## 证据边界

这证明了当前复制快照在所执行的临时环境／故障边界下满足断言，不是断电持久性证明，不覆盖真实磁盘损坏、杀毒软件锁定、任意并发外部编辑、供应商行为、生产迁移和完整 ChatService 启动。reset 使用真实产品 begin/before_open/after_open/commit_import_plan 函数及真实归档文件移动，未实例化带外部子系统的 ChatService。阶段异常的注入只替换 I/O 或事务边界，未用测试代码重写恢复算法。

`run_recovery.py` 的 interruption 场景暂时阻止内联恢复以保留现场；因此单列为异常边界模拟。真正进程中断证据只来自独立的 `crash_helper.py` 组。真人验收及合并放行要求仍需另外满足。
