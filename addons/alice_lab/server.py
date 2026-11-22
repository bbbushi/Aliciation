"""Independent, local-only Alice Lab dashboard. Uses only Alice public transports."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import http.client
import json
import os
import re
import secrets
import stat
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
MAX_RESULT = 8 * 1024 * 1024
CONTEXT_ASSETS = {
    "/context-inspector/": ("context-inspector/index.html", "text/html"),
    "/context-inspector/inspector.css": ("context-inspector/inspector.css", "text/css"),
    "/context-inspector/inspector.js": ("context-inspector/inspector.js", "application/javascript"),
    "/context-inspector/observer-client.js": ("context-inspector/observer-client.js", "application/javascript"),
    "/context-inspector/context-viewer.js": ("context-inspector/context-viewer.js", "application/javascript"),
    "/context-inspector/context-viewer.css": ("context-inspector/context-viewer.css", "text/css"),
}


class ReadError(ValueError):
    def __init__(self, code, message, status=400):
        super().__init__(message)
        self.code = code
        self.status = status


def launcher_home():
    root = Path.home() / ".alice-lab"
    root.mkdir(mode=0o700, exist_ok=True)
    info = root.stat()
    if (root.is_symlink() or not root.is_dir()
            or os.name != "nt" and (info.st_uid != os.getuid() or info.st_mode & 0o077)):
        raise ValueError("Lab 注册目录必须仅当前用户可访问")
    return root


def write_private(path, data):
    fd, name = tempfile.mkstemp(prefix=".lab-", dir=path.parent)
    partial = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False)
        partial.replace(path)
    finally:
        partial.unlink(missing_ok=True)


@contextmanager
def instance_lease(root):
    fd = os.open(root / "instance.lock", os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("Lab 实例锁无效")
        try:
            if os.name == "nt":
                import msvcrt
                if os.fstat(fd).st_size == 0:
                    os.write(fd, b"\0")
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            yield False
        else:
            yield True
    finally:
        os.close(fd)


def active_url(root):
    path = root / "endpoint.json"
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 1024:
            return None
        data = json.loads(path.read_bytes())
        port, token = data["port"], data["key"]
        if (type(port) is not int or not 1 <= port <= 65535 or not isinstance(token, str)
                or not re.fullmatch(r"[A-Za-z0-9_-]{43}", token)):
            return None
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=.35)
        try:
            origin = f"http://127.0.0.1:{port}"
            connection.request("POST", "/api", '{"action":"launcher_status"}',
                               {"Origin": origin, "X-Alice-Lab": token, "Content-Type": "application/json"})
            response = connection.getresponse()
            if response.status == 200 and json.loads(response.read(256)) == {"status": "alice-lab-ready"}:
                return f"{origin}/#{token}"
        finally:
            connection.close()
    except (ValueError, OSError, TypeError, KeyError, http.client.HTTPException):
        pass
    return None


def open_from_alice(root):
    if url := active_url(root):
        return 0 if webbrowser.open(url, new=2) else 1
    options = {"creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
    subprocess.Popen([sys.executable, "-I", str(Path(__file__).resolve()), "--no-open"],
                     cwd=HERE, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, **options)
    deadline = time.monotonic() + 6
    while time.monotonic() < deadline:
        if url := active_url(root):
            return 0 if webbrowser.open(url, new=2) else 1
        time.sleep(.12)
    return 1


class Lab:
    def __init__(self, command):
        self.command = command
        self.lock = threading.Lock()
        self.process = None
        self.session = None
        self.pair = None
        self.flags = {}
        self.connection_id = None

    def start(self, test_controls):
        if self.process is not None and self.process.poll() is None:
            raise ValueError("先退出当前隔离实例")
        nonce = secrets.token_hex(16)
        self.process = subprocess.Popen([*self.command, "desktop-host", "--lab-session",
                                         *(["--lab-test-controls"] if test_controls else [])],
                                        cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        env={**os.environ, "ALICE_DESKTOP_NONCE": nonce})
        raw = self.process.stdout.readline(MAX_RESULT + 1)
        frame = json.loads(raw) if raw and len(raw) <= MAX_RESULT else {}
        if (frame.get("event") != "ready" or frame.get("nonce") != nonce or frame.get("protocol") != 1
                or frame.get("lab_protocol") != "alice-lab/1"
                or not re.fullmatch(r"http://127\.0\.0\.1:\d{1,5}", frame.get("url", ""))):
            self.close_pipe()
            raise ValueError("后端没有就绪，或尚不支持此版本的 Lab")
        self.session = frame
        self.pair = None
        self.connection_id = uuid.uuid4().hex
        self.flags = {"test_controls": test_controls, "capture": False, "review": False, "search_quota": False}
        return self.connection()

    def connection(self):
        if self.session is not None and self.process is not None and self.process.poll() is None:
            return {key: self.session[key] for key in ("url", "app_version", "lab_profile")} | {
                "mode": "isolated", "lab_experiments": self.session.get("lab_experiments") is True,
                "connection_id": self.connection_id, **self.flags}
        if self.session is not None:
            self.close_pipe()
        if self.pair is not None:
            result = self.observe("schedule", {})
            return {key: result[key] for key in ("mode", "expires_at")} | {"connection_id": self.connection_id}
        return {"mode": None}

    def pipe(self, op, **params):
        if self.session is None or self.process is None or self.process.poll() is not None:
            raise ValueError("请先启动隔离实例")
        request_id = uuid.uuid4().hex
        frame = {"protocol": 1, "instance_id": self.session["instance_id"], "nonce": self.session["nonce"],
                 "request_id": request_id, "op": op, **params}
        self.process.stdin.write((json.dumps(frame, ensure_ascii=True) + "\n").encode("ascii"))
        self.process.stdin.flush()
        raw = self.process.stdout.readline(MAX_RESULT + 1)
        if not raw or len(raw) > MAX_RESULT:
            self.close_pipe()
            raise ValueError("回执未能确认，请勿重复发送")
        result = json.loads(raw)
        if any(result.get(key) != frame[key] for key in ("protocol", "instance_id", "nonce", "request_id")):
            self.close_pipe()
            raise ValueError("返回身份不匹配，连接已关闭")
        return {key: value for key, value in result.items() if key not in ("nonce", "instance_id")}

    def close_pipe(self):
        if self.process is not None and self.process.stdin is not None:
            self.process.stdin.close()
        self.session = None
        self.connection_id = None

    def observe(self, topic, params, *, pair=None):
        target = pair or self.pair
        if target is None:
            reply = self.pipe("lab_observe", topic=topic, params=params)
            if reply.get("status") != "ok":
                raise ReadError(reply.get("code") or "read_failed", reply.get("error", "读取未完成"))
            return {"mode": "isolated", "result": reply["result"]}
        port, token = target
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=6)
        try:
            connection.request("POST", "/api/lab/v1/read", json.dumps({"topic": topic, "params": params}),
                               {"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
            response = connection.getresponse()
            raw = response.read(MAX_RESULT + 1)
            if len(raw) > MAX_RESULT:
                raise ValueError("此份观察结果过大，请按请求 ID 查询")
            if response.status == 401 and target == self.pair:
                self.pair = None
                self.connection_id = None
            result = json.loads(raw)
            if response.status != 200:
                raise ReadError(result.get("code") or "read_failed",
                                result.get("error", "观察请求未完成"), response.status)
            return result
        finally:
            connection.close()

    def dispatch(self, body):
        action = body.get("action")
        params = body.get("params", {})
        if not isinstance(params, dict):
            raise ValueError("请求参数无效")
        if action == "connection":
            return self.connection()
        if action == "start":
            if set(params) != {"test_controls"} or type(params["test_controls"]) is not bool:
                raise ValueError("测试策略参数无效")
            return self.start(params["test_controls"])
        if action == "pair":
            if self.session is not None:
                raise ValueError("请先退出隔离实例，再切换到日常观察")
            key = params.get("key", "")
            match = re.fullmatch(r"([0-9]{1,5}):([A-Za-z0-9_-]{43})", key) if isinstance(key, str) else None
            if not match or not 1 <= int(match[1]) <= 65535:
                raise ValueError("请从 Alice 复制完整的观察授权码")
            pair = (int(match[1]), match[2])
            result = self.observe("schedule", {}, pair=pair)
            self.pair = pair
            self.connection_id = uuid.uuid4().hex
            return {key: result[key] for key in ("mode", "expires_at")} | {"connection_id": self.connection_id}
        if action == "disconnect":
            self.pair = None
            self.connection_id = None
            return {}
        if action == "stop":
            result = self.pipe("shutdown", reason="quit")
            self.close_pipe()
            return result
        if action == "observe":
            if body.get("connection_id") is not None and body["connection_id"] != self.connection_id:
                raise ReadError("connection_changed", "观察连接已变化，请重新打开观测器", 409)
            return self.observe(body.get("topic"), params)
        if self.pair is not None:
            raise ValueError("日常连接只读")
        if action == "control":
            command = body.get("command")
            reply = self.pipe("lab_control", action=command, params=params)
            if reply.get("status") == "accepted" and command in ("capture", "review", "search_quota"):
                self.flags[command] = params["enabled"]
            return reply
        if action == "chat":
            return self.pipe("lab_chat", params=params)
        if action == "experiment":
            return self.pipe("lab_experiment", params=params)
        raise ValueError("未知操作")


def handler_for(lab, session_key):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def safe_host(self):
            return self.headers.get_all("Host", []) == [f"127.0.0.1:{self.server.server_port}"]

        def write(self, code, raw, content_type, *, inspector=False):
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            frames = "'self'" if inspector else "'none'"
            self.send_header("Content-Security-Policy", f"default-src 'self'; frame-ancestors {frames}; base-uri 'none'")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            try:
                self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def reply(self, code, data):
            self.write(code, json.dumps(data, ensure_ascii=False).encode(), "application/json; charset=utf-8")

        def do_GET(self):
            assets = {"/": ("index.html", "text/html"), "/lab.js": ("lab.js", "application/javascript"),
                      "/lab.css": ("lab.css", "text/css"), **CONTEXT_ASSETS}
            if not self.safe_host() or self.path not in assets:
                self.reply(404, {"error": "页面不存在"})
                return
            name, kind = assets[self.path]
            self.write(200, (HERE / "web" / name).read_bytes(), kind + "; charset=utf-8",
                       inspector=self.path in CONTEXT_ASSETS)

        def do_POST(self):
            self.connection.settimeout(10)
            if (not self.safe_host() or self.path != "/api"
                    or self.headers.get_all("Origin", []) != [f"http://127.0.0.1:{self.server.server_port}"]
                    or not secrets.compare_digest(self.headers.get("X-Alice-Lab", "").encode("utf-8"), session_key)
                    or len(self.headers.get_all("X-Alice-Lab", [])) != 1
                    or self.headers.get("Transfer-Encoding") or self.headers.get_content_type() != "application/json"):
                self.reply(403, {"error": "只接受本页操作"})
                return
            try:
                lengths = self.headers.get_all("Content-Length", [])
                size = int(lengths[0]) if len(lengths) == 1 else 0
                if not 0 < size <= 12000:
                    self.close_connection = True
                    raise ValueError("请求过大")
                body = json.loads(self.rfile.read(size))
                if not isinstance(body, dict):
                    raise ValueError("请求格式错误")
                if body == {"action": "launcher_status"}:
                    self.reply(200, {"status": "alice-lab-ready"})
                    return
                if not lab.lock.acquire(blocking=False):
                    self.reply(409, {"code": "busy", "error": "正在处理另一项操作"})
                    return
                try:
                    if body == {"action": "exit_lab", "params": {}}:
                        lab.close_pipe()
                        reply = {"status": "closed"}
                    else:
                        reply = lab.dispatch(body)
                finally:
                    lab.lock.release()
                self.reply(200, reply)
                if reply.get("status") == "closed":
                    threading.Thread(target=self.server.shutdown, daemon=True).start()
            except ReadError as exc:
                self.reply(exc.status, {"error": str(exc)[:180], "code": exc.code})
            except ValueError as exc:
                self.reply(400, {"error": str(exc)[:180]})
            except (OSError, http.client.HTTPException):
                self.reply(503, {"error": "连接未能确认，请检查目标实例，勿重复提交"})

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", type=Path, help="已支持 Lab 的打包后端路径；默认当前源码")
    parser.add_argument("--no-open", action="store_true", help="只显示本地地址")
    commands = parser.add_mutually_exclusive_group()
    commands.add_argument("--register", action="store_true", help="将此独立 Lab 注册为 Alice 的打开入口")
    commands.add_argument("--open-from-alice", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    root = launcher_home()
    if args.register:
        script = Path(__file__).resolve()
        write_private(root / "launcher.json", {"protocol": "alice-lab-launcher/1",
                     "python": str(Path(sys.executable).absolute()), "script": str(script),
                     "sha256": hashlib.sha256(script.read_bytes()).hexdigest()})
        print("已注册。可在 Alice 的「设置 → 外部观察」打开 Lab。")
        return 0
    if args.open_from_alice:
        return open_from_alice(root)
    with instance_lease(root) as owned:
        if not owned:
            if not args.no_open and not args.backend and (url := active_url(root)):
                return 0 if webbrowser.open(url, new=2) else 1
            print("已有 Alice Lab 实例；如需更换测试后端，请先退出它。", file=sys.stderr)
            return 1
        return serve(root, args)


def serve(root, args):
    lab = Lab([str(args.backend.resolve())] if args.backend else [sys.executable, "-m", "alice"])
    session_key = secrets.token_urlsafe(32)
    endpoint = root / "endpoint.json"
    with ThreadingHTTPServer(("127.0.0.1", 0), handler_for(lab, session_key.encode("ascii"))) as server:
        url = f"http://127.0.0.1:{server.server_port}/#{session_key}"
        try:
            write_private(endpoint, {"port": server.server_port, "key": session_key})
            print(f"Alice Lab: {url}", flush=True)
            if not args.no_open:
                threading.Timer(.1, lambda: webbrowser.open(url, new=2)).start()
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            lab.close_pipe()
            endpoint.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
