# P2修复独立复审证据

2026-11-04独立复审：28个场景全部通过，未发现阻挡本次个人分支交付的新问题。

本包和原修复证据包各用局部`.gitattributes`关闭Git换行转换，保留执行时原始字节，避免Windows检出改变证据SHA。原93文件内容及manifest保持原样；新增属性文件属于交付元数据。

- `summary.json`：新临时现场完整复跑原观测10项＋补充用量15项，25／25；含实际案例与子命令退出码。
- `recap_usage/results.json`：新增前情生成／审查3个场景、4个变体轨迹，3／3。
- `verification.json`：当前工作区、source和三套实际工作区的70文件复核；原修复包93文件哈希核验；28项总计。无源码漂移，无保护违规。
- `source-manifest.json`、`script-manifest.json`及实际脚本：用于绑定检测版本和回放；`source-differences.json`记录相对历史版本只变更llm.py及loop.py。
- stdout/stderr为完整25项复跑的子命令输出；不包含真实用户数据库或模型凭据。JSON保留本机绝对路径用于追溯现场。

完整25项重跑：

```powershell
python -I -S -B 'D:/alice/docs/设计文档/整体设计迭代概览/P2用量聚合修复证据-20261104/run_observability.py' --repo D:/alice --allow-changed-source --run
```

要复跑追加3项，先用上面的入口准备一个新临时目录，再将本包的`recap_usage/check_recap_usage.py`复制到该目录的同名子目录，执行：

```powershell
python -I -S -B '<新临时目录>/recap_usage/check_recap_usage.py'
```

本包归档的脚本依赖新临时目录中的`source/`和`check_support.py`，不可直接在证据目录中运行。检查实际案例数、失败数、退出码与guard，并在运行后按manifest核源码；不能把“没有报错”单独当作通过。

供应商、人格loader和退避为替身，SQLite与主循环／前情路径真实执行。未启动应用或真实工人；不代表真人验收或main合并放行。详见[复审报告](D:/alice/docs/设计文档/整体设计迭代概览/Alice架构完善-P2修复推送前复审-20261104.md)。
