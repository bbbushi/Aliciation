"""Explicit, bounded installation of the fixed QQ addon's locked dependencies.

No addon imports, shell commands, private configuration reads or network work on
status requests. Runtime leases coordinate with both public Node entry points.
"""
from __future__ import annotations

import ctypes
import errno
import json
import os
import re
import shutil
import signal
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


MESSAGES = {
    "ready": "QQ 组件已准备好，可以扫码连接。",
    "node_missing": "未找到 Node.js。请先从官网下载并安装，然后点击重新检测。",
    "node_old": "Node.js 版本过旧或无法运行。请安装 22.19 或更新版本，然后重新检测。",
    "npm_missing": "未找到 Node.js 配套的 npm。请修复或重新安装 Node.js，然后重新检测。",
    "component_missing": "QQ 组件文件或安装清单不完整，请重新取得完整项目文件。",
    "dependencies_missing": "首次使用需要安装 QQ 组件，点击安装后会联网下载。",
    "installing": "正在下载并安装 QQ 组件，请稍候。",
    "failed": "安装未完成，请提供下方排查编号，以便确认具体原因。",
}
FAILURES = {
    "busy": "另一个 QQ 连接或安装仍在运行，请先停止后重新检测。",
    "lock_unknown": "QQ 组件的占用状态无法确认，请按接入说明核对残留锁后重试。",
    "timeout": "安装等待超过 5 分钟，已停止；请检查网络后重试。",
    "cancelled": "安装已随 Alice 关闭而停止，下次可重新安装。",
    "permission": "安装目录或文件不允许写入，请检查 Alice 所在文件夹的权限，并关闭占用组件的程序后重试。",
    "disk_full": "安装所在磁盘空间不足，请释放空间后重试。",
    "project_config_present": "项目有自定义安装设置，请按接入说明使用终端安装，成功后点击重新检测。",
    "manifest_invalid": "安装清单不完整或与组件版本不一致，请更新完整项目后重新检测。",
    "supervision_failed": "系统未能准备安装进程，下载尚未开始。请提供下方排查编号。",
    "launch_failed": "安装程序未能启动，请重新检测 Node.js；仍失败时请提供下方排查编号。",
    "network": "安装工具未能连接组件下载服务，请检查网络或代理设置后重试。",
    "certificate": "下载连接的证书校验未通过，请检查电脑时间、网络代理及证书设置后重试。",
    "package_unavailable": "下载服务未能提供所需组件，请稍后重试；仍失败时请提供下方排查编号。",
    "integrity": "下载的组件未通过完整性检查，请检查下载连接后重试。",
    "runtime_incompatible": "Node.js 与安装工具版本不兼容，请安装配套的官方 Node.js 和 npm 后重新检测。",
    "npm_failed": "安装工具已退出，但未完成安装。请提供下方排查编号，或按接入说明使用终端安装。",
    "verify_failed": "安装程序已结束，但组件文件仍不齐全，请重试；也可使用终端安装后重新检测。",
    "failed": MESSAGES["failed"],
}
STAGES = {"prepare", "supervision", "launch", "install", "verify", "inspect", "stop"}
NPM_ERRORS = {
    "EACCES": "permission", "EPERM": "permission", "EBUSY": "permission", "EROFS": "permission",
    "ENOSPC": "disk_full", "EDQUOT": "disk_full",
    "ECONNRESET": "network", "ECONNREFUSED": "network", "ETIMEDOUT": "network",
    "ESOCKETTIMEDOUT": "network", "ENOTFOUND": "network", "EAI_AGAIN": "network",
    "ENETUNREACH": "network", "EHOSTUNREACH": "network", "ECONNABORTED": "network",
    "CERT_HAS_EXPIRED": "certificate", "CERT_NOT_YET_VALID": "certificate",
    "DEPTH_ZERO_SELF_SIGNED_CERT": "certificate", "SELF_SIGNED_CERT_IN_CHAIN": "certificate",
    "UNABLE_TO_VERIFY_LEAF_SIGNATURE": "certificate", "UNABLE_TO_GET_ISSUER_CERT_LOCALLY": "certificate",
    "E401": "package_unavailable", "E403": "package_unavailable", "E404": "package_unavailable",
    "ETARGET": "package_unavailable", "EINTEGRITY": "integrity", "EBADENGINE": "runtime_incompatible",
    "EUSAGE": "manifest_invalid", "ENOENT": "npm_failed", "EINVAL": "npm_failed",
    "ERR_REQUIRE_ESM": "runtime_incompatible", "MODULE_NOT_FOUND": "launch_failed",
}
EXPECTED = {"@tencent-connect/qqbot-nodejs": "1.0.4",
            "@tencent-connect/qqbot-connector": "1.2.0", "qrcode-terminal": "0.12.0"}


