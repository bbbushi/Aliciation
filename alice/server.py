"""本地图形界面：python -m alice serve → http://127.0.0.1:<port>

纯标准库实现（无第三方依赖）：静态页 + JSON API + SSE 流式对话。
只绑定回环地址，不出本机（数据本地不变量）。
"""
from __future__ import annotations

import json
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from .config import Config, load
from .service import ChatService

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
DEFAULT_PORT = 8800


def _json_bytes(obj) -> bytes:
    return json.dumps(obj, ensure_ascii=False).encode("utf-8")


def make_handler(svc: ChatService):
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

        # ---------- 路由 ----------

        def do_GET(self):
            path = urlparse(self.path).path
            if path in ("/", "/index.html"):
                self._file("index.html", "text/html; charset=utf-8")
            elif path == "/app.js":
                self._file("app.js", "application/javascript; charset=utf-8")
            elif path == "/style.css":
                self._file("style.css", "text/css; charset=utf-8")
            elif path == "/api/state":
                self._json(svc.state())
            elif path == "/api/history":
                self._json({"turns": svc.history()})
            elif path == "/api/metrics":
                self._json({
                    "thinking": svc.cfg.llm.thinking,
                    "snapshot": svc.metrics_view(),
                })
            else:
                self._json({"error": "not found"}, 404)

        def do_POST(self):
            path = urlparse(self.path).path
            if path == "/api/seed":
                tags = self._body().get("tags") or []
                if not (2 <= len(tags) <= 4) or not set(tags) <= set(svc.state()["tags"]):
                    self._json({"error": "请选择 2~4 个有效标签"}, 400)
                else:
                    try:
                        self._json(svc.seed(tags))
                    except Exception as e:  # 种子生成要走 LLM，失败须回传可读错误
                        self._json({"error": f"人格底色生成失败：{e}"}, 502)
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
            else:
                self._json({"error": "not found"}, 404)

        # ---------- SSE 流式对话 ----------

        def _sse_chat(self, text: str) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()

            def emit(obj) -> None:
                self.wfile.write(b"data: " + _json_bytes(obj) + b"\n\n")
                self.wfile.flush()

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
                emit({"type": "done", "reply": result["reply"], "memory": result["memory"],
                      "metrics": result.get("metrics")})
            except (BrokenPipeError, ConnectionResetError):
                pass  # 客户端提前断开
            except Exception as e:
                try:
                    emit({"type": "error", "message": f"这一轮没走通（{e}），请把话再发一遍"})
                except Exception:
                    pass

    return Handler


def run(port: int = DEFAULT_PORT, host: str = "127.0.0.1", open_browser: bool = True,
        cfg: Config | None = None) -> None:
    try:
        svc = ChatService(cfg or load())
    except SystemExit as e:
        print(f"启动失败：{e}")  # 配置缺失（如 API Key）给出可读提示而非 traceback
        return
    boot_events = svc.prepare()
    httpd = ThreadingHTTPServer((host, port), make_handler(svc))
    httpd.daemon_threads = True
    url = f"http://{host}:{port}"
    print(f"=== {svc.cfg.persona.name} 图形界面 ===")
    for line in boot_events:
        print(f"（{line}）")
    print(f"已就绪：{url} （Ctrl+C 退出）")
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n再见。")
    finally:
        httpd.server_close()
        svc.close()
