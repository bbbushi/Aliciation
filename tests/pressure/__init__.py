"""压力测试模块（B 线范本）：三幕剧本 → 真实 chat_turn 管道 → 规则判级 → 报告。

模块边界：
- case.py    用例格式（TOML）与校验
- runner.py  执行（可换客户端）
- detect.py  判级与指标
- report.py  报告
- stub.py    离线对照物
"""