class SetupFailure(Exception):
    def __init__(self, code="failed", *, stage=None, npm_code=None, system_code=None, exit_code=None):
        self.code = code if code in FAILURES else "failed"
        self.stage = stage if stage in STAGES else None
        self.npm_code = npm_code if npm_code in NPM_ERRORS else None
        self.system_code = system_code if type(system_code) is int and 0 < system_code <= 0xFFFFFFFF else None
        self.exit_code = exit_code if type(exit_code) is int and -(2 ** 31) <= exit_code <= 0xFFFFFFFF else None
        super().__init__(FAILURES[self.code])

    def diagnostic(self, default_stage):
        result = {"code": self.code, "stage": self.stage or default_stage}
        for name in ("npm_code", "system_code", "exit_code"):
            value = getattr(self, name)
            if value is not None:
                result[name] = value
        return result


def _os_failure(exc: OSError, stage: str) -> SetupFailure:
    number = getattr(exc, "winerror", None) or exc.errno
    if exc.errno in (errno.EACCES, errno.EPERM, errno.EROFS, errno.EBUSY) or getattr(exc, "winerror", None) in (5, 32, 33):
        code = "permission"
    elif exc.errno in (errno.ENOSPC, getattr(errno, "EDQUOT", errno.ENOSPC)) or getattr(exc, "winerror", None) == 112:
        code = "disk_full"
    else:
        code = "launch_failed" if stage == "launch" else "failed"
    return SetupFailure(code, stage=stage, system_code=number)


class _NpmDiagnostic:
    """Drain stderr with bounded memory; retain only an allowlisted error code."""
    def __init__(self, stream):
        self.code = None
        self._stream = stream
        self._thread = threading.Thread(target=self._read, name="alice-npm-diagnostic", daemon=True)
        self._thread.start()

    def _read(self):
        tail = b""
        try:
            while chunk := self._stream.read1(4096):
                block = tail + chunk
                for match in re.finditer(rb"(?:npm (?:ERR!|error) code |code: ['\"])([A-Z][A-Z0-9_]{1,63})(?=[\s'\"\r\n]|$)", block):
                    code = match.group(1).decode("ascii")
                    if self.code is None and code in NPM_ERRORS:
                        self.code = code
                tail = block[-128:]
        except (OSError, ValueError):
            pass
        finally:
            self._stream.close()

    def finish(self):
        self._thread.join(timeout=1)

    def failure(self, exit_code):
        self.finish()
        return SetupFailure(NPM_ERRORS.get(self.code, "npm_failed"), stage="install",
                            npm_code=self.code, exit_code=exit_code)


@dataclass(frozen=True)
class Environment:
    state: str
    node: str | None = None
    npm: str | None = None

    def public(self) -> dict:
        return {"state": self.state, "message": MESSAGES[self.state],
                "can_install": self.state == "dependencies_missing",
                "can_recheck": self.state != "installing"}


def _json_file(path: Path, maximum=262144):
    if not path.is_file() or path.stat().st_size > maximum:
        raise ValueError("file")
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _manifest(root: Path) -> dict:
    package = _json_file(root / "package.json")
    lock = _json_file(root / "package-lock.json")
    if (package.get("dependencies") != EXPECTED or lock.get("lockfileVersion") != 3
            or lock.get("packages", {}).get("", {}).get("dependencies") != EXPECTED):
        raise ValueError("manifest")
    packages = lock["packages"]
    if set(packages) != {"", *("node_modules/" + name for name in EXPECTED), "node_modules/ws"}:
        raise ValueError("packages")
    for name, item in packages.items():
        if not name:
            continue
        url = urlsplit(item.get("resolved", ""))
        if (url.scheme != "https" or url.netloc != "registry.npmjs.org" or url.query or url.fragment
                or not item.get("integrity", "").startswith("sha512-")):
            raise ValueError("registry")
    return packages


def _node_path() -> str | None:
    candidates = [shutil.which("node")]
    if os.name == "nt":
        # A running Alice retains its old PATH after the user installs Node.
        for key in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
            base = os.environ.get(key)
            if base:
                candidates.extend((str(Path(base) / "nodejs" / "node.exe"),
                                   str(Path(base) / "Programs" / "nodejs" / "node.exe")))
        base = os.environ.get("NVM_SYMLINK")
        if base:
            candidates.append(str(Path(base) / "node.exe"))
    return next((str(Path(p).resolve()) for p in candidates if p and Path(p).is_file()), None)


