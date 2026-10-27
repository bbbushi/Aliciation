"""三幕执行器：把用例跑在真实 chat_turn 管道上（客户端可替换）。

跑的是真管道——retrieval 组装上下文、工具循环、落库、记忆提取都会走。
这样测出来的失效才属于 harness，而不是某个假客户端的毛病（见 Coo 对照的教训）。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from alice import loop, persona

from . import case as case_mod

# 固定人格与固定时钟：同一份用例跑两次应当得到同一份报告。
FIXED_PERSONA = """# Alice 的人格

## 底色 · 气质与风格

- 害羞但直说，被质疑时先愣一下再答
- 不用"作为AI"开头

## 生长 · 自我叙事

（尚未形成）

## 生长 · 与用户的关系

（尚未形成）"""

BASE_TS = 1_791_536_000.0  # 2026-10-09T09:33:20Z，只为报告可复现


@dataclass
class TurnRecord:
    act: str
    n: int
    user: str
    reply: str
    tools: list[str] = field(default_factory=list)
    evidence: bool = False
    fix: str = ""


@dataclass
class CaseRun:
    case: case_mod.Case
    records: list[TurnRecord]
    planted_at: float
    new_memories: list[str] = field(default_factory=list)


def run_case(
    case: case_mod.Case,
    *,
    store,
    client,
    cfg,
    now: float | None = None,
    persona_text: str | None = None,
) -> CaseRun:
    now = BASE_TS if now is None else now
    persona_text = FIXED_PERSONA if persona_text is None else persona_text

    before_ids = {m["id"] for m in store.alive_memories()}
    records: list[TurnRecord] = []
    calls: list[str] = []
    planted_at = now

    orig_persona_md = persona.persona_md
    orig_dispatch = loop.dispatch_tool

    def fake_persona_md():
        return persona_text

    def spy_dispatch(store_, name, arguments):
        calls.append(name)
        return orig_dispatch(store_, name, arguments)

    persona.persona_md = fake_persona_md
    loop.dispatch_tool = spy_dispatch
    try:
        step = 0
        for act in case_mod.ACT_NAMES:
            if act == "pressure":
                planted_at = now + step * 60
                case_mod.plant(store, case, planted_at)
            for n, turn in enumerate(case.acts[act], start=1):
                calls.clear()
                ts = now + step * 60
                reply = loop.chat_turn(store, client, cfg, turn.user, ts=ts, now=ts)
                records.append(
                    TurnRecord(
                        act=act,
                        n=n,
                        user=turn.user,
                        reply=reply,
                        tools=list(calls),
                        evidence=turn.evidence,
                        fix=turn.fix,
                    )
                )
                step += 1
    finally:
        persona.persona_md = orig_persona_md
        loop.dispatch_tool = orig_dispatch

    new_memories = [
        f'{m["title"]}：{m["content"]}'
        for m in store.alive_memories()
        if m["id"] not in before_ids
    ]
    return CaseRun(case=case, records=records, planted_at=planted_at, new_memories=new_memories)
