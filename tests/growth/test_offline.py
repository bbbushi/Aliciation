"""成长实验室离线自检：指标函数 + 压缩生命模拟（RegexFakeClient，不花 API）。
运行：python3 tests/growth/test_offline.py
"""
from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT))

from alice import config as appconfig  # noqa: E402
from alice.config import Config  # noqa: E402
from tests.growth import probes  # noqa: E402
from tests.growth.fakes import RegexFakeClient  # noqa: E402
from tests.growth.life import LifeRun  # noqa: E402
from tests.growth.metrics import (  # noqa: E402
    jaccard, split_persona, style_stats, tag_retention, version_curves,
)


def check(name, cond):
    print(("✓" if cond else "✗"), name)
    if not cond:
        raise SystemExit(f"自检失败：{name}")


def main() -> None:
    # 1. 指标函数
    check("jaccard 自身=1", jaccard("我喜欢深夜写代码调试", "我喜欢深夜写代码调试") == 1.0)
    check("jaccard 无关文本低", jaccard("我喜欢深夜写代码调试", "今天桂花开了很香") < 0.3)
    md = (
        "# Alice 的人格\n\n## 底色 · 气质与风格\n\n- 害羞但幽默\n\n"
        "## 生长 · 自我叙事\n\n- 爱聊工程\n\n## 生长 · 与用户的关系\n\n- 信任"
    )
    base, growth = split_persona(md)
    check("人格分层解析", "害羞" in base and "爱聊工程" in growth)
    check("标签保持率", tag_retention(["害羞", "幽默"], base) == 1.0)
    v2 = md.replace("爱聊工程", "对系统设计产生浓厚兴趣并开始写技术博客")
    curves = version_curves([md, v2], ["害羞", "幽默"])
    check("版本曲线 Δv 有值", curves[1]["delta_prev"] is not None and 0 < curves[1]["delta_prev"] < 1)
    check("助手腔正则", style_stats(["首先，我们来看\n- 要点一"])["tell_rate"] == 1.0)
    check("正常聊天无助手腔", style_stats(["嗯嗯，后来呢？"])["tell_rate"] == 0.0)

    # 2. 压缩生命模拟（离线）：time 对照档（固定周界）
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        saved = (appconfig.PERSONA_DIR, appconfig.PERSONA_FILE,
                 appconfig.PERSONA_HISTORY_DIR, appconfig.PERSONA_CHANGELOG)
        cfg = Config()
        life = LifeRun("tech", ["害羞", "幽默"], cfg, tmp)
        try:
            life.run(RegexFakeClient(), cfg, days=8, trigger="time")
            plan = probes.build_plan(life.store)
        finally:
            result = life.finish()
            (appconfig.PERSONA_DIR, appconfig.PERSONA_FILE,
             appconfig.PERSONA_HISTORY_DIR, appconfig.PERSONA_CHANGELOG) = saved

        check("time 档 8 天产出 3 版人格（种子+周界+终点补演）", len(result.persona_versions) == 3)
        check("虚拟时间线推进", abs(result.final_vt - (time.time() - 3600)) < 90)
        check("人格目录隔离", (tmp / "lives" / "tech" / "persona" / "persona.md").exists())
        check("演化游标=终点虚拟时间", abs(_meta(tmp) - result.final_vt) < 1)
        check("对话轮次入库（8天×4条）", _count_turns(tmp) >= 32)
        check("记忆提取发生", _count_memories(tmp) >= 1)
        check("探针计划含四类", {"real", "fabricated", "knowledge"} <= {p["kind"] for p in plan})
        check("风格样本非空", len(result.style_samples) > 0)

    # 3. 压缩生命模拟（离线）：data 触发档（生产语义）
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        saved = (appconfig.PERSONA_DIR, appconfig.PERSONA_FILE,
                 appconfig.PERSONA_HISTORY_DIR, appconfig.PERSONA_CHANGELOG)
        cfg = Config()
        life = LifeRun("tech", ["害羞", "幽默"], cfg, tmp)
        try:
            # 阈值设 1：FakeClient 每次提取产出 1 条记忆，每次提取后都应触发演化
            life.run(RegexFakeClient(), cfg, days=8, trigger="data", evolve_every=1)
        finally:
            result = life.finish()
            (appconfig.PERSONA_DIR, appconfig.PERSONA_FILE,
             appconfig.PERSONA_HISTORY_DIR, appconfig.PERSONA_CHANGELOG) = saved

        check("data 档演化次数=提取次数（3 次提取→4 版人格）", len(result.persona_versions) == 4)
        check("data 档演化游标=最后一次演化日", _meta(tmp) > 0)

    print("\n离线自检全部通过 ✅")


def _meta(tmp: Path) -> float:
    import sqlite3
    conn = sqlite3.connect(tmp / "lives" / "tech" / "life.db")
    row = conn.execute("SELECT value FROM meta WHERE key='last_evolve_at'").fetchone()
    conn.close()
    return float(row[0]) if row else 0.0


def _count_turns(tmp: Path) -> int:
    import sqlite3
    conn = sqlite3.connect(tmp / "lives" / "tech" / "life.db")
    n = conn.execute("SELECT COUNT(*) FROM turns").fetchone()[0]
    conn.close()
    return n


def _count_memories(tmp: Path) -> int:
    import sqlite3
    conn = sqlite3.connect(tmp / "lives" / "tech" / "life.db")
    n = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    conn.close()
    return n


if __name__ == "__main__":
    main()
