"""Fixed optional browser-reader protocol; core remains standard-library only.

Only the dedicated profile path crosses into the addon. Account cookies never
cross back. Each browser resource/redirect requests admission before proceeding.
"""
from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import threading
import time
from urllib.parse import urlsplit

from .runtime_paths import RESOURCE_ROOT

ROOT = RESOURCE_ROOT
ADDON = ROOT / "addons" / "browser_reader"
PLATFORMS = ("bilibili", "zhihu")


class BrowserError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def edge_path():
    for directory in (os.environ.get("PROGRAMFILES(X86)"), os.environ.get("PROGRAMFILES")):
        if directory:
            path = Path(directory) / "Microsoft" / "Edge" / "Application" / "msedge.exe"
            if path.is_file():
                return path
    return None


def availability():
    import sys
    if getattr(sys, "frozen", False):
        return False, "此桌面包尚未适配独立浏览器读取组件"
    if os.name != "nt":
        return False, "浏览器读取组件目前支持 Windows Edge"
    if not edge_path():
        return False, "未找到 Microsoft Edge"
    if not (ADDON / ".venv" / "Scripts" / "python.exe").is_file() or not (ADDON / ".venv" / "alice-runtime.json").is_file():
        return False, "请先安装独立浏览器组件：python addons/browser_reader/install.py"
    return True, "独立浏览器已就绪；是否需要登录以实际访问结果为准"


def profile_path(user_dir, platform):
    if platform not in PLATFORMS:
        raise BrowserError("browser_scope")
    return Path(user_dir).resolve() / "browser_sessions" / platform


def open_browser(user_dir, platform):
    """User-requested interactive window. No remote-debug port or account import."""
    executable = edge_path()
    if not executable or platform not in PLATFORMS:
        raise BrowserError("browser_missing")
    profile = profile_path(user_dir, platform)
    profile.mkdir(parents=True, exist_ok=True)
    url = "https://www.bilibili.com/" if platform == "bilibili" else "https://www.zhihu.com/"
    return subprocess.Popen([str(executable), "--user-data-dir=" + str(profile), "--no-first-run",
                             "--disable-background-mode", "--new-window", url],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def allowed_request(platform, url, method, resource):
    """Independent host scope check; never trusts a returned page's destinations."""
    if not isinstance(url, str) or len(url) > 16384 or any(ord(c) < 32 for c in url):
        return False
    try:
        p = urlsplit(url)
        if p.scheme != "https" or p.port not in (None, 443) or p.username or p.password:
            return False
    except ValueError:
        return False
    if method not in ("GET", "HEAD") or resource not in ("Document", "Script", "Stylesheet", "XHR", "Fetch", "Other"):
        return False
    host = p.hostname or ""
    if platform == "bilibili":
        if host in ("www.bilibili.com", "search.bilibili.com"):
            return (p.path in ("/", "/all", "/video") or bool(re.fullmatch(r"/video/BV[0-9A-Za-z]{10}/?", p.path))
                    or resource in ("Script", "Stylesheet"))
        if host == "api.bilibili.com":
            return p.path in ("/x/web-interface/nav", "/x/web-interface/view", "/x/frontend/finger/spi",
                              "/x/web-interface/wbi/search/type", "/x/web-interface/wbi/search/all/v2",
                              "/x/web-interface/search/type", "/x/web-interface/search/all/v2")
        return host.endswith(".hdslb.com") and resource in ("Script", "Stylesheet")
    if platform == "zhihu":
        if host == "www.zhihu.com":
            return p.path == "/" or bool(re.fullmatch(r"/api/v4/(?:search_v3|answers/\d+)", p.path))
        return host.endswith(".zhimg.com") and resource in ("Script", "Stylesheet")
    return False


class ProcessTree:
    """Windows job closes the worker, Playwright driver and Edge together."""
    def __init__(self, process):
        from ctypes import wintypes as w
        class Basic(ctypes.Structure):
            _fields_ = [("process_time", ctypes.c_longlong), ("job_time", ctypes.c_longlong),
                        ("flags", w.DWORD), ("min_ws", ctypes.c_size_t), ("max_ws", ctypes.c_size_t),
                        ("active", w.DWORD), ("affinity", ctypes.c_size_t), ("priority", w.DWORD), ("scheduling", w.DWORD)]
        class IO(ctypes.Structure):
            _fields_ = [(n, ctypes.c_ulonglong) for n in ("r", "w", "o", "rb", "wb", "ob")]
        class Extended(ctypes.Structure):
            _fields_ = [("basic", Basic), ("io", IO), ("pm", ctypes.c_size_t), ("jm", ctypes.c_size_t),
                        ("peakp", ctypes.c_size_t), ("peakj", ctypes.c_size_t)]
        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.CreateJobObjectW.argtypes = [ctypes.c_void_p, w.LPCWSTR]
        api.CreateJobObjectW.restype = w.HANDLE
        api.SetInformationJobObject.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD]
        api.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
        api.CloseHandle.argtypes = [w.HANDLE]
        self.api, self.handle = api, api.CreateJobObjectW(None, None)
        limits = Extended()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.handle or not api.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            self.close()
            raise BrowserError("browser_supervision")
        if not api.AssignProcessToJobObject(self.handle, w.HANDLE(process._handle)):
            self.close()
            raise BrowserError("browser_supervision")

    def close(self):
        if self.handle:
            self.api.CloseHandle(self.handle)
            self.handle = None


