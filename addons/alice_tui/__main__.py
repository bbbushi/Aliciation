"""Alice terminal client for an existing local profile."""
from __future__ import annotations

import argparse
import os
import queue
import sys
import threading
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from client import StopWaiting, TuiApiError, TuiClient, socket_path  # noqa: E402
from term import Editor, Terminal, scrub, style, tool_label, wrap_line  # noqa: E402

HINT = "Enter 发送 · Ctrl+J 换行 · /help · Ctrl+O 进度 · Ctrl+D 退出"
SYNC_INTERVAL = 1.25


class App:
    def __init__(self, client: TuiClient, info: dict):
        self.client = client
        self.epoch = info["session_epoch"]
        self.name = scrub(info.get("name") or "Alice").replace("\n", " ")
        self.cursor = None
        self.status_text = ""
        self.preview = ""
        self.tool_events: list[str] = []
        self.stream: StopWaiting | None = None
        self.events = queue.SimpleQueue()
        self.pending: dict | None = None
        self.last_request: dict | None = None
        self.retry_request: dict | None = None
        self.recovery_text: str | None = None
        self.next_sync = 0.0
        self.offline = False
        self.dirty = True
        self.drawn = 0
        self.term_size = None
        self.term = Terminal()
        self.editor = Editor(self.term)

    def redraw(self) -> None:
        width = self.term.width()
        self.erase()
        rows = []
        if self.status_text:
            rows.extend(style("muted", "  " + row) for row in wrap_line(self.status_text, width - 3))
        if self.preview:
            preview = [row for line in scrub(self.preview).split("\n") for row in wrap_line(line, width - 3)]
            rows.extend("  " + row for row in preview[-5:])
        draft_rows, caret, _ = self.editor.render_rows(max(4, width - 3))
        hints = wrap_line(HINT, width - 3) if width >= 32 else ["/help 查看键位"]
        budget = max(1, self.term.height() - len(rows) - len(hints) - 1)
        first = max(0, caret - budget + 1)
        rows.extend(("> " if first + i == 0 else "  ") + row
                    for i, row in enumerate(draft_rows[first:first + budget]))
        rows.extend(style("muted", "  " + row) for row in hints)
        self.term.draw_prompt(rows)
        self.drawn = len(rows)
        self.dirty = False

    def erase(self) -> None:
        self.term.erase_prompt(self.drawn)
        self.drawn = 0

    def transcript(self, lines: list[str]) -> None:
        self.erase()
        self.term.print_lines(lines)
        self.dirty = True

    def note(self, text: str, tone: str = "muted") -> None:
        self.transcript([style(tone, scrub(text))])

    def set_status(self, text: str) -> None:
        if self.status_text != text:
            self.status_text = text
            self.dirty = True

    def render_turn(self, turn: dict) -> None:
        user = turn["role"] == "user"
        label = "你" if user else self.name
        if turn.get("proactive"):
            label += " · 主动邀请"
        # Terminal scrollback is outside our control. Source material lives in
        # the temporary history view, where it can be reprojected on refresh.
        text = turn.get("content") or ""
        if turn.get("source_bound") and turn.get("source_available"):
            text = "此条包含限时来源，输入 /history 查看。"
        prefix = style("user" if user else "alice", label)
        self.transcript([f"{prefix}  {scrub(text)}"])

    def sync_history(self) -> None:
        try:
            page = self.client.history(after_id=self.cursor, limit=30, session_epoch=self.epoch)
        except TuiApiError as exc:
            if exc.code == "session_changed":
                if self.pending is not None:
                    self._restore_draft(self.pending)
                self.epoch = self.cursor = self.pending = self.retry_request = None
                self.preview = ""
                self.erase()
                self.term.write("\x1b[3J\x1b[2J\x1b[H")
                self.note("会话已更换；草稿保留，确认后可重新发送。")
                self.set_status("")
            elif exc.code == "unreachable" and not self.offline:
                self.offline = True
                self.note("与 Alice 的连接中断，正在等待恢复。", "danger")
            return
        if self.offline:
            self.offline = False
            self.note("已重新连接 Alice。")
        self.epoch = page["session_epoch"]
        for turn in page["turns"]:
            if self.cursor is None or turn["id"] > self.cursor:
                self.render_turn(turn)
            if self.pending and turn["role"] == "assistant" and turn["request_id"] == self.pending["request_id"]:
                self.pending = None
                self.set_status("")
        self.cursor = page["next_after_id"]
        if page["has_more"] and page["next_before_id"] is None:
            self.next_sync = 0.0

    def begin_send(self, draft: str) -> None:
        if self.stream or self.pending:
            self.set_status("正在核对上一条消息，草稿已保留。")
            return
        if not self.epoch or self.offline:
            self.set_status("等待连接恢复，草稿已保留。")
            return
        request = self.retry_request
        if not request or request["text"] != draft or request["session_epoch"] != self.epoch:
            request = {"text": draft, "request_id": uuid.uuid4().hex, "session_epoch": self.epoch}
        self.last_request = self.pending = request
        self.retry_request = None
        self.editor.clear()
        self.tool_events.clear()
        self.preview = ""
        stop = self.stream = StopWaiting()
        self.set_status("正在提交")

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
        self.preview = ""
        self.next_sync = 0.0
        if kind == "done":
            self.pending = None
            self.set_status("")
        elif value.status in {400, 401, 403, 413} or value.code in {"busy", "draining", "session_changed"}:
            if self.pending is not None:
                self._restore_draft(self.pending)
            self.pending = None
            self.set_status("")
            self.note(str(value), "danger")
        else:
            self.set_status("结果待核对，正在查看原请求。")
            self.note(str(value))

    def update(self) -> None:
        while not self.events.empty():
            kind, value = self.events.get_nowait()
            if kind != "event":
                self._finish_stream(kind, value)
                continue
            event_type = value.get("type")
            if event_type == "start":
                self.set_status("Alice 正在回复…")
            elif event_type == "search":
                if value.get("status") == "running":
                    self.set_status("正在读取资料…" if value.get("phase") == "read" else "正在查找资料…")
                else:
                    self.set_status("Alice 正在回复…")
            elif event_type == "delta":
                self.preview = (self.preview + (value.get("text") or ""))[-8192:]
                self.dirty = True
            elif event_type == "tool":
                self.tool_events.append(scrub(tool_label(value.get("name", ""))))
                self.set_status("工具处理中…  Ctrl+O 查看进度")
            elif event_type == "reply_saved":
                self.set_status("回复已保存，正在同步…")
        now = time.monotonic()
        if self.stream is None and now >= self.next_sync:
            self.next_sync = now + SYNC_INTERVAL
            self.sync_history()
            if self.pending is not None:
                self.check_pending()

    def check_pending(self) -> None:
        try:
            result = self.client.request_status(self.pending)
        except TuiApiError:
            return
        state = result["status"]
        if state in {"accepted", "running"}:
            self.set_status("宿主仍在处理，结果会同步到这里。")
        elif state == "not_found":
            self.retry_request = self.pending
            self._restore_draft(self.pending)
            self.pending = None
            self.set_status("尚未找到接单记录。按 Enter 重试原请求。")
        elif state in {"completed", "completed_with_warning", "failed", "interrupted"}:
            self.pending = None
            self.next_sync = 0.0
            self.set_status("")
            if not result.get("assistant_turn_id"):
                self.note("原请求已结束，未保存回复。", "danger")

    def cmd_status(self) -> None:
        try:
            info = self.client.status()
        except TuiApiError as exc:
            self.note(f"暂不可确认：{exc}", "danger")
            return
        wake = info.get("wake") or {}
        background = {"awake": "清醒", "sleeping": "睡眠"}.get(wake.get("effective"), "暂不可确认")
        lines = [
            f"本机 socket · 会话 {info['session_epoch'][:8]}",
            f"前台 {'忙碌' if info.get('busy') else '空闲'} · 后台 {background}",
        ]
        if self.last_request:
            try:
                result = self.client.request_status(self.last_request)
                states = {"not_found": "未找到接单记录", "accepted": "已接单", "running": "处理中",
                          "failed": "失败", "interrupted": "已中断", "completed": "已完成",
                          "completed_with_warning": "已完成，有收尾提示"}
                state = states.get(result["status"], "暂不可确认")
                lines.append(f"上一次请求 {state}")
            except TuiApiError as exc:
                lines.append(f"上一次请求暂不可确认（{exc}）")
        self.note("\n".join(lines))

    def _history_lines(self, turns: list[dict], keyword: str, width: int) -> list[str]:
        lines = []
        now = time.time()
        for turn in turns:
            body = scrub(turn.get("content") or "")
            if turn.get("source_available") and turn.get("source_bound") and (turn.get("source_expires_at") or 0) <= now:
                body = "此条资料已到期。"
            if keyword not in body:
                continue
            label = "你" if turn["role"] == "user" else self.name
            if turn.get("proactive"):
                label += " · 主动邀请"
            lines.extend(wrap_line(f"#{turn['id']} {label}", width))
            for line in body.split("\n"):
                lines.extend(wrap_line(line, width))
            lines.append("")
        return lines or ["本页未找到该关键词。" if keyword else "暂无记录。"]

    def cmd_history(self, keyword: str) -> None:
        cursor = None
        offset = 0
        frame = None
        self.erase()
        self.term.write("\x1b[?1049h")
        try:
            while True:
                try:
                    page = self.client.history(before_id=cursor, limit=15, session_epoch=self.epoch)
                except TuiApiError as exc:
                    page = {}
                    heading = "历史暂不可读"
                    error = scrub(str(exc))
                else:
                    turns = page.get("turns", [])
                    scope = f"#{turns[0]['id']}–#{turns[-1]['id']}" if turns else "暂无记录"
                    heading = f"历史 {scope} · 本页 {len(turns)} 条"
                refresh_at = time.monotonic() + SYNC_INTERVAL
                layout = None
                deadlines = [turn.get("source_expires_at") or 0 for turn in page.get("turns", [])
                             if turn.get("source_bound") and turn.get("source_available")]
                while time.monotonic() < refresh_at:
                    width, height = max(4, self.term.width() - 1), max(1, self.term.height() - 3)
                    now = time.time()
                    current = (width, sum(deadline <= now for deadline in deadlines))
                    if current != layout:
                        layout = current
                        lines = self._history_lines(page["turns"], keyword, width) if page else wrap_line(error, width)
                    last = max(0, len(lines) - height)
                    offset = min(last, max(0, offset))
                    hint = "↑↓/PgUp/PgDn 滚动 · " + ("Enter 更早 · " if page.get("has_more") else "") + "r 刷新 · q 返回"
                    drawn = [self.term.clip(f"{heading} · {offset + 1}/{len(lines)}"),
                             *lines[offset:offset + height], self.term.clip(hint)]
                    if drawn != frame:
                        self.term.write("\x1b[2J\x1b[H")
                        self.term.print_lines(drawn)
                        frame = drawn
                    key = self.editor.poll_key(timeout=0.25)
                    if key in ("q", "esc", "exit", "interrupt"):
                        return
                    if key == "r":
                        break
                    if key == "enter" and page.get("has_more"):
                        cursor = page["next_before_id"]
                        offset = 0
                        break
                    if key == "home":
                        offset = 0
                    elif key == "end":
                        offset = last
                    else:
                        offset += {"up": -1, "down": 1, "page_up": -height, "page_down": height}.get(key, 0)
        finally:
            self.term.write("\x1b[2J\x1b[H\x1b[?1049l")
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
            self.note("/history [关键词]  查看历史，方向键或 PgUp/PgDn 滚动\n/status  会话与原请求状态\n"
                      "Ctrl+C  停止等待或清空草稿\nCtrl+O  工具进度\nCtrl+D  关闭终端客户端")
        else:
            return False
        return True

    def run(self) -> None:
        with self.term:
            self.note(self.name)
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
                        else:
                            return
                    elif key == "expand":
                        self.note("\n".join(self.tool_events) if self.tool_events else "本轮还没有工具进度。")
                    elif key == "enter":
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
                    elif key.startswith("paste:"):
                        self.editor.insert(key[6:])
                    elif key == "newline":
                        self.editor.insert("\n")
                    elif key == "backspace":
                        self.editor.backspace()
                    elif key in ("up", "down", "left", "right", "home", "end"):
                        self.editor.move(key)
                    elif key == "esc":
                        self.editor.clear()
                    elif len(key) == 1 and ord(key) >= 32:
                        self.editor.insert(key)
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
    try:
        info = client.status()
    except TuiApiError as exc:
        raise SystemExit(f"{exc}。请启动选定档案的 Alice 服务，再打开终端。") from None
    if not info.get("ready"):
        print("请先在网页或桌面完成模型设置和人格安装。")
    try:
        App(client, info).run()
    except (EOFError, KeyboardInterrupt):
        print()


if __name__ == "__main__":
    main()