def _environment() -> dict:
    allowed = {"path", "systemroot", "windir", "comspec", "temp", "tmp", "tmpdir", "home",
               "userprofile", "localappdata", "appdata", "programfiles", "programfiles(x86)",
               "https_proxy", "http_proxy", "no_proxy", "lang", "lc_all"}
    return {key: value for key, value in os.environ.items() if key.lower() in allowed}


def _node_version(node: str) -> tuple | None:
    child = subprocess.Popen((node, "--version"), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, env=_environment(),
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    output = []
    def read():
        try:
            output.append(child.stdout.read(101))
        finally:
            child.stdout.close()
    reader = threading.Thread(target=read, daemon=True, name="alice-node-version")
    reader.start()
    try:
        child.wait(timeout=4)
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait(timeout=1)
        return None
    finally:
        reader.join(timeout=1)
    if child.returncode or not output or len(output[0]) > 100:
        return None
    match = re.fullmatch(rb"v(\d+)\.(\d+)\.(\d+)\s*", output[0])
    return tuple(map(int, match.groups())) if match else None


def inspect_environment(root: Path) -> Environment:
    """Bounded local inspection at startup or after an explicit action; never GET."""
    try:
        node = _node_path()
        if node is None:
            return Environment("node_missing")
        version = _node_version(node)
        if version is None or version < (22, 19, 0):
            return Environment("node_old", node)
    except (subprocess.SubprocessError, OSError, ValueError):
        return Environment("node_old", locals().get("node"))
    npm = None
    try:
        npm_candidates = [Path(node).parent / "node_modules/npm/bin/npm-cli.js",
                          Path(node).parent.parent / "lib/node_modules/npm/bin/npm-cli.js"]
        npm_command = shutil.which("npm")
        if npm_command:
            npm_candidates.extend((Path(npm_command).resolve(),
                                   Path(npm_command).parent / "node_modules/npm/bin/npm-cli.js"))
        npm = next((str(p.resolve()) for p in npm_candidates if p.name == "npm-cli.js" and p.is_file()), None)
        if not all((root / p).is_file() for p in ("src/web-control.mjs", "src/main.mjs", "src/runtime-lease.mjs")):
            return Environment("component_missing", node, npm)
        packages = _manifest(root)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return Environment("component_missing", node, npm)
    try:
        for name, item in packages.items():
            if not name:
                continue
            package = _json_file(root / name / "package.json")
            # This pinned package declares "./lib/main". Node resolves that to
            # main.js; checking the literal extensionless path rejects a valid
            # installation on every platform. Check its actual entry instead.
            main = "lib/main.js" if name == "node_modules/qrcode-terminal" else package.get("main", "index.js")
            entry = (root / name / main).resolve()
            entry.relative_to((root / name).resolve())
            if package.get("version") != item["version"] or not entry.is_file():
                return Environment("dependencies_missing" if npm else "npm_missing", node, npm)
        if not all((root / "node_modules" / file).is_file() for file in (
                "@tencent-connect/qqbot-connector/dist/esm/index.js",
                "qrcode-terminal/vendor/QRCode/index.js", "qrcode-terminal/vendor/QRCode/QRErrorCorrectLevel.js")):
            return Environment("dependencies_missing" if npm else "npm_missing", node, npm)
        return Environment("ready", node, npm)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return Environment("dependencies_missing" if npm else "npm_missing", node, npm)


def _pid_alive(pid: int) -> bool:
    """Unknown means occupied; Windows os.kill(pid, 0) is deliberately avoided."""
    if type(pid) is not int or not 0 < pid <= 0xFFFFFFFF:
        raise SetupFailure("lock_unknown")
    if os.name == "nt":
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return ctypes.get_last_error() != 87  # ERROR_INVALID_PARAMETER = no such PID.
        try:
            code = wintypes.DWORD()
            return not kernel.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _occupied(info: dict) -> bool:
    if not isinstance(info, dict) or not isinstance(info.get("nonce"), str):
        raise SetupFailure("lock_unknown")
    alive = _pid_alive(info.get("pid"))
    if "child_pid" in info:
        alive = _pid_alive(info["child_pid"]) or alive
    return alive


def _remove_dead_file(file: Path, *, required=False) -> None:
    try:
        with file.open("rb") as source:
            original = source.read(2049)
        if len(original) > 2048 or _occupied(json.loads(original)):
            raise SetupFailure("busy")
        with file.open("rb") as source:
            current = source.read(2049)
        if current != original:
            raise SetupFailure("busy")
        file.unlink()
    except FileNotFoundError:
        if required:
            raise SetupFailure("lock_unknown") from None
        return
    except (ValueError, OSError, TypeError):
        raise SetupFailure("lock_unknown") from None


class InstallGuard:
    def __init__(self, root: Path):
        self.root = root
        self.directory = root / ".install-lock"
        self.nonce = uuid.uuid4().hex
        self.owned = False
        self.owner_written = False
        self.mutex = None

    def __enter__(self):
        try:
            self.mutex = open(self.root / ".install-guard", "a+b")
            self.mutex.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(self.mutex.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self.mutex.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise SetupFailure("busy") from None
            try:
                self.directory.mkdir()
            except FileExistsError:
                try:
                    previous = _json_file(self.directory / "owner.json", 2048)
                    old_nonce = previous.get("nonce", "")
                    if not re.fullmatch(r"[0-9a-f]{32}", old_nonce):
                        raise ValueError("nonce")
                    known = {"owner.json", "npm-user.conf", "npm-global.conf", old_nonce + ".tmp"}
                    if any(path.name not in known for path in self.directory.iterdir()):
                        raise ValueError("unexpected file")
                except (OSError, ValueError, AttributeError, TypeError):
                    raise SetupFailure("lock_unknown") from None
                _remove_dead_file(self.directory / "owner.json", required=True)
                # An empty or incomplete gate remains conservative. Never remove
                # a directory created by a live installer before it writes its owner.
                try:
                    for name in ("npm-user.conf", "npm-global.conf", old_nonce + ".tmp"):
                        (self.directory / name).unlink(missing_ok=True)
                    self.directory.rmdir()
                except OSError:
                    raise SetupFailure("lock_unknown") from None
                self.directory.mkdir()
            self.owned = True
            self.set_child(None)
            for lease in (self.root / ".runtime-leases").glob("*.json"):
                _remove_dead_file(lease)
            # Older running bridges predate runtime leases. Default bridge lock
            # is checked for existence only; no credentials/configuration is read.
            legacy = self.root.parent.parent / "user/data/qq-bridge/bridge.lock"
            if legacy.exists():
                raise SetupFailure("busy")
            return self
        except OSError as exc:
            self.__exit__(None, None, None)
            raise _os_failure(exc, "prepare") from None
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def set_child(self, pid):
        info = {"pid": os.getpid(), "nonce": self.nonce}
        if pid is not None:
            info["child_pid"] = pid
        file = self.directory / "owner.json"
        temporary = self.directory / (self.nonce + ".tmp")
        temporary.write_text(json.dumps(info), encoding="utf-8")
        os.replace(temporary, file)
        self.owner_written = True

    def __exit__(self, *_):
        try:
            if self.owned:
                file = self.directory / "owner.json"
                if not self.owner_written or _json_file(file, 2048).get("nonce") == self.nonce:
                    for name in ("npm-user.conf", "npm-global.conf", self.nonce + ".tmp"):
                        (self.directory / name).unlink(missing_ok=True)
                    file.unlink(missing_ok=True)
                    self.directory.rmdir()
        except (OSError, ValueError, AttributeError):
            pass
        finally:
            if self.mutex is not None:
                self.mutex.close()
                self.mutex = None


def stop_process(child) -> None:
    if child.poll() is not None:
        return
    if os.name != "nt":
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    else:
        try:
            child.kill()
        except OSError:
            pass
    try:
        child.wait(timeout=3)
    except subprocess.TimeoutExpired:
        raise SetupFailure("lock_unknown") from None


class WindowsJob:
    """Kill our entire npm process tree when the host closes this handle."""
    def __init__(self):
        self.handle = None
        if os.name != "nt":
            return
        from ctypes import wintypes as w
        class Basic(ctypes.Structure):
            _fields_ = [("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64),
                        ("flags", w.DWORD), ("min_ws", ctypes.c_size_t), ("max_ws", ctypes.c_size_t),
                        ("active", w.DWORD), ("affinity", ctypes.c_size_t),
                        ("priority", w.DWORD), ("scheduling", w.DWORD)]
        class Limits(ctypes.Structure):
            _fields_ = [("basic", Basic), ("io", ctypes.c_uint64 * 6),
                        ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
                        ("peak_process", ctypes.c_size_t), ("peak_job", ctypes.c_size_t)]
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.CreateJobObjectW.argtypes = (ctypes.c_void_p, w.LPCWSTR)
        self.kernel.CreateJobObjectW.restype = w.HANDLE
        self.kernel.SetInformationJobObject.argtypes = (w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD)
        self.kernel.AssignProcessToJobObject.argtypes = (w.HANDLE, w.HANDLE)
        self.kernel.CloseHandle.argtypes = (w.HANDLE,)
        handle = self.kernel.CreateJobObjectW(None, None)
        limits = Limits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not handle:
            raise SetupFailure("supervision_failed", stage="supervision", system_code=ctypes.get_last_error())
        if not self.kernel.SetInformationJobObject(handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            error = ctypes.get_last_error()
            self.kernel.CloseHandle(handle)
            raise SetupFailure("supervision_failed", stage="supervision", system_code=error)
        self.handle = handle

    def assign(self, child):
        if self.handle and not self.kernel.AssignProcessToJobObject(self.handle, int(child._handle)):
            raise SetupFailure("supervision_failed", stage="supervision", system_code=ctypes.get_last_error())

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


# npm may start only after the parent has recorded its PID and attached its job.
# If the host dies before that handshake, stdin closes and this launcher exits
# without touching dependencies. After the handshake, stale-gate checks include
# the child PID (also on POSIX where a host crash need not terminate children).
_NPM_LAUNCHER = r"""
const timer = setTimeout(() => process.exit(2), 10000);
let input = '';
process.stdin.setEncoding('utf8');
process.stdin.on('end', () => process.exit(2));
process.stdin.on('data', chunk => {
  input += chunk;
  if (input.length > 16) process.exit(2);
  if (input !== 'install\n') return;
  clearTimeout(timer);
  process.stdin.removeAllListeners();
  process.stdin.pause();
  process.argv = [process.execPath, ...process.argv.slice(1)];
  require(process.argv[1]);
});
"""


def install_dependencies(root: Path, environment: Environment, cancel, report) -> Environment:
    """User-triggered only; raw output is never stored, logged or sent to the GUI."""
    if environment.state != "dependencies_missing" or not environment.node or not environment.npm:
        return environment
    child = None
    diagnostic = None
    with InstallGuard(root) as guard:
        if cancel.is_set():
            raise SetupFailure("cancelled")
        try:
            _manifest(root)  # Fail closed if a local update replaced the locked manifest.
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            raise SetupFailure("manifest_invalid", stage="prepare") from None
        if (root / ".npmrc").exists():
            raise SetupFailure("project_config_present", stage="prepare")
        try:
            for name in ("npm-user.conf", "npm-global.conf"):
                (guard.directory / name).write_text("", encoding="utf-8")
        except OSError as exc:
            raise _os_failure(exc, "prepare") from None
        command = (environment.node, "--input-type=commonjs", "-e", _NPM_LAUNCHER, "--", environment.npm,
                   "ci", "--ignore-scripts", "--no-audit", "--no-fund", "--global=false",
                   f"--prefix={root}", f"--userconfig={guard.directory / 'npm-user.conf'}",
                   f"--globalconfig={guard.directory / 'npm-global.conf'}", f"--cache={root / '.npm-cache'}",
                   "--registry=https://registry.npmjs.org", "--logs-max=0", "--fetch-retries=1",
                   "--fetch-timeout=30000")
        job = WindowsJob()
        stage = "launch"
        try:
            report("正在启动安装程序，请稍候。")
            child = subprocess.Popen(command, cwd=root, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.PIPE, start_new_session=os.name != "nt",
                                     env=_environment(),
                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            diagnostic = _NpmDiagnostic(child.stderr)
            job.assign(child)
            guard.set_child(child.pid)
            if cancel.is_set():
                raise SetupFailure("cancelled")
            child.stdin.write(b"install\n")
            child.stdin.flush()
            stage = "install"
            report("正在下载并安装 QQ 组件，请稍候。")
            deadline = time.monotonic() + 300
            while child.poll() is None:
                if cancel.wait(0.2):
                    raise SetupFailure("cancelled")
                if time.monotonic() >= deadline:
                    raise SetupFailure("timeout")
            if child.returncode:
                raise diagnostic.failure(child.returncode)
            stage = "verify"
            report("正在检查安装结果，请稍候。")
            checked = inspect_environment(root)
            if checked.state != "ready":
                raise SetupFailure("verify_failed", stage="verify")
            return checked
        except SetupFailure as exc:
            if exc.stage is None:
                exc.stage = stage
            raise
        except OSError as exc:
            raise _os_failure(exc, stage) from None
        except Exception:
            raise SetupFailure("failed", stage=stage) from None
        finally:
            job.close()
            if child is not None:
                try:
                    stop_process(child)
                except SetupFailure:
                    # Keep the gate if process termination could not be confirmed.
                    guard.owned = False
                    raise
                finally:
                    if child.stdin:
                        try:
                            child.stdin.close()
                        except OSError:
                            pass
                    if diagnostic is not None:
                        diagnostic.finish()
