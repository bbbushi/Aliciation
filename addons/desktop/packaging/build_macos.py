"""Create a self-contained, ad-hoc signed macOS arm64 development app and DMG.

Run with this directory's isolated .venv after installing its hashed lockfile.
Builds and inspects artifacts only; never starts Alice or opens a user profile.
"""
from __future__ import annotations

import datetime as dt
import ast
import hashlib
import importlib.metadata as metadata
import json
import os
from pathlib import Path
import platform
import plistlib
import shutil
import subprocess
import sys
import sysconfig
import tomllib

PACKAGING = Path(__file__).resolve().parent
DESKTOP = PACKAGING.parent
ROOT = DESKTOP.parents[1]
BUILD = DESKTOP / "build"
LOCK = PACKAGING / "requirements-macos-arm64-py314.lock.txt"
MACHO = {bytes.fromhex(s) for s in ("cffaedfe", "cefaedfe", "feedfacf", "feedface", "cafebabe", "bebafeca")}


def run(*args, **kwargs):
    print("Building:", Path(str(args[0])).name, flush=True)
    return subprocess.run([str(a) for a in args], check=True, cwd=ROOT, **kwargs)


def capture(*args):
    return subprocess.check_output([str(a) for a in args], cwd=ROOT, text=True).strip()


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def resources(manifest):
    result = []
    seen = set()
    for item in manifest["include"]:
        source = ROOT / item["source"]
        if not source.exists():
            raise ValueError(f"Missing resource: {item['source']}")
        candidates = sorted(source.rglob("*")) if source.is_dir() else [source]
        for path in candidates:
            relative = path.relative_to(source) if source.is_dir() else Path()
            if any(p in manifest["excludeDirectories"] for p in relative.parts):
                continue
            if path.is_symlink():
                raise ValueError(f"Resource symlink not permitted: {path}")
            if not path.is_file() or (item.get("extensions") and path.suffix not in item["extensions"]):
                continue
            target = Path(item["target"]) / relative
            if target.is_absolute() or ".." in target.parts or target.as_posix() in seen:
                raise ValueError(f"Invalid or duplicate resource: {target}")
            seen.add(target.as_posix())
            result.append({"source": path.relative_to(ROOT).as_posix(),
                           "target": target.as_posix(), "sha256": sha(path)})
    return result


def licenses(destination):
    """Record both runtime and build dependencies; retain their supplied notices."""
    inventory = {"python": [], "cargo_lock_packages": []}
    for dist in sorted(metadata.distributions(), key=lambda d: d.metadata["Name"].lower()):
        name, version = dist.metadata["Name"], dist.version
        if name.lower() == "pip":
            continue
        inventory["python"].append({"name": name, "version": version,
                                    "license": dist.metadata.get("License-Expression") or dist.metadata.get("License", "")})
        for entry in dist.files or ():
            if ".dist-info" not in str(entry) or not any(
                    part.lower().startswith(("license", "copying", "notice")) for part in entry.parts):
                continue
            source = Path(dist.locate_file(entry))
            if source.is_file():
                target = destination / "python" / f"{name}-{version}" / Path(*entry.parts[1:])
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
    python_license = Path(sysconfig.get_path("stdlib")) / "LICENSE.txt"
    if not python_license.is_file():
        raise ValueError("CPython license not found")
    shutil.copy2(python_license, destination / "CPython-LICENSE.txt")
    vendor = DESKTOP / ".toolchain/vendor"
    lock = tomllib.loads((DESKTOP / "src-tauri/Cargo.lock").read_text())
    for package in lock["package"]:
        if "checksum" not in package:
            continue
        name, version = package["name"], package["version"]
        source = vendor / f"{name}-{version}"
        crate = tomllib.loads((source / "Cargo.toml").read_text())
        inventory["cargo_lock_packages"].append({"name": name, "version": version,
            "license": crate.get("package", {}).get("license", ""), "checksum": package["checksum"]})
        # The inventory covers all locked platforms, including optional crates.
        for entry in source.rglob("*"):
            if entry.is_file() and entry.name.lower().startswith(("license", "copying", "notice")):
                target = destination / "rust" / f"{name}-{version}" / entry.relative_to(source)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(entry, target)
    write_json(destination / "dependencies.json", inventory)


