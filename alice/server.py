"""本地图形界面：python -m alice serve → http://127.0.0.1:<port>

纯标准库实现（无第三方依赖）：静态页 + JSON API + SSE 流式对话。
只绑定回环地址，不出本机（数据本地不变量）。
"""
from __future__ import annotations

import json
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .config import Config, PROJECT_ROOT, USER_DIR, load
from .config_edit import ConfigConflict
from .service import ChatService

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
DEFAULT_PORT = 8800
REQUEST_IO_TIMEOUT = 30.0


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


def make_handler(svc: ChatService, gate: _ShutdownGate, runtime_info: dict):
    class Handler(BaseHTTPRequestHandler):
        server_version = "AliceGUI/0.1"

        def log_message(self, fmt, *args):  # 只留错误，安静运行
            pass

        # ---------- 基础 ----------

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code: int = 200) -> None:
            self._send(code, _json_bytes(obj), "application/json; charset=utf-8")

        def _file(self, name: str, ctype: str) -> None:
            path = WEB_DIR / name
            if not path.exists():
                self._json({"error": "not found"}, 404)
            else:
                self._send(200, path.read_bytes(), ctype)

        def _body(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b""
            try:
                return json.loads(raw or b"{}")
            except json.JSONDecodeError:
                return {}

        def _trusted_intent(self, intent: str, *, json_body: bool = False) -> bool:
            """Only the local GUI may issue sensitive requests; a custom header blocks forms."""
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
            try:
                n = int(self.headers.get("Content-Length", ""))
            except ValueError as e:
                raise ValueError("请求长度无效") from e
            if not 0 < n <= 16384:
                raise ValueError("配置请求过大或为空")
            try:
                body = json.loads(self.rfile.read(n))
            except (UnicodeDecodeError, json.JSONDecodeError) as e:
                raise ValueError("请求内容不是有效 JSON") from e
            if not isinstance(body, dict):
                raise ValueError("配置请求必须是 JSON 对象")
            return body

        # ---------- 路由 ----------

        def do_GET(self):
            path = urlparse(self.path).path
            if path.startswith("/api/"):
                if not gate.admit():
                    self._json({"error": "服务正在退出"}, 503)
                    return
                try:
                    self._route_get(path)
                finally:
                    gate.leave()
            else:
                self._route_get(path)

        def _route_get(self, path: str) -> None:
            if path in ("/", "/index.html"):
                self._file("index.html", "text/html; charset=utf-8")
            elif path == "/app.js":
                self._file("app.js", "application/javascript; charset=utf-8")
            elif path == "/observer.js":
                self._file("observer.js", "application/javascript; charset=utf-8")
            elif path == "/style.css":
                self._file("style.css", "text/css; charset=utf-8")
            elif path == "/api/state":
                self._json(svc.state())
            elif path == "/api/history":
                self._json({"turns": svc.history()})
            elif path == "/api/persona":
                self._json(svc.persona_view())
            elif path in ("/api/observer/snapshot", "/api/observer/list", "/api/observer/detail"):
                self._observer_get(path)
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
                      "/api/observer/detail": svc.observer_detail}[path]
            self._json(reader(**params))

        def do_POST(self):
            path = urlparse(self.path).path
            if path == "/api/shutdown":
                self._shutdown()
                return
            if not gate.admit():
                self._json({"error": "服务正在退出"}, 503)
                return
            try:
                self._route_post(path)
            finally:
                gate.leave()

        def _route_post(self, path: str) -> None:
            if path == "/api/persona":  # 已初始化之后也能替换人格（旧版自动快照）
                body = self._body()
                md_text = str(body.get("persona_md") or "").strip()
                source = str(body.get("source") or "用户导入").strip()[:60] or "用户导入"
                reset = bool(body.get("reset"))
                if not md_text:
                    self._json({"error": "人格文档是空的"}, 400)
                else:
                    try:
                        self._json(svc.install_persona(md_text, source, reset=reset))
                    except ValueError as e:
                        self._json({"error": str(e)}, 400)
            elif path == "/api/seed":
                body = self._body()
                md_text = str(body.get("persona_md") or "").strip()
                source = str(body.get("source") or "用户导入").strip()[:60] or "用户导入"
                reset = bool(body.get("reset"))
                if md_text:  # 自定义初始人格导入（不走 LLM）
                    try:
                        self._json(svc.install_persona(md_text, source, reset=reset))
                    except ValueError as e:
                        self._json({"error": str(e)}, 400)
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
                text = str(self._body().get("text") or "").strip()
                if not text:
                    self._json({"error": "空消息"}, 400)
                else:
                    self._sse_chat(text)
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

        def _sse_chat(self, text: str) -> None:
            connected = True
            try:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
            except OSError:
                connected = False

            def emit(obj) -> None:
                nonlocal connected
                if not connected:
                    return
                try:
                    self.wfile.write(b"data: " + _json_bytes(obj) + b"\n\n")
                    self.wfile.flush()
                except OSError:
                    connected = False  # Never turn a browser disconnect into another model call.

            try:
                emit({"type": "start"})
                result = svc.send(
                    text,
                    on_delta=lambda piece: emit({"type": "delta", "text": piece}),
                    on_tool=lambda name: emit({"type": "tool", "name": name}),
                    on_reasoning=lambda piece: emit({"type": "reasoning", "text": piece}),
                )
                for line in result["events"]:
                    emit({"type": "meta", "text": line})
                emit({"type": "done", "reply": result["reply"], "memory": result["memory"]})
            except Exception:
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
    try:
        runtime_info = {"restart_command": _restart_command(config_path, httpd.server_port)}
        svc = ChatService(cfg if cfg is not None else load(config_path), config_path=config_path)
        boot_events = svc.prepare()
        httpd.RequestHandlerClass = make_handler(svc, gate, runtime_info)
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