def run_browser(payload, *, user_dir, deadline, admit, settle, check, unreaped, authorize=lambda action: action()):
    if not availability()[0]:
        raise BrowserError("browser_missing")
    platform = payload["platform"]
    initial = {"version": 1, **payload, "profile": str(profile_path(user_dir, platform)),
               "executable": str(edge_path()), "timeout_seconds": max(1, deadline - time.monotonic())}
    env = {k: v for k, v in os.environ.items() if k.upper() in
           {"SYSTEMROOT", "WINDIR", "PATH", "PATHEXT", "TEMP", "TMP", "LANG", "LC_ALL"}}
    env["PYTHONIOENCODING"] = "utf-8"
    process = tree = reader = None
    events = queue.Queue(maxsize=128)
    stopped = threading.Event()
    grants = {}
    def send(frame):
        raw = json.dumps(frame, ensure_ascii=True, allow_nan=False).encode() + b"\n"
        if len(raw) > 65536:
            raise BrowserError("input_limit")
        process.stdin.write(raw)
        process.stdin.flush()
    def read():
        try:
            while not stopped.is_set():
                raw = process.stdout.readline(256 * 1024 + 1)
                value = json.loads(raw) if raw.endswith(b"\n") and len(raw) <= 256 * 1024 else None
                while not stopped.is_set():
                    try:
                        events.put(value, timeout=.1)
                        break
                    except queue.Full:
                        pass
                if value is None:
                    break
        except (OSError, ValueError):
            try:
                events.put_nowait(None)
            except queue.Full:
                pass
    try:
        check()
        process = subprocess.Popen([str(ADDON / ".venv" / "Scripts" / "python.exe"), str(ADDON / "worker.py")],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   cwd=str(ADDON), env=env, creationflags=subprocess.CREATE_NO_WINDOW)
        tree = ProcessTree(process)
        reader = threading.Thread(target=read, daemon=True, name="alice-browser-reader")
        reader.start()
        send(initial)  # Small bounded frame; worker is already reading stdin.
        started, serial = False, 0
        while True:
            check()
            if time.monotonic() >= deadline:
                raise BrowserError("timeout")
            try:
                frame = events.get(timeout=.05)
            except queue.Empty:
                continue
            if not isinstance(frame, dict) or frame.get("version") != 1:
                raise BrowserError("browser_protocol")
            kind = frame.get("type")
            if kind == "ready" and not started:
                check()
                authorize(lambda: send({"version": 1, "type": "go"}))
                started = True
            elif kind == "request" and started:
                serial += 1
                if frame.get("id") != str(serial) or not allowed_request(platform, frame.get("url"), frame.get("method"), frame.get("resource")):
                    raise BrowserError("browser_scope")
                token = admit(frame)
                grants[frame["id"]] = token
                check()
                authorize(lambda: send({"version": 1, "id": frame["id"], "type": "grant", "max_bytes": payload["response_bytes"]}))
            elif kind == "finished" and frame.get("id") in grants:
                amount = frame.get("bytes")
                if amount is not None and (type(amount) is not int or not 0 <= amount <= payload["response_bytes"]):
                    raise BrowserError("browser_protocol")
                settle(grants.pop(frame["id"]), amount)
            elif kind == "result" and started:
                if frame.get("ok") is not True:
                    value = frame.get("code", "browser_failed")
                    raise BrowserError(value if isinstance(value, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,59}", value) else "browser_failed")
                if not isinstance(frame.get("data"), dict):
                    raise BrowserError("browser_protocol")
                return frame["data"]
            else:
                raise BrowserError("browser_protocol")
    finally:
        stopped.set()
        if tree:
            tree.close()
        if process:
            if process.poll() is None:
                process.kill()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                unreaped(process)
            if reader:
                reader.join(timeout=5)
                if reader.is_alive():
                    unreaped(process)
            for stream in (process.stdin, process.stdout):
                stream.close()
        for token in grants.values():
            settle(token, None)  # Interrupted resources retain their reservation.


def main():
    """Explicit real-environment CLI diagnosis; never opens the conversation DB."""
    import argparse
    parser = argparse.ArgumentParser(description="Alice independent browser reader")
    parser.add_argument("operation", choices=("open", "search", "read"))
    parser.add_argument("platform", choices=PLATFORMS)
    parser.add_argument("--query", default="科技")
    parser.add_argument("--url")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--requests", type=int, default=120)
    parser.add_argument("--response-bytes", type=int, default=8 * 1024 * 1024)
    parser.add_argument("--timeout", type=float, default=45)
    parser.add_argument("--show-window", action="store_true", help="Use a normal Edge window for this manual diagnosis")
    parser.add_argument("--user-dir", help="独立用户根目录（绝对路径）")
    args = parser.parse_args()
    if min(args.limit, args.requests, args.response_bytes, args.timeout) <= 0:
        parser.error("limits must be positive")
    from .runtime_paths import configure_user_root
    try:
        user_dir = configure_user_root(args.user_dir)
    except (ValueError, RuntimeError) as exc:
        parser.error(str(exc))
    if args.operation == "open":
        open_browser(user_dir, args.platform)
        print("Opened dedicated browser. Log in yourself if needed, then close its window.")
        return
    from .discovery_network import resolve_system_proxy
    usage = {"network_requests": 0, "received_bytes": 0, "unknown_transfers": 0, "model_calls": 0}
    def admit(frame):
        if usage["network_requests"] >= args.requests:
            raise BrowserError("quota_exceeded")
        usage["network_requests"] += 1
        return frame["id"]
    def settle(_token, amount):
        if amount is None:
            usage["unknown_transfers"] += 1
        else:
            usage["received_bytes"] += amount
    try:
        payload = {"op": args.operation, "platform": args.platform, "limit": args.limit,
                   "response_bytes": args.response_bytes, "network_proxy": resolve_system_proxy(), "show_window": args.show_window}
        payload.update({"query": args.query} if args.operation == "search" else {"url": args.url})
        result = run_browser(payload, user_dir=user_dir, deadline=time.monotonic() + args.timeout,
                             admit=admit, settle=settle, check=lambda: None,
                             unreaped=lambda _: (_ for _ in ()).throw(BrowserError("worker_not_reaped")))
        safe = result if args.operation == "search" else {k: result[k] for k in ("url", "coverage")}
        if args.operation == "read":
            safe["text_chars"] = len(result["text"])
        print(json.dumps({"ok": True, "data": safe, "usage": usage}, ensure_ascii=True))
    except BrowserError as exc:
        print(json.dumps({"ok": False, "code": exc.code, "usage": usage}))


if __name__ == "__main__":
    main()