def inspect_app(app, info):
    """Static package integrity checks, without executing the packaged backend."""
    content = app / "Contents"
    internal = content / "Resources/backend/_internal"
    for item in info["resources"]:
        path = internal / item["target"]
        if not path.is_file() or sha(path) != item["sha256"]:
            raise ValueError(f"Frozen resource mismatch: {item['target']}")
    if json.loads((internal / "build-info.json").read_text())["build_id"] != info["build_id"]:
        raise ValueError("Host/backend build identity mismatch")
    binaries = []
    for path in sorted(content.rglob("*")):
        if path.is_symlink():
            if not path.resolve().is_relative_to(app.resolve()):
                raise ValueError(f"Escaping bundle symlink: {path}")
            continue
        if not path.is_file():
            continue
        with path.open("rb") as stream:
            magic = stream.read(4)
        if magic not in MACHO:
            continue
        architectures = capture("/usr/bin/lipo", "-archs", path)
        if "arm64" not in architectures.split():
            raise ValueError(f"Not an arm64 executable: {path}")
        linked = capture("/usr/bin/otool", "-L", path).splitlines()[1:]
        dependencies = [line.strip().split(" (", 1)[0] for line in linked]
        if any(d.startswith("/") and not d.startswith(("/usr/lib/", "/System/Library/")) for d in dependencies):
            raise ValueError(f"External non-system library: {path}: {dependencies}")
        commands = capture("/usr/bin/otool", "-l", path).splitlines()
        rpaths = []
        for index, line in enumerate(commands):
            if line.strip() == "cmd LC_RPATH":
                rpaths.append(commands[index + 2].strip().removeprefix("path ").split(" (offset", 1)[0])
        if any(p.startswith("/") and not p.startswith(("/usr/lib/", "/System/Library/")) for p in rpaths):
            raise ValueError(f"External non-system library search path: {path}: {rpaths}")
        binaries.append({"path": path.relative_to(app).as_posix(), "architectures": architectures,
                         "dependencies": dependencies, "rpaths": rpaths})
    for bad in ("user", "matplotlib", "numpy", "PIL"):
        if (internal / bad).exists():
            raise ValueError(f"Unexpected runtime resource: {bad}")
    if not any(p.name == "Python" or p.name.startswith("libpython") for p in internal.rglob("*")):
        raise ValueError("Python runtime missing")
    return binaries


