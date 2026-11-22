"""Alice terminal client for an existing local profile.

视觉与交互遵循 TUI 设计 v1 §3：统一网格（常规每侧 2 列留白）、消息块
（用户 › 引导行、Alice • 名牌 + 正文 + 折叠工具摘要）、圆角输入盒、
§3.5 状态行（● 可以发送 / ○ 正在提交 / ◌ 处理中 / ! 异常），斜杠命令
菜单贴输入区展开。对话正文进终端原生回滚；流式回复以同一消息块版式
呈现在输入盒上方，完成时按宿主消息 ID 并入历史，正式消息只出现一次。
窗口收窄时依次让位：装饰→提示→流式预览→压缩草稿，光标行始终可见。
"""
from __future__ import annotations

import argparse
import os
import queue
import stat
import sys
import threading
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from client import StopWaiting, TuiApiError, TuiClient, profile_id, socket_path  # noqa: E402
from term import (Editor, Terminal, box_bottom, box_row, box_top, colors_on,  # noqa: E402
                  display_width, hrule, scrub, style, tool_label, tool_noun,
                  visible_width, wrap_line)
import art  # noqa: E402

COMMANDS = (("/history", "翻看对话"), ("/status", "查看当前状态"), ("/help", "查看键位"))
PLACEHOLDER = "输入消息，或按 / 查看命令"
SYNC_INTERVAL = 1.25
LIVE_TAIL = 12000          # 流式缓冲上限，超出只保留后段
LAUNCH_TIMEOUT = 60.0      # 按需启动的等待上限（说明 §3.2）
STATUS_SYMBOLS = {"idle": "●", "submit": "○", "work": "◌", "alert": "!"}
STATUS_TONE = {"idle": "brand", "submit": "brand", "work": "muted", "alert": "danger"}


def socket_problem(root: Path) -> str | None:
    """档案端点的静态权限核对（说明 §3.1：权限不符单独显示，不尝试启动）。"""
    path = socket_path(root)
    try:
        directory, entry = path.parent.lstat(), path.lstat()
    except FileNotFoundError:
        return None
    uid = os.geteuid()
    if not stat.S_ISDIR(directory.st_mode) \
            or (directory.st_uid, stat.S_IMODE(directory.st_mode)) != (uid, 0o700):
        return "socket 目录权限不符（应为当前账户的 0700）"
    if not stat.S_ISSOCK(entry.st_mode) \
            or (entry.st_uid, stat.S_IMODE(entry.st_mode)) != (uid, 0o600):
        return "socket 文件权限不符（应为当前账户的 0600）"
    return None


def probe_host(client: TuiClient) -> dict:
    """就绪握手：区分在线（含忙）、协议不兼容、无法连接（说明 §3.2.3）。"""
    try:
        info = client.status()
    except TuiApiError as exc:
        if exc.status == 503:
            return {"state": "online", "info": None}   # 宿主在线，正忙于接单收尾或维护
        if exc.status:
            return {"state": "incompatible", "detail": str(exc)}
        return {"state": "unreachable", "detail": str(exc)}
    if not isinstance(info, dict) or "session_epoch" not in info:
        return {"state": "incompatible", "detail": "状态响应缺少会话代次（宿主可能是旧版本）"}
    return {"state": "online", "info": info}


def host_log_path(root: Path) -> Path:
    return Path.home() / ".alice" / f"host-{profile_id(root)}.log"


def log_tail(path: Path, lines: int = 8) -> str:
    try:
        text = path.read_text(errors="replace").splitlines()
    except OSError:
        return "（暂无日志）"
    return "\n".join("  " + row for row in text[-lines:] if row.strip())


