"""Build/run the source desktop prototype; never imports Alice internals.

Run with --build to compile only. The default starts a real desktop instance
with its own OS user directory; it does not open the checkout's user directory.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", action="store_true", help="只编译，不启动窗口或后端")
    parser.add_argument("--signer", action="store_true", help="只编译发布签名工具，不启动 Alice")
    args = parser.parse_args()
    desktop = Path(__file__).resolve().parent
    env = os.environ.copy()
    cargo = shutil.which("cargo")
    native = desktop / ".toolchain" / "native" / "bin" / ("cargo.exe" if os.name == "nt" else "cargo")
    if cargo is None and native.is_file():
        cargo = str(native)
        env["CARGO_HOME"] = str(desktop / ".toolchain" / "cargo")
        env["PATH"] = str(native.parent) + os.pathsep + env.get("PATH", "")
    if cargo is None:
        local = Path("/private/tmp/alice-desktop-toolchain") if os.name != "nt" else Path(tempfile.gettempdir()) / "alice-desktop-toolchain"
        binary = local / "cargo" / "bin" / ("cargo.exe" if os.name == "nt" else "cargo")
        if not binary.is_file():
            parser.error("未找到 Rust/cargo，请先按 README 准备构建工具")
        cargo = str(binary)
        env["CARGO_HOME"] = str(local / "cargo")
        env["RUSTUP_HOME"] = str(local / "rustup")
        env["PATH"] = str(binary.parent) + os.pathsep + env.get("PATH", "")
    extra = []
    if args.signer:
        extra = ["--release", "--features", "release-tools", "--bin", "alice-update-signer"]
        env.setdefault("CARGO_PROFILE_RELEASE_STRIP", "none")
    return subprocess.call([cargo, "build" if args.build or args.signer else "run", "--locked", *extra,
                            "--manifest-path", str(desktop / "src-tauri/Cargo.toml")], env=env, cwd=desktop)


if __name__ == "__main__":
    raise SystemExit(main())
