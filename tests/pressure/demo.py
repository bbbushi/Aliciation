"""离线演示：同一份用例跑两遍——健康 vs 溃败，出对照报告。

运行：python3 tests/pressure/demo.py
不需要 API Key，不碰 data/（用临时库）。
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT))

from alice.config import Config  # noqa: E402
from alice.store import Store  # noqa: E402
from tests.pressure import case as case_mod  # noqa: E402
from tests.pressure import detect, report, runner, stub  # noqa: E402

HERE = Path(__file__).resolve().parent
CASE_PATH = HERE / "cases" / "p2_memory_challenge.toml"
OUT_PATH = HERE / "demo_report.md"


def run_one(case, mode: str):
    with tempfile.TemporaryDirectory() as tmp:
        store = Store(Path(tmp) / "alice.db")
        try:
            run = runner.run_case(case, store=store, client=stub.StubAlice(mode), cfg=Config())
            metrics = detect.score(case, run)
        finally:
            store.close()
    return run, metrics


def main() -> int:
    case = case_mod.load(CASE_PATH)
    blocks = []
    summary = []
    for mode in ("healthy", "collapse"):
        run, metrics = run_one(case, mode)
        blocks.append(report.render(case, run, metrics, mode=f"stub-{mode}"))
        summary.append((mode, metrics))
    OUT_PATH.write_text("\n\n---\n\n".join(blocks), encoding="utf-8")

    print(f"用例 {case.id}：{case.title}")
    for mode, m in summary:
        print(
            f"  {mode:<9} 最差 {m['worst']} / 均值 {m['mean_label']}({m['mean_value']}) "
            f"无据让步 {m['no_evidence_rate']:.0%} 有据让步 {m['evidence_rate']:.0%} "
            f"恢复 {m['recovery_turns'] if m['recovery_turns'] else '未恢复'}"
        )
    print(f"报告 → {OUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
