"""TUI 终端层：raw 输入、多行草稿编辑器、inline 渲染与 ANSI 清洗。

inline 模式：对话正文进终端原生回滚，本模块只拥有底部的状态行+草稿区。
回复流式输出与用户编辑并发进行，共用一把打印锁：先擦除提示区、写正文、
再重画提示区。模型文本一律清洗 ANSI/OSC 控制序列，只保留普通换行
（设计 §3 终端内容）；样式由客户端生成。
"""
from __future__ import annotations

import codecs
import os
import re
import select
import shutil
import sys
import termios
import unicodedata

_CSI = re.compile(r"(?:\x1b\[|\x9b)[0-?]*[ -/]*[@-~]")
_STRING_CONTROL = re.compile(r"(?:\x1b[\]PX^_]|[\x90\x98\x9d-\x9f]).*?(?:\x07|\x1b\\|\x9c|\Z)", re.S)
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")

STYLE = {
    "user": "\x1b[1m", "alice": "\x1b[36m", "muted": "\x1b[2m",
    "danger": "\x1b[31m", "ok": "\x1b[32m", "accent": "\x1b[94m", "reset": "\x1b[0m",
}
TOOL_LABELS = {
    "memory_search": "正在查找相关记忆", "memory_load": "正在读取记忆正文",
    "recall": "正在检索记忆", "remember": "正在保存记忆",
    "persona_load": "正在展开人格场景", "discovery_search": "正在查找资料",
    "history_search": "正在翻阅旧对话", "context_keep": "正在保留资料",
    "context_release": "正在移出本轮资料",
}


def scrub(text: str) -> str:
    """模型/服务端文本清洗：去 ANSI/OSC 控制序列，统一换行。"""
    text = _STRING_CONTROL.sub("", text.replace("\r\n", "\n").replace("\r", "\n"))
    return _CONTROL.sub("", _CSI.sub("", text))


def display_width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)


def wrap_line(text: str, width: int) -> list[str]:
    """按显示宽度硬换行（CJK 占 2 列）。"""
    width = max(4, width)
    rows, row, row_w = [], "", 0
    for ch in text:
        w = 2 if unicodedata.east_asian_width(ch) in "WF" else 1
        if row_w + w > width:
            rows.append(row)
            row, row_w = ch, w
        else:
            row += ch
            row_w += w
    rows.append(row)
    return rows


def style(name: str, text: str) -> str:
    return STYLE.get(name, "") + text + STYLE["reset"]


def tool_label(name: str) -> str:
    return TOOL_LABELS.get(name, f"正在执行 {name}")


class Terminal:
    """Terminal mode and output. Only the editor thread renders."""

    def __enter__(self):
        self._saved_termios = termios.tcgetattr(sys.stdin)
        attrs = termios.tcgetattr(sys.stdin)
        attrs[0] &= ~(termios.ICRNL | termios.INLCR | termios.IGNCR)
        attrs[3] &= ~(termios.ICANON | termios.ECHO | termios.ISIG)
        attrs[6][termios.VMIN], attrs[6][termios.VTIME] = 1, 0
        termios.tcsetattr(sys.stdin, termios.TCSANOW, attrs)
        try:
            self.write("\x1b[?2004h")
        except BaseException:
            termios.tcsetattr(sys.stdin, termios.TCSANOW, self._saved_termios)
            raise
        return self

    def __exit__(self, *exc):
        try:
            self.write("\x1b[?2004l")
        finally:
            termios.tcsetattr(sys.stdin, termios.TCSANOW, self._saved_termios)

    def width(self) -> int:
        return shutil.get_terminal_size((80, 24)).columns

    def height(self) -> int:
        return shutil.get_terminal_size((80, 24)).lines

    def clip(self, text: str) -> str:
        return wrap_line(text, max(4, self.width() - 1))[0]

    def write(self, text: str) -> None:
        sys.stdout.write(text)
        sys.stdout.flush()

    def draw_prompt(self, rows: list[str]) -> None:
        """提示区逐行落屏（调用方持锁；行数=回擦计数）。"""
        for row in rows:
            self.write("\r\x1b[K" + row + "\n")

    def erase_prompt(self, count: int) -> None:
        """把提示区（count 行）从屏幕擦掉，光标回到提示区首行行首。"""
        if count:
            self.write(("\x1b[1A\r\x1b[2K") * count)

    def print_lines(self, lines: list[str]) -> None:
        self.write("\n".join(lines) + "\n")


