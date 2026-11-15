"""Render the prototype's code-defined orb as an RGBA PNG, using stdlib only."""
from __future__ import annotations

import math
import argparse
from pathlib import Path
import struct
import zlib


def chunk(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload))


def render(path: Path, size: int) -> None:
    rows = bytearray()
    for y in range(size):
        rows.append(0)
        for x in range(size):
            px, py = (x + 0.5) * 128 / size - 0.5, (y + 0.5) * 128 / size - 0.5
            radius = math.hypot(px - 63.5, py - 63.5) / 56
            light = max(0, 1 - math.hypot(px - 43, py - 37) / 100)
            alpha = max(0, min(1, (1 - radius) * 56 * size / 128))
            rows.extend((int(35 + light * 176), int(51 + light * 182),
                         int(72 + light * 183), int(alpha * 255)))
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(bytes(rows))) + chunk(b"IEND", b"")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(png)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iconset", type=Path)
    args = parser.parse_args()
    if args.iconset:
        for size in (16, 32, 128, 256, 512):
            render(args.iconset / f"icon_{size}x{size}.png", size)
            render(args.iconset / f"icon_{size}x{size}@2x.png", size * 2)
    else:
        render(Path(__file__).resolve().parents[1] / "src-tauri/icons/icon.png", 128)


if __name__ == "__main__":
    main()
