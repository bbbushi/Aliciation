"""本地图形界面：python -m alice serve → http://127.0.0.1:<port>

纯标准库实现（无第三方依赖）：静态页 + JSON API + SSE 流式对话。
只绑定回环地址，不出本机（数据本地不变量）。
"""
from __future__ import annotations

import json
import re
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .config import Config, PROJECT_ROOT, USER_DIR, load
from .config_edit import ConfigConflict
from .channels import ChannelError
from .local_connector import ConnectorSpec, LocalConnectorManager
from .runtime_paths import runtime_distribution
from .service import ChatService
from .lab_observation import ObservationGrant
from .local_socket import TrackedConnections, start_unix_listener

WEB_DIR = PROJECT_ROOT / "web"
DEFAULT_PORT = 8800
REQUEST_IO_TIMEOUT = 30.0
CHANNEL_PREFIX = "/api/channels/v1"
TUI_PREFIX = "/api/tui/v1"
GUI_READ_PATHS = {"/api/state", "/api/history", "/api/persona", "/api/settings",
                  "/api/metrics", "/api/chat/activity", "/api/user-intro"}
GUI_WRITE_PATHS = {"/api/persona", "/api/seed", "/api/reset", "/api/settings",
                   "/api/metrics/toggle", "/api/persona/expression",
                   "/api/persona/expression-update", "/api/persona/residency",
                   "/api/persona/material", "/api/user-intro"}


def _strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("JSON 字段重复")
        result[key] = value
    return result


def _invalid_json_constant(_):
    raise ValueError("JSON 数值无效")


def _json_bytes(obj) -> bytes:
    return json.dumps(obj, ensure_ascii=False).encode("utf-8")


def _restart_command(config_path: Path, port: int) -> str:
    """PowerShell literals preserve spaces and quotes in this instance's paths."""
    def literal(value) -> str:
        return "'" + str(value).replace("'", "''") + "'"

    return (
        f"Set-Location -LiteralPath {literal(PROJECT_ROOT)}\n"
        "& " + " ".join(literal(arg) for arg in _runtime_restart_args(config_path, port))
    )


def _runtime_restart_args(config_path: Path, port: int) -> list[str]:
    from .runtime_process import backend_command

    return backend_command("--user-dir", str(USER_DIR), "--config", str(config_path),
                           "serve", "--port", str(port))


class _ShutdownGate:
    """Stop admitting API work, then let the server owner drain it before closing Store."""

    def __init__(self):
        self._cv = threading.Condition()
        self._active = 0
        self._connections: set[object] = set()
        self._closing = False
        self._finished = threading.Event()
        self._result: tuple[int, dict] | None = None

    def admit(self) -> bool:
        with self._cv:
            if self._closing:
                return False
            self._active += 1
            return True

    def leave(self) -> None:
        with self._cv:
            self._active -= 1
            self._cv.notify_all()

    def begin(self) -> bool:
        with self._cv:
            if self._closing:
                return False
            self._closing = True
            self._cv.notify_all()
            return True

    def wait_idle(self) -> None:
        with self._cv:
            while self._active or self._connections:
                self._cv.wait()

    def connection_opened(self, request) -> None:
        with self._cv:
            self._connections.add(request)

    def connection_done(self, request) -> None:
        with self._cv:
            self._connections.discard(request)
            self._cv.notify_all()

    def detach_shutdown_connection(self, request) -> None:
        """The shutdown handler waits for drain and must not wait on itself."""
        self.connection_done(request)

    def finish(self, code: int, result: dict) -> None:
        self._result = (code, result)
        self._finished.set()

    def wait_result(self) -> tuple[int, dict]:
        self._finished.wait()
        assert self._result is not None
        return self._result


class _DrainingHTTPServer(TrackedConnections, ThreadingHTTPServer):
    def __init__(self, address, gate: _ShutdownGate):
        self._gate = gate
        self.request_timeout = REQUEST_IO_TIMEOUT
        super().__init__(address, BaseHTTPRequestHandler)


