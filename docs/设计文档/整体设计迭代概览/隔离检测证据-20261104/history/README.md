# C0／C3 隔离行为检测

`run_history.py first` 已执行，**12 个语义场景通过，0 个失败**。其中完整后缀性质矩阵包含多组条数上限及实际计量临界预算，详细点位在 `results.json` 的 `suffix_property_matrix` 中。

执行命令：

```powershell
D:/Anaconda/python.exe -I -S -B ./history/run_history.py UNIQUE_SUFFIX
```

每次使用未用过的后缀，工作区复制自检测根的 `source/`，业务导入前安装 `check_support.protect()`，无网络或子进程。每场景独立真实 SQLite，使用产品的 channel／worker schema；合成请求回执和隔离标记仅是 fixture，不代表真实渠道已执行。没有启动 ChatService／HTTP／模型或访问真实数据。

已执行：

1. 真实 `retrieval.build_messages` 选取 N=16、1—32 完成往返＋当前33，保留19—33；HistoryContext／RecapContext使用同一boundary对象，17／18真实搜索并完整加载。
2. 后缀性质矩阵：当前用户原文完整、无孤立assistant、有序连续后缀、预算单调性、保留与排除原因无重叠且全集一致；包括实际消息计量边界±1。
3. 紧预算实际装配仅保留当前33，1—32完整旧对可经history资格访问；31／32完整加载，recap采用相同floor。
4. 同epoch、渠道、已完成状态及source／worker绑定整对隔离；任一侧有已撤回空正文标记仍排除，报告通知／无渠道旧CLI记录排除；GUI／QQ各自旧原文集合正确。
5. 跨请求旧ref不能读取。
6. 原文版本改变后旧ref失效。
7. epoch改变后旧ref及recap失效。
8. 原文后来绑定source后整对失效。
9. 原文后来绑定worker后整对失效。
10. 超字符预算或剩余token不足不截断完整对；充足预算下两侧正文与数据库逐字一致。
11. recap字面引用、一次性claim、版本化来源绑定及跨渠道隔离；原文变化后已注入资料失效且新请求不再注入旧卡。
12. 翻页游标在原文变化后返回stale_cursor。

`results.json` 汇总每项，`workspace_first/fixtures/*/evidence.json` 保存边界／回执及矩阵证据；保护拦截尝试为空。

这些是复制快照中的隔离行为证据。recap检测直接提供明确标为fixture的审查决定，仅验证宿主结构、来源及生命周期，不验证模型语义审查效果；没有执行生成前情或人工验收。没有更改业务源码／AGENTS／真实user数据。
