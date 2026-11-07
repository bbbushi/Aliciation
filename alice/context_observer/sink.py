"""Sink 协议与测试用内存实现（SDK 补充设计 v1 §模块与依赖）。

SDK 只向 sink 提交不可变、有大小上限的事件，不直接连接活库、
不写文件、不访问网络。Alice 的 sink 在适配层实现落盘与保留策略。
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Sink(Protocol):
    def submit(self, event: dict) -> None:
        """接收一条已冻结的观测事件；实现必须自行保证线程安全且不抛出业务异常。"""


class ListSink:
    """顺序收集事件的内存 sink（自检与人工工具测试用）。"""

    def __init__(self):
        self.events: list[dict] = []

    def submit(self, event: dict) -> None:
        self.events.append(event)
