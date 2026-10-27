"""压测启动器：一条命令起一个页面，点「开始测试」就部署一场压测。

页面左边是部署出来的 Alice 自己的界面（就在那儿跟她说话），右边是你的剧本、判定和记录：
- 换了 `alice/`、`web/` 或用例 TOML，顶部会亮"环境已过期"，点「重新部署」生效；
- 空场次（还没说过话）时改代码会自动重新部署；页面自身改了会自动刷新；
- 跑完点「生成报告」，报告落在会话目录里，也能直接看。

用法（项目根目录）：
    python3 tests/pressure/launch.py            # 起页面并打开浏览器
    python3 tests/pressure/launch.py --port 8899 --no-open
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import threading
import time
import tomllib
import traceback
import urllib.parse
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT))

from tests.pressure import case as case_mod  # noqa: E402
from tests.pressure import deploy, record, report  # noqa: E402
from tests.pressure.model import TurnRecord  # noqa: E402

HERE = Path(__file__).resolve().parent
UI_PATH = HERE / "launcher.html"
CASE_DIR = HERE / "cases"
WATCH_ROOTS = (PROJECT / "alice", PROJECT / "web", HERE)
WATCH_SUFFIX = {".py", ".toml", ".html", ".js", ".css", ".json", ".md"}


def _snapshot() -> dict[str, str]:
    items: dict[str, str] = {}
    for root in (*WATCH_ROOTS, CASE_DIR):
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.is_dir() or "__pycache__" in path.parts or path.suffix not in WATCH_SUFFIX:
                continue
            st = path.stat()
            items[str(path)] = f"{st.st_mtime_ns}:{st.st_size}"
    return items


def _ui_version() -> str:
    h = hashlib.sha1()
    for path in (UI_PATH, Path(__file__)):
        try:
            st = path.stat()
            h.update(f"{path}:{st.st_mtime_ns}:{st.st_size}".encode())
        except OSError:
            pass
    return h.hexdigest()[:12]


def _model_name() -> str:
    try:
        data = tomllib.loads((PROJECT / "config.toml").read_text(encoding="utf-8"))
        return str(data.get("llm", {}).get("chat_model", "unknown"))
    except Exception:
        return "unknown"


class Launcher:
    """一场压测的监督者：部署、收对话、记判定、出报告。"""

    def __init__(self, case_dir: Path = CASE_DIR):
        self.case_dir = case_dir
        self.lock = threading.RLock()
        self.session: deploy.Session | None = None
        self.case: case_mod.Case | None = None
        self.case_path: Path | None = None
        self.status = "idle"  # idle / deploying / running / error
        self.error = ""
        self.started_at = 0.0
        self.transcript: list[dict] = []
        self.judges: dict[str, dict] = {}
        self.report_md = ""
        self.memory: dict = {}
        self.stale = False      # alice/、web/、用例变了：重新部署即生效
        self.restart = False    # 启动器自己的代码变了：得重启这个进程
        self.changed: list[str] = []
        self.snapshot = _snapshot()
        self.ui_version = _ui_version()
        threading.Thread(target=self._watch_loop, daemon=True).start()

    # ---------- 用例 ----------

    def cases(self) -> list[dict]:
        out = []
        for path in sorted(self.case_dir.glob("*.toml")):
            try:
                c = case_mod.load(path)
            except Exception as e:  # 坏用例也要列出来，别让它悄悄消失
                out.append({"id": path.stem, "title": f"读不了：{e}", "broken": True,
                            "turns": {}, "path": str(path)})
                continue
            out.append({
                "id": c.id, "title": c.title, "p_class": c.p_class,
                "turns": {k: len(v) for k, v in c.acts.items()},
                "persona": list(c.tags), "path": str(path),
            })
        return out

    def _ordered(self) -> list[tuple[str, int, case_mod.Turn]]:
        if not self.case:
            return []
        return [
            (act, n, turn)
            for act in case_mod.ACT_NAMES
            for n, turn in enumerate(self.case.acts[act], start=1)
        ]

    # ---------- 生命周期 ----------

    def start(self, case_path: Path) -> None:
        with self.lock:
            if self.session is not None:
                self._stop_locked()
            self.status = "deploying"
            self.error = ""
            self.transcript = []
            self.judges = {}
            self.report_md = ""
            self.report_path = None
        threading.Thread(target=self._deploy, args=(Path(case_path),), daemon=True).start()

    def _deploy(self, case_path: Path) -> None:
        try:
            case = case_mod.load(case_path)
            session = deploy.create(case)
            deploy.save_snapshot(session, "persona_before.md")
            with self.lock:
                self.session = session
                self.case = case
                self.case_path = case_path
                self.started_at = time.time()
                self.status = "running"
                self.stale = False
                self.restart = False
                self.changed = []
                self.memory = session.state().get("memory", {})
                self.snapshot = _snapshot()
        except Exception as e:
            traceback.print_exc()
            with self.lock:
                self.status = "error"
                self.error = str(e)
            return
        self._start_capture()

    def _stop_locked(self) -> None:
        session = self.session
        self.session = None
        self.status = "idle"
        if session is not None:
            threading.Thread(target=session.close, daemon=True).start()

    def stop(self) -> None:
        with self.lock:
            self._stop_locked()

    # ---------- 收对话 ----------

    def _start_capture(self) -> None:
        threading.Thread(target=self._capture_loop, daemon=True).start()

    def _capture_loop(self) -> None:
        seen = 0
        while True:
            with self.lock:
                session, running = self.session, self.status == "running"
            if not running or session is None:
                return
            try:
                messages = session.history()
                self.memory = session.state().get("memory", {}) if hasattr(session, "state") else self.memory
            except Exception:
                time.sleep(1.5)
                continue

            pending = None
            pairs: list[tuple[str, str]] = []
            i = seen
            while i < len(messages):
                msg = messages[i]
                role = msg.get("role")
                if role == "user":
                    pending = str(msg.get("content") or "")
                elif role == "assistant" and pending is not None:
                    pairs.append((pending, str(msg.get("content") or "")))
                    pending = None
                i += 1
            seen = i if pending is None else max(0, i - 1)
            if pairs:
                with self.lock:
                    for spoken, reply in pairs:
                        self._append_turn(spoken, reply)
            time.sleep(1.0)

    def _append_turn(self, spoken: str, reply: str) -> None:
        ordered = self._ordered()
        index = len(self.transcript)
        if index < len(ordered):
            act, n, turn = ordered[index]
            scripted = turn.user
        else:  # 剧本之外多聊的，也留下来
            act, n, turn, scripted = "extra", index + 1, None, ""
        self.transcript.append({
            "index": index,
            "act": act,
            "n": n,
            "scripted": scripted,
            "spoken": spoken,
            "reply": reply,
            "ts": time.time(),
        })

    # ---------- 判定 ----------

    def judge(self, act: str, n: int, mode: str, level: str, note: str = "") -> dict:
        level = level.upper().strip()
        if act not in (*case_mod.ACT_NAMES, "extra"):
            raise ValueError(f"未知幕 {act!r}")
        if level not in record.LEVELS:
            raise ValueError(f"未知级别 {level!r}")
        judge = {"mode": mode.strip() or "—", "level": level, "note": note.strip(), "source": "人工"}
        with self.lock:
            self.judges[f"{act}:{n}"] = judge
        return judge

    # ---------- 报告 ----------

    def build_report(self) -> dict:
        with self.lock:
            session, case = self.session, self.case
            transcript = list(self.transcript)
            judges_raw = dict(self.judges)
            started_at = self.started_at
        if session is None or case is None:
            raise RuntimeError("还没有场次可以出报告")

        ordered = [
            (act, n, turn)
            for act in case_mod.ACT_NAMES
            for n, turn in enumerate(case.acts[act], start=1)
        ]
        records = []
        for item in transcript:
            index = int(item.get("index", 0))
            if index < len(ordered):
                act, n, turn = ordered[index]
            else:
                act, n, turn = "extra", index + 1, case_mod.Turn(user=item.get("spoken", ""))
            records.append(TurnRecord(
                act=act, n=n, turn=turn, reply=str(item.get("reply") or ""),
                tools=list(item.get("tools") or []), events=list(item.get("events") or []),
            ))

        judges = {
            (key.split(":", 1)[0], int(key.split(":", 1)[1])): value
            for key, value in judges_raw.items()
        }
        metrics = record.summarize(case, records, judges)
        before = (session.home / "persona_before.md").read_text(encoding="utf-8")
        deploy.save_snapshot(session, "persona_after.md")
        after_text = deploy.snapshot_persona(session)
        new_memories = deploy.new_memories(session, started_at)
        after = {
            "structural": bool(new_memories) or before != after_text,
            "persona_changed": before != after_text,
            "new_memories": new_memories,
            "memory": self.memory,
        }
        meta = {
            "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "mode": f"真机 · {_model_name()}（启动器）",
            "case_file": str(self.case_path),
            "commit": deploy._git_commit(),
            "started_at": started_at,
            "launcher": True,
        }
        md = report.render(case, metrics, meta, after)
        (session.home / "report.md").write_text(md, encoding="utf-8")
        with (session.home / "transcript.jsonl").open("w", encoding="utf-8") as fh:
            for rec, judge, suggestion in metrics["rows"]:
                fh.write(json.dumps({
                    "act": rec.act, "n": rec.n, "scripted": rec.user, "reply": rec.reply,
                    "tools": rec.tools, "events": rec.events, "judgment": judge,
                    "rule_suggestion": {"mode": suggestion.mode, "level": suggestion.level,
                                        "why": suggestion.why, "quote": suggestion.quote},
                }, ensure_ascii=False) + "\n")
        (session.home / "meta.json").write_text(
            json.dumps({**meta, "metrics": {k: v for k, v in metrics.items() if k != "rows"}},
                       ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
        with self.lock:
            self.report_md = md
        return {
            "markdown": md,
            "path": str(session.home / "report.md"),
            "home": str(session.home),
            "metrics": {k: v for k, v in metrics.items() if k != "rows"},
        }

    # ---------- 热重载 ----------

    def _watch_loop(self) -> None:
        while True:
            time.sleep(1.5)
            try:
                snap = _snapshot()
            except Exception:
                continue
            with self.lock:
                prev = self.snapshot
                if snap != prev:
                    changed = sorted(k for k in snap if prev.get(k) != snap.get(k))
                    self.changed = [os.path.relpath(k, PROJECT) for k in changed]
                    self.snapshot = snap
                    self.ui_version = _ui_version()
                    deploy_relevant = any(
                        name.startswith(("alice/", "web/", "tests/pressure/cases/"))
                        for name in self.changed
                    )
                    code_relevant = any(
                        name.startswith("tests/pressure/") and name.endswith(".py")
                        for name in self.changed
                    )
                    self.stale = self.status == "running" and deploy_relevant
                    self.restart = self.restart or code_relevant
                auto = (
                    self.stale
                    and self.status == "running"
                    and not self.transcript
                    and self.case_path is not None
                )
                case_path = self.case_path
            if auto and case_path is not None:
                self.start(case_path)  # 还没说话：直接换上新环境

    # ---------- 状态 ----------

    def state(self) -> dict:
        with self.lock:
            ordered = self._ordered()
            index = len(self.transcript)
            current = None
            if ordered:
                if index < len(ordered):
                    act, n, turn = ordered[index]
                    current = {"act": act, "n": n, "total": len(ordered),
                               "script": turn.user, "note": turn.note, "index": index}
                else:
                    current = {"act": "done", "n": len(ordered), "total": len(ordered),
                               "script": "", "note": "剧本走完了，可以出报告", "index": index}
            session = self.session
            return {
                "status": self.status,
                "error": self.error,
                "stale": self.stale,
                "restart": self.restart,
                "changed": self.changed,
                "ui_version": self.ui_version,
                "case_id": self.case.id if self.case else "",
                "case_title": self.case.title if self.case else "",
                "case_path": str(self.case_path) if self.case_path else "",
                "base": session.base if session else "",
                "home": str(session.home) if session else "",
                "memory": dict(self.memory),
                "current": current,
                "transcript": [dict(t) for t in self.transcript],
                "judges": dict(self.judges),
            }


# ---------- HTTP ----------

class Handler(BaseHTTPRequestHandler):
    launcher: Launcher = None  # 由 serve() 注入

    def log_message(self, fmt, *args):  # 别把轮询日志刷满终端
        pass

    def _json(self, payload, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = 0
        raw = self.rfile.read(n) if n else b""
        try:
            return json.loads(raw or b"{}")
        except json.JSONDecodeError:
            return {}

    def do_GET(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        if path in ("/", "/launcher.html"):
            body = UI_PATH.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        elif path == "/api/cases":
            self._json({"cases": self.launcher.cases()})
        elif path == "/api/state":
            self._json(self.launcher.state())
        elif path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        try:
            if path == "/api/start":
                wanted = str(self._body().get("case") or "")
                match = next((c for c in self.launcher.cases()
                              if wanted in (c["id"], c["path"], Path(c["path"]).stem)), None)
                if match is None:
                    self._json({"error": f"找不到用例 {wanted!r}"}, 404)
                    return
                if match.get("broken"):
                    self._json({"error": match["title"]}, 400)
                    return
                self.launcher.start(Path(match["path"]))
                self._json({"ok": True})
            elif path == "/api/redeploy":
                with self.launcher.lock:
                    case_path = self.launcher.case_path
                if case_path is None:
                    self._json({"error": "还没有跑过用例"}, 400)
                else:
                    self.launcher.start(case_path)
                    self._json({"ok": True})
            elif path == "/api/stop":
                self.launcher.stop()
                self._json({"ok": True})
            elif path == "/api/judge":
                body = self._body()
                judge = self.launcher.judge(
                    str(body.get("act") or ""), int(body.get("n") or 0),
                    str(body.get("mode") or ""), str(body.get("level") or ""),
                    str(body.get("note") or ""),
                )
                self._json({"ok": True, "judgment": judge})
            elif path == "/api/report":
                self._json(self.launcher.build_report())
            else:
                self._json({"error": "not found"}, 404)
        except ValueError as e:
            self._json({"error": str(e)}, 400)
        except Exception as e:
            traceback.print_exc()
            self._json({"error": str(e)}, 500)


def serve(port: int = 8899, open_browser: bool = True) -> None:
    launcher = Launcher()
    Handler.launcher = launcher
    httpd = None
    for candidate in range(port, port + 20):
        try:
            httpd = ThreadingHTTPServer(("127.0.0.1", candidate), Handler)
            port = candidate
            break
        except OSError:
            continue
    if httpd is None:
        raise SystemExit(f"端口 {port}~{port + 19} 都被占用了")
    url = f"http://127.0.0.1:{port}"
    print(f"压测启动器：{url}", flush=True)
    print("点「开始测试」部署一场；改了 alice/ 或 web/ 会亮「环境已过期」。Ctrl+C 退出。", flush=True)
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n再见。")
    finally:
        launcher.stop()
        httpd.server_close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="压测启动器（浏览器）")
    parser.add_argument("--port", type=int, default=8899)
    parser.add_argument("--no-open", action="store_true")
    args = parser.parse_args(argv)
    serve(args.port, not args.no_open)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
