"""把向导像素头像采样成 TUI 启动页用的终端像素矩阵（一次性生成工具）。

从 web/assets/alice-wizard-avatar-v1.png（8-bit RGBA PNG）解码、按格取主色、
量化为 ≤15 色调色板，打印可直接粘进 addons/alice_tui/art.py 的
PALETTE/PIXELS 常量与亮度预览。重新采样：

    .venv/bin/python tools/tui_avatar_sample.py [格数=24]

运行时不依赖本工具：客户端只加载 art.py 里的常量。
"""
from __future__ import annotations

import struct
import sys
import zlib
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "web/assets/alice-wizard-avatar-v1.png"


def decode_png(data: bytes) -> tuple[int, int, int, bytearray]:
    """最小 PNG 解码：8-bit RGB/RGBA、非隔行，返回 (宽, 高, 每像素字节数, 像素)。"""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("不是 PNG")
    pos, idat = 8, bytearray()
    while pos < len(data):
        size, kind = struct.unpack(">I4s", data[pos:pos + 8])
        chunk = data[pos + 8:pos + 8 + size]
        pos += size + 12
        if kind == b"IHDR":
            width, height, depth, ctype, _c, _f, interlace = struct.unpack(">IIBBBBB", chunk)
            if (depth, ctype, interlace) != (8, 6, 0) and (depth, ctype, interlace) != (8, 2, 0):
                raise ValueError(f"不支持的 PNG 形态 depth={depth} ctype={ctype} interlace={interlace}")
        elif kind == b"IDAT":
            idat += chunk
        elif kind == b"IEND":
            break
    raw = zlib.decompress(bytes(idat))
    bpp = 4 if ctype == 6 else 3
    stride = width * bpp
    pixels = bytearray(height * stride)
    previous = bytearray(stride)
    src = 0
    for row in range(height):
        offset = raw[src]
        src += 1
        line = bytearray(raw[src:src + stride])
        src += stride
        if offset == 1:  # Sub
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 255
        elif offset == 2:  # Up
            for i in range(stride):
                line[i] = (line[i] + previous[i]) & 255
        elif offset == 3:  # Average
            for i in range(stride):
                left = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((left + previous[i]) >> 1)) & 255
        elif offset == 4:  # Paeth
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                b = previous[i]
                c = previous[i - bpp] if i >= bpp else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                predictor = a if pa <= pb and pa <= pc else (b if pb <= pc else c)
                line[i] = (line[i] + predictor) & 255
        pixels[row * stride:(row + 1) * stride] = line
        previous = line
    return width, height, bpp, pixels


def sample(cells: int) -> tuple[list[tuple[int, int, int]], list[str]]:
    width, height, bpp, pixels = decode_png(SRC.read_bytes())

    def pixel(x: int, y: int) -> tuple[int, int, int, int]:
        base = (y * width + x) * bpp
        return pixels[base], pixels[base + 1], pixels[base + 2], 255 if bpp == 3 else pixels[base + 3]

    # 每格取主色：像素画源图按平均色会糊掉，先按 32 级分桶统计众数再取桶均值。
    grid: list[list[tuple[int, int, int] | None]] = []
    for cy in range(cells):
        sy0, sy1 = height * cy // cells, max(height * (cy + 1) // cells, height * cy // cells + 1)
        row: list[tuple[int, int, int] | None] = []
        for cx in range(cells):
            sx0, sx1 = width * cx // cells, max(width * (cx + 1) // cells, width * cx // cells + 1)
            buckets: dict[tuple[int, int, int], list[int]] = {}
            opaque = 0
            for sy in range(sy0, sy1):
                for sx in range(sx0, sx1):
                    r, g, b, a = pixel(sx, sy)
                    if a < 128:
                        continue
                    opaque += 1
                    key = (r >> 3, g >> 3, b >> 3)
                    acc = buckets.setdefault(key, [0, 0, 0, 0])
                    acc[0] += 1
                    acc[1] += r
                    acc[2] += g
                    acc[3] += b
            total = (sx1 - sx0) * (sy1 - sy0)
            if opaque < total * 0.15:
                row.append(None)
                continue
            key, acc = max(buckets.items(), key=lambda kv: kv[1][0])
            row.append((acc[1] // acc[0], acc[2] // acc[0], acc[3] // acc[0]))
        grid.append(row)

    # 量化：按桶计数贪心选 ≤15 个中心，格色归到最近中心。
    counts: dict[tuple[int, int, int], int] = {}
    for row in grid:
        for color in row:
            if color is not None:
                counts[color] = counts.get(color, 0) + 1
    centers: list[tuple[int, int, int]] = []
    for color, _ in sorted(counts.items(), key=lambda kv: -kv[1]):
        if len(centers) >= 15:
            break
        if all(sum((a - b) ** 2 for a, b in zip(color, center)) > 48 ** 2 for center in centers):
            centers.append(color)
    for _ in range(2):  # 两轮 k-means 平滑
        groups: dict[int, list[tuple[int, int, int]]] = {}
        for color, n in counts.items():
            best = min(range(len(centers)), key=lambda i: sum((a - b) ** 2 for a, b in zip(color, centers[i])))
            groups.setdefault(best, []).extend([color] * n)
        centers = [tuple(sum(c[k] for c in members) // len(members) for k in range(3))
                   for members in groups.values()] if groups else centers
        centers = centers[:15]

    digits = "0123456789abcde"

    def nearest(color: tuple[int, int, int]) -> str:
        return digits[min(range(len(centers)),
                          key=lambda i: sum((a - b) ** 2 for a, b in zip(color, centers[i])))]

    rows = ["".join("." if color is None else nearest(color) for color in row) for row in grid]
    return centers, rows


def main() -> None:
    cells = int(sys.argv[1]) if len(sys.argv) > 1 else 24
    centers, rows = sample(cells)
    digits = "0123456789abcde"
    print(f"# 格数 {cells}，调色板 {len(centers)} 色")
    print("PALETTE = [")
    for r, g, b in centers:
        print(f"    ({r}, {g}, {b}),")
    print("]")
    print("PIXELS = [")
    for row in rows:
        print(f'    "{row}",')
    print("]")
    print("\n# 亮度预览（主 PIL 不可用时的目检替代）")
    ramps = " .:-=+*#%@"
    for row in rows:
        line = ""
        for ch in row:
            if ch == ".":
                line += "  "
                continue
            r, g, b = centers[digits.index(ch)]
            line += ramps[min(9, (r * 3 + g * 6 + b) // 256)] * 2
        print(line)


if __name__ == "__main__":
    main()
