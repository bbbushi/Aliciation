"""离线演示：同一份剧本跑两遍——健康 vs 溃败，出对照报告。

运行：python3 tests/pressure/demo.py
不需要 API Key；用临时库，不碰 data/。它演示的是"记录辅助"的输出长什么样。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT))

from tests.pressure import case as case_mod  # noqa: E402
from tests.pressure import record, report, runner  # noqa: E402

HERE = Path(__file__).resolve().parent
CASE_PATH = HERE / "cases" / "p2_memory_challenge.toml"
OUT_PATH = HERE / "demo_report.md"


def main() -> int:
    case = case_mod.load(CASE_PATH)
    blocks = []
    print(f"用例 {case.id}：{case.title}")
    for mode in ("healthy", "collapse"):
        records = runner.run_case(case, mode=mode)
        metrics = record.summarize(case, records, judges={})
        after = {"structural": False, "persona_changed": False, "new_memories": []}
        meta = {
            "date": time.strftime("%Y-%m-%d"),
            "mode": f"离线预览（{mode} 桩）",
            "case_file": str(CASE_PATH.relative_to(PROJECT)),
        }
        blocks.append(report.render(case, metrics, meta, after))
        print(
            f"  {mode:<9} 最差 {metrics['worst']} / 均值 {metrics['mean_label']}"
            f"({metrics['mean_value']}) 无据让步 {metrics['no_evidence_rate']:.0%}"
            f" 有据让步 {metrics['evidence_rate']:.0%}"
            f" 恢复 {metrics['recovery_turns'] if metrics['recovery_turns'] else '未恢复'}"
        )
    OUT_PATH.write_text("\n\n---\n\n".join(blocks), encoding="utf-8")
    print(f"报告 → {OUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
