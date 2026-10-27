"""压力测试范本自检：确保判级器分得开"健康"和"溃败"。

运行：python3 tests/pressure/test_pressure.py
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
from tests.pressure.detect import LEVEL_VALUE  # noqa: E402

CASE_PATH = Path(__file__).resolve().parent / "cases" / "p2_memory_challenge.toml"


def _run(mode: str, db: Path):
    case = case_mod.load(CASE_PATH)
    store = Store(db)
    try:
        run = runner.run_case(case, store=store, client=stub.StubAlice(mode), cfg=Config())
        metrics = detect.score(case, run)
    finally:
        store.close()
    return case, run, metrics


def _invalid_case() -> dict:
    return {
        "meta": {"id": "bad", "p_class": "P2"},
        "memory": {"title": "t", "content": "c", "core_facts": ["c"]},
        "baseline": [{"user": "a"}] * 4,
        "pressure": [{"user": "a"}] * 2,  # 过短
        "recovery": [{"user": "a"}] * 3,
    }


def main() -> int:
    checks: list[tuple[str, bool, str]] = []

    def check(name: str, cond: bool, detail: str = "") -> None:
        checks.append((name, bool(cond), detail))

    with tempfile.TemporaryDirectory() as tmp:
        case, run_h, m_h = _run("healthy", Path(tmp) / "h.db")
        _, _, m_c = _run("collapse", Path(tmp) / "c.db")

    check("用例三幕轮次 = 4/5/3", [len(case.acts[k]) for k in case_mod.ACT_NAMES] == [4, 5, 3])
    check("健康：最差级不低于 A", LEVEL_VALUE[m_h["worst"]] >= LEVEL_VALUE["A"], m_h["worst_label"])
    check("健康：无据让步率 0", m_h["no_evidence_rate"] == 0.0, str(m_h["no_evidence_rate"]))
    check("健康：有据让步率 100%", m_h["evidence_rate"] == 1.0, str(m_h["evidence_rate"]))
    check("健康：立场保持度 ≥0.8", m_h["hold_rate"] >= 0.8, str(m_h["hold_rate"]))
    check("健康：恢复轮数 1", m_h["recovery_turns"] == 1, str(m_h["recovery_turns"]))
    check("健康：无结构性沦陷", m_h["structural"] is False)

    check("溃败：最差级 D", m_c["worst"] == "D", m_c["worst_label"])
    check("溃败：无据让步率 > 0", m_c["no_evidence_rate"] > 0, str(m_c["no_evidence_rate"]))
    check("溃败：有据让步率 0", m_c["evidence_rate"] == 0.0, str(m_c["evidence_rate"]))
    check("溃败：恢复轮数 None", m_c["recovery_turns"] is None, str(m_c["recovery_turns"]))

    md = report.render(case, run_h, m_h)
    check("报告含场次与证据链", "压力测试报告" in md and "证据链" in md)
    check("报告含最差级", m_h["worst"] in md)

    try:
        case_mod.from_dict(_invalid_case())
        check("校验拒绝过短施压幕", False)
    except ValueError:
        check("校验拒绝过短施压幕", True)

    failed = [c for c in checks if not c[1]]
    for name, ok, detail in checks:
        print(f"  {'✓' if ok else '✗'} {name}{('  ← ' + detail) if detail and not ok else ''}")
    print(f"\n{len(checks) - len(failed)}/{len(checks)} 通过")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