def main():
    if sys.platform != "darwin" or platform.machine() != "arm64" or sys.version_info[:2] != (3, 14):
        raise SystemExit("This build requires macOS arm64 with the locked CPython 3.14 environment")
    expected_env = PACKAGING / ".venv"
    if Path(sys.prefix).resolve() != expected_env.resolve():
        raise SystemExit(f"Use {expected_env}/bin/python")
    # Refuse drift instead of silently resolving newer build/runtime versions.
    for line in LOCK.read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        name, version = line.split(" --hash", 1)[0].split("==")
        if metadata.version(name) != version:
            raise ValueError(f"Dependency drift: {name}")
    config = json.loads((DESKTOP / "src-tauri/tauri.conf.json").read_text())
    core_version = next(ast.literal_eval(node.value)
                        for node in ast.parse((ROOT / "alice/__init__.py").read_text()).body
                        if isinstance(node, ast.Assign) and any(
                            isinstance(target, ast.Name) and target.id == "__version__" for target in node.targets))
    if core_version != config["version"]:
        raise ValueError("Host and core versions must match")
    manifest = json.loads((PACKAGING / "resource-manifest.json").read_text())
    revision = capture("git", "rev-parse", "HEAD")
    dirty = bool(capture("git", "status", "--porcelain"))
    now = dt.datetime.now(dt.timezone.utc)
    build_id = now.strftime("%Y%m%dT%H%M%SZ") + "-" + revision[:8] + ("-dirty" if dirty else "")
    release = BUILD / "releases" / build_id
    release.mkdir(parents=True, exist_ok=False)
    minimum = max(11, int(str(sysconfig.get_config_var("MACOSX_DEPLOYMENT_TARGET")).split(".")[0]))
    info = {"schema": 1, "build_id": build_id, "version": config["version"],
            "application_id": config["identifier"], "target": "darwin-aarch64",
            "update_channel": "desktop-dev",
            "updater_pubkey_sha256": hashlib.sha256((PACKAGING / "updater.pub").read_text().strip().encode()).hexdigest(),
            "source_revision": revision, "source_dirty": dirty, "channel": "local-development",
            "platform": "macos-arm64", "minimum_macos": f"{minimum}.0", "python": platform.python_version(),
            "python_dependency_lock_sha256": sha(LOCK),
            "cargo_lock_sha256": sha(DESKTOP / "src-tauri/Cargo.lock"),
            "signing": "ad-hoc", "notarized": False, "human_acceptance": "pending",
            "maintenance_protocol": 1, "data_format": {"read": [1], "write": 1, "migration_from": []},
            "components": manifest["components"], "resources": resources(manifest)}
    staging = BUILD / "staging"
    write_json(staging / "build-info.json", info)
    env = os.environ.copy()
    env["PYINSTALLER_CONFIG_DIR"] = str(BUILD / "pyinstaller-cache")
    run(sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--distpath", BUILD / "pyinstaller-dist",
        "--workpath", BUILD / "pyinstaller-work", PACKAGING / "alice-backend.spec", env=env)
    native = DESKTOP / ".toolchain/native/bin"
    cargo = native / "cargo"
    if not cargo.is_file():
        raise ValueError("Local locked Rust toolchain missing")
    env.update(CARGO_HOME=str(DESKTOP / ".toolchain/cargo"),
               PATH=str(native) + os.pathsep + env.get("PATH", ""),
               MACOSX_DEPLOYMENT_TARGET=info["minimum_macos"], CARGO_PROFILE_RELEASE_STRIP="none")
    run(cargo, "build", "--release", "--locked", "--offline", "--features", "custom-protocol",
        "--manifest-path", DESKTOP / "src-tauri/Cargo.toml", env=env)
    app = release / "Alice.app"
    content = app / "Contents"
    (content / "MacOS").mkdir(parents=True)
    (content / "Resources").mkdir()
    shutil.copy2(DESKTOP / "src-tauri/target/release/alice-desktop", content / "MacOS/alice-desktop")
    shutil.copytree(BUILD / "pyinstaller-dist/alice-backend", content / "Resources/backend", symlinks=True)
    iconset = BUILD / "Alice.iconset"
    run(sys.executable, PACKAGING / "make_icon.py", "--iconset", iconset)
    run("/usr/bin/iconutil", "-c", "icns", iconset, "-o", content / "Resources/Alice.icns")
    plist = {"CFBundleIdentifier": config["identifier"], "CFBundleName": "Alice", "CFBundleDisplayName": "Alice",
             "CFBundleExecutable": "alice-desktop", "CFBundleIconFile": "Alice.icns", "CFBundlePackageType": "APPL",
             "CFBundleShortVersionString": config["version"], "CFBundleVersion": config["version"],
             "LSMinimumSystemVersion": info["minimum_macos"], "NSHighResolutionCapable": True,
             "NSPrincipalClass": "NSApplication", "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True}}
    with (content / "Info.plist").open("wb") as stream:
        plistlib.dump(plist, stream)
    (content / "PkgInfo").write_bytes(b"APPL????")
    write_json(content / "Resources/build-info.json", info)
    write_json(content / "Resources/release.json", info)
    licenses(content / "Resources/ThirdPartyNotices")
    binaries = inspect_app(app, info)
    run("/usr/bin/plutil", "-lint", content / "Info.plist")
    run("/usr/bin/codesign", "--force", "--sign", "-", "--timestamp=none", content / "MacOS/alice-desktop")
    run("/usr/bin/codesign", "--force", "--sign", "-", "--timestamp=none", app)
    run("/usr/bin/codesign", "--verify", "--deep", "--strict", "--verbose=2", app)
    write_json(release / "build-report.json", {**info, "macho_binaries": binaries,
               "static_checks": ["resources_sha256", "symlinks_inside_app", "arm64", "system_or_relative_dylibs",
                                 "plist", "codesign_deep_strict"], "behavior_tests": "not-run"})
    image_root = release / "image"
    image_root.mkdir()
    # Hard links avoid copying a second full bundle; hdiutil only reads this tree.
    shutil.copytree(app, image_root / "Alice.app", symlinks=True, copy_function=os.link)
    (image_root / "Applications").symlink_to("/Applications")
    (image_root / "安装说明.txt").write_text(
        "将 Alice 拖入 Applications（应用程序）后打开。\n"
        f"本包适用于 Apple Silicon，macOS {minimum} 及以上，已内置 Python 和运行依赖。\n"
        "首次配置模型 API；已有桌面原型沿用本机数据。覆盖安装前请先退出 Alice。\n"
        "这是本机开发包，采用临时签名，尚未取得 Apple Developer ID 签名或公证。\n"
        "数据保存在 ~/Library/Application Support/Alice/，删除应用不会删除数据。\n"
        "QQ、独立浏览器、B站和外部协作者组件、自动更新本批未打包。\n", encoding="utf-8")
    dmg = release / f"Alice-{config['version']}-macos-arm64.dmg"
    run("/usr/bin/hdiutil", "create", "-volname", "Alice", "-srcfolder", image_root,
        "-format", "UDZO", "-ov", dmg)
    run("/usr/bin/hdiutil", "verify", dmg)
    (release / "SHA256SUMS.txt").write_text(f"{sha(dmg)}  {dmg.name}\n", encoding="utf-8")
    write_json(BUILD / "latest-release.json", {"build_id": build_id, "app": str(app), "dmg": str(dmg),
                                                "report": str(release / "build-report.json")})
    print(f"App: {app}\nDMG: {dmg}\nHuman acceptance: pending", flush=True)


if __name__ == "__main__":
    main()
