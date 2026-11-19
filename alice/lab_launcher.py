"""Open a separately registered Alice Lab; the browser cannot provide a command."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess

from .channels import ChannelError


def open_registered_lab():
    root = Path.home() / ".alice-lab"
    registration = root / "launcher.json"
    try:
        if not root.is_dir() or root.is_symlink():
            raise FileNotFoundError
        folder = root.stat()
        if os.name != "nt" and (folder.st_uid != os.getuid() or folder.st_mode & 0o077):
            raise ValueError
        fd = os.open(registration, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "rb") as file:
            info = os.fstat(file.fileno())
            if (registration.is_symlink() or not stat.S_ISREG(info.st_mode)
                    or info.st_size > 8192
                    or os.name != "nt" and (info.st_uid != os.getuid() or info.st_mode & 0o077)):
                raise ValueError
            data = json.loads(file.read(8193))
        if not isinstance(data, dict) or set(data) != {"protocol", "python", "script", "sha256"} or data["protocol"] != "alice-lab-launcher/1":
            raise ValueError
        python, script = Path(data["python"]), Path(data["script"])
        if (not python.is_absolute() or not script.is_absolute() or not python.is_file()
                or not re.fullmatch(r"python(?:3(?:\.\d+)?)?(?:\.exe)?", python.name, re.I)
                or script.is_symlink() or script.name != "server.py" or script.parent.name != "alice_lab"
                or not script.is_file() or script.stat().st_size > 1024 * 1024
                or hashlib.sha256(script.read_bytes()).hexdigest() != data["sha256"]):
            raise ValueError
    except FileNotFoundError as exc:
        raise ChannelError(404, "lab_not_registered", "尚未注册 Alice Lab，请先在 Lab 目录执行一次注册") from exc
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise ChannelError(409, "lab_registration_invalid", "Lab 注册已失效或文件已更新，请重新注册") from exc

    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("_PYI_", "PYTHON", "DYLD_"))
           and key not in {"LD_LIBRARY_PATH", "LD_LIBRARY_PATH_ORIG", "LD_PRELOAD", "ALICE_DESKTOP_NONCE"}}
    try:
        result = subprocess.run([str(python), "-I", str(script), "--open-from-alice"],
                                cwd=script.parent, env=env, stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                timeout=12, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ChannelError(503, "lab_launch_failed", "未能确认 Lab 已打开，请稍后重试") from exc
    if result.returncode != 0:
        raise ChannelError(503, "lab_launch_failed", "未能打开 Lab，请从终端启动查看状态")
    return {"status": "opened"}
