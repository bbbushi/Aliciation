"""Resolve immutable resource/user roots before importing business modules.

The source CLI keeps its checkout-local user directory. Desktop launchers must
select an absolute user directory explicitly; a frozen bundle is never writable
application state. Importing this module does not create directories.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys

RESOURCE_ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent)).resolve()
_selected: Path | None = None
_OPTIONAL_COMPONENTS = ("context-inspector", "p1-evaluation", "qq", "browser-reader", "bilibili",
                        "claude-agent", "dsh-agent")
_COMPONENT_STATES = ("packaged", "not-packaged", "external-alice-lab", "external-alice-lab-source-runner")


def runtime_distribution() -> dict:
    """Describe shipped components. Their connection and authorization states are separate."""
    if not getattr(sys, "frozen", False):
        return {"kind": "source"}
    distribution = {"kind": "packaged", "components": {}}
    try:
        build = json.loads((RESOURCE_ROOT / "build-info.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return distribution
    components = build.get("components") if isinstance(build, dict) else None
    if isinstance(components, dict):
        distribution["components"] = {name: components[name] for name in _OPTIONAL_COMPONENTS
                                      if components.get(name) in _COMPONENT_STATES}
    return distribution


def user_root() -> Path:
    if _selected is not None:
        return _selected
    value = os.environ.get("ALICE_USER_DIR")
    if value:
        return _absolute(value)
    if getattr(sys, "frozen", False):
        raise ValueError("桌面后端必须通过 --user-dir 指定独立数据目录")
    return RESOURCE_ROOT / "user"


def _absolute(value: str | Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError("--user-dir / ALICE_USER_DIR 必须为绝对路径")
    path = path.resolve()
    if getattr(sys, "frozen", False):
        # Never write inside the immutable bundle, including via symlink aliases.
        binary_dir = Path(sys.executable).resolve().parent
        protected = [RESOURCE_ROOT, binary_dir]
        if binary_dir.name == "backend":
            protected.append(binary_dir.parent)
        protected.extend(parent for parent in binary_dir.parents if parent.suffix == ".app")
        if any(path == root or root in path.parents for root in protected):
            raise ValueError("桌面数据目录不能位于程序资源目录内")
    return path


def configure_user_root(value: str | Path | None) -> Path:
    """One selection per process. Refuse late mutation of imported path constants."""
    global _selected
    target = _absolute(value) if value is not None else user_root()
    loaded = sys.modules.get("alice.config")
    if loaded is not None and getattr(loaded, "USER_DIR", target) != target:
        raise RuntimeError("请在导入 Alice 业务模块之前指定数据目录")
    if _selected is not None and target != _selected:
        raise RuntimeError("同一进程不能切换 Alice 数据目录")
    _selected = target
    return target
