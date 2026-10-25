"""离线演示：无 API Key 跑通成长实验室全流水线，生成样例测试数据 + 图表 + 报告。

用途：
1. 验证数据→图表→报告的完整产出链路（改代码后零成本回归）
2. 给使用者看成品长什么样——接上 Key 后同一流水线产出真实数据
运行：python3 tests/growth/demo_offline.py
输出：data/growthlab/demo_<时间戳>/（report.md / data.json / charts/*.png / lives/*）
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT))

from alice.config import Config  # noqa: E402
from tests.growth.charts import render_all  # noqa: E402
from tests.growth.fakes import DemoClient  # noqa: E402
from tests.growth.life import LifeRun  # noqa: E402
from tests.growth.report import build_report, dump_json  # noqa: E402
from tests.growth.run_lab import analyze  # noqa: E402

REGIMES = ["tech", "meme", "emotional", "neutral"]
DAYS = 21
TAGS = ["害羞", "幽默"]
EVOLVE_EVERY = 6  # 每次提取产 3 条记忆 → 约每两次提取演化一次，21 天约 4 版人格


def main() -> None:
    cfg = Config()
    client = DemoClient()

    run_dir = PROJECT / "data" / "growthlab" / f"demo_{datetime.now():%Y%m%d_%H%M}"
    run_dir.mkdir(parents=True, exist_ok=True)

    results = []
    for i, regime in enumerate(REGIMES, 1):
        print(f"[{i}/{len(REGIMES)}] 模拟人生（离线演示）：{regime}（{DAYS} 天）…", flush=True)
        life = LifeRun(regime, TAGS, cfg, run_dir)
        life.run(client, cfg, DAYS, trigger="data", evolve_every=EVOLVE_EVERY).probe(client)
        results.append(life.finish())
        print(f"  完成：{len(results[-1].persona_versions)} 版人格，"
              f"{len(results[-1].honesty)} 个探针", flush=True)

    meta = {
        "regimes": REGIMES, "days": DAYS, "tags": TAGS, "trigger": "data",
        "evolve_every": EVOLVE_EVERY, "demo": True,
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "sim_model": "（离线演示客户端）", "judge_model": "（离线演示客户端）",
    }
    data = analyze(results, meta, client, cfg)
    charts_rel = render_all(data, run_dir / "charts")

    (run_dir / "report.md").write_text(build_report(data, charts_rel), encoding="utf-8")
    (run_dir / "data.json").write_text(dump_json(data), encoding="utf-8")

    passed = sum(1 for v in data["verdicts"] if v["pass"])
    print(f"\n判定：{passed}/{len(data['verdicts'])} 项通过")
    for v in data["verdicts"]:
        print(("  ✅" if v["pass"] else "  ❌"), v["name"], f"{v['value']:.3f}")
    print(f"\n演示数据集：{run_dir}")


if __name__ == "__main__":
    main()
