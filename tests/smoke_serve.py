"""GUI 服务冒烟测试：不依赖 API Key，起真实 HTTP 服务打全流程。运行：python3 tests/smoke_serve.py"""
from __future__ import annotations

import json
import sys
import tempfile
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from alice import config as appconfig  # noqa: E402
from alice.config import Config  # noqa: E402
from alice.server import make_handler  # noqa: E402
from alice.service import ChatService  # noqa: E402
from alice.loop import _stream_round  # noqa: E402
from tests.growth.fakes import RegexFakeClient  # noqa: E402

PASS = 0


def check(name: str, ok: bool, detail: str = ""):
    global PASS
    print(f"{'✓' if ok else '✗'} {name}" + (f"（{detail}）" if detail and not ok else ""))
    assert ok, name
    PASS += 1


def http(method: str, url: str, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, r.read().decode("utf-8")
    except urllib.error.HTTPError as e:  # 4xx 也作为正常响应返回
        return e.code, e.read().decode("utf-8")


# ---------- 1) _stream_round 单元测试：内容增量 + 工具调用累积 ----------

class FakeStreamClient:
    """按 chunk 序列回放的流式客户端。"""

    def __init__(self, chunks):
        self.chunks = chunks
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        assert kwargs.get("stream") is True
        return iter(self.chunks)


def _chunk(content=None, tc=None):
    delta = SimpleNamespace(content=content, tool_calls=tc)
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta)])


def _tc(index, id=None, name=None, arguments=None):
    fn = SimpleNamespace(name=name, arguments=arguments) if (name or arguments) else None
    return SimpleNamespace(index=index, id=id, function=fn)


deltas = []
client = FakeStreamClient([
    _chunk(content="你"),
    _chunk(content="好"),
    _chunk(tc=[_tc(0, id="call_1", name="re"), _tc(0, name=None, arguments="call")]),
    _chunk(tc=[_tc(0, arguments=" 翻页")]),
])
msg = _stream_round(client, Config(), [], on_delta=deltas.append)
check("流式内容增量按序推出", deltas == ["你", "好"])
check("流式工具调用累积", msg.tool_calls[0].id == "call_1"
      and msg.tool_calls[0].function.name == "re"
      and msg.tool_calls[0].function.arguments == "call 翻页")

# ---------- 2) HTTP 全流程：种子 → 对话(SSE) → 历史 ----------

with tempfile.TemporaryDirectory() as td:
    tmp = Path(td)
    appconfig.PERSONA_DIR = tmp / "persona"
    appconfig.PERSONA_FILE = appconfig.PERSONA_DIR / "persona.md"
    appconfig.PERSONA_HISTORY_DIR = appconfig.PERSONA_DIR / "history"
    appconfig.PERSONA_CHANGELOG = appconfig.PERSONA_DIR / "CHANGELOG.md"

    svc = ChatService(Config(), db_path=tmp / "alice.db", client=RegexFakeClient())
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(svc))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"

    status, body = http("GET", f"{base}/")
    check("首页可访问", status == 200 and "Alice" in body)

    status, body = http("GET", f"{base}/api/state")
    state = json.loads(body)
    check("状态接口：需种子", state["needs_seed"] is True and len(state["tags"]) >= 8)
    check("状态接口：记忆栏", state["memory"]["active"] == 0)

    status, body = http("POST", f"{base}/api/seed", {"tags": ["害羞", "幽默"]})
    state = json.loads(body)
    check("种子接口：落盘并就绪", status == 200 and state["needs_seed"] is False)
    check("人格文件写入临时目录", appconfig.PERSONA_FILE.exists())

    status, body = http("POST", f"{base}/api/seed", {"tags": ["只有一个"]})
    check("种子校验：拒绝 1 个标签", status == 400)

    status, body = http("POST", f"{base}/api/chat", {"text": "在吗"})
    frames = [json.loads(l[6:]) for l in body.split("\n") if l.startswith("data: ")]
    kinds = [f["type"] for f in frames]
    check("对话接口：SSE 帧序完整", kinds[0] == "start" and kinds[-1] == "done")
    check("对话接口：回复内容正确", frames[-1]["reply"] == "嗯嗯，我在听。")

    status, body = http("GET", f"{base}/api/history")
    turns = json.loads(body)["turns"]
    check("历史接口：两轮入库", len(turns) == 2 and turns[0]["role"] == "user")

    status, body = http("GET", f"{base}/api/metrics")
    m = json.loads(body)
    check("监测接口：结构完整", status == 200 and m["thinking"] == "on"
          and set(m["snapshot"]["cards"]) == {"tokens", "drift", "honesty", "human"})

    status, body = http("POST", f"{base}/api/metrics/toggle",
                        {"module": "drift", "on": False})
    m = json.loads(body)
    check("监测组装：拆下 drift", m["snapshot"]["cards"]["drift"]["enabled"] is False)
    status, body = http("POST", f"{base}/api/metrics/toggle",
                        {"module": "drift", "on": True})
    check("监测组装：装回 drift", json.loads(body)["snapshot"]["cards"]["drift"]["enabled"] is True)

    status, body = http("POST", f"{base}/api/settings", {"thinking": "off"})
    check("设置接口：思考深度可切换", status == 200 and json.loads(body)["thinking"] == "off")
    status, body = http("GET", f"{base}/api/metrics")
    check("设置接口：已持久化", json.loads(body)["thinking"] == "off")
    status, body = http("POST", f"{base}/api/settings", {"thinking": "on"})
    check("设置接口：切回 on", json.loads(body)["thinking"] == "on")

    status, body = http("GET", f"{base}/nope")
    check("未知路径 404", status == 404)

    httpd.shutdown()
    svc.close()

print(f"\nGUI 冒烟全部通过（{PASS} 项）。")
