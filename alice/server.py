"""本地图形界面：python -m alice serve → http://127.0.0.1:<port>

纯标准库实现（无第三方依赖）：静态页 + JSON API + SSE 流式对话。
只绑定回环地址，不出本机（数据本地不变量）。
"""
from __future__ import annotations

import json
import os
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
from .service import ChatService

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
DEFAULT_PORT = 8800
REQUEST_IO_TIMEOUT = 30.0
CHANNEL_PREFIX = "/api/channels/v1"
GUI_READ_PATHS = {"/api/state", "/api/history", "/api/persona", "/api/settings",
                  "/api/metrics", "/api/chat/activity"}
GUI_WRITE_PATHS = {"/api/persona", "/api/seed", "/api/reset", "/api/settings",
                   "/api/metrics/toggle", "/api/persona/expression",
                   "/api/persona/expression-update", "/api/persona/residency",
                   "/api/persona/material"}


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
        f"& {literal(sys.executable)} -m alice --config {literal(config_path)} serve --port {port}"
    )


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


class _DrainingHTTPServer(ThreadingHTTPServer):
    """Count accepted workers, including incomplete and non-API requests."""

    daemon_threads = False
    block_on_close = False  # The shutdown handler needs the owner's final result first.

    def __init__(self, address, gate: _ShutdownGate):
        self._gate = gate
        super().__init__(address, BaseHTTPRequestHandler)

    def get_request(self):
        request, address = super().get_request()
        try:
            request.settimeout(REQUEST_IO_TIMEOUT)
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


