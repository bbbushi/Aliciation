"""TUI 启动页与品牌区的像素素材：向导头像低分辨率采样 + ALICE 点阵字标。

PALETTE/PIXELS 由 tools/tui_avatar_sample.py 一次性生成（源图
web/assets/alice-wizard-avatar-v1.png 的 24×24 逐格主色采样，'.' 为透明，
十六进制位是调色板下标）。渲染用半块字符（▀/▄）按真彩色着色，透明格留出
终端底色；无色终端由调用方跳过头像。重新采样只改常量块。
"""
from __future__ import annotations

PALETTE = [
    (16, 18, 50),
    (62, 60, 111),
    (127, 134, 201),
    (180, 190, 239),
    (251, 220, 199),
    (252, 251, 251),
    (220, 123, 24),
    (253, 211, 97),
    (226, 165, 146),
]
PIXELS = [
    ".........001100.........",
    ".......0111111110.......",
    ".....0011111110110......",
    "....021111111111110.....",
    "....2221111111111110....",
    "...02112221111101111....",
    "...111111212122011110...",
    "..0111111111112111111...",
    "..11111111110111011310..",
    "..01101111114111413310..",
    ".0011011104001114411010.",
    ".0011011100410114411110.",
    ".0101011005001100001100.",
    ".01014010066414055011010",
    ".11104011475444455010000",
    "011100101444444440011000",
    "00110001114444444010110.",
    ".0000001144444440001000.",
    "..00100018844440001100..",
    "....00001188040000100...",
    "......2222233320000.....",
    "....000222233330.0......",
    "...022311223333000......",
    "...2232313333130032.....",
]

_DIGITS = "0123456789abcde"
_SIZE = len(PIXELS)

# 点阵字标：5×7 整版（启动页）与 5×5 紧凑版（可作头部）；逐字蓝→紫渐进。
GLYPHS = {
    "A": (".###.", "#...#", "#...#", "#####", "#...#", "#...#", "#...#"),
    "L": ("#....", "#....", "#....", "#....", "#....", "#....", "#####"),
    "I": ("#####", "..#..", "..#..", "..#..", "..#..", "..#..", "#####"),
    "C": (".####", "#....", "#....", "#....", "#....", "#....", ".####"),
    "E": ("#####", "#....", "#....", "####.", "#....", "#....", "#####"),
}
COMPACT = {
    "A": (".###.", "#...#", "#####", "#...#", "#...#"),
    "L": ("#....", "#....", "#....", "#....", "#####"),
    "I": ("#####", "..#..", "..#..", "..#..", "#####"),
    "C": (".####", "#....", "#....", "#....", ".####"),
    "E": ("#####", "#....", "####.", "#....", "#####"),
}
LETTER_COLORS = (62, 61, 99, 105, 140)


def _fg(index: str, text: str) -> str:
    r, g, b = PALETTE[_DIGITS.index(index)]
    return f"\x1b[38;2;{r};{g};{b}m{text}\x1b[0m"


def _fg_bg(top: str, bottom: str, text: str) -> str:
    r, g, b = PALETTE[_DIGITS.index(top)]
    br, bg, bb = PALETTE[_DIGITS.index(bottom)]
    return f"\x1b[38;2;{r};{g};{b};48;2;{br};{bg};{bb}m{text}\x1b[0m"


def render_avatar(cells: int) -> list[str]:
    """把 24×24 采样矩阵渲染成 cells 列宽（高约 cells/2 行）的半块字符画。"""
    if cells <= 0:
        return []
    text_rows = max(1, (cells + 1) // 2)
    columns = [min(_SIZE - 1, _SIZE * cx // cells) for cx in range(cells)]
    rows = []
    for tr in range(text_rows):
        top = PIXELS[min(_SIZE - 1, _SIZE * (2 * tr) // (2 * text_rows))]
        bottom = PIXELS[min(_SIZE - 1, _SIZE * (2 * tr + 1) // (2 * text_rows))]
        line = []
        for sx in columns:
            t, b = top[sx], bottom[sx]
            if t == "." and b == ".":
                line.append(" ")
            elif b == ".":
                line.append(_fg(t, "▀"))
            elif t == ".":
                line.append(_fg(b, "▄"))
            else:
                line.append(_fg_bg(t, b, "▀"))
        rows.append("".join(line).rstrip())
    return rows


def wordmark_width(scale: int) -> int:
    return 29 * scale


def render_wordmark(scale: int = 1, compact: bool = False, color: bool = True) -> list[str]:
    """点阵字标：scale 为每点半宽；compact 用 5 行字形（窄高预算）。"""
    glyphs = COMPACT if compact else GLYPHS
    height = len(next(iter(glyphs.values())))
    dot, gap = "█" * scale, " " * scale
    rows = []
    for r in range(height):
        parts = []
        for li, letter in enumerate("ALICE"):
            cell = "".join(dot if ch == "#" else gap for ch in glyphs[letter][r])
            if color and cell.strip():
                parts.append(f"\x1b[38;5;{LETTER_COLORS[li]}m{cell}\x1b[0m")
            else:
                parts.append(cell)
        rows.append(gap.join(parts).rstrip())
    return rows