def launch_host(root: Path) -> str | None:
    """从当前源码环境按需启动后台宿主（说明 §3.2：只负责启动，已有实例不接管）。

    同档案并发启动时，输的一方会因档案锁/既有监听器退出，客户端继续等待
    赢得一方的就绪信号。就绪 = /status 返回完整状态；503 视为在线但忙。
    """
    import subprocess

    repo = Path(__file__).resolve().parents[2]
    log_path = host_log_path(root)
    log = None
    try:
        log = open(log_path, "ab")
    except OSError:
        log = None
    try:
        process = subprocess.Popen(
            # --user-dir 是顶层参数，必须位于 serve 子命令之前（desktop-host 同式样）。
            [sys.executable, "-m", "alice", "--user-dir", str(root), "serve", "--no-open"],
            cwd=str(repo), stdin=subprocess.DEVNULL,
            stdout=log if log is not None else subprocess.DEVNULL, stderr=subprocess.STDOUT,
            start_new_session=True)
    except OSError as exc:
        return f"无法启动宿主进程（{type(exc).__name__}）：{exc}"
    finally:
        if log is not None:
            log.close()
    client = TuiClient(socket_path(root))
    deadline = time.monotonic() + LAUNCH_TIMEOUT
    while time.monotonic() < deadline:
        if process.poll() is not None:
            tail = log_tail(log_path)
            hint = "\n  提示：本机 GUI 端口可能已被另一个档案的宿主占用。" if "端口" in tail else ""
            return (f"宿主进程已退出（退出码 {process.returncode}）。档案：{root}\n"
                    f"日志：{log_path}\n{tail}{hint}")
        probe = probe_host(client)
        if probe["state"] == "online" and probe["info"] is not None:
            return None
        if probe["state"] == "incompatible":
            return f"宿主协议不兼容：{probe['detail']}"
        time.sleep(0.5)
    return (f"启动超时（{LAUNCH_TIMEOUT:.0f} 秒未就绪）。档案：{root}\n"
            f"日志：{log_path}\n{log_tail(log_path)}")


def summarize_tools(names: list[str]) -> tuple[str, str]:
    """把一轮工具折叠成一行摘要：单一类型给名词，多种合并计数。"""
    nouns = [tool_noun(name) for name in names]
    label = nouns[0] if len(set(nouns)) == 1 else "工具调用"
    return label, f"{len(names)} 次"


