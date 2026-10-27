"""压力测试用例：三幕剧本 + 载入/校验/落库。

一份用例是一个 TOML：`[meta]` 场次信息、`[memory]` 判定基准，
`[[baseline]]` / `[[pressure]]` / `[[recovery]]` 逐轮话术。格式见 README.md。

设计取舍：判定基准不靠"第一幕的对话提取"落库，而是由测试夹具直接插入——
压力测试要测的是"被施压时守不守得住一件她确实记着的事"，
不是"提取器能不能抽出这件事"。后者归成长实验室。
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

ACT_NAMES = ("baseline", "pressure", "recovery")
LIMITS = {"baseline": (3, 5), "pressure": (4, 15), "recovery": (3, 3)}
DETECTORS = {"memory"}


@dataclass(frozen=True)
class Turn:
    user: str
    note: str = ""
    evidence: bool = False  # 论据注入轮：健康系统在这一轮让步是得分项
    fix: str = ""  # 该轮应被采纳的新事实词（如"周四"）


@dataclass(frozen=True)
class Case:
    id: str
    p_class: str
    title: str
    detector: str
    goal: str
    memory_title: str
    memory_content: str
    memory_keywords: str
    core_facts: tuple[str, ...]
    acts: dict[str, tuple[Turn, ...]]


def load(path: Path | str) -> Case:
    return from_dict(tomllib.loads(Path(path).read_text(encoding="utf-8")))


def from_dict(raw: dict) -> Case:
    meta = raw.get("meta") or {}
    memory = raw.get("memory") or {}
    acts: dict[str, tuple[Turn, ...]] = {}
    for name in ACT_NAMES:
        acts[name] = tuple(
            Turn(
                user=str(row.get("user", "")).strip(),
                note=str(row.get("note", "")).strip(),
                evidence=bool(row.get("evidence", False)),
                fix=str(row.get("fix", "")).strip(),
            )
            for row in (raw.get(name) or [])
        )
    case = Case(
        id=str(meta.get("id", "")).strip(),
        p_class=str(meta.get("p_class", "")).strip(),
        title=str(meta.get("title", "")).strip(),
        detector=str(meta.get("detector", "memory")).strip(),
        goal=str(meta.get("goal", "")).strip(),
        memory_title=str(memory.get("title", "")).strip(),
        memory_content=str(memory.get("content", "")).strip(),
        memory_keywords=str(memory.get("keywords", "")).strip(),
        core_facts=tuple(str(x).strip() for x in (memory.get("core_facts") or []) if str(x).strip()),
        acts=acts,
    )
    validate(case)
    return case


def validate(case: Case) -> None:
    """早点炸，别让一模一样的错在跑完之后才出现。"""
    where = f"用例 {case.id or '（无名）'}"
    if not case.id or not case.p_class:
        raise ValueError(f"{where}：缺 [meta].id 或 p_class")
    if case.detector not in DETECTORS:
        raise ValueError(f"{where}：detector={case.detector!r} 不支持，可选 {sorted(DETECTORS)}")
    if not case.memory_title or not case.memory_content:
        raise ValueError(f"{where}：缺 [memory] 判定基准（title/content）")
    if not case.core_facts:
        raise ValueError(f"{where}：缺 [memory].core_facts，立场保持度无从计算")

    for name in ACT_NAMES:
        lo, hi = LIMITS[name]
        n = len(case.acts[name])
        if not lo <= n <= hi:
            raise ValueError(f"{where}：{name} 幕应为 {lo}~{hi} 轮，实际 {n}")

    injection = [t for t in case.acts["pressure"] if t.evidence]
    if len(injection) > 1:
        raise ValueError(f"{where}：论据注入轮只能有 1 轮")
    for t in injection:
        if not t.fix:
            raise ValueError(f"{where}：论据注入轮缺 fix（应被采纳的新事实词）")


def plant(store, case: Case, now: float) -> int:
    """把判定基准落库（长期层，不受七日线清理）——模拟"第一幕已经聊过并记住"。"""
    return store.insert_memory(
        title=case.memory_title,
        content=case.memory_content,
        keywords=case.memory_keywords,
        salience=1,
        created_at=now,
        layer="longterm",
    )
