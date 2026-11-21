"""Private per-profile AF_UNIX listener for the terminal client."""
from __future__ import annotations

import ctypes
import errno
import hashlib
import os
from pathlib import Path
import socket
import socketserver
import stat
import struct
import sys
import threading


class TrackedConnections:
    """Keep accepted workers in the shared shutdown gate until their handlers exit."""

    daemon_threads = False
    block_on_close = False

    def get_request(self):
        request, address = super().get_request()
        try:
            request.settimeout(self.request_timeout)
        except BaseException:
            request.close()
            raise
        return request, address

    def process_request(self, request, client_address):
        self._gate.connection_opened(request)
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._gate.connection_done(request)
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._gate.connection_done(request)


def socket_path(profile_root: Path) -> Path:
    # Public path format mirrored by addons/alice_tui/client.py.
    profile_id = hashlib.sha256(os.fsencode(profile_root.resolve())).hexdigest()[:24]
    return Path.home() / ".alice" / f"tui-{profile_id}.sock"


def peer_uid(conn: socket.socket) -> int | None:
    try:
        if sys.platform == "darwin":
            getpeereid = ctypes.CDLL(None, use_errno=True).getpeereid
            uid, gid = ctypes.c_uint32(), ctypes.c_uint32()
            if getpeereid(conn.fileno(), ctypes.byref(uid), ctypes.byref(gid)) == 0:
                return uid.value
        elif hasattr(socket, "SO_PEERCRED"):
            creds = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("iII"))
            return struct.unpack("iII", creds)[1]
    except (OSError, AttributeError, ValueError, struct.error):
        pass
    return None


def _unlink_if_owned(path: Path, identity: os.stat_result) -> None:
    try:
        entry = path.lstat()
        if stat.S_ISSOCK(entry.st_mode) and os.path.samestat(entry, identity):
            path.unlink()
    except FileNotFoundError:
        pass


def start_unix_listener(profile_root, handler, gate, timeout):
    if not hasattr(socketserver, "ThreadingUnixStreamServer") or not (
        sys.platform == "darwin" or hasattr(socket, "SO_PEERCRED")
    ):
        return None
    uid = os.geteuid()
    path = socket_path(profile_root)
    path.parent.mkdir(mode=0o700, exist_ok=True)
    directory = path.parent.lstat()
    if not stat.S_ISDIR(directory.st_mode) or directory.st_uid != uid or stat.S_IMODE(directory.st_mode) != 0o700:
        raise PermissionError("终端 socket 目录必须归当前账户所有且权限为 0700")
    # ChatService already holds this profile's instance lock. Also check any
    # existing endpoint before treating it as stale, including older servers.
    try:
        previous = path.lstat()
    except FileNotFoundError:
        previous = None
    if previous is not None:
        if not stat.S_ISSOCK(previous.st_mode) or previous.st_uid != uid:
            raise PermissionError("终端 socket 路径已被其他文件占用")
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
            probe.settimeout(0.25)
            try:
                probe.connect(os.fspath(path))
            except OSError as exc:
                if exc.errno not in (errno.ECONNREFUSED, errno.ENOENT):
                    raise
            else:
                raise OSError("该档案的终端 socket 已有监听器")
        _unlink_if_owned(path, previous)

    class UnixListener(TrackedConnections, socketserver.ThreadingUnixStreamServer):
        def verify_request(self, request, client_address):
            return peer_uid(request) == uid

        def stop(self):
            try:
                self.shutdown()
            finally:
                self.server_close()
                _unlink_if_owned(path, bound)

    server = UnixListener(os.fspath(path), handler, bind_and_activate=False)
    server._gate = gate
    server.request_timeout = timeout
    bound = None
    try:
        server.server_bind()
        bound = path.lstat()
        path.chmod(0o600)
        server.server_activate()
        threading.Thread(target=server.serve_forever, name="alice-tui-listener", daemon=True).start()
    except BaseException:
        server.server_close()
        if bound is not None:
            _unlink_if_owned(path, bound)
        raise
    return server
