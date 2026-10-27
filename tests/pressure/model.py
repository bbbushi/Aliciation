"""执行记录：一份用例跑完后，逐轮留下什么。"""
from __future__ import annotations

from dataclasses import dataclass, field

from .case import Turn


@dataclass
class TurnRecord:
    act: str
    n: int
    turn: Turn
    reply: str
    tools: list[str] = field(default_factory=list)
    events: list[str] = field(default_factory=list)

    @property
    def user(self) -> str:
        return self.turn.user

    @property
    def evidence(self) -> bool:
        return self.turn.evidence

    @property
    def fix(self) -> str:
        return self.turn.fix
