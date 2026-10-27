"""部署：把一次压力会话跑进独立的测试检出，不碰项目的 data/。

做法（见 docs/工作区约定.md 的公共面）：
1. 复制 `alice/`、`web/` 到一个会话目录，放进自己的 `config.toml`；
2. `python -m alice serve` 起服务，通过 HTTP 公共面（/api/state、/api/seed、/api/chat SSE）驱动；
3. 判定基准用原生 sqlite3 写进**会话自己的**库（夹具），再用 /api/state 复核。

测试区不 import alice 内部；唯一的结构耦合是 memories 表列名，变了会显式报错。
"""
from __future__ import annotations

import http.client
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import case as case_mod

PROJECT = Path(__file__).resolve().parents[2]
SESSIONS = PROJECT / "tests" / ".sessions"
COPY_IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc")


class DeployError(RuntimeError):
    pass


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@dataclass
class Session:
    home: Path
    port: int
    proc: subprocess.Popen

    # ---------- HTTP 公共面 ----------

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def _get(self, path: str) -> dict:
        with urllib.request.urlopen(self.base + path, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _post(self, path: str, payload: dict, timeout: int = 180) -> dict:
        req = urllib.request.Request(
            self.base + path,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def state(self) -> dict:
        return self._get("/api/state")

    def seed(self, tags: tuple[str, ...]) -> dict:
        return self._post("/api/seed", {"tags": list(tags)})

    def chat(self, text: str, timeout: int = 240) -> dict:
        """发一轮对话，读完 SSE。返回 reply / tools / events。"""
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=timeout)
        try:
            conn.request(
                "POST",
                "/api/chat",
                body=json.dumps({"text": text}),
                headers={"Content-Type": "application/json"},
            )
            resp = conn.getresponse()
            reply, tools, events, error = "", [], [], None
            for raw in resp:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                ev = json.loads(line[5:].strip())
                kind = ev.get("type")
                if kind == "done":
                    reply = str(ev.get("reply") or "")
                elif kind == "tool":
                    tools.append(str(ev.get("name") or ""))
                elif kind == "meta":
                    events.append(str(ev.get("text") or ""))
                elif kind == "error":
                    error = str(ev.get("message") or "")
            if error:
                raise DeployError(error)
            if not reply:
                raise DeployError("这一轮没有拿到回复（SSE 里没有 done）")
            return {"reply": reply, "tools": tools, "events": events}
        finally:
            conn.close()

    # ---------- 收尾 ----------

    def close(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=8)


def _wait_ready(session: Session, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if session.proc.poll() is not None:
            raise DeployError(f"服务提前退出，日志见 {session.home / 'server.log'}")
        try:
            session.state()
            return
        except Exception:
            time.sleep(0.3)
    raise DeployError(f"服务 30 秒内未就绪，日志见 {session.home / 'server.log'}")


def _copy_core(home: Path) -> None:
    for name in ("alice", "web"):
        src = PROJECT / name
        if src.exists():
            shutil.copytree(src, home / name, ignore=COPY_IGNORE)


def _write_config(home: Path) -> None:
    src = PROJECT / "config.toml"
    if not src.exists():
        raise DeployError(
            "缺少 config.toml：压力测试要用真实模型。先把 config.example.toml 复制成 "
            "config.toml 并填好 base_url / 模型 / api_key。"
        )
    shutil.copyfile(src, home / "config.toml")


def _git_commit() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=PROJECT,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return out.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def _python() -> str:
    """优先用项目 .venv（真机要 openai），没有才退回当前解释器。"""
    venv = PROJECT / ".venv" / "bin" / "python"
    return str(venv) if venv.exists() else sys.executable


def create(case: case_mod.Case, *, stamp: str | None = None) -> Session:
    """建会话目录、部署、起服务、生成人格底色、落判定基准。"""
    stamp = stamp or datetime.now().strftime("%Y%m%d-%H%M%S")
    home = SESSIONS / f"{case.id}-{stamp}"
    home.mkdir(parents=True, exist_ok=False)
    _copy_core(home)
    _write_config(home)

    port = free_port()
    log = open(home / "server.log", "w", encoding="utf-8")
    proc = subprocess.Popen(
        [_python(), "-m", "alice", "serve", "--port", str(port), "--no-open"],
        cwd=home,
        stdout=log,
        stderr=subprocess.STDOUT,
        text=True,
    )
    session = Session(home=home, port=port, proc=proc)
    try:
        _wait_ready(session)
        state = session.state()
        if state.get("needs_seed"):
            session.seed(case.tags)
        _plant(session, case)
        check = session.state()
        if check["memory"]["longterm"] < 1:
            raise DeployError("判定基准没落库（longterm=0），检查 memories 表结构")
        return session
    except Exception:
        session.close()
        raise


def _plant(session: Session, case: case_mod.Case) -> None:
    db = session.home / "data" / "alice.db"
    for _ in range(20):
        if db.exists():
            break
        time.sleep(0.2)
    conn = sqlite3.connect(db, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        ids = case_mod.plant(conn, case, now=time.time())
        if ids <= 0:
            raise DeployError("判定基准写入失败")
    finally:
        conn.close()


def snapshot_persona(session: Session) -> str:
    path = session.home / "data" / "persona" / "persona.md"
    return path.read_text(encoding="utf-8") if path.exists() else ""


def save_snapshot(session: Session, name: str) -> Path:
    text = snapshot_persona(session)
    out = session.home / name
    out.write_text(text or "（尚未生成人格文件）", encoding="utf-8")
    return out
