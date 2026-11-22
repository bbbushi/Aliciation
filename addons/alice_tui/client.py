"""HTTP/SSE client for Alice's public per-profile Unix socket."""
from __future__ import annotations

import hashlib
import http.client
import json
import os
from pathlib import Path
import socket
import stat
import threading
import urllib.parse

PREFIX = "/api/tui/v1"


def profile_id(root: Path) -> str:
    """档案身份：规范绝对路径按文件系统编码后的 SHA-256 前 24 位（宿主同式样）。"""
    return hashlib.sha256(os.fsencode(Path(root).resolve())).hexdigest()[:24]


def socket_path(profile: Path) -> Path:
    # Public path format documented by Alice, without importing the host.
    return Path.home() / ".alice" / f"tui-{profile_id(profile)}.sock"


class TuiApiError(Exception):
    def __init__(self, message: str, code: str = "", status: int = 0):
        super().__init__(message)
        self.code = code
        self.status = status


class StopWaiting:
    """Interrupt the local stream without cancelling the admitted host turn."""

    def __init__(self):
        self._lock = threading.Lock()
        self._socket: socket.socket | None = None
        self.stopped = False

    def attach(self, sock: socket.socket) -> None:
        with self._lock:
            if self.stopped:
                raise OSError("已停止等待")
            self._socket = sock.dup()

    def stop(self) -> None:
        with self._lock:
            self.stopped = True
            if self._socket is not None:
                try:
                    self._socket.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

    def close(self) -> None:
        with self._lock:
            if self._socket is not None:
                self._socket.close()
                self._socket = None


class UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, path: Path, timeout: float, stop: StopWaiting | None):
        super().__init__("localhost", timeout=timeout)
        self._path = path
        self._stop = stop

    def connect(self):
        uid = os.geteuid()
        directory, entry = self._path.parent.lstat(), self._path.lstat()
        if (directory.st_uid, stat.S_IMODE(directory.st_mode)) != (uid, 0o700) or not stat.S_ISDIR(directory.st_mode):
            raise PermissionError("Alice socket 目录权限无效")
        if (entry.st_uid, stat.S_IMODE(entry.st_mode)) != (uid, 0o600) or not stat.S_ISSOCK(entry.st_mode):
            raise PermissionError("Alice socket 权限无效")
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.settimeout(self.timeout)
            sock.connect(os.fspath(self._path))
            if self._stop is not None:
                self._stop.attach(sock)
        except BaseException:
            sock.close()
            raise
        self.sock = sock


class TuiClient:
    def __init__(self, path: Path):
        self.path = path

    def _open(self, method, path, body=None, *, timeout=2, stop=None):
        payload = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
        conn = UnixHTTPConnection(self.path, timeout, stop)
        try:
            headers = {"Content-Type": "application/json"} if body is not None else {}
            conn.request(method, PREFIX + path, body=payload, headers=headers)
            response = conn.getresponse()
            if response.status >= 400:
                with response:
                    detail = json.loads(response.read(16384))
                raise TuiApiError(detail.get("error") or f"HTTP {response.status}",
                                  detail.get("code", ""), response.status)
            return conn, response
        except BaseException:
            conn.close()
            raise

    def _request(self, method, path, body=None):
        try:
            conn, response = self._open(method, path, body)
            try:
                with response:
                    return json.loads(response.read())
            finally:
                conn.close()
        except (OSError, http.client.HTTPException, ValueError):
            raise TuiApiError("无法通过本机 socket 读取 Alice", "unreachable") from None

    def status(self) -> dict:
        return self._request("GET", "/status")

    def history(self, *, after_id=None, before_id=None, limit=30, session_epoch=None) -> dict:
        params = {"after_id": after_id, "before_id": before_id, "limit": limit, "session_epoch": session_epoch}
        query = urllib.parse.urlencode({key: value for key, value in params.items() if value is not None})
        return self._request("GET", f"/history?{query}")

    def request_status(self, request: dict) -> dict:
        return self._request("POST", "/chat/status", request)

    def chat_stream(self, request: dict, on_event, stop: StopWaiting) -> dict:
        try:
            conn, response = self._open("POST", "/chat", request, timeout=600, stop=stop)
            try:
                with response:
                    for line in response:
                        if not line.startswith(b"data: "):
                            continue
                        event = json.loads(line[6:])
                        if event.get("type") == "done":
                            return event
                        if event.get("type") == "error":
                            raise TuiApiError(event.get("message") or "聊天结果待核对",
                                              event.get("code") or "stream_broken")
                        on_event(event)
            finally:
                conn.close()
        except (OSError, http.client.HTTPException, ValueError):
            pass
        finally:
            stop.close()
        if stop.stopped:
            raise TuiApiError("已停止等待，宿主会继续处理", "stream_stopped")
        raise TuiApiError("连接中断，结果待核对", "stream_broken")
