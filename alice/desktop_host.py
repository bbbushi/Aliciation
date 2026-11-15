"""Public desktop-host v1 transport over private inherited pipes.

Implements status, quit and offline update preparation, not network installation.
It never simulates browser headers or exposes arbitrary commands/paths.
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import uuid

MAX_FRAME = 16384


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate field")
        result[key] = value
    return result


class DesktopHost:
    def __init__(self, reader, writer, nonce: str):
        if not re.fullmatch(r"[0-9a-f]{32}", nonce):
            raise ValueError("desktop launch identity missing")
        self.reader, self.writer = reader, writer
        self.nonce = nonce
        self.instance_id = uuid.uuid4().hex
        self._lock = threading.Lock()
        self._state = "starting"
        self._stop = threading.Event()
        self._reader_thread: threading.Thread | None = None
        self.extract_on_exit = True
        self.maintenance_activation = False
        self.exit_reason = "quit"
        self._replies: dict[str, tuple[dict, dict]] = {}

    def emit(self, **frame):
        with self._lock:
            try:
                self.writer.write(json.dumps({"protocol": 1, "instance_id": self.instance_id,
                                              "nonce": self.nonce, **frame}, ensure_ascii=False) + "\n")
                self.writer.flush()
            except (OSError, ValueError):
                pass  # Pipe loss is handled by the input owner, never by replay.

    def ready(self, httpd, gate, service):
        from . import __version__
        from .profile_maintenance import activation_ready
        from .runtime_paths import user_root

        activation_ready(user_root())
        self._state = "ready"
        self.emit(event="ready", url=f"http://127.0.0.1:{httpd.server_port}",
                  app_version=__version__, capabilities=["status", "shutdown", "maintenance_status"], updates=False)

        def shutdown(*, lost=False, reserved=False):
            if lost:
                self.extract_on_exit = False
            self._state = "quiescing"
            if reserved or gate.begin():
                try:
                    service.begin_shutdown()  # Stop background admission immediately, too.
                finally:
                    httpd.shutdown()  # Server owner performs drain/close on its own thread.

        def read():
            while not self._stop.is_set():
                try:
                    raw = self.reader.readline(MAX_FRAME + 1)
                except OSError:
                    if not self._stop.is_set():
                        shutdown(lost=True)
                    return
                if self._stop.is_set():
                    return
                if not raw:
                    shutdown(lost=True)
                    return
                try:
                    if len(raw) > MAX_FRAME or not raw.endswith(b"\n"):
                        shutdown(lost=True)
                        return
                    frame = json.loads(raw, object_pairs_hook=_unique_object)
                    if (not isinstance(frame, dict) or type(frame.get("protocol")) is not int or frame.get("protocol") != 1
                            or frame.get("instance_id") != self.instance_id
                            or frame.get("nonce") != self.nonce
                            or not isinstance(frame.get("request_id"), str)
                            or not re.fullmatch(r"[0-9a-f]{32}", frame["request_id"])):
                        raise ValueError("protocol")
                    request_id = frame["request_id"]
                    old = self._replies.get(request_id)
                    if old is not None:
                        if old[0] != frame:
                            raise ValueError("request_id reused")
                        self.emit(**old[1])
                        continue
                    operation = frame.get("op")
                    allowed = {"protocol", "instance_id", "nonce", "request_id", "op"}
                    if operation == "shutdown":
                        allowed.add("reason")
                    if set(frame) != allowed:
                        raise ValueError("fields")
                    accepted_shutdown = False
                    if operation == "status":
                        reply = {"request_id": request_id, "status": self._state, "updates": False}
                    elif operation == "maintenance_status":
                        from .profile_maintenance import status
                        try:
                            reply = {"request_id": request_id, "maintenance": status(user_root())}
                        except (ValueError, OSError, KeyError):
                            reply = {"request_id": request_id, "error": "无法读取维护记录，请检查本机日志"}
                    elif (operation == "shutdown" and frame.get("reason") in {"quit", "update", "recovery"}
                          and self._state == "ready"):
                        accepted_shutdown = gate.begin()
                        reply = {"request_id": request_id, "status": "quiescing" if accepted_shutdown else "busy"}
                    else:
                        reply = {"request_id": request_id, "status": "unsupported"}
                    if len(self._replies) >= 128:
                        self._replies.pop(next(iter(self._replies)))
                    self._replies[request_id] = (frame, reply)
                    self.emit(**reply)
                    if accepted_shutdown:
                        self.exit_reason = frame["reason"]
                        if self.exit_reason != "quit":
                            self.extract_on_exit = False
                        shutdown(reserved=True)
                except (ValueError, TypeError, UnicodeError, RecursionError):
                    self.emit(event="protocol_error", error="控制帧无效")

        def own_reader():
            try:
                read()
            finally:
                self.reader.close()

        self._reader_thread = threading.Thread(target=own_reader, name="alice-desktop-control", daemon=True)
        self._reader_thread.start()

    def closed(self, code, result):
        from . import profile_maintenance as maintenance
        from .runtime_paths import user_root
        if self.exit_reason in {"update", "recovery"}:
            try:
                if code != 200:
                    maintenance.mark_recovery(user_root())
                elif self.exit_reason == "update":
                    self.emit(event="backup_started")
                    backup = maintenance.prepare_update(user_root())
                    self.emit(event="backup_complete", backup=backup)
                else:
                    maintenance.mark_recovery(user_root())
            except BaseException as exc:
                print(f"（更新前备份未完成：{type(exc).__name__}）", file=sys.stderr)
                code = 500
                self.emit(event="backup_failed", error=maintenance.public_error(exc))
        self._state = "closed" if code == 200 else "failed"
        self._stop.set()
        # The native owner closes its input pipe on this frame, unblocking read.
        self.emit(event="closed", status=self._state, clean=code == 200, reason=self.exit_reason)

    def close_control(self):
        self._stop.set()
        if self._reader_thread is None:
            self.reader.close()
        else:
            self._reader_thread.join(timeout=2)
            if self._reader_thread.is_alive():
                # A lost/unresponsive host must not block shutdown indefinitely.
                # The dedicated unbuffered reader has no BufferedReader lock.
                print("（桌面控制管道尚未释放，结束后台读取等待）", file=sys.stderr)


def main(*, port: int = 0, config_path=None) -> int:
    protocol_output = sys.stdout
    sys.stdout = sys.stderr  # stdout is exclusively the framed public transport.
    host = None
    control_input = None
    lease = None
    try:
        # Do not let a daemon thread hold sys.stdin's buffered lock during
        # interpreter finalization. The input owner closes this duplicate only.
        control_input = os.fdopen(os.dup(sys.stdin.fileno()), "rb", buffering=0)
        host = DesktopHost(control_input, protocol_output, os.environ.get("ALICE_DESKTOP_NONCE", ""))
        from .instance_lock import ProfileLease
        from .runtime_paths import user_root
        from . import profile_maintenance as maintenance
        lease = ProfileLease(user_root()).acquire()
        try:
            host.maintenance_activation = maintenance.begin_activation(user_root(), host.nonce)
        except maintenance.MaintenanceRequired:
            host.emit(event="maintenance_required", maintenance=maintenance.status(user_root()))
            return 0
        from .server import run
        run(port=port, open_browser=False, config_path=config_path, host_control=host)
        return 0
    except BaseException as exc:
        print(f"桌面后端启动失败：{type(exc).__name__}", file=sys.stderr)
        if host is not None:
            host.emit(event="failed", error="桌面后端未能启动，请检查本机日志", kind=type(exc).__name__)
        return 1
    finally:
        if host is not None:
            host.close_control()
        elif control_input is not None:
            control_input.close()
        if lease is not None:
            lease.release()
        sys.stdout = protocol_output
