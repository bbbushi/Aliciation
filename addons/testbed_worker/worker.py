"""Testbed worker：零成本机制验证用 adapter（契约 §一 的最小实现）。

无网络、无模型、无凭据。行为由 start 载荷的 limits.behavior 控制：
  normal  — 回显任务书到 output/result.txt，正常 progress/usage/result
  slow    — ready 后停 60s（取消路径人工验收用）
  crash   — ready 后立即异常退出（unexpected-exit → unknown 路径）
  protocol_violation — 连发两个 result（宿主必须判协议违规）
  oversize — 发一帧 > 256KiB（帧上限路径）
本 addon 仅供机制层开发与 DSH 人工验收清单第 3~6 项使用，不构成业务验收；
注册后默认禁用，启用须经用户政策。
"""
from __future__ import annotations

import json
import os
import sys
import time

PROTOCOL_VERSION = 1


def emit(name: str, payload: dict) -> None:
    data = json.dumps({"protocol_version": PROTOCOL_VERSION, name: payload}, ensure_ascii=False)
    sys.stdout.write(data + "\n")
    sys.stdout.flush()


def read_frames():
    for raw in sys.stdin:
        try:
            frame = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(frame, dict):
            for key in ("start", "go", "cancel"):
                if key in frame:
                    yield key, frame[key]


def main() -> int:
    start = None
    for key, payload in read_frames():
        if key == "start":
            start = payload
            emit("ready", {"adapter_version": "testbed-1.0.0", "cli_version": "-",
                           "effective_capabilities": {"usage": "exact_zero", "cancel": "local"}})
            break
    if start is None:
        return 1
    behavior = (start.get("limits", {}) or {}).get("behavior", "normal")

    for key, payload in read_frames():
        if key == "cancel":
            emit("stopped", {"local_confirmed": True, "remote_state": "none"})
            return 0
        if key != "go":
            continue
        if behavior == "protocol_violation":
            sys.stdout.write("这不是 JSON 帧\n")
            sys.stdout.flush()
            emit("result", {"status": "succeeded", "artifacts": [], "errors": []})
            return 0
        if behavior == "oversize":
            sys.stdout.write(json.dumps({"protocol_version": 1, "progress": {"pad": "x" * 300000}}) + "\n")
            sys.stdout.flush()
            time.sleep(1)
            return 0
        if behavior == "crash":
            os._exit(2)
        if behavior == "slow":
            time.sleep(60)

        out_dir = start.get("output_root") or "."
        os.makedirs(out_dir, exist_ok=True)
        result_path = os.path.join(out_dir, "result.txt")
        inputs = start.get("input_handles", []) or []
        body = "\n".join([
            "testbed worker 回显",
            f"brief: {start.get('brief', '')[:500]}",
            f"inputs: {json.dumps(inputs, ensure_ascii=False)}",
            f"task: {start.get('task_id', '')} attempt: {start.get('attempt_id', '')}",
        ])
        with open(result_path, "w", encoding="utf-8") as fh:
            fh.write(body)
        emit("progress", {"phase": "work", "counters": {"steps": 1}})
        emit("usage", {"sequence": 1, "cumulative": {"calls": 0, "token": 0}, "confidence": "exact"})
        sys.stderr.write("testbed: stderr 仅进诊断缓冲，不进 UI/模型\n")
        emit("result", {"status": "succeeded",
                        "artifacts": [{"rel_path": "result.txt", "media": "text/plain"}],
                        "usage_final": {"calls": 0, "token": 0},
                        "errors": []})
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