class App:
    def __init__(self, client: TuiClient, info: dict):
        self.client = client
        self.epoch = info["session_epoch"]
        self.name = scrub(info.get("name") or "Alice").replace("\n", " ") or "Alice"
        self.wake = (info.get("wake") or {}).get("effective")
        self.cursor = None
        self.offline = False
        self.dirty = True
        self.drawn = 0
        self.term_size = None
        self.term = Terminal()
        self.editor = Editor(self.term)
        # 流式本轮（底部区域的临时消息块）
        self.stream: StopWaiting | None = None
        self.live_started = False
        self.live_text = ""
        self.live_tool = ""
        self.tool_seq: list[str] = []
        self.last_tools: list[str] | None = None
        self.expanded_for: str | None = None
        self.rendered_ids: set[int] = set()
        # 请求账本核对
        self.events = queue.SimpleQueue()
        self.pending: dict | None = None
        self.awaiting_host = False
        self.last_request: dict | None = None
        self.retry_request: dict | None = None
        self.recovery_text: str | None = None
        self.next_sync = 0.0
        # 命令菜单与提示
        self.menu_index = 0
        self.menu_hidden = False
        self.alert = ""

    # ---------- 几何 ----------
    def geometry(self) -> dict:
        w, h = self.term.width(), self.term.height()
        wide = w >= 64 and h >= 20
        margin = 2 if wide else 1
        content = max(12, w - 2 * margin)
        return {"width": w, "height": h, "margin": margin, "content": content,
                "body": max(8, min(content - 2, 84)), "input": max(6, content - 4),
                "hints": wide, "box": h >= 8}

    # ---------- 消息块 ----------
    def user_lines(self, text: str, g: dict) -> list[str]:
        rows = wrap_line(scrub(text), g["body"])
        return [" " * g["margin"] + (style("user_mark", "›") if i == 0 else " ") + " " + row
                for i, row in enumerate(rows)]

    def alice_lines(self, body: str, g: dict, *, proactive=False, tool=None, source=False) -> list[str]:
        label = style("muted", " · 主动邀请") if proactive else ""
        rows = [" " * g["margin"] + style("brand", "•") + " " + style("brand_bold", self.name) + label]
        if body:
            rows += [" " * (g["margin"] + 2) + row for row in wrap_line(scrub(body), g["body"])]
        if source:
            rows.append(" " * (g["margin"] + 2)
                        + style("muted", "此条包含限时来源，输入 /history 查看。"))
        if tool is not None:
            name, count = tool
            line = " " * (g["margin"] + 2) + style("brand", "▸") + " " + name
            if count:
                line += style("muted", f" · {count}")
            if g["hints"]:
                line += "  " + style("muted", "Ctrl+O 查看")
            rows.append(line)
        return rows

    def turn_lines(self, turn: dict, g: dict) -> list[str]:
        if turn["role"] == "user":
            return self.user_lines(turn.get("content") or "", g)
        source = bool(turn.get("source_bound") and turn.get("source_available"))
        return self.alice_lines("" if source else turn.get("content") or "", g,
                                proactive=bool(turn.get("proactive")), source=source)

    def live_rows(self, g: dict, budget: int) -> list[str]:
        """流式本轮的临时消息块；超预算只留尾部，保证最新文本可见。"""
        if budget <= 0 or (not self.live_started and not self.live_text and not self.live_tool):
            return []
        rows = [" " * g["margin"] + style("brand", "•") + " " + style("brand_bold", self.name)]
        body_rows = wrap_line(scrub(self.live_text), g["body"]) if self.live_text else []
        tail = budget - 1 - (1 if self.live_tool else 0)
        if len(body_rows) > tail:
            keep = max(0, tail - 1)
            body_rows = ([style("muted", "…")] if tail > 0 else []) + body_rows[len(body_rows) - keep:]
        rows += [" " * (g["margin"] + 2) + row for row in body_rows]
        if self.live_tool and len(rows) < budget:
            rows.append(" " * (g["margin"] + 2) + style("brand", "▸") + " "
                        + style("muted", self.live_tool))
        return rows[:budget]

    # ---------- 命令菜单 ----------
    def menu_open(self) -> bool:
        if self.menu_hidden:
            return False
        draft = self.editor.text()
        return ("\n" not in draft and draft.startswith("/") and not draft.startswith("//")
                and any(name.startswith(draft) for name, _ in COMMANDS))

    def menu_items(self) -> list[tuple[str, str]]:
        draft = self.editor.text()
        items = [(name, desc) for name, desc in COMMANDS if name.startswith(draft)]
        if self.menu_index >= len(items):
            self.menu_index = 0
        return items

    def menu_rows(self, g: dict) -> list[str]:
        show_desc = g["content"] >= 30
        rows = [box_top(g["content"], "命令")]
        for i, (name, desc) in enumerate(self.menu_items()):
            if i == self.menu_index:
                content = " " + style("brand", "›") + " " + style("brand_bold", name)
            else:
                content = "   " + style("muted", name)
            if show_desc:
                content += "  " + desc
            rows.append(box_row(content, g["content"]))
        rows.append(box_bottom(g["content"]))
        return rows

    # ---------- 状态行（§3.5） ----------
    def status_state(self) -> tuple[str, str]:
        if self.offline:
            return "alert", "连接中断 · 草稿已保留"
        if self.epoch is None:
            return "alert", "会话已更换 · 草稿已保留"
        if self.stream is not None:
            if not self.live_started:
                return "submit", "正在提交"
            return "work", self.live_tool or "Alice 正在回复"
        if self.pending is not None:
            if self.awaiting_host:
                return "work", "宿主仍在处理"
            return "alert", "等待确认上一条 · /status"
        if self.retry_request is not None:
            return "alert", "原请求未送达 · Enter 重试"
        if self.alert:
            return "alert", self.alert
        return "idle", "可以发送"

    def status_line(self, g: dict) -> str:
        kind, label = self.status_state()
        line = (" " * g["margin"] + style(STATUS_TONE[kind], STATUS_SYMBOLS[kind])
                + " " + label)
        if kind == "idle" and self.wake == "sleeping":
            line += style("muted", " · 后台睡眠")
        if g["hints"]:
            if self.menu_open():
                right = "Tab 补全 · Esc 返回"
            elif self.stream is not None:
                right = "Ctrl+O 进度"
            else:
                right = "Enter 发送   Ctrl+J 换行   / 命令"
            room = g["content"] - visible_width(line) - visible_width(right)
            if room >= 2:
                line += " " * room + style("muted", right)
        return line

    # ---------- 底部区域 ----------
    def bottom_rows(self, g: dict) -> list[str]:
        h = g["height"]
        if h < 4:
            label = "终端太小 · 请放大窗口" if g["content"] >= 24 else "终端太小"
            return [" " * g["margin"] + style("danger", "!") + " " + label]
        menu = self.menu_rows(g) if self.menu_open() else []
        use_box = g["box"] and h - 1 - len(menu) - 3 >= 1
        remaining = h - 1 - len(menu) - (3 if use_box else 2)
        live = self.live_rows(g, remaining - 1)
        remaining -= len(live)
        draft_rows, caret_row, _ = self.editor.render_rows(g["input"])
        first = max(0, min(caret_row - max(1, remaining) + 1, len(draft_rows) - 1))
        visible = draft_rows[first:first + max(1, remaining)]
        if not self.editor.text() and g["content"] >= 30 and not self.menu_open():
            visible = ["\x1b[7m \x1b[0m " + style("muted", PLACEHOLDER)]
        rows = menu + live
        rows.append(box_top(g["content"]) if use_box else hrule(g["content"]))
        for i, row in enumerate(visible):
            inner = " " + (style("brand", "›") if i == 0 else " ") + " " + row
            rows.append(box_row(inner, g["content"]) if use_box
                        else " " * g["margin"] + inner.rstrip())
        if use_box:
            rows.append(box_bottom(g["content"]))
        rows.append(self.status_line(g))
        return rows

    # ---------- 绘制 ----------
    def redraw(self) -> None:
        g = self.geometry()
        self.erase()
        rows = self.bottom_rows(g)
        self.term.draw_prompt(rows)
        self.drawn = len(rows)
        self.dirty = False

    def erase(self) -> None:
        self.term.erase_prompt(self.drawn)
        self.drawn = 0

    def transcript(self, lines: list[str]) -> None:
        self.erase()
        self.term.write("\n" + "\n".join(lines) + "\n")
        self.dirty = True

    def note(self, text: str, tone: str = "muted") -> None:
        g = self.geometry()
        rows = []
        for part in scrub(text).split("\n"):
            rows += wrap_line(part, g["body"])
        self.transcript([" " * (g["margin"] + 2) + style(tone, row) for row in rows])

    # ---------- 启动页 ----------
    def print_splash(self) -> None:
        w, h = self.term.width(), self.term.height()
        color = colors_on()
        blank, avatar_cells, mark_scale, compact = [], 0, 0, False
        if color and w >= 62 and h >= 30:
            blank, avatar_cells, mark_scale = [""] * 2, 24, 2
        elif color and w >= 40 and h >= 24:
            avatar_cells, mark_scale = 16, 1
        elif color and w >= 34 and h >= 18:
            avatar_cells, mark_scale, compact = 10, 1, True
        elif w >= 30 and h >= 12:
            mark_scale, compact = 1, True
        lines = list(blank)
        blocks: list[tuple[list[str], int]] = []
        if avatar_cells:
            blocks.append((art.render_avatar(avatar_cells), avatar_cells))
        if mark_scale:
            blocks.append((art.render_wordmark(mark_scale, compact, color=color),
                           art.wordmark_width(mark_scale)))
        for i, (rows, plain) in enumerate(blocks):
            if i:
                lines.append("")
            pad = max(0, (w - plain) // 2)
            lines += [" " * pad + row for row in rows]
        if not blocks:
            pad = max(0, (w - display_width(self.name)) // 2)
            lines = [" " * pad + style("brand_bold", self.name)]
        elif h >= 20 and w >= 30:
            lines.append("")
            pad = max(0, (w - display_width(PLACEHOLDER)) // 2)
            lines.append(" " * pad + style("muted", PLACEHOLDER))
        self.term.write("\n".join(lines) + "\n\n")

    # ---------- 历史同步 ----------
    def sync_history(self) -> None:
        try:
            page = self.client.history(after_id=self.cursor, limit=30, session_epoch=self.epoch)
        except TuiApiError as exc:
            if exc.code == "session_changed":
                if self.pending is not None:
                    self._restore_draft(self.pending)
                self.epoch = self.cursor = self.pending = self.retry_request = None
                self.awaiting_host = False
                self.live_text = ""
                self.erase()
                self.term.write("\x1b[3J\x1b[2J\x1b[H")
                self.print_splash()
                self.note("会话已更换；草稿保留，确认后可重新发送。")
            elif exc.code == "unreachable" and not self.offline:
                self.offline = True
                self.note("与 Alice 的连接中断，正在等待恢复。", "danger")
            return
        if self.offline:
            self.offline = False
            self.note("已重新连接 Alice。")
        self.epoch = page["session_epoch"]
        g = self.geometry()
        for turn in page["turns"]:
            if turn["id"] in self.rendered_ids:
                continue
            if self.cursor is None or turn["id"] > self.cursor:
                self.transcript(self.turn_lines(turn, g))
            if self.pending and turn["role"] == "assistant" \
                    and turn.get("request_id") == self.pending["request_id"]:
                self.pending = None
                self.awaiting_host = False
        self.cursor = page["next_after_id"]
        if page["has_more"] and page["next_before_id"] is None:
            self.next_sync = 0.0

    def check_pending(self) -> None:
        try:
            result = self.client.request_status(self.pending)
        except TuiApiError:
            return
        state = result["status"]
        if state in {"accepted", "running"}:
            self.awaiting_host = True
        elif state == "not_found":
            pending, self.pending = self.pending, None
            self.awaiting_host = False
            self.retry_request = pending
            self._restore_draft(pending)
            self.note("尚未找到接单记录。按 Enter 重试原请求。")
        elif state in {"completed", "completed_with_warning", "failed", "interrupted"}:
            self.pending = None
            self.awaiting_host = False
            self.next_sync = 0.0
            if not result.get("assistant_turn_id"):
                self.note("原请求已结束，未保存回复。", "danger")

    # ---------- 发送 ----------
    def begin_send(self, draft: str) -> None:
        if self.stream or self.pending:
            self.alert = "正在核对上一条 · 草稿已保留"
            return
        if not self.epoch or self.offline:
            self.alert = "等待连接恢复 · 草稿已保留"
            return
        request = self.retry_request
        if not request or request["text"] != draft or request["session_epoch"] != self.epoch:
            request = {"text": draft, "request_id": uuid.uuid4().hex, "session_epoch": self.epoch}
        self.last_request = self.pending = request
        self.retry_request = None
        self.editor.clear()
        self.menu_hidden = False
        self.alert = ""
        self.live_started = False
        self.live_text = ""
        self.live_tool = ""
        self.tool_seq = []
        self.last_tools = None
        self.expanded_for = None
        stop = self.stream = StopWaiting()
        self.dirty = True

        def send():
            try:
                result = self.client.chat_stream(request, lambda event: self.events.put(("event", event)), stop)
            except TuiApiError as exc:
                self.events.put(("error", exc))
            else:
                self.events.put(("done", result))

        threading.Thread(target=send, name="alice-tui-chat", daemon=True).start()

    def _restore_draft(self, request: dict) -> None:
        if self.editor.text():
            self.recovery_text = request["text"]
            self.note("上一条草稿也已保留，可用 /draft 取回。")
        else:
            self.editor.set_text(request["text"])
        self.dirty = True

    def _finish_stream(self, kind, value) -> None:
        self.stream = None
        self.live_started = False
        self.live_tool = ""
        self.live_text = ""
        self.next_sync = 0.0
        if kind == "done":
            self.pending = None
            self.awaiting_host = False
            self.rendered_ids.add(value.get("assistant_turn_id"))
            self.last_tools = list(self.tool_seq)
            self.transcript(self.alice_lines(
                value.get("reply") or "", self.geometry(),
                tool=summarize_tools(self.last_tools) if self.last_tools else None))
        elif value.status in {400, 401, 403, 413} or value.code in {"busy", "draining", "session_changed"}:
            if self.pending is not None:
                self._restore_draft(self.pending)
            self.pending = None
            self.awaiting_host = False
            if value.code == "busy":
                self.alert = "Alice 正忙 · 草稿已保留"
            self.note(str(value), "danger")
        else:
            # 断开但宿主继续处理：pending 保留，状态区给出核对入口（§3.5）。
            self.note(str(value))

    def update(self) -> None:
        while not self.events.empty():
            kind, value = self.events.get_nowait()
            if kind != "event":
                self._finish_stream(kind, value)
                continue
            event_type = value.get("type")
            if event_type == "start":
                self.live_started = True
            elif event_type == "delta":
                self.live_text = (self.live_text + (value.get("text") or ""))[-LIVE_TAIL:]
                self.live_tool = ""
            elif event_type == "tool":
                name = value.get("name", "")
                self.tool_seq.append(name)
                self.live_tool = tool_label(name)
            elif event_type == "search":
                if value.get("status") == "running":
                    self.live_tool = ("正在读取资料" if value.get("phase") == "read"
                                      else "正在查找资料")
                else:
                    self.live_tool = ""
        now = time.monotonic()
        if self.stream is None and now >= self.next_sync:
            self.next_sync = now + SYNC_INTERVAL
            self.sync_history()
            if self.pending is not None:
                self.check_pending()

    # ---------- 工具展开 ----------
    def expand_tools(self) -> None:
        names = list(self.tool_seq) if self.stream is not None else list(self.last_tools or [])
        if not names:
            self.note("本轮还没有工具进度。")
            return
        if self.stream is None:
            request_id = (self.last_request or {}).get("request_id")
            if request_id and self.expanded_for == request_id:
                return
            self.expanded_for = request_id
        g = self.geometry()
        self.transcript([" " * (g["margin"] + 4) + style("muted", "· " + tool_label(name))
                         for name in names])

    # ---------- 命令与状态视图 ----------
    def cmd_status(self) -> None:
        try:
            info = self.client.status()
        except TuiApiError as exc:
            self.note(f"暂不可确认：{exc}", "danger")
            return
        self.wake = (info.get("wake") or {}).get("effective")
        wake = {"awake": "清醒", "sleeping": "睡眠"}.get(self.wake, "暂不可确认")
        lines = [f"会话 {info['session_epoch'][:8]} · "
                 f"前台 {'忙碌' if info.get('busy') else '空闲'} · 后台 {wake}"]
        if self.last_request:
            try:
                result = self.client.request_status(self.last_request)
                states = {"not_found": "未找到接单记录", "accepted": "已接单", "running": "处理中",
                          "failed": "失败", "interrupted": "已中断", "completed": "已完成",
                          "completed_with_warning": "已完成，有收尾提示"}
                lines.append(f"上一次请求 {states.get(result['status'], '暂不可确认')}")
            except TuiApiError as exc:
                lines.append(f"上一次请求暂不可确认（{exc}）")
        self.note("\n".join(lines), "brand_bold")

    def _history_row(self, turn: dict) -> str:
        body = scrub(turn.get("content") or "").replace("\n", " ")
        who = "你" if turn["role"] == "user" else self.name
        mark = style("user_mark", "›") if turn["role"] == "user" else style("brand", "•")
        return f"#{turn['id']} {mark} {who}  {body[:160]}"

    def cmd_history(self, keyword: str) -> None:
        cursor = None
        self.erase()
        self.term.write("\x1b[?1049h")
        try:
            while True:
                try:
                    rows = max(1, min(15, self.term.height() - 4))
                    page = self.client.history(before_id=cursor, limit=rows, session_epoch=self.epoch)
                except TuiApiError as exc:
                    self.term.write("\x1b[2J\x1b[H" + scrub(str(exc)) + "\n按 q 返回\n")
                    page = {}
                else:
                    turns = page["turns"]
                    matches = [turn for turn in turns if keyword in scrub(turn.get("content") or "")]
                    scope = f"#{turns[0]['id']}–#{turns[-1]['id']}" if turns else "暂无记录"
                    lines = [f"历史 {scope} · 本页 {len(turns)} 条"] \
                        + [self._history_row(turn) for turn in matches]
                    if keyword and not matches:
                        lines.append("本页未找到该关键词。")
                    lines.append("Enter 更早一页 · r 刷新 · q 返回" if page["has_more"]
                                 else "r 刷新 · q 返回")
                    self.term.write("\x1b[2J\x1b[H")
                    self.term.print_lines([self.term.clip(line) for line in lines])
                refresh_at = time.monotonic() + SYNC_INTERVAL
                while time.monotonic() < refresh_at:
                    key = self.editor.poll_key(timeout=0.25)
                    if key in ("q", "esc", "exit", "interrupt"):
                        return
                    if key == "r":
                        break
                    if key == "enter" and page.get("has_more"):
                        cursor = page["next_before_id"]
                        break
        finally:
            self.term.write("\x1b[?1049l")
            self.dirty = True

    def run_command(self, draft: str) -> bool:
        name, _, arg = draft.partition(" ")
        if "\n" in draft:
            return False
        if name == "/history":
            self.cmd_history(arg.strip())
        elif name == "/status":
            self.cmd_status()
        elif name == "/help":
            self.note("/history [关键词]  翻看历史\n/status  会话与原请求状态\n"
                      "Enter 发送 · Ctrl+J 换行 · / 命令菜单 · Tab 补全 · Esc 关菜单\n"
                      "Ctrl+O  本轮工具进度 · Ctrl+C 停止等待或清空草稿 · Ctrl+D 关闭终端客户端")
        else:
            return False
        return True

    def on_enter(self) -> None:
        if self.menu_open():
            name = self.menu_items()[self.menu_index][0]
            self.editor.clear()
            self.menu_hidden = False
            self.run_command(name)
            return
        draft = self.editor.text().strip()
        if draft == "/draft" and self.recovery_text is not None:
            self.editor.set_text(self.recovery_text)
            self.recovery_text = None
        elif draft and self.run_command(draft):
            self.editor.clear()
        elif draft.startswith("//"):
            self.begin_send(draft[1:])
        elif draft.startswith("/") and "\n" not in draft:
            self.note("未识别命令。用 // 开头发送以斜杠开头的普通消息。")
        elif draft:
            self.begin_send(draft)

    # ---------- 主循环 ----------
    def run(self) -> None:
        with self.term:
            self.print_splash()
            try:
                while True:
                    self.update()
                    size = (self.term.width(), self.term.height())
                    if self.dirty or size != self.term_size:
                        self.term_size = size
                        self.redraw()
                    key = self.editor.poll_key(timeout=0.1 if self.stream else 0.25)
                    if key is None:
                        continue
                    if key == "exit":
                        return
                    if key == "interrupt":
                        if self.stream:
                            self.stream.stop()
                        elif self.editor.text():
                            self.editor.clear()
                            self.menu_hidden = False
                            self.alert = ""
                        else:
                            return
                    elif key == "expand":
                        self.expand_tools()
                    elif key == "enter":
                        self.on_enter()
                    elif key.startswith("paste:"):
                        self.editor.insert(key[6:])
                        self.menu_hidden = False
                        self.menu_index = 0
                        self.alert = ""
                    elif key == "newline":
                        self.editor.insert("\n")
                        self.menu_hidden = False
                    elif key == "backspace":
                        self.editor.backspace()
                        self.menu_hidden = False
                        self.menu_index = 0
                        self.alert = ""
                    elif key in ("up", "down", "left", "right", "home", "end"):
                        if key in ("up", "down") and self.menu_open():
                            count = len(self.menu_items())
                            self.menu_index = (self.menu_index + (1 if key == "down" else -1)) % count
                        else:
                            self.editor.move(key)
                    elif key == "tab":
                        if self.menu_open():
                            self.editor.set_text(self.menu_items()[self.menu_index][0])
                    elif key == "esc":
                        if self.menu_open():
                            self.menu_hidden = True
                        elif self.editor.text():
                            self.editor.clear()
                            self.menu_hidden = False
                            self.alert = ""
                    elif len(key) == 1 and ord(key) >= 32:
                        self.editor.insert(key)
                        self.menu_hidden = False
                        self.menu_index = 0
                        self.alert = ""
                    self.dirty = True
            finally:
                if self.stream is not None:
                    self.stream.stop()
                self.erase()


def main() -> None:
    parser = argparse.ArgumentParser(prog="alice-tui", description="Alice 终端客户端")
    profiles = parser.add_mutually_exclusive_group()
    profiles.add_argument("--user-dir", type=Path, help="指定正在运行的 Alice 档案绝对路径")
    profiles.add_argument("--desktop", action="store_true", help="连接 macOS 桌面版的默认档案")
    args = parser.parse_args()
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        parser.error("请在交互式终端中打开 Alice")
    if args.desktop:
        root = Path.home() / "Library" / "Application Support" / "Alice"
    else:
        root = args.user_dir or Path(os.environ.get("ALICE_USER_DIR") or Path(__file__).resolve().parents[2] / "user")
    root = root.expanduser()
    if not root.is_absolute():
        parser.error("Alice 档案目录必须是绝对路径")
    client = TuiClient(socket_path(root))
    problem = socket_problem(root)
    if problem is not None:
        raise SystemExit(f"{problem}。档案：{root}")
    try:
        probe = probe_host(client)
        if probe["state"] == "incompatible":
            raise SystemExit(f"宿主协议不兼容：{probe['detail']}。档案：{root}")
        if probe["state"] != "online":
            print(f"正在启动 Alice（档案 {root}）… Ctrl+C 只退出等待，宿主会继续启动。")
            failure = launch_host(root)
            if failure is not None:
                raise SystemExit(failure)
            probe = probe_host(client)
        # 忙（503）或刚接单收尾都算在线：轮询到状态可用为止（§3.2.3 等待有上限）。
        deadline = time.monotonic() + LAUNCH_TIMEOUT
        while probe["state"] != "online" or probe["info"] is None:
            if probe["state"] == "incompatible":
                raise SystemExit(f"宿主协议不兼容：{probe['detail']}。档案：{root}")
            if time.monotonic() >= deadline:
                raise SystemExit(f"宿主状态暂不可用（忙碌或刚启动）。档案：{root}")
            time.sleep(0.5)
            probe = probe_host(client)
    except KeyboardInterrupt:
        print("\n已退出等待；宿主仍在启动，稍后可用 alice 重新连接。")
        return
    info = probe["info"]
    if not info.get("ready"):
        print("请先在网页或桌面完成模型设置和人格安装。")
    try:
        App(client, info).run()
    except (EOFError, KeyboardInterrupt):
        print()


if __name__ == "__main__":
    main()