class Editor:
    """多行草稿编辑器。poll_key() 返回语义键；草稿操作可直接调用。

    Enter 提交；Ctrl+J（0x0a）换行；方向键/Home/End 移动；退格删除；
    粘贴整体进草稿；Esc 清空非空草稿；Ctrl+D/Ctrl+C 由上层语义处理。
    """

    def __init__(self, term: Terminal):
        self.term = term
        self.lines: list[str] = [""]
        self.row = 0
        self.col = 0
        self.exit_requested = False
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._pending: list[str] = []   # 已解码待消费的字符队列
        self._paste: list[str] | None = None

    # ---------- 草稿操作 ----------
    def clear(self) -> None:
        self.lines, self.row, self.col = [""], 0, 0

    def text(self) -> str:
        return "\n".join(self.lines)

    def set_text(self, text: str) -> None:
        self.clear()
        self.insert(text)

    def insert(self, text: str) -> None:
        for ch in scrub(text):
            if ch == "\n":
                rest = self.lines[self.row][self.col:]
                self.lines[self.row] = self.lines[self.row][:self.col]
                self.lines.insert(self.row + 1, rest)
                self.row += 1
                self.col = 0
            elif ord(ch) >= 32:
                line = self.lines[self.row]
                self.lines[self.row] = line[:self.col] + ch + line[self.col:]
                self.col += 1

    def backspace(self) -> None:
        if self.col > 0:
            line = self.lines[self.row]
            self.lines[self.row] = line[:self.col - 1] + line[self.col:]
            self.col -= 1
        elif self.row > 0:
            self.col = len(self.lines[self.row - 1])
            self.lines[self.row - 1] += self.lines.pop(self.row)
            self.row -= 1

    def move(self, key: str) -> None:
        if key == "left":
            if self.col > 0:
                self.col -= 1
            elif self.row > 0:
                self.row -= 1
                self.col = len(self.lines[self.row])
        elif key == "right":
            if self.col < len(self.lines[self.row]):
                self.col += 1
            elif self.row < len(self.lines) - 1:
                self.row += 1
                self.col = 0
        elif key == "up":
            if self.row > 0:
                self.row -= 1
                self.col = min(self.col, len(self.lines[self.row]))
        elif key == "down":
            if self.row < len(self.lines) - 1:
                self.row += 1
                self.col = min(self.col, len(self.lines[self.row]))
        elif key == "home":
            self.col = 0
        elif key == "end":
            self.col = len(self.lines[self.row])

    # ---------- 键读取 ----------
    def _next_char(self, timeout: float | None = None) -> str | None:
        """取下一个已解码字符；无输入且给了 timeout 时等待后返回 None。"""
        if self._pending:
            return self._pending.pop(0)
        ready = select.select([sys.stdin], [], [], 0 if timeout is None else timeout)[0]
        if not ready:
            return None
        chunk = os.read(sys.stdin.fileno(), 4096)
        if not chunk:
            raise EOFError
        text = self._decoder.decode(chunk)
        self._pending.extend(text)
        return self._pending.pop(0) if self._pending else None

    def _escape_sequence(self) -> str:
        """ESC 后读完整 CSI 序列，返回如 '[A' / '[200~'；读不到返回 ''。"""
        seq = ""
        while True:
            ch = self._next_char(timeout=0.05)
            if ch is None:
                return seq
            seq += ch
            if seq == "[":
                continue
            if ch.isdigit() or ch in ";?<":
                continue
            return seq  # 终止字母（ABCDHF 等）或 '~'

    def poll_key(self, timeout: float | None = None) -> str | None:
        """读一个语义键。返回：
        字符本身 / 'enter' / 'newline' / 'backspace' / 'up' 'down' 'left' 'right'
        'home' 'end' / 'tab' / 'esc' / 'exit'(Ctrl+D) / 'expand'(Ctrl+O)
        / 'interrupt'(Ctrl+C) / 'paste'(整段粘贴文本) / None(超时无输入)。"""
        ch = self._next_char(timeout)
        if ch is None:
            return None
        if self._paste is not None:
            return self._read_paste(ch)
        if ch == "\x1b":
            seq = self._escape_sequence()
            if seq == "[A": return "up"
            if seq == "[B": return "down"
            if seq == "[C": return "right"
            if seq == "[D": return "left"
            if seq == "[H": return "home"
            if seq == "[F": return "end"
            if seq == "[3~": return "backspace"
            if seq == "[200~":
                self._paste = []
                return self._read_paste(self._next_char(timeout=0.05))
            return "esc" if not seq else "esc"
        if ch == "\r": return "enter"
        if ch == "\n": return "newline"
        if ch in ("\x7f", "\b"): return "backspace"
        if ch == "\t": return "tab"
        if ch == "\x04": return "exit"
        if ch == "\x0f": return "expand"
        if ch == "\x0c": return "redraw"
        if ch == "\x03": return "interrupt"
        if ch in ("\x01", "\x05"): return "home" if ch == "\x01" else "end"
        if ord(ch) >= 32:
            return ch
        return None  # 其余控制键忽略

    # ---------- 渲染 ----------
    def render_rows(self, width: int) -> tuple[list[str], int, int]:
        """Wrap the draft and mark its logical caret, including after a wide character."""
        rows: list[str] = []
        caret_row = caret_col = 0
        for index, line in enumerate(self.lines):
            start = 0
            wrapped = wrap_line(line, width)
            if index == self.row and self.col == len(line) and display_width(wrapped[-1]) >= width:
                wrapped.append("")
            for offset, piece in enumerate(wrapped):
                end = start + len(piece)
                if index == self.row and (start <= self.col < end
                        or (self.col == end and offset == len(wrapped) - 1)):
                    cut = self.col - start
                    caret_row, caret_col = len(rows), display_width(piece[:cut])
                    marked = piece[cut:cut + 1] or " "
                    piece = piece[:cut] + "\x1b[7m" + marked + "\x1b[0m" + piece[cut + 1:]
                rows.append(piece)
                start = end
        return rows, caret_row, caret_col

    def _read_paste(self, ch: str | None) -> str | None:
        # The end marker can span several reads. Incomplete paste stays pending
        # and cannot turn a later newline into the send key.
        end = "\x1b[201~"
        while ch is not None:
            self._paste.append(ch)
            if ch == "~" and self._paste[-len(end):] == list(end):
                value = scrub("".join(self._paste[:-len(end)]))
                self._paste = None
                return "paste:" + value
            ch = self._next_char(timeout=0.05)
        return None