def make_handler(svc: ChatService, gate: _ShutdownGate, runtime_info: dict, connectors=None,
                 *, terminal=False, on_stop=None):
    observation_grant = None if terminal else ObservationGrant(svc)

    class Handler(BaseHTTPRequestHandler):
        server_version = "AliceGUI/0.1"

        def log_message(self, fmt, *args):  # 只留错误，安静运行
            pass

        # ---------- 基础 ----------

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            try:
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                # A cancelled browser request has no recipient for another response.
                # Keep this boundary around socket writes, not business execution.
                self.close_connection = True

        def _json(self, obj, code: int = 200) -> None:
            self._send(code, _json_bytes(obj), "application/json; charset=utf-8")

        def _file(self, name: str, ctype: str) -> None:
            path = WEB_DIR / name
            if not path.exists():
                self._json({"error": "not found"}, 404)
            else:
                self._send(200, path.read_bytes(), ctype)  # _send 已全局 Cache-Control: no-store

        def _body(self) -> dict:
            return self._bounded_body(1024 * 1024)

        def _bounded_body(self, maximum: int = 16384) -> dict:
            lengths = self.headers.get_all("Content-Length", [])
            if self.headers.get("Transfer-Encoding") or len(lengths) != 1:
                raise ValueError("请求必须提供唯一的内容长度")
            try:
                n = int(lengths[0])
            except (ValueError, TypeError) as exc:
                raise ValueError("请求长度无效") from exc
            if not 0 < n <= maximum:
                raise ValueError("请求过大或为空")
            raw = self.rfile.read(n)
            if len(raw) != n:
                raise ValueError("请求内容不完整")
            try:
                value = json.loads(raw, object_pairs_hook=_strict_object,
                                   parse_constant=_invalid_json_constant)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError("请求内容不是有效 JSON") from exc
            if not isinstance(value, dict):
                raise ValueError("请求必须是 JSON 对象")
            return value

        def _trusted_intent(self, intent: str, *, json_body: bool = False) -> bool:
            """Only the local GUI may issue sensitive requests; a custom header blocks forms."""
            if self.headers.get("Authorization") or len(self.headers.get_all("Host", [])) != 1:
                return False
            host = self.headers.get("Host", "")
            port = self.server.server_port
            if host not in (f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"):
                return False
            origin = self.headers.get("Origin")
            if self.command == "POST":
                if origin != f"http://{host}":
                    return False
            elif origin and origin != f"http://{host}":
                return False
            if self.headers.get("Sec-Fetch-Site") not in (None, "same-origin", "none"):
                return False
            if self.headers.get("X-Alice-Intent") != intent:
                return False
            if json_body and self.headers.get_content_type() != "application/json":
                return False
            return True

        def _trusted_json(self, intent: str) -> bool:
            """POST helper for shutdown and editable model configuration routes."""
            return self._trusted_intent(intent, json_body=True)

        def _lab_observation(self, *, grant=False):
            if urlparse(self.path).query:
                raise ValueError("Lab 接口不接受查询参数")
            if grant:
                if (not self._trusted_json("lab-pairing")
                        or len(self.headers.get_all("Origin", [])) != 1
                        or len(self.headers.get_all("X-Alice-Intent", [])) != 1):
                    raise ChannelError(403, "gui_only", "只能在 Alice 窗口授权")
                body = self._bounded_body(256)
                if body == {"action": "revoke"}:
                    observation_grant.revoke()
                    self._json({"status": "revoked"})
                elif set(body) == {"action", "mode"} and body["action"] == "issue":
                    self._json({"status": "ok", **observation_grant.issue(body["mode"]),
                                "port": self.server.server_port})
                else:
                    raise ValueError("观察授权参数无效")
                return
            port = self.server.server_port
            auth = self.headers.get_all("Authorization", [])
            match = re.fullmatch(r"Bearer ([A-Za-z0-9_-]{43})", auth[0]) if len(auth) == 1 else None
            if (self.headers.get_all("Host", []) != [f"127.0.0.1:{port}"]
                    or any(self.headers.get(name) is not None for name in ("Origin", "Sec-Fetch-Site", "X-Alice-Intent"))
                    or self.headers.get_content_type() != "application/json" or not match):
                raise ChannelError(403, "observer_only", "仅接受持有授权的本机 Lab 服务")
            body = self._bounded_body(2048)
            if set(body) != {"topic", "params"}:
                raise ValueError("观察请求格式无效")
            self._json(observation_grant.read(match[1], body["topic"], body["params"]))

        @staticmethod
        def _public_thoughts(data):
            if "scheduler" not in data:
                return data
            public = {key: value for key, value in data.items()
                      if key not in ("budget", "events", "communication_decisions", "migration_plan")}
            public["scheduler"] = {key: data["scheduler"][key] for key in ("status", "idle")}
            public["runs"] = [{key: run[key] for key in ("id", "status", "reason", "activity_plan")}
                              | {"activity": {"activity": (run.get("activity") or {}).get("activity")}}
                              for run in data["runs"]]
            return public

        def _sensitive_body(self) -> dict:
            """Bound the size and shape of credential-bearing requests without echoing them."""
            return self._bounded_body()

        def _channel_error(self, exc: ChannelError) -> None:
            self._json({"error": str(exc), "code": exc.code}, exc.status)

        def _discovery_route(self) -> None:
            posting = self.command == "POST"
            if (not self._trusted_intent("discovery", json_body=posting)
                    or len(self.headers.get_all("X-Alice-Intent", [])) != 1
                    or len(self.headers.get_all("Origin", [])) > 1):
                self._json({"error": "请从本机网页管理内容发现", "code": "gui_only"}, 403)
                return
            if urlparse(self.path).query:
                self._json({"error": "内容发现不接受查询参数", "code": "invalid_request"}, 400)
                return
            try:
                if not posting:
                    self._json(svc.discovery())
                else:
                    body = self._bounded_body(16384)
                    if not isinstance(body.get("action"), str):
                        raise ValueError("缺少操作")
                    self._json(svc.discovery(body["action"], body))
            except ChannelError as exc:
                self._channel_error(exc)
            except ValueError:
                self._json({"error": "内容发现参数无效", "code": "invalid_request"}, 400)
            except Exception:
                self._json({"error": "内容发现暂不可用，请刷新后查看", "code": "discovery_unavailable"}, 503)

        def _maintenance_route(self) -> None:
            """双维护命令面：预览/排队/取消/回退；执行不占本请求（空闲执行器串行）。"""
            posting = self.command == "POST"
            if (not self._trusted_intent("gui", json_body=posting)
                    or len(self.headers.get_all("X-Alice-Intent", [])) != 1
                    or len(self.headers.get_all("Origin", [])) > 1):
                self._json({"error": "请从本机网页操作维护任务", "code": "gui_only"}, 403)
                return
            if urlparse(self.path).query:
                self._json({"error": "维护操作不接受查询参数", "code": "invalid_request"}, 400)
                return
            try:
                if not posting:
                    self._json(svc.maintenance())
                else:
                    body = self._bounded_body(16384)
                    if not isinstance(body.get("action"), str):
                        raise ValueError("缺少操作")
                    self._json(svc.maintenance(body["action"], body))
            except ChannelError as exc:
                self._channel_error(exc)
            except ValueError as exc:
                self._json({"error": str(exc), "code": "invalid_request"}, 400)
            except Exception:
                self._json({"error": "维护状态暂不可用，请刷新后查看", "code": "maintenance_unavailable"}, 503)

        def _connector_route(self) -> None:
            """Local GUI controls a fixed helper; request data never selects code or paths."""
            posting = self.command == "POST"
            if (not self._trusted_intent("connectors", json_body=posting)
                    or len(self.headers.get_all("X-Alice-Intent", [])) != 1
                    or len(self.headers.get_all("Origin", [])) > 1):
                self._json({"error": "请从本机网页打开 QQ 连接", "code": "gui_only"}, 403)
                return
            if urlparse(self.path).query:
                self._json({"error": "连接操作不接受查询参数", "code": "invalid_request"}, 400)
                return
            if connectors is None:
                self._json({"error": "连接入口尚未准备好，请重新启动 Alice", "code": "connector_unavailable"}, 503)
                return
            switch = False
            try:
                if not posting:
                    self._json(connectors.status())
                    return
                body = self._bounded_body()
                switch = body.get("action") == "set_channel_enabled"
                fields = {"action", "operation_id", "enabled", "session_epoch", "config_revision"} if switch else {"action", "operation_id"}
                if (set(body) - fields
                        or body.get("action") not in {"scan", "cancel", "pair", "confirm", "run", "stop", "install", "recheck", "set_channel_enabled"}
                        or ("operation_id" in body and not isinstance(body["operation_id"], str))):
                    raise ChannelError(400, "invalid_request", "连接操作无效，请刷新页面")
                self._json(connectors.action(body), 202)
            except ChannelError as exc:
                self._channel_error(exc)
            except ConfigConflict as exc:
                self._json({"error": str(exc), "code": "config_conflict"}, 409)
            except ValueError as exc:
                self._json({"error": str(exc) if switch else "连接操作无效，请刷新页面", "code": "invalid_request"}, 400)
            except Exception:
                self._json({"error": "连接状态暂时无法确认，请刷新后查看", "code": "connector_unavailable"}, 503)

        def _scoped_worker_result(self, path: str, query: dict, posting: bool):
            if path == "/api/workers":
                if posting:
                    raise ChannelError(405, "method_not_allowed", "工具目录只接受读取")
                if query:
                    raise ValueError("工具目录不接受查询参数")
                return svc.worker_catalog()
            match = re.fullmatch(r"/api/workers/(dsh|claude)/(.+)", path)
            if not match:
                raise ChannelError(404, "not_found", "协作接口不存在")
            worker, action = match.groups()
            reads = {"status", "policy", "task-settings", "tasks", "tasks/detail", "tasks/events", "permissions/list"}
            writes = {"policy", "connection", "task-settings", "tasks/detail", "tasks/events",
                      "tasks/cancel", "tasks/review", "tasks/revoke", "tasks/archive", "tasks/restore",
                      "tasks/delete", "tasks/recover_local", "permissions/list", "permissions/answer",
                      "inputs", "artifacts/download"}
            if action not in reads | writes:
                raise ChannelError(404, "not_found", "协作接口不存在")
            if action not in (writes if posting else reads):
                raise ChannelError(405, "method_not_allowed", "该协作接口不支持此请求方式")
            if posting:
                payload = self._bounded_body(32768)
            else:
                keys = ({"limit", "before", "archived"} if action == "tasks" else
                        {"session_epoch", "task_id"} if action == "tasks/detail" else
                        {"session_epoch", "task_id", "after_cursor", "limit"} if action == "tasks/events" else set())
                if set(query) - keys or any(len(values) != 1 for values in query.values()):
                    raise ValueError("查询参数无效")
                payload = {key: values[0] if key in {"session_epoch", "task_id"} else int(values[0])
                           for key, values in query.items()} if keys else None
            read_only = not posting or action in {"tasks/detail", "tasks/events", "permissions/list"}
            return svc.worker_api(worker, action, payload, read_only=read_only)

        def _worker_route(self, path: str) -> None:
            posting = self.command == "POST"
            if (not self._trusted_intent("workers", json_body=posting)
                    or len(self.headers.get_all("X-Alice-Intent", [])) != 1
                    or len(self.headers.get_all("Origin", [])) > 1):
                self._json({"error": "协作记录仅供本机 GUI 使用", "code": "gui_only"}, 403)
                return
            code = 200
            try:
                query = parse_qs(urlparse(self.path).query, keep_blank_values=True)
                if posting and query:
                    raise ValueError("写操作不接受查询参数")
                if path == "/api/workers" or path.startswith("/api/workers/"):
                    result = self._scoped_worker_result(path, query, posting)
                elif posting:
                    payload = self._bounded_body(32768)
                    if path == "/api/collaborators":
                        result = svc.collaborators(payload)
                    elif path == "/api/claude-policy":
                        result = svc.claude_policy(payload)
                    elif path == "/api/worker-task-settings":
                        result = svc.worker_task_settings(payload)
                    elif path == "/api/worker-inputs":
                        result = svc.worker_inputs(payload)
                    elif path == "/api/worker-artifacts/download":
                        result = svc.worker_artifacts(payload)
                    elif path in {"/api/worker-tasks/" + action for action in
                                  ("detail", "events", "cancel", "review", "revoke", "archive", "restore", "delete", "recover_local")}:
                        result = svc.worker_tasks(path.rsplit("/", 1)[1], payload)
                    elif path == "/api/worker-permissions/list":
                        result = svc.worker_permissions("list")
                    elif path == "/api/worker-permissions/answer":
                        result = svc.worker_permissions("answer", payload)
                    else:
                        raise ChannelError(404, "not_found", "协作接口不存在")
                elif path == "/api/collaborators" and not query:
                    result = svc.collaborators()
                elif path == "/api/claude-policy" and not query:
                    result = svc.claude_policy()
                elif path == "/api/worker-permissions/list" and not query:
                    result = svc.worker_permissions("list")
                elif path == "/api/worker-tasks":
                    if set(query) - {"limit", "before", "archived", "worker_id"} or any(len(v) != 1 for v in query.values()):
                        raise ValueError("分页参数无效")
                    result = svc.worker_tasks("list", {
                        key: value[0] if key == "worker_id" else int(value[0])
                        for key, value in query.items()})
                else:
                    raise ValueError("查询无效")
            except ChannelError as exc:
                result, code = {"error": str(exc), "code": exc.code}, exc.status
            except (ValueError, TypeError):
                result, code = {"error": "协作请求参数无效", "code": "invalid_request"}, 400
            except Exception:
                result, code = {"error": "协作记录暂不可用，请重新同步", "code": "workers_unavailable"}, 503
            # Sending is outside business-error translation: never send a second
            # response because the first response could not reach the browser.
            self._json(result, code)

        def _channel_route(self, path: str) -> None:
            """Machine credentials never grant GUI, memory, model or P1 authority."""
            port = self.server.server_port
            if (len(self.headers.get_all("Host", [])) != 1
                    or self.headers.get("Host") not in (f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}")
                    or self.headers.get("Origin") is not None
                    or self.headers.get("Sec-Fetch-Site") is not None
                    or self.headers.get("X-Alice-Intent") is not None
                    or urlparse(self.path).query):
                self._json({"error": "机器入口只接受本机适配器请求", "code": "forbidden"}, 403)
                return
            auth = self.headers.get_all("Authorization", [])
            match = re.fullmatch(r"Bearer ([A-Za-z0-9_-]{32,256})", auth[0]) if len(auth) == 1 else None
            channel_id = svc.authenticate_channel(match.group(1)) if match else None
            if channel_id is None:
                self._json({"error": "渠道凭据无效或未启用", "code": "unauthorized"}, 401)
                return
            if self.command == "POST" and self.headers.get_content_type() != "application/json":
                self._json({"error": "只接受 JSON 请求", "code": "invalid_content_type"}, 415)
                return
            if self.command == "POST":
                try:
                    if int(self.headers.get("Content-Length", "0")) > 16384:
                        self._json({"error": "渠道请求超过 16 KiB", "code": "request_too_large"}, 413)
                        return
                except ValueError:
                    self._json({"error": "请求长度无效", "code": "invalid_request"}, 400)
                    return
            try:
                if self.command == "GET" and path == CHANNEL_PREFIX + "/status":
                    self._json(svc.channel_status(channel_id))
                elif self.command == "POST" and path == CHANNEL_PREFIX + "/requests":
                    code, body = svc.submit_channel_request(channel_id, self._bounded_body())
                    self._json(body, code)
                elif self.command == "POST" and path == CHANNEL_PREFIX + "/deliveries":
                    self._json(svc.record_channel_delivery(channel_id, self._bounded_body()))
                elif self.command == "GET" and (request := re.fullmatch(
                        re.escape(CHANNEL_PREFIX) + r"/requests/([A-Za-z0-9_-]{16,128})", path)):
                    self._json(svc.channel_request_status(channel_id, request.group(1)))
                else:
                    self._json({"error": "渠道接口不存在", "code": "not_found"}, 404)
            except ChannelError as exc:
                self._channel_error(exc)
            except ValueError:
                self._json({"error": "渠道请求格式无效或超过限制", "code": "invalid_request"}, 400)
            except Exception:
                self._json({"error": "渠道状态暂时无法确认，请保留原请求身份", "code": "status_unavailable"}, 503)

        def _history(self) -> None:
            query = urlparse(self.path).query
            if len(query) > 2048:
                raise ValueError("历史查询参数过长")
            parsed = parse_qs(query, keep_blank_values=True, max_num_fields=4)
            if set(parsed) - {"after_id", "limit", "session_epoch", "source_turn_ids"} or any(len(v) != 1 for v in parsed.values()):
                raise ValueError("历史查询参数无效")
            after = int(parsed["after_id"][0]) if "after_id" in parsed else None
            limit = int(parsed.get("limit", ["60"])[0])
            if (after is not None and after < 0) or not 1 <= limit <= 100:
                raise ValueError("历史查询范围无效")
            source_turn_ids = None
            if "source_turn_ids" in parsed:
                values = parsed["source_turn_ids"][0].split(",")
                if not 1 <= len(values) <= 100 or any(not re.fullmatch(r"[1-9][0-9]{0,15}", value) for value in values):
                    raise ValueError("来源轮次列表无效")
                source_turn_ids = [int(value) for value in values]
            self._json(svc.history_page(after_id=after, limit=limit,
                                        session_epoch=parsed.get("session_epoch", [None])[0],
                                        source_turn_ids=source_turn_ids))

        # ---------- 路由 ----------

        def do_GET(self):
            path = urlparse(self.path).path
            if path.startswith("/api/"):
                if not gate.admit():
                    self._json({"error": "服务正在退出", "code": "draining"}, 503)
                    return
                try:
                    self._route_get(path)
                except ChannelError as exc:
                    self._channel_error(exc)
                except ValueError:
                    self._json({"error": "查询参数无效", "code": "invalid_request"}, 400)
                finally:
                    gate.leave()
            else:
                self._route_get(path)

        def _route_get(self, path: str) -> None:
            if path.startswith("/api/channels/"):
                self._channel_route(path)
                return
            if path in GUI_READ_PATHS and not self._trusted_intent("gui"):
                self._json({"error": "请从本机 GUI 读取；适配器使用渠道接口", "code": "gui_only"}, 403)
                return
            if path in ("/", "/index.html"):
                self._file("index.html", "text/html; charset=utf-8")
            elif path == "/app.js":
                self._file("app.js", "application/javascript; charset=utf-8")
            elif path in ("/desktop-chat.html", "/desktop-chat.js", "/desktop-chat.css", "/desktop-settings.js", "/desktop-speech.js"):
                media = {".html": "text/html; charset=utf-8", ".js": "application/javascript; charset=utf-8",
                         ".css": "text/css; charset=utf-8"}
                self._file(path[1:], media[Path(path).suffix])
            elif path == "/room.js":
                self._file("room.js", "application/javascript; charset=utf-8")
            elif path == "/lab-pairing.js":
                self._file("lab-pairing.js", "application/javascript; charset=utf-8")
            elif path == "/qq.js":
                self._file("qq.js", "application/javascript; charset=utf-8")
            elif path == "/discovery.js":
                self._file("discovery.js", "application/javascript; charset=utf-8")
            elif path == "/thoughts.js":
                self._file("thoughts.js", "application/javascript; charset=utf-8")
            elif path == "/thoughts.css":
                self._file("thoughts.css", "text/css; charset=utf-8")
            elif path == "/theme-orbit.css":
                self._file("theme-orbit.css", "text/css; charset=utf-8")
            elif path == "/orbit.js":
                self._file("orbit.js", "application/javascript; charset=utf-8")
            elif path == "/orbit-bg.js":
                self._file("orbit-bg.js", "application/javascript; charset=utf-8")
            elif path == "/orbit-ring.js":
                self._file("orbit-ring.js", "application/javascript; charset=utf-8")
            elif path == "/chat-search.js":
                self._file("chat-search.js", "application/javascript; charset=utf-8")
            elif path == "/chat-search.css":
                self._file("chat-search.css", "text/css; charset=utf-8")
            elif path == "/worker-tasks.js":
                self._file("worker-tasks.js", "application/javascript; charset=utf-8")
            elif path == "/claude-gate.js":
                self._file("claude-gate.js", "application/javascript; charset=utf-8")
            elif path == "/claude-settings.js":
                self._file("claude-settings.js", "application/javascript; charset=utf-8")
            elif path == "/worker-tasks.css":
                self._file("worker-tasks.css", "text/css; charset=utf-8")
            elif path == "/assets/dsh-avatar-a-v1.png":
                self._file("assets/dsh-avatar-a-v1.png", "image/png")
            elif path == "/assets/dsh-avatar-small-v1.png":
                self._file("assets/dsh-avatar-small-v1.png", "image/png")
            elif path in {"/assets/claude-avatar-large-v1.png", "/assets/claude-avatar-small-v1.png"}:
                self._file(path.lstrip("/"), "image/png")
            elif path == "/assets/alice-wizard-avatar-v1.png":
                self._file("assets/alice-wizard-avatar-v1.png", "image/png")
            elif path == "/discovery.css":
                self._file("discovery.css", "text/css; charset=utf-8")
            elif path == "/style.css":
                self._file("style.css", "text/css; charset=utf-8")
            elif path == "/api/state":
                self._json(svc.state())
            elif path == "/api/user-intro":
                self._json(svc.user_introduction())
            elif path == "/api/history":
                self._history()
            elif path == "/api/chat/activity":
                self._json(svc.chat_activity(include_thought_state=True))
            elif path == "/api/connectors/qq":
                self._connector_route()
            elif (path in ("/api/collaborators", "/api/worker-tasks", "/api/worker-permissions/list", "/api/claude-policy", "/api/workers")
                  or path.startswith("/api/workers/")):
                self._worker_route(path)
            elif path == "/api/discovery":
                self._discovery_route()
            elif path == "/api/thoughts":
                if not self._trusted_intent("thoughts"):
                    self._json({"error": "请从本机GUI读取思想与探索", "code": "gui_only"}, 403)
                    return
                self._json(self._public_thoughts(svc.thoughts()))
            elif path == "/api/maintenance":
                self._maintenance_route()
            elif path == "/api/persona":
                self._json(svc.persona_view())
            elif path == "/api/settings":
                self._json({"thinking": svc.thinking_view()})
            elif path == "/api/runtime":
                if not self._trusted_intent("runtime"):
                    self._json({"error": "只能从本机 GUI 读取启动信息"}, 403)
                else:
                    self._json(runtime_info)
            elif path == "/api/model-config":
                if not self._trusted_intent("model-config"):
                    self._json({"error": "只能从本机 GUI 读取模型设置"}, 403)
                else:
                    try:
                        self._json(svc.model_config_view())
                    except ConfigConflict as e:
                        self._json({"error": str(e)}, 409)
                    except ValueError as e:
                        self._json({"error": str(e)}, 400)
            elif path == "/api/metrics":
                self._json({
                    "thinking": svc.cfg.llm.thinking,
                    "snapshot": svc.metrics_view(),
                })
            else:
                self._json({"error": "not found"}, 404)

        def do_POST(self):
            path = urlparse(self.path).path
            if path == "/api/shutdown":
                self._shutdown()
                return
            if not gate.admit():
                self._json({"error": "服务正在退出", "code": "draining"}, 503)
                return
            try:
                self._route_post(path)
            except ChannelError as exc:
                self._channel_error(exc)
            except ValueError:
                self._json({"error": "请求参数无效", "code": "invalid_request"}, 400)
            finally:
                gate.leave()

        def _route_post(self, path: str) -> None:
            if path == "/api/lab/v1/open":
                if (not self._trusted_json("lab-pairing") or urlparse(self.path).query
                        or len(self.headers.get_all("Origin", [])) != 1
                        or len(self.headers.get_all("X-Alice-Intent", [])) != 1):
                    raise ChannelError(403, "gui_only", "只能在 Alice 设置里打开 Lab")
                if self._bounded_body(32) != {}:
                    raise ValueError("打开 Lab 不接受额外参数")
                from .lab_launcher import open_registered_lab
                self._json(open_registered_lab())
                return
            if path in ("/api/lab/v1/grant", "/api/lab/v1/read"):
                self._lab_observation(grant=path.endswith("/grant"))
                return
            if path == "/api/thoughts":
                if not self._trusted_json("thoughts"):
                    self._json({"error": "请从本机GUI操作思想与探索", "code": "gui_only"}, 403)
                    return
                body = self._bounded_body(32768)
                action = body.pop("action", None)
                if action in ("migration_prepare", "migration_apply") or (
                        action == "detail" and body.get("kind") == "run"):
                    self._json({"error": "运行诊断已移到 Alice Lab"}, 404)
                    return
                try:
                    self._json(self._public_thoughts(svc.thoughts(action, body)))
                except ValueError as exc:
                    self._json({"error": str(exc), "code": "invalid_request"}, 400)
                return
            if (path in ("/api/collaborators", "/api/worker-inputs", "/api/worker-artifacts/download",
                         "/api/worker-task-settings", "/api/worker-permissions/list",
                         "/api/worker-permissions/answer", "/api/claude-policy")
                    or path.startswith("/api/worker-tasks/") or path == "/api/workers"
                    or path.startswith("/api/workers/")):
                self._worker_route(path)
                return
            if path in ("/api/chat/search/status", "/api/chat/search/cancel", "/api/discovery/memory"):
                intent = "discovery" if path == "/api/discovery/memory" else "gui"
                if not self._trusted_json(intent):
                    self._json({"error": "请从本机 GUI 操作", "code": "gui_only"}, 403)
                    return
                body = self._body()
                result = (svc.observation_memory(body) if path == "/api/discovery/memory"
                          else svc.chat_search_control(body, cancel=path.endswith("/cancel")))
                self._json(result)
                return
            if path == "/api/discovery":
                self._discovery_route()
                return
            if path == "/api/maintenance":
                self._maintenance_route()
                return
            if path == "/api/connectors/qq":
                self._connector_route()
                return
            if path.startswith("/api/channels/"):
                self._channel_route(path)
                return
            if path in GUI_WRITE_PATHS and not self._trusted_json("gui"):
                self._json({"error": "此操作仅供本机 GUI 使用", "code": "gui_only"}, 403)
                return
            if path == "/api/user-intro":
                try:
                    self._json(svc.user_introduction(self._bounded_body(8192)))
                except ValueError as exc:
                    self._json({"error": str(exc)}, 400)
            elif path == "/api/persona/residency":
                try:
                    self._json(svc.persona_residency(self._body()))
                except ChannelError:
                    raise
                except ConfigConflict as exc:
                    self._json({"error": str(exc)}, 409)
                except ValueError as exc:
                    self._json({"error": str(exc)}, 400)
                except Exception:
                    self._json({"error": "人格装配操作未能确认，请刷新状态核对后再重试"}, 500)
            elif path == "/api/persona/material":
                try:
                    body = self._bounded_body(2097152)
                    if body.get("action") in ("prepare", "reinterpret_active"):
                        # The two model-backed actions can legally run past the ~60s
                        # no-bytes timeout in WKWebView; stream progress instead.
                        self._material_stream(svc, body)
                    else:
                        self._json(svc.material_import(body))
                except ChannelError:
                    raise
                except ValueError as exc:
                    self._json({"error": str(exc)}, 400)
                except Exception:
                    self._json({"error": "原文材料导入操作未能确认，请刷新状态核对后再重试"}, 500)
            elif path == "/api/persona/expression-update":
                try:
                    self._json(svc.persona_expression_update(self._bounded_body(1048576)))
                except ChannelError:
                    raise
                except ValueError as exc:
                    self._json({"error": str(exc)}, 400)
                except Exception:
                    self._json({"error": "表达更新结果未能确认，请查看更新记录；不要连续重新准备。"}, 500)
            elif path == "/api/persona/expression":
                try:
                    self._json(svc.revise_persona_expression(self._body()))
                except ChannelError:
                    raise
                except ValueError as exc:
                    self._json({"error": str(exc)}, 400)
                except Exception:
                    self._json({"error": "表达修订未能确认，请先查看当前人格与修改记录"}, 500)
            elif path == "/api/persona":  # 已初始化之后也能替换人格（旧版自动快照）
                body = self._body()
                md_text = str(body.get("persona_md") or "").strip()
                source = str(body.get("source") or "用户导入").strip()[:60] or "用户导入"
                reset = bool(body.get("reset"))
                resume_op_id = body.get("resume_op_id")
                if not md_text:
                    self._json({"error": "人格文档是空的"}, 400)
                else:
                    try:
                        self._json(svc.install_persona(md_text, source, reset=reset, resume_op_id=resume_op_id))
                    except ValueError as e:
                        result = {"error": str(e)}
                        if isinstance(getattr(e, "import_status", None), dict):
                            result["import_status"] = e.import_status
                        self._json(result, 400)
                    except Exception:
                        self._json({"error": "人格安装未能确认，请先核对当前状态与导入记录，不要连续重试。"}, 500)
            elif path == "/api/seed":
                body = self._body()
                md_text = str(body.get("persona_md") or "").strip()
                source = str(body.get("source") or "用户导入").strip()[:60] or "用户导入"
                reset = bool(body.get("reset"))
                resume_op_id = body.get("resume_op_id")
                if md_text:  # 自定义导入；外部角色文档需要模型准备阶段
                    try:
                        self._json(svc.install_persona(md_text, source, reset=reset, resume_op_id=resume_op_id))
                    except ValueError as e:
                        result = {"error": str(e)}
                        if isinstance(getattr(e, "import_status", None), dict):
                            result["import_status"] = e.import_status
                        self._json(result, 400)
                else:
                    tags = body.get("tags") or []
                    if not (2 <= len(tags) <= 4) or not set(tags) <= set(svc.state()["tags"]):
                        self._json({"error": "请选择 2~4 个有效标签"}, 400)
                    else:
                        try:
                            self._json(svc.seed(tags, reset=reset))
                        except Exception as e:  # 种子生成要走 LLM，失败须回传可读错误
                            self._json({"error": f"人格底色生成失败：{e}"}, 502)
            elif path == "/api/reset":  # 丢掉当前会话（旧数据归档），回到第一次见面
                self._json(svc.reset_session())
            elif path == "/api/chat/request/status":
                if not self._trusted_json("gui"):
                    self._json({"error": "请从本机 GUI 核对原请求", "code": "gui_only"}, 403)
                    return
                self._json(svc.gui_request_status(self._bounded_body(65536)))
            elif path == "/api/chat":
                if not self._trusted_json("gui"):
                    self._json({"error": "请从本机 GUI 聊天；适配器使用渠道接口", "code": "gui_only"}, 403)
                    return
                body = self._body()
                if set(body) - {"text", "request_id", "session_epoch", "search_intent", "worker_target"} or not isinstance(body.get("text"), str):
                    raise ValueError("聊天请求无效")
                text = body["text"].strip()
                if not text:
                    self._json({"error": "空消息"}, 400)
                else:
                    self._sse_chat(text,
                                   request_id=body.get("request_id"), session_epoch=body.get("session_epoch"),
                                   search_intent=body.get("search_intent"), worker_target=body.get("worker_target"))
            elif path == "/api/metrics/toggle":
                body = self._body()
                module, on = str(body.get("module") or ""), bool(body.get("on"))
                if not svc.metrics_toggle(module, on):
                    self._json({"error": "未知指标"}, 400)
                else:
                    self._json({"thinking": svc.cfg.llm.thinking,
                                "snapshot": svc.metrics_view()})
            elif path == "/api/settings":
                value = str(self._body().get("thinking") or "")
                if value not in ("on", "off"):
                    self._json({"error": "thinking 仅支持 on/off"}, 400)
                else:
                    self._json({"thinking": svc.set_thinking(value)})
            elif path in ("/api/model-config", "/api/model-config/test", "/api/model-config/models"):
                if not self._trusted_json("model-config"):
                    self._json({"error": "只能从本机 GUI 修改模型设置"}, 403)
                    return
                try:
                    body = self._sensitive_body()
                    result = ({"/api/model-config": svc.save_model_config,
                               "/api/model-config/test": svc.test_model_config,
                               "/api/model-config/models": svc.list_model_config_models}[path])(body)
                    self._json(result)
                except ConfigConflict as e:
                    self._json({"error": str(e)}, 409)
                except ValueError as e:
                    self._json({"error": str(e)}, 400)
                except Exception:
                    self._json({"error": "模型设置操作未能确认，请重新打开设置检查当前配置"}, 500)
            else:
                self._json({"error": "not found"}, 404)

        def _shutdown(self) -> None:
            if not self._trusted_json("shutdown"):
                self._json({"error": "只能从本机 GUI 确认关闭服务"}, 403)
                return
            if not gate.begin():
                self._json({"error": "服务正在退出"}, 409)
                return
            svc.begin_shutdown()
            gate.detach_shutdown_connection(self.connection)
            # This handler runs on a request thread, separate from serve_forever.
            # run() remains the owner of ChatService.close and publishes the result.
            self.server.shutdown()
            code, result = gate.wait_result()
            try:
                self._json(result, code)
            except OSError:
                pass  # The browser closed; the server still completes shutdown.

        # ---------- SSE 流式对话 ----------

        def _material_stream(self, svc, payload: dict) -> None:
            """SSE for the two model-backed material actions (prepare / reinterpret_active).

            These preparations can legally run past the ~60s no-bytes timeout that
            WKWebView's network stack enforces, which read as "分类失败" on the client
            while the server actually finished. Phase events plus a heartbeat keep the
            channel alive; a client disconnect never cancels the persisted result.
            """
            import threading as _threading
            done_event = _threading.Event()
            write_lock = _threading.Lock()
            connected = True
            outcome: dict = {}

            def emit(frame: dict) -> None:
                nonlocal connected
                if not connected:
                    return
                try:
                    data = json.dumps(frame, ensure_ascii=True).encode("ascii")
                    with write_lock:
                        self.wfile.write(b"data: " + data + b"\n\n")
                        self.wfile.flush()
                except OSError:
                    connected = False  # 断开不改写后台结果；候选照常落盘

            def worker() -> None:
                try:
                    outcome["result"] = svc.material_import(payload, on_progress=emit)
                except BaseException as exc:
                    outcome["error"] = exc
                finally:
                    done_event.set()

            try:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
            except OSError:
                connected = False
            emit({"type": "start"})
            worker_thread = _threading.Thread(target=worker, daemon=True, name="material-prepare")
            worker_thread.start()
            while not done_event.wait(15.0):  # 心跳周期必须短于 socket 的 30s 超时
                emit({"type": "hb"})
            worker_thread.join()
            if "error" in outcome:
                exc = outcome["error"]
                emit({"type": "error", "message": str(exc) or type(exc).__name__,
                      "code": getattr(exc, "code", None)})
                return
            emit({"type": "done", "result": outcome.get("result")})

        def _sse_chat(self, text: str, *, request_id=None, session_epoch=None,
                      search_intent=None, worker_target=None, public_tui=False) -> None:
            connected = True
            started = False

            def start(info=None) -> None:
                nonlocal connected, started
                if started:
                    return
                started = True
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                except OSError:
                    connected = False
                event_info = info or {}
                if public_tui:
                    event_info = {key: event_info[key] for key in ("request_id", "session_epoch") if key in event_info}
                emit({"type": "start", **event_info})

            def emit(obj) -> None:
                nonlocal connected
                if not started:
                    start()
                if not connected:
                    return
                try:
                    # Escape even malformed provider Unicode so display encoding
                    # cannot bubble into the model's streaming fallback/retry.
                    payload = json.dumps(obj, ensure_ascii=True).encode("ascii")
                    self.wfile.write(b"data: " + payload + b"\n\n")
                    self.wfile.flush()
                except OSError:
                    connected = False  # Never turn a browser disconnect into another model call.

            def search_progress(info) -> None:
                fields = {"status": info.get("status"), "phase": info.get("phase")} if public_tui else info
                emit({"type": "search", **fields})

            try:
                result = svc.send(
                    text,
                    on_delta=lambda piece: emit({"type": "delta", "text": piece}),
                    on_tool=lambda name: emit({"type": "tool", "name": name}),
                    on_reasoning=None if public_tui else lambda piece: emit({"type": "reasoning", "text": piece}),
                    request_id=request_id, session_epoch=session_epoch,
                    nonblocking=True, on_accepted=start,
                    search_intent=search_intent, on_search_progress=search_progress,
                    worker_target=worker_target,
                    on_reply_saved=lambda: emit({"type": "reply_saved"}),
                )
                done = {"type": "done",
                        **{key: result.get(key) for key in (
                            "request_id", "session_epoch", "user_turn_id", "assistant_turn_id")}}
                if not public_tui:
                    for line in result["events"]:
                        emit({"type": "meta", "text": line})
                    done.update(memory=result["memory"], evaluation_id=result.get("evaluation_id"),
                                worker_target=result.get("worker_target"))
                if connected:
                    done.update(svc.gui_reply_for_delivery(session_epoch=done["session_epoch"],
                                                           assistant_turn_id=done["assistant_turn_id"]))
                    emit(done)
            except ChannelError as exc:
                if not started:
                    self._channel_error(exc)
                else:
                    emit({"type": "error", "code": exc.code, "message": str(exc)})
            except Exception:
                if not started:
                    self._json({"error": "这一轮结果尚未确认；请先查看历史", "code": "status_unavailable"}, 503)
                    return
                try:
                    emit({"type": "error", "message": "这一轮没走通；请先查看历史记录，再决定是否重发"})
                except Exception:
                    pass

    class TuiHandler(Handler):
        """Only the terminal contract is reachable on the UID-authenticated listener."""

        def do_GET(self):
            self._dispatch(self._get_terminal)

        def do_POST(self):
            self._dispatch(self._post_terminal)

        def _dispatch(self, route):
            parsed = urlparse(self.path)
            if parsed.path == TUI_PREFIX + "/stop":
                # 停止请求不计入在途连接（与 GUI /api/shutdown 同例）：它要等
                # drain 完成（在途归零）才返回，把自己算进去就是等自己。
                try:
                    route(parsed.path, parsed.query)
                except ChannelError as exc:
                    self._channel_error(exc)
                except ValueError:
                    self._json({"error": "停止请求无效", "code": "invalid_request"}, 400)
                return
            if not gate.admit():
                self._json({"error": "服务正在退出", "code": "draining"}, 503)
                return
            try:
                route(parsed.path, parsed.query)
            except ChannelError as exc:
                self._channel_error(exc)
            except ValueError:
                self._json({"error": "终端请求参数无效", "code": "invalid_request"}, 400)
            finally:
                gate.leave()

        def _get_terminal(self, path, query):
            if path == TUI_PREFIX + "/status" and not query:
                self._json(svc.tui_status())
            elif path == TUI_PREFIX + "/gui" and not query:
                # 独立启动说明 §4：本机控制面向原生端提供 GUI 就绪信息（socket 已核验同账户）。
                from . import __version__
                self._json({"url": runtime_info["gui_url"], "app_version": __version__})
            elif path == TUI_PREFIX + "/history":
                params = parse_qs(query, keep_blank_values=True, max_num_fields=5)
                allowed = {"after_id", "before_id", "limit", "session_epoch", "source_turn_ids"}
                if set(params) - allowed or any(len(v) != 1 or not v[0] for v in params.values()):
                    raise ValueError("历史查询参数无效")
                params = {key: value[0] for key, value in params.items()}
                for key in ("after_id", "before_id", "limit"):
                    if key in params:
                        params[key] = int(params[key])
                if "source_turn_ids" in params:
                    params["source_turn_ids"] = [int(value) for value in params["source_turn_ids"].split(",")]
                self._json(svc.history_page(**params, include_older=True))
            else:
                self._json({"error": "终端接口不存在", "code": "not_found"}, 404)

        def _post_terminal(self, path, query):
            if query:
                self._json({"error": "终端接口不存在", "code": "not_found"}, 404)
                return
            if path == TUI_PREFIX + "/stop":
                # 显式停止（独立启动说明 §5）：socket 已核验同账户身份，控制面与
                # 日常聊天权限分开；与 GUI 关闭走同一条排空收尾链，等同一个结果。
                if self.headers.get_content_type() != "application/json" or self._bounded_body(16) != {}:
                    raise ValueError("停止请求无效")
                if on_stop is None:
                    self._json({"error": "终端接口不存在", "code": "not_found"}, 404)
                    return
                if not gate.begin():
                    self._json({"error": "服务正在退出", "code": "draining"}, 409)
                    return
                svc.begin_shutdown()
                gate.detach_shutdown_connection(self.connection)
                # 本线程与 serve_forever 分离；run() 仍是 ChatService.close 的所有者。
                on_stop()
                code, result = gate.wait_result()
                try:
                    self._json(result, code)
                except OSError:
                    pass  # 终端已断开；宿主仍会完成收尾。
                return
            if path not in (TUI_PREFIX + "/chat", TUI_PREFIX + "/chat/status"):
                self._json({"error": "终端接口不存在", "code": "not_found"}, 404)
                return
            if self.headers.get_content_type() != "application/json":
                raise ValueError("终端请求必须使用 JSON")
            body = self._bounded_body(65536)
            if (set(body) != {"text", "request_id", "session_epoch"}
                    or any(not isinstance(value, str) or not value.strip() for value in body.values())):
                raise ValueError("终端请求身份无效")
            if path.endswith("/status"):
                self._json(svc.gui_request_status(body))
            else:
                self._sse_chat(**body, public_tui=True)

    return TuiHandler if terminal else Handler


def run(port: int = DEFAULT_PORT, host: str = "127.0.0.1", open_browser: bool = True,
        cfg: Config | None = None, config_path: Path | None = None, host_control=None) -> None:
    if host not in ("127.0.0.1", "localhost"):
        raise ValueError("服务只能监听本机回环地址 127.0.0.1 或 localhost")
    config_path = Path(config_path).resolve() if config_path is not None else USER_DIR / "config.toml"
    gate = _ShutdownGate()
    unix_server = None
    # Bind before constructing Store or running startup maintenance. A duplicate launch
    # must fail without opening or changing the existing local conversation.
    try:
        httpd = _DrainingHTTPServer((host, port), gate)
    except OSError:
        if host_control is not None:
            raise
        print(f"启动失败：本机端口 {port} 无法绑定，请检查是否已有 Alice 服务在运行")
        return
    svc: ChatService | None = None
    connectors = None
    try:
        runtime_info = {"restart_command": _restart_command(config_path, httpd.server_port),
                        "desktop": host_control is not None,
                        "gui_url": f"http://127.0.0.1:{httpd.server_port}"}
        effective_cfg = cfg if cfg is not None else load(config_path)
        if getattr(sys, "frozen", False):
            effective_cfg.plugins.context_inspector_enabled = False
            effective_cfg.context.observer_body_capture = False
            effective_cfg.context.reply_annotation_review = False
        svc = ChatService(effective_cfg, config_path=config_path,
                          allow_unconfigured=host_control is not None,
                          _maintenance_activation=bool(host_control and host_control.maintenance_activation),
                          lab_experiments=bool(getattr(host_control, "lab_session", False) and not getattr(sys, "frozen", False)))
        boot_events = svc.prepare()
        # Fixed public addon command; no import of addon code and no request-
        # supplied program, arguments, filesystem path or callback address.
        connector_root = PROJECT_ROOT / "addons" / "qq"
        connector_command = (
            "node",
            str(connector_root / "src" / "web-control.mjs"),
            "--config", str(USER_DIR / "qq" / "config.json"),
            "--alice-url", f"http://127.0.0.1:{httpd.server_port}",
        )
        frozen = getattr(sys, "frozen", False)
        connectors = LocalConnectorManager(ConnectorSpec(
            command=() if frozen else connector_command, cwd=PROJECT_ROOT,
            setup_root=None if frozen else connector_root,
            unavailable_reason="此桌面包尚未适配 QQ 组件" if frozen else ""), svc)
        httpd.RequestHandlerClass = make_handler(
            svc, gate, {**runtime_info, "distribution": runtime_distribution()}, connectors)
        url = f"http://{host}:{httpd.server_port}"
        print(f"=== {svc.cfg.persona.name} 图形界面 ===")
        for line in boot_events:
            print(f"（{line}）")
        print(f"已就绪：{url} （Ctrl+C 退出）")
        if not getattr(host_control, "lab_session", False):
            try:
                unix_server = start_unix_listener(
                    USER_DIR,
                    make_handler(svc, gate, runtime_info, terminal=True, on_stop=httpd.shutdown),
                    gate, REQUEST_IO_TIMEOUT)
            except OSError as exc:
                print(f"（终端入口未启用：{exc}）")
        if open_browser:
            timer = threading.Timer(0.5, lambda: webbrowser.open(url))
            timer.daemon = True
            timer.start()
    except BaseException as exc:
        try:
            if unix_server is not None:
                unix_server.stop()
            if connectors is not None:
                connectors.close()
            if svc is not None:
                svc.close(extract=False)  # Startup failed; do not issue a new model request.
        except Exception as close_error:
            print(f"（启动资源收尾失败：{type(close_error).__name__}）")
        finally:
            httpd.server_close()
        if isinstance(exc, SystemExit):
            if host_control is not None:
                raise
            print(f"启动失败：{exc}")
            return
        raise
    try:
        if host_control is not None:
            host_control.ready(httpd, gate, svc)
        httpd.serve_forever()
        print("\n收到停止请求，正在收尾。")
    except KeyboardInterrupt:
        print("\n再见。")
    finally:
        gate.begin()
        if unix_server is not None:
            try:
                unix_server.stop()
            except OSError as exc:
                print(f"（本机 socket 收尾失败：{exc}）")
        if connectors is not None:
            connectors.close()
        svc.begin_shutdown()
        # Wait for both transports before closing Store. The GUI shutdown handler
        # detaches its own connection so it can receive the final result.
        gate.wait_idle()
        try:
            warning = svc.close(extract=host_control.extract_on_exit if host_control is not None else True)
            if warning:
                code, result = 500, {"status": "closed_with_warning", "error": warning}
            else:
                code, result = 200, {"status": "closed", "message": "服务已关闭，对话与记忆保存在本机"}
        except BaseException as e:
            print(f"（服务收尾失败：{type(e).__name__}）")
            code, result = 500, {"status": "failed", "error": "服务收尾未能确认；请检查本机数据后再重启"}
        try:
            httpd.server_close()
        except BaseException as e:
            print(f"（监听端口关闭失败：{type(e).__name__}）")
            code, result = 500, {"status": "failed", "error": "监听端口关闭未能确认；请在终端检查进程"}
        result.update(runtime_info)
        gate.finish(code, result)
        if host_control is None:
            print("已停止，对话与记忆已保存。")
        if host_control is not None:
            host_control.closed(code, result)
