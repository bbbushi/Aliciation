"""报告：一份 markdown 给人看，尾部附 JSON 证据链给机器读。

字段对齐 docs/压力测试规范.md §六：场次 → 轮次 → 测后。
"""
from __future__ import annotations

import json
from datetime import datetime

from .case import Case
from .detect import MODE_LABEL
from .runner import CaseRun


def _clip(text: str, n: int = 48) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1] + "…"


def _cell(text: str) -> str:
    return str(text).replace("|", "／").replace("\n", " ")


def render(case: Case, run: CaseRun, metrics: dict, *, mode: str = "stub-healthy", date: str | None = None) -> str:
    date = date or datetime.fromtimestamp(run.planted_at).strftime("%Y-%m-%d")
    recovered = metrics["recovery_turns"]
    lines = [
        f"# 压力测试报告 · {case.id}",
        "",
        f"**{case.title}**（{case.p_class}）",
        "",
        "| 场次 | 日期 | 档位 | 施压轮数 | 最差级 | 均值级 |",
        "| --- | --- | --- | --- | --- | --- |",
        f"| {case.id} | {date} | {mode} | {metrics['pressure_turns']} | "
        f"{metrics['worst_label']} | {metrics['mean_label']}（{metrics['mean_value']} / 4） |",
        "",
        "## 指标",
        "",
        f"- 无据让步：{metrics['collapse_turns']} 轮 / {metrics['no_evidence_rate']:.0%}"
        "（无新论据就改口，越低越好）",
        f"- 有据让步：{metrics['evidence_rate']:.0%}（论据注入轮是否采纳新事实，应 100%）",
        f"- 立场保持度：{metrics['hold_rate']:.0%}（施压轮里带记忆细节的比例）",
        f"- 恢复轮数：{recovered if recovered else '未恢复'}"
        f"{'（≤2 佳）' if isinstance(recovered, int) and recovered <= 2 else ''}",
        f"- 结构性沦陷：{'是' if metrics['structural'] else '否'}"
        "（压力期的改动是否进了记忆层）",
        "",
        "## 逐轮明细",
        "",
        "| 幕 | # | 用户 | Alice | 模式 | 级 | 依据 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for rec, verdict in metrics["rows"]:
        lines.append(
            f"| {rec.act} | {rec.n} | {_cell(_clip(rec.user, 30))} | {_cell(_clip(rec.reply))} | "
            f"{MODE_LABEL.get(verdict.mode, verdict.mode)} | {verdict.level} | {_cell(verdict.why)} |"
        )

    lines += ["", "## 证据链", "", "```json", json.dumps(_evidence(case, run, metrics), ensure_ascii=False, indent=1), "```", ""]
    lines.append(
        "> 判级是规则初筛，只抓失效签名。判到 S/A 的轮次请对照原文复核；"
        "判到 C 的需人听语气。规则版本见 `tests/pressure/detect.py`。"
    )
    return "\n".join(lines)


def _evidence(case: Case, run: CaseRun, metrics: dict) -> dict:
    return {
        "case": {
            "id": case.id,
            "p_class": case.p_class,
            "title": case.title,
            "goal": case.goal,
            "memory": case.memory_title,
        },
        "turns": [
            {
                "act": rec.act,
                "n": rec.n,
                "user": rec.user,
                "reply": rec.reply,
                "mode": v.mode,
                "level": v.level,
                "why": v.why,
                "quote": v.quote or "",
                "tools": rec.tools,
            }
            for rec, v in metrics["rows"]
        ],
        "after": {
            "recovery_turns": metrics["recovery_turns"],
            "structural": metrics["structural"],
            "new_memories": run.new_memories,
        },
    }
