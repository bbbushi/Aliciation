"""Fixed executable modes; frozen Python is not a general script interpreter."""
from __future__ import annotations

import sys

from .runtime_paths import RESOURCE_ROOT

_ADDONS = {
    "bilibili": ("bilibili", "worker.py"),
    "claude-agent": ("claude_agent", "adapter.py"),
    "dsh-agent": ("dsh_agent", "adapter.py"),
}


def discovery_command(input_limit: int) -> list[str]:
    if type(input_limit) is not int or input_limit <= 0:
        raise ValueError("invalid discovery input limit")
    prefix = ([sys.executable, "discovery-worker"] if getattr(sys, "frozen", False)
              else [sys.executable, "-m", "alice.discovery_worker"])
    return [*prefix, "--input-bytes", str(input_limit)]


def addon_command(name: str, *args: str) -> list[str]:
    """Only independently packaged, fixed addon entries may run in frozen mode."""
    directory, script = _ADDONS[name]
    if getattr(sys, "frozen", False):
        suffix = ".exe" if sys.platform == "win32" else ""
        entry = RESOURCE_ROOT / "addon-bin" / ("alice-" + name + suffix)
        if not entry.is_file():
            raise FileNotFoundError("此桌面包未适配组件：" + name)
        return [str(entry), *args]
    return [sys.executable, "-I", str(RESOURCE_ROOT / "addons" / directory / script), *args]


def addon_available(name: str) -> bool:
    try:
        command = addon_command(name)
    except FileNotFoundError:
        return False
    if getattr(sys, "frozen", False):
        return bool(command)
    directory, script = _ADDONS[name]
    return (RESOURCE_ROOT / "addons" / directory / script).is_file()


def backend_command(*args: str) -> list[str]:
    prefix = [sys.executable] if getattr(sys, "frozen", False) else [sys.executable, "-m", "alice"]
    return [*prefix, *args]
