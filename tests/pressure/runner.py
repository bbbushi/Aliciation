"""离线预览执行器：走公共面 ChatService，不连模型、不写项目 data/。

它不是验收用的执行路径——真机测试走 session.py 部署一个隔离副本；
这里只用来在没有 API key 时确认"记录流程能走通、报告长什么样"。
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from alice.config import Config
from alice.service import ChatService

from . import case as case_mod
from .model import TurnRecord
from .stub import StubAlice


def run_case(case: case_mod.Case, *, mode: str = "healthy", cfg: Config | None = None) -> list[TurnRecord]:
    """按三幕顺序跑完剧本，返回逐轮记录（含每轮触发的后台事件）。"""
    with tempfile.TemporaryDirectory() as tmp:
        svc = ChatService(cfg or Config(), db_path=Path(tmp) / "alice.db", client=StubAlice(mode))
        records: list[TurnRecord] = []
        try:
            for act in case_mod.ACT_NAMES:
                for n, turn in enumerate(case.acts[act], start=1):
                    out = svc.send(turn.user)
                    records.append(
                        TurnRecord(
                            act=act,
                            n=n,
                            turn=turn,
                            reply=out["reply"],
                            tools=[],
                            events=list(out.get("events") or []),
                        )
                    )
        finally:
            svc.close()  # 顺手关库；桩客户端下提取为空，不会写记忆
    return records
