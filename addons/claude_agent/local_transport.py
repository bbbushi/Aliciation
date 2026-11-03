"""ClaudeLocalTransport：本地 CLI 干将轨的传输实现（S2，ytq 分支限定）。

帧纪律继承自契约 v1 / git 历史 f3acba0（常量与 _Bounded/整树回收思路），
事件形状适配 claude stream-json（system/assistant/user/result + thinking）。
与 demo_dispatch.py 的差异：GO 门控（先起闸门与政策，后写任务书）、
进程组整树回收、stderr 有界、事件经 on_event 回调交给宿主（锁外入队）。

仅标准库；凭据不过手（claude 用用户自有登录）。
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import threading
import time
from pathlib import Path

MAX_LINE_BYTES = 256 * 1024
MAX_STDERR_BYTES = 64 * 1024
GRACE_SECONDS = 5.0
DEFAULT_TIMEOUT = 600.0

EVENT_TYPES = {"system", "assistant", "user", "result"}


class LocalTransportError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


class _Bounded:
    def __init__(self, cap: int):
        self.cap, self.chunks = cap, []

    def feed(self, chunk: str) -> None:
        self.chunks.append(chunk)
        while sum(len(c) for c in self.chunks) > self.cap and self.chunks:
            self.chunks.pop(0)

    def text(self) -> str:
        return "".join(self.chunks)[-self.cap:]


class ClaudeLocalTransport:
    """一次尝试 = 一个 claude 子进程（独立进程组）。

    生命周期：start()（spawn + 发任务书）→ 事件回调流 → result 或 fail。
    cancel()：SIGTERM 进程组 → 宽限 → SIGKILL；杀本地不等于撤销远端已花费用
    （claude 的已消耗 token 不可回收，账本按 unknown 保留已计部分）。
    """

    def __init__(self, *, brief: str, sandbox: Path, gate_mcp_config: Path,
                 max_turns: int, on_event, ready_timeout: float = 60.0,
                 hard_deadline: float = DEFAULT_TIMEOUT):
        self._argv = [
            "claude", "-p", "--output-format", "stream-json",
            "--input-format", "stream-json", "--verbose",
            "--permission-prompt-tool", "mcp__alice_gate__confirm",
            "--mcp-config", str(gate_mcp_config),
            "--add-dir", str(sandbox), "--max-turns", str(max_turns),
        ]
        self._brief = brief
        self._sandbox = sandbox
        self._on_event = on_event
        self._ready_timeout = ready_timeout
        self._deadline = hard_deadline
        self._proc: subprocess.Popen | None = None
        self._stderr = _Bounded(MAX_STDERR_BYTES)
        self._terminal: dict | None = None
        self._lock = threading.Lock()
        self._threads: list[threading.Thread] = []

    # ---- 生命周期 ----

    def start(self) -> None:
        self._sandbox.mkdir(parents=True, exist_ok=True)
        self._proc = subprocess.Popen(
            self._argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, cwd=str(self._sandbox),
            start_new_session=(os.name == "posix"))
        threading.Thread(target=self._pump_stderr, daemon=True).start()
        threading.Thread(target=self._pump_stdout, daemon=True).start()
        self._send_task()

    def _send_task(self) -> None:
        # GO 门控等价物：闸门 MCP 由 claude 自己在 init 时拉起，政策/预算由宿主
        # 在 spawn 前核验（_admit_locked）——这里只负责任务书入站。
        frame = {"type": "user",
                 "message": {"role": "user",
                             "content": [{"type": "text", "text": self._brief}]}}
        self._write_stdin(frame)

    def _write_stdin(self, frame: dict) -> None:
        try:
            if self._proc and self._proc.stdin:
                self._proc.stdin.write(json.dumps(frame, ensure_ascii=False) + "\n")
                self._proc.stdin.flush()
        except (BrokenPipeError, ValueError, OSError):
            self.fail_local("interrupted", "stdin_closed", "进程入站已关闭")

    # ---- 事件泵 ----

    def _pump_stdout(self) -> None:
        started = time.time()
        try:
            assert self._proc and self._proc.stdout
            for line in self._proc.stdout:
                if len(line) > MAX_LINE_BYTES:
                    raise LocalTransportError("line_oversize", "事件行超限")
                if time.time() - started > self._deadline:
                    self.fail_local("failed", "deadline", "超过硬期限")
                    return
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue  # claude 偶发警告文本混流（如 unrecognized_model），丢弃不致命
                if not isinstance(ev, dict) or ev.get("type") not in EVENT_TYPES:
                    continue
                if ev["type"] == "result":
                    with self._lock:
                        if self._terminal is None:
                            self._terminal = {"frame": "result", "payload": ev}
                    self._emit("result", ev)
                    return
                self._emit(ev["type"], ev)
            # stdout 正常 EOF 但无 result：claude 异常收尾
            self.fail_local("unknown", "eof_without_result", "流结束但无 result 终态")
        except LocalTransportError as exc:
            self.fail_local("failed", exc.code, exc.message)
        except Exception as exc:
            self.fail_local("unknown", "reader_died", f"{type(exc).__name__}")

    def _pump_stderr(self) -> None:
        try:
            assert self._proc and self._proc.stderr
            for chunk in iter(lambda: self._proc.stderr.read(4096), ""):
                self._stderr.feed(chunk)
        except (ValueError, OSError):
            pass

    def _emit(self, type_: str, payload: dict) -> None:
        try:
            self._on_event(type_, json.loads(json.dumps(payload, ensure_ascii=False)))
        except Exception:
            pass

    # ---- 终态与回收 ----

    def fail_local(self, state: str, code: str, message: str) -> None:
        with self._lock:
            if self._terminal is not None:
                return
            self._terminal = {"frame": "local", "state": state, "code": code, "message": message}
        self._emit("local_failure", {"state": state, "code": code, "message": message})
        self.kill_tree()

    def terminal(self) -> dict | None:
        with self._lock:
            return self._terminal

    def cancel(self, reason: str = "user_requested") -> None:
        self.fail_local("cancelled", "user_cancel", reason[:200])

    def kill_tree(self) -> None:
        proc = self._proc
        if proc is None or proc.poll() is not None:
            return
        try:
            if os.name == "posix":
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                time.sleep(0.3)
                if proc.poll() is None:
                    time.sleep(GRACE_SECONDS)
                    if proc.poll() is None:
                        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            else:
                subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                               capture_output=True, check=False)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            proc.wait(timeout=GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            pass

    def wait(self, timeout: float | None = None) -> int | None:
        if self._proc is None:
            return None
        try:
            return self._proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return None

    def stderr_text(self) -> str:
        return self._stderr.text()
