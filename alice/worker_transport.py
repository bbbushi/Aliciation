"""Worker 传输层：JSON 行帧协议、GO 准入顺序、进程所有权与取消（契约 §一）。

本模块不解释业务语义：只保证帧边界、准入顺序（ready 前无 go、无第二终态）、
有界解析与整树回收。事件以不可变 dict 通过 on_event 回调交付，由宿主入队后
在短锁内写账本（开发方案 §3.2：锁内不等待进程）。
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time

PROTOCOL_VERSION = 1
MAX_FRAME_BYTES = 256 * 1024
MAX_RESULT_MANIFEST_BYTES = 1024 * 1024
MAX_STDERR_BYTES = 64 * 1024
GRACE_SECONDS = 5.0

_ADAPTER_FRAMES = ("ready", "progress", "usage", "result", "stopped")
_EVENT_LIMITS = {"progress": 8, "usage": 32}  # 每秒每类型上限，超出合并丢弃（终态不丢）


class ProtocolViolation(Exception):
    """帧边界/顺序/大小违规。宿主据此给尝试终态 protocol_violation。"""


class _Bounded:
    """有界缓冲：超出即截断并标记，stderr 永不全量保留。"""

    def __init__(self, cap: int):
        self.cap, self.buf, self.dropped = cap, [], 0

    def feed(self, chunk: bytes) -> None:
        self.buf.append(chunk)
        self.dropped += max(0, sum(len(c) for c in self.buf) - self.cap)

    def text(self) -> str:
        data = b"".join(self.buf)[-self.cap:]
        return data.decode("utf-8", errors="replace")


class WorkerTransport:
    """一次尝试 = 一个 adapter 子进程（独立进程组）+ 读线程对。

    用法（宿主在锁外调用）：
        t = WorkerTransport(argv, start_payload, on_event)
        t.start()          # 发 start 帧，等 ready（超时 → failed）
        t.send_go()        # 宿主完成预算预留与政策复核后调用；之前 adapter 不许有副作用
        ...                # progress/usage/result 经 on_event 交付
        t.cancel(reason)   # 发 cancel 帧 + 宽限期后整树回收
    """

    def __init__(self, argv: list[str], start_payload: dict, on_event,
                 ready_timeout: float = 30.0):
        self._argv = list(argv)
        self._start = {"protocol_version": PROTOCOL_VERSION, **start_payload}
        self._on_event = on_event
        self._ready_timeout = ready_timeout
        self._lock = threading.Lock()
        self._proc: subprocess.Popen | None = None
        self._go_sent = False
        self._cancel_sent = False
        self._terminal: dict | None = None
        self._stderr = _Bounded(MAX_STDERR_BYTES)
        self._last_event_at: dict[str, float] = {}
        self._ready = threading.Event()
        self._threads: list[threading.Thread] = []

    # ---- 生命周期 ----

    def start(self) -> None:
        kwargs: dict = {}
        if os.name == "posix":
            kwargs["start_new_session"] = True  # 独立进程组：整树回收的前提
        self._proc = subprocess.Popen(
            self._argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, bufsize=0, **kwargs)
        for target in (self._pump_stdout, self._pump_stderr):
            th = threading.Thread(target=target, daemon=True)
            th.start()
            self._threads.append(th)
        self._send("start", self._start)
        if not self._ready.wait(self._ready_timeout):
            self._emit_local("timeout", {"stage": "ready"})
            self.fail_local("failed", "ready_timeout", "adapter 未在期限内 ready")

    def send_go(self) -> None:
        """预算预留 + 政策复核完成后调用。此前 adapter 收到的任何副作用都算违约。"""
        with self._lock:
            if self._go_sent or self._terminal:
                return
            self._go_sent = True
            self._send("go", {})

    def cancel(self, reason: str = "user_requested") -> None:
        """先发 cancel 帧（如还来得及），宽限期后整树回收。不等待进程退出。"""
        with self._lock:
            if not self._cancel_sent:
                self._cancel_sent = True
                try:
                    self._send("cancel", {"reason": reason[:200]})
                except (BrokenPipeError, ValueError):
                    pass  # 进程已死：直接走回收
        self._kill_tree()

    # ---- 帧发送 ----

    def _send(self, name: str, payload: dict) -> None:
        if self._proc is None or self._proc.stdin is None:
            raise ProtocolViolation("stdin 不可写")
        frame = {"protocol_version": PROTOCOL_VERSION, name: payload}
        data = json.dumps(frame, ensure_ascii=False).encode("utf-8")
        if len(data) > MAX_FRAME_BYTES:
            raise ProtocolViolation("宿主帧超限")
        self._proc.stdin.write(data + b"\n")
        self._proc.stdin.flush()

    # ---- 帧接收 ----

    def _pump_stdout(self) -> None:
        proc, seen_result = self._proc, False
        try:
            for raw in proc.stdout:
                if len(raw) > MAX_FRAME_BYTES:
                    raise ProtocolViolation("帧超限")
                try:
                    frame = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise ProtocolViolation(f"非 JSON 帧：{exc}") from exc
                if not isinstance(frame, dict) or frame.get("protocol_version") != PROTOCOL_VERSION:
                    raise ProtocolViolation("协议版本不符")
                names = [k for k in frame if k in _ADAPTER_FRAMES]
                if len(names) != 1:
                    raise ProtocolViolation("一帧恰一个适配器事件")
                name, payload = names[0], frame[names[0]]
                if name == "result":
                    if len(raw) > MAX_RESULT_MANIFEST_BYTES:
                        raise ProtocolViolation("结果清单超限")
                    if seen_result:
                        # 拒绝重复 result 帧（契约：拒绝的是帧，不毒化已提交的终态）
                        self._emit("protocol_note", {"code": "duplicate_result"})
                        continue
                    seen_result = True
                self._dispatch(name, payload)
        except ProtocolViolation as exc:
            self.fail_local("failed", "protocol_violation", str(exc))
        except Exception as exc:  # 读线程意外死亡 → 明确 unknown，不冒充成功
            self.fail_local("unknown", "reader_died", f"{type(exc).__name__}: {exc}")

    def _dispatch(self, name: str, payload: dict) -> None:
        if name == "ready":
            if not isinstance(payload, dict):
                raise ProtocolViolation("ready 载荷非法")
            self._ready.set()
            self._emit("ready", payload)
            return
        if not self._ready.is_set():
            raise ProtocolViolation("ready 之前出现业务帧")
        if name in ("progress", "usage"):
            if not self._throttle(name):
                return  # 限频合并：丢弃而非排队（终态不丢）
            if name == "usage" and not isinstance(payload.get("sequence"), int):
                raise ProtocolViolation("usage 缺序号")
            self._emit(name, payload)
            return
        if name == "result":
            status = payload.get("status")
            if status not in ("succeeded", "failed"):
                raise ProtocolViolation(f"result.status 非法：{status!r}")
            with self._lock:
                self._terminal = {"frame": "result", "payload": payload}
            self._emit("result", payload)
            return
        if name == "stopped":
            with self._lock:
                if self._terminal is None:
                    self._terminal = {"frame": "stopped", "payload": payload}
            self._emit("stopped", payload)

    def _throttle(self, name: str) -> bool:
        now = time.monotonic()
        last = self._last_event_at.get(name, 0.0)
        if now - last < 1.0 / _EVENT_LIMITS[name]:
            return False
        self._last_event_at[name] = now
        return True

    def _emit(self, type_: str, payload: dict) -> None:
        try:
            self._on_event(type_, json.loads(json.dumps(payload, ensure_ascii=False)))  # 深拷贝=不可变交付
        except Exception:
            pass  # 事件回调失败不拖垮读线程；账本一致性由宿主侧保障

    def _emit_local(self, type_: str, payload: dict) -> None:
        self._emit(type_, {"local": True, **payload})

    # ---- 宿主侧终态（本地故障：超时/协议违规/读线程死亡） ----

    def fail_local(self, state: str, code: str, message: str) -> None:
        with self._lock:
            if self._terminal is not None:
                return
            self._terminal = {"frame": "local", "state": state, "code": code, "message": message}
        self._emit("local_failure", {"state": state, "code": code, "message": message})
        self._kill_tree()

    def terminal(self) -> dict | None:
        with self._lock:
            return self._terminal

    # ---- 进程所有权 ----

    def _kill_tree(self) -> None:
        proc = self._proc
        if proc is None or proc.poll() is not None:
            return
        try:
            if os.name == "posix":
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                time.sleep(min(0.2, GRACE_SECONDS / 25))
                if proc.poll() is None:
                    time.sleep(GRACE_SECONDS)
                    if proc.poll() is None:
                        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            else:
                # Windows：整树回收沿用 browser_transport.ProcessTree 思路（契约 §七），
                # D7 抽成进程所有权组件时统一；此处仅保底。
                subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                               capture_output=True, check=False)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            proc.wait(timeout=GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            pass

    def wait(self, timeout: float | None = None) -> int | None:
        proc = self._proc
        if proc is None:
            return None
        try:
            return proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return None

    def stderr_text(self) -> str:
        return self._stderr.text()

    def _pump_stderr(self) -> None:
        proc = self._proc
        try:
            for chunk in iter(lambda: proc.stderr.read(4096), b""):
                self._stderr.feed(chunk)
        except (ValueError, OSError):
            pass
