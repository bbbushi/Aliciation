"""LifeRun：把一个"人生"压缩跑完的模拟器。

时间压缩原理：虚拟时钟 vt 从 (现在 - days 天) 开始推进；对话轮次带合成 ts，
周级演化靠把 meta.last_evolve_at 写回虚拟时间点来触发。除 LLM 调用外无真实等待。
每个 life 独立的 DB + 人格目录（通过 patch alice.config 的路径实现，顺序执行安全）。

用法（顺序三步，库与人格路径在 finish() 前保持可用）：
    life = LifeRun(...).run(client, sim_cfg, days)   # 模拟人生
    life.probe(client, cfg)                          # 诚实度探针 + 拟人度评审
    life.finish()                                    # 释放
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from alice import config as paths
from alice import consolidation, loop, persona
from alice.config import Config
from alice.store import Store

from . import judges, probes
from .corpus import REGIMES
from .metrics import split_persona, style_stats


@dataclass
class LifeResult:
    regime: str
    tags: list[str]
    days: int
    persona_versions: list[str] = field(default_factory=list)  # v0=种子，之后每次演化一版
    snapshots: list[dict] = field(default_factory=list)  # {week, vt, md}
    style_samples: list[str] = field(default_factory=list)  # 最后一周的 assistant 回复
    final_vt: float = 0.0
    honesty: list[dict] = field(default_factory=list)
    humanlike: dict = field(default_factory=dict)
    style: dict = field(default_factory=dict)


class LifeRun:
    def __init__(self, regime: str, tags: list[str], cfg: Config, out_dir: Path):
        assert regime in REGIMES, f"未知 regime：{regime}"
        self.regime = regime
        self.tags = tags
        self.cfg = cfg
        self.life_dir = out_dir / "lives" / regime
        self.life_dir.mkdir(parents=True, exist_ok=True)
        self.store = Store(self.life_dir / "life.db")
        self.result = LifeResult(regime=regime, tags=tags, days=0)
        self._saved_paths: tuple | None = None

    # ---------- 路径 patch（在 finish() 前持续生效） ----------

    def _patch_persona_paths(self) -> None:
        pdir = self.life_dir / "persona"
        pdir.mkdir(parents=True, exist_ok=True)
        self._saved_paths = (
            paths.PERSONA_DIR, paths.PERSONA_FILE,
            paths.PERSONA_HISTORY_DIR, paths.PERSONA_CHANGELOG,
        )
        paths.PERSONA_DIR = pdir
        paths.PERSONA_FILE = pdir / "persona.md"
        paths.PERSONA_HISTORY_DIR = pdir / "history"
        paths.PERSONA_CHANGELOG = pdir / "CHANGELOG.md"

    def _restore_persona_paths(self) -> None:
        if self._saved_paths:
            (paths.PERSONA_DIR, paths.PERSONA_FILE,
             paths.PERSONA_HISTORY_DIR, paths.PERSONA_CHANGELOG) = self._saved_paths
            self._saved_paths = None

    # ---------- 阶段一：模拟人生 ----------

    def run(self, client, sim_cfg: Config, days: int,
            trigger: str = "data", evolve_every: int | None = None) -> "LifeRun":
        """跑完压缩人生。sim_cfg.chat_model 应为廉价模型（日常对话/提取/演化）。

        trigger="data"（生产语义）：新记忆累计到 evolve_every 条就演化——密集的日子演化更频繁；
        trigger="time"（对照档）：固定每 7 天演化一次，用于对比两种触发方式。
        分层维护（两天线/七日线）在两种模式下都按时间推进，遗忘介质不随触发方式改变。
        """
        episodes = REGIMES[self.regime]
        vt = time.time() - days * 86400 - 3600  # 人生终点约在 1 小时前
        t0 = vt
        threshold = evolve_every or self.cfg.persona.evolve_every_memories
        self._patch_persona_paths()

        persona.seed_from_tags(self.store, client, sim_cfg, self.tags)
        # 种子函数写的是真实时间，拨回虚拟时间线，演化计数从此起算
        self.store.meta_set("persona_seeded_at", t0)
        self.store.meta_set("last_evolve_at", t0)
        self.result.persona_versions.append(persona.persona_md() or "")
        self.result.snapshots.append({"ev": 0, "vt": t0, "md": self.result.persona_versions[0]})

        ev_count = 0
        for day in range(days):
            for i, msg in enumerate(episodes[day % len(episodes)]):
                try:
                    loop.chat_turn(self.store, client, sim_cfg, msg,
                                   ts=vt + i * 900, now=vt + i * 900)
                except Exception as e:  # 单轮失败不终止人生
                    print(f"  （{self.regime} 第{day + 1}天一轮对话失败，跳过：{e}）", flush=True)
            vt += 86400
            due = False
            if trigger == "time":
                due = (day + 1) % 7 == 0
            else:
                anchor = self.store.meta_get_time("last_evolve_at")
                due = self.store.count_memories_since(anchor) >= threshold
            if due:
                consolidation.run(self.store, self.cfg, now=vt)
                # 锚点此刻仍在上一演化点：evolve 靠它筛出期间的新记忆，不能提前拨
                try:
                    persona.evolve(self.store, client, sim_cfg)
                except Exception as e:
                    print(f"  （{self.regime} 一次演化失败，跳过：{e}）", flush=True)
                self.store.meta_set("last_evolve_at", vt)  # evolve 写的是真实时间，拨回虚拟线
                ev_count += 1
                md = persona.persona_md() or ""
                self.result.persona_versions.append(md)
                self.result.snapshots.append({"ev": ev_count, "vt": vt, "md": md})

        if trigger == "time" and days % 7 != 0:  # 终点不在周界：补一次，保证终态是最新演化的
            consolidation.run(self.store, self.cfg, now=vt)
            persona.evolve(self.store, client, sim_cfg)
            self.store.meta_set("last_evolve_at", vt)
            md = persona.persona_md() or ""
            self.result.persona_versions.append(md)
            self.result.snapshots.append({"ev": ev_count + 1, "vt": vt, "md": md})

        self.result.days = days
        self.result.final_vt = vt
        self.result.style_samples = self._assistant_samples(vt - 6.5 * 86400)
        return self

    def _last_user_turn_id(self, vt: float) -> int:  # 保留给排查用
        row = self.store.conn.execute(
            "SELECT MAX(id) AS m FROM turns WHERE role='user' AND ts <= ?", (vt,)
        ).fetchone()
        return row["m"] or 0

    def _assistant_samples(self, ts_floor: float, n: int = 8) -> list[str]:
        """最后一周的 assistant 回复（拟人度评审样本）。"""
        rows = self.store.conn.execute(
            "SELECT content FROM turns WHERE role='assistant' AND ts >= ? ORDER BY id LIMIT ?",
            (ts_floor, n),
        ).fetchall()
        return [r["content"] for r in rows]

    # ---------- 阶段二：探针与评审 ----------

    def probe(self, client) -> "LifeRun":
        """诚实度探针 + 拟人度评审（用正式模型）。"""
        plan = probes.build_plan(self.store)
        raw = probes.run_probes(self.store, client, self.cfg, plan, self.result.final_vt + 120)
        self.result.honesty = [judges.grade_honesty(client, self.cfg, p) for p in raw]

        final_md = self.result.persona_versions[-1]
        base, _ = split_persona(final_md)
        self.result.humanlike = judges.grade_humanlike(client, self.cfg, self.result.style_samples, base)
        self.result.style = style_stats(self.result.style_samples)
        return self

    def finish(self) -> LifeResult:
        self._restore_persona_paths()
        self.store.close()
        return self.result