def make_handler(svc: ChatService, gate: _ShutdownGate, runtime_info: dict, connectors=None):
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
            elif path == "/observer.js":
                self._file("observer.js", "application/javascript; charset=utf-8")
            elif path == "/context-viewer.js":
                self._file("context-viewer.js", "application/javascript; charset=utf-8")
            elif path == "/context-viewer.css":
                self._file("context-viewer.css", "text/css; charset=utf-8")
            elif path == "/evaluations.js":
                self._file("evaluations.js", "application/javascript; charset=utf-8")
            elif path == "/qq.js":
                self._file("qq.js", "application/javascript; charset=utf-8")
            elif path == "/discovery.js":
                self._file("discovery.js", "application/javascript; charset=utf-8")
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
            elif path == "/discovery.css":
                self._file("discovery.css", "text/css; charset=utf-8")
            elif path == "/style.css":
                self._file("style.css", "text/css; charset=utf-8")
            elif path == "/api/state":
                self._json(svc.state())
            elif path == "/api/history":
                self._history()
            elif path == "/api/chat/activity":
                self._json(svc.chat_activity())
            elif path == "/api/connectors/qq":
                self._connector_route()
            elif (path in ("/api/collaborators", "/api/worker-tasks", "/api/worker-permissions/list", "/api/claude-policy", "/api/workers")
                  or path.startswith("/api/workers/")):
                self._worker_route(path)
            elif path == "/api/discovery":
                self._discovery_route()
            elif path == "/api/maintenance":
                self._maintenance_route()
            elif path == "/api/persona":
                self._json(svc.persona_view())
            elif path == "/addons/context-inspector" or path.startswith("/addons/context-inspector/"):
                self._addon_static(path)
            elif path in ("/api/plugins/context-inspector/status", "/api/plugins/context-inspector/requests",
                          "/api/plugins/context-inspector/request", "/api/plugins/context-inspector/body", "/api/plugins/context-inspector/call"):
                self._plugin_get(path)
            elif path in ("/api/observer/snapshot", "/api/observer/list", "/api/observer/detail", "/api/observer/context", "/api/observer/calls", "/api/observer/persona-operations", "/api/observer/requests", "/api/observer/body", "/api/observer/call"):
                self._observer_get(path)
            elif path == "/api/evaluations" or path.startswith("/api/evaluations/"):
                self._evaluation_route(path)
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

        def _observer_get(self, path: str) -> None:
            if not self._trusted_intent("observer"):
                self._json({"status": "error", "error": "只能从本机 GUI 读取观察数据"}, 403)
                return
            allowed = {
                "/api/observer/snapshot": {"scope"},
                "/api/observer/list": {"scope", "view", "layer", "kind", "q", "before", "ceiling"},
                "/api/observer/detail": {"scope", "view", "id", "offset"},
                "/api/observer/context": {"scope", "limit"},
                "/api/observer/calls": {"scope", "limit", "request_id"},
                "/api/observer/persona-operations": {"scope", "limit"},
                "/api/observer/requests": {"scope", "limit", "request_id"},
                "/api/observer/body": {"scope", "hash", "request_id"},
                "/api/observer/call": {"scope", "request_id", "call_id"},
            }[path]
            try:
                query = urlparse(self.path).query
                if len(query) > 4096:
                    raise ValueError
                parsed = parse_qs(query, keep_blank_values=True, max_num_fields=8)
                if set(parsed) - allowed or any(len(values) != 1 for values in parsed.values()):
                    raise ValueError
                params = {key: values[0] for key, values in parsed.items()}
                scope = params.get("scope")
                if scope is not None and (len(scope) != 32 or any(ch not in "0123456789abcdef" for ch in scope)):
                    raise ValueError
            except ValueError:
                self._json({"status": "error", "error": "观察请求参数无效"}, 400)
                return
            reader = {"/api/observer/snapshot": svc.observer_snapshot,
                      "/api/observer/list": svc.observer_list,
                      "/api/observer/detail": svc.observer_detail,
                      "/api/observer/context": svc.observer_context_basis,
                      "/api/observer/calls": svc.observer_llm_calls,
                      "/api/observer/persona-operations": svc.observer_persona_operations,
                      "/api/observer/requests": svc.observer_context_requests,
                      "/api/observer/body": svc.observer_context_body,
                      "/api/observer/call": svc.observer_context_call}[path]
            self._json(reader(**params))

        # ---- 上下文观测插件（20261107 插件设计 §4/§6）：固定静态托管 + 薄查询命名空间 ----

        ADDON_STATIC_TYPES = {"html": "text/html; charset=utf-8",
                              "js": "application/javascript; charset=utf-8",
                              "css": "text/css; charset=utf-8",
                              "json": "application/json; charset=utf-8"}

        def _addon_static(self, path: str) -> None:
            """只读托管清单内固定资源；缺文件、禁用或版本不符时给明确状态，聊天照常。"""
            root = Path(__file__).resolve().parent.parent / "addons" / "context_inspector"
            try:
                manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                self._json({"status": "error", "error": "插件资源不可用"}, 404)
                return
            if (not svc.cfg.plugins.context_inspector_enabled
                    or manifest.get("id") != "context-inspector"
                    or manifest.get("protocol_version") != "ctx-obs/1"):
                self._json({"status": "disabled", "error": "插件未启用或协议版本不符"}, 404)
                return
            rel = path[len("/addons/context-inspector"):].lstrip("/") or (manifest.get("entry") or "index.html")
            if rel not in (manifest.get("files") or []):
                self._json({"status": "error", "error": "插件资源不存在"}, 404)
                return
            webroot = (root / "web").resolve()
            target = (webroot / rel).resolve()
            if (not str(target).startswith(str(webroot) + os.sep) or target.is_symlink()
                    or not target.is_file()):
                self._json({"status": "error", "error": "插件资源不存在"}, 404)
                return
            ctype = self.ADDON_STATIC_TYPES.get(target.suffix.lstrip("."))
            if ctype is None or target.stat().st_size > 4 * 1024 * 1024:
                self._json({"status": "error", "error": "插件资源类型或大小不受支持"}, 404)
                return
            try:
                payload = target.read_bytes()
            except OSError:
                self._json({"status": "error", "error": "插件资源不可用"}, 404)
                return
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            try:
                self.wfile.write(payload)
            except OSError:
                pass

        def _plugin_get(self, path: str) -> None:
            """插件专用查询：同源观察意图校验 + 启用检查；委托同一核心观察服务。"""
            if not self._trusted_intent("observer"):
                self._json({"status": "error", "error": "只能从本机 GUI 读取观察数据"}, 403)
                return
            allowed = {
                "/api/plugins/context-inspector/status": {"scope"},
                "/api/plugins/context-inspector/requests": {"scope", "limit"},
                "/api/plugins/context-inspector/request": {"scope", "request_id"},
                "/api/plugins/context-inspector/body": {"scope", "request_id", "hash"},
                "/api/plugins/context-inspector/call": {"scope", "request_id", "call_id"},
            }[path]
            try:
                query = urlparse(self.path).query
                if len(query) > 4096:
                    raise ValueError
                parsed = parse_qs(query, keep_blank_values=True, max_num_fields=8)
                if set(parsed) - allowed or any(len(values) != 1 for values in parsed.values()):
                    raise ValueError
                params = {key: values[0] for key, values in parsed.items()}
                scope = params.get("scope")
                if scope is not None and (len(scope) != 32 or any(ch not in "0123456789abcdef" for ch in scope)):
                    raise ValueError
            except ValueError:
                self._json({"status": "error", "error": "观察请求参数无效"}, 400)
                return
            if not svc.cfg.plugins.context_inspector_enabled:
                self._json({"status": "disabled", "scope": scope})
                return
            reader = {"/api/plugins/context-inspector/status": svc.plugin_inspector_status,
                      "/api/plugins/context-inspector/requests": svc.plugin_inspector_requests,
                      "/api/plugins/context-inspector/request": svc.plugin_inspector_request,
                      "/api/plugins/context-inspector/body": svc.plugin_inspector_body,
                      "/api/plugins/context-inspector/call": svc.plugin_inspector_call}[path]
            self._json(reader(**params))

        def _evaluation_route(self, path: str) -> None:
            posting = self.command == "POST"
            if not self._trusted_intent("evaluations", json_body=posting):
                self._json({"status": "error", "error": "只能从本机P1核查面板操作"}, 403)
                return
            try:
                if posting:
                    params = self._sensitive_body()
                    if urlparse(self.path).query:
                        raise ValueError("P1写请求不接受查询参数")
                else:
                    query = urlparse(self.path).query
                    if len(query) > 2048:
                        raise ValueError("P1查询参数过长")
                    parsed = parse_qs(query, keep_blank_values=True, max_num_fields=4)
                    if any(len(v) != 1 for v in parsed.values()):
                        raise ValueError("P1查询参数重复")
                    params = {k: v[0] for k, v in parsed.items()}
                fixed = {
                    (False, "/api/evaluations"): ("list", {"before", "dataset_id", "filter"}),
                    (False, "/api/evaluations/settings"): ("settings", {"dataset_id"}),
                    (True, "/api/evaluations/settings"): ("set_limit", {"dataset_id", "daily_limit", "allow_paid"}),
                    (True, "/api/evaluations/arm"): ("arm", {"dataset_id", "action_id"}),
                    (False, "/api/evaluations/calibrations"): ("calibrations", {"dataset_id"}),
                    (True, "/api/evaluations/calibrations"): ("calibrate", {"dataset_id", "run_ids", "minimum_agreement", "attest"}),
                }
                target = fixed.get((posting, path))
                match = re.fullmatch(r"/api/evaluations/([0-9a-f]{32})(?:/(cancel|delete|labels))?", path)
                if target is None and match:
                    identity, suffix = match.groups()
                    if not posting and suffix is None:
                        target = ("detail", {"dataset_id", "reveal"})
                    elif posting and suffix == "cancel":
                        target = ("cancel", {"dataset_id"})
                    elif posting and suffix == "delete":
                        target = ("delete", {"dataset_id"})
                    elif posting and suffix == "labels":
                        target = ("label", {"dataset_id", "ordinal", "verdict", "revealed"})
                    else:
                        raise ValueError("P1请求方法与路径不匹配")
                if target is None:
                    self._json({"status": "error", "error": "P1接口不存在"}, 404)
                    return
                action, allowed = target
                if set(params) - allowed:
                    raise ValueError("P1请求包含未知参数")
                dataset = params.pop("dataset_id", None)
                if dataset is not None and (not isinstance(dataset, str) or not re.fullmatch(r"[0-9a-f]{32}", dataset)):
                    raise ValueError("会话身份无效")
                if (posting or action == "detail") and dataset is None:
                    raise ValueError("请先读取当前会话身份")
                if action == "detail":
                    reveal = params.get("reveal", "0")
                    if reveal not in ("0", "1"):
                        raise ValueError("揭示选项无效")
                    params["reveal"] = reveal == "1"
                if match:
                    params["id"] = match.group(1)
                self._json(svc.evaluations(action, dataset_id=dataset, **params))
            except ValueError as exc:
                self._json({"status": "error", "error": str(exc)}, 400)

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
            if path.startswith("/api/evaluations/"):
                self._evaluation_route(path)
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
                    self._json(svc.material_import(self._bounded_body(262144)))
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
            elif path == "/api/chat":
                if not (self._trusted_json("gui") or self._trusted_json("evaluations")):
                    self._json({"error": "请从本机 GUI 聊天；适配器使用渠道接口", "code": "gui_only"}, 403)
                    return
                body = self._body()
                if set(body) - {"text", "evaluation_id", "request_id", "session_epoch", "search_intent", "worker_target"} or not isinstance(body.get("text"), str):
                    raise ValueError("聊天请求无效")
                text = body["text"].strip()
                evaluation_id = body.get("evaluation_id")
                if evaluation_id is not None and (
                    not self._trusted_json("evaluations") or not isinstance(evaluation_id, str)
                    or not re.fullmatch(r"[0-9a-f]{32}", evaluation_id)
                ):
                    self._json({"error": "核查动作无效，请从本机P1面板重新选择下一轮"}, 400)
                    return
                if not text:
                    self._json({"error": "空消息"}, 400)
                else:
                    self._sse_chat(text, evaluation_id=evaluation_id,
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
            elif path in ("/api/model-config", "/api/model-config/test"):
                if not self._trusted_json("model-config"):
                    self._json({"error": "只能从本机 GUI 修改模型设置"}, 403)
                    return
                try:
                    body = self._sensitive_body()
                    result = (svc.save_model_config(body) if path == "/api/model-config"
                              else svc.test_model_config(body))
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

        def _sse_chat(self, text: str, *, evaluation_id=None, request_id=None, session_epoch=None,
                      search_intent=None, worker_target=None) -> None:
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
                emit({"type": "start", **(info or {})})

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

            try:
                result = svc.send(
                    text,
                    on_delta=lambda piece: emit({"type": "delta", "text": piece}),
                    on_tool=lambda name: emit({"type": "tool", "name": name}),
                    on_reasoning=lambda piece: emit({"type": "reasoning", "text": piece}),
                    evaluation_id=evaluation_id,
                    request_id=request_id, session_epoch=session_epoch,
                    nonblocking=True, on_accepted=start,
                    search_intent=search_intent, on_search_progress=lambda info: emit({"type": "search", **info}),
                    worker_target=worker_target,
                    on_reply_saved=lambda: emit({"type": "reply_saved"}),
                )
                for line in result["events"]:
                    emit({"type": "meta", "text": line})
                emit({"type": "done", "reply": result["reply"], "memory": result["memory"],
                      "evaluation_id": result.get("evaluation_id"),
                      **{key: result.get(key) for key in ("request_id", "session_epoch", "user_turn_id", "assistant_turn_id", "worker_target")}})
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

    return Handler


def run(port: int = DEFAULT_PORT, host: str = "127.0.0.1", open_browser: bool = True,
        cfg: Config | None = None, config_path: Path | None = None) -> None:
    if host not in ("127.0.0.1", "localhost"):
        raise ValueError("服务只能监听本机回环地址 127.0.0.1 或 localhost")
    config_path = Path(config_path).resolve() if config_path is not None else USER_DIR / "config.toml"
    gate = _ShutdownGate()
    # Bind before constructing Store or running startup maintenance. A duplicate launch
    # must fail without opening or changing the existing local conversation.
    try:
        httpd = _DrainingHTTPServer((host, port), gate)
    except OSError:
        print(f"启动失败：本机端口 {port} 无法绑定，请检查是否已有 Alice 服务在运行")
        return
    svc: ChatService | None = None
    connectors = None
    try:
        runtime_info = {"restart_command": _restart_command(config_path, httpd.server_port)}
        svc = ChatService(cfg if cfg is not None else load(config_path), config_path=config_path)
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
        connectors = LocalConnectorManager(ConnectorSpec(command=connector_command, cwd=PROJECT_ROOT,
                                                        setup_root=connector_root), svc)
        httpd.RequestHandlerClass = make_handler(svc, gate, runtime_info, connectors)
        url = f"http://{host}:{httpd.server_port}"
        print(f"=== {svc.cfg.persona.name} 图形界面 ===")
        for line in boot_events:
            print(f"（{line}）")
        print(f"已就绪：{url} （Ctrl+C 退出）")
        if open_browser:
            timer = threading.Timer(0.5, lambda: webbrowser.open(url))
            timer.daemon = True
            timer.start()
    except BaseException as exc:
        try:
            if connectors is not None:
                connectors.close()
            if svc is not None:
                svc.close(extract=False)  # Startup failed; do not issue a new model request.
        except Exception as close_error:
            print(f"（启动资源收尾失败：{type(close_error).__name__}）")
        finally:
            httpd.server_close()
        if isinstance(exc, SystemExit):
            print(f"启动失败：{exc}")
            return
        raise
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n再见。")
    finally:
        gate.begin()
        if connectors is not None:
            connectors.close()
        svc.begin_shutdown()
        # The shutdown request is detached from connection accounting; all other
        # workers, even half-open or static requests, must leave before Store closes.
        gate.wait_idle()
        try:
            warning = svc.close()
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
