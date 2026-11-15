"""Offline profile backups and journaled recovery; standard library only.

Public callers use the maintenance CLI. No Store, model client, migrations or
network access is constructed here. All mutations hold stable profile/data locks.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import errno
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import uuid

from .instance_lock import InstanceLock
from .runtime_paths import RESOURCE_ROOT

FORMAT = 1
PROTOCOL = 1
_ID = re.compile(r"[a-f0-9]{32}\Z")
_SKIP_ROOT = {".maintenance", ".alice-profile.lock", ".alice-instance.lock"}
_BROWSER_CACHE = {"Cache", "Code Cache", "GPUCache", "ShaderCache", "GrShaderCache", "DawnCache", "Crashpad"}


class MaintenanceRequired(RuntimeError):
    pass


def public_error(exc):
    if isinstance(exc, PermissionError):
        return "维护位置不可写，请检查本机目录权限"
    if isinstance(exc, OSError) and exc.errno == errno.ENOSPC:
        return "磁盘空间不足，维护未完成；当前数据与已有恢复点仍保留"
    if isinstance(exc, (ValueError, RuntimeError)):
        return str(exc)
    if isinstance(exc, sqlite3.Error):
        return "数据库核验未通过，维护已停止；请保留现场与已有恢复点"
    return "维护核验或文件操作失败，请检查本机日志"


def _now():
    return datetime.now(timezone.utc).isoformat()


def release_info():
    from . import __version__
    path = RESOURCE_ROOT / "build-info.json"
    if path.is_file():
        value = json.loads(path.read_text(encoding="utf-8"))
        declared = value.get("data_format", {})
        if (value.get("version") != __version__ or value.get("maintenance_protocol") != PROTOCOL
                or declared.get("write") != FORMAT or FORMAT not in declared.get("read", [])):
            raise ValueError("程序包版本或维护兼容声明不一致，拒绝打开数据")
        return {"version": __version__, "build_id": value["build_id"], "data_format": FORMAT}
    return {"version": __version__, "build_id": "source-" + __version__, "data_format": FORMAT}


def _digest(path):
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    with os.fdopen(os.open(path, flags), "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("备份仅接受独立普通文件")
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _directory(path):
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError("维护目录不能是符号链接")
    missing = [part for part in (path, *path.parents) if not part.exists()]
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    for created in reversed(missing):
        _sync(created.parent)
    if not path.is_dir():
        raise ValueError("维护路径必须是目录")
    os.chmod(path, 0o700)
    return path


def _area(root):
    return _directory(Path(root) / ".maintenance")


def _sync(directory):
    if os.name != "nt":
        fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _read(path):
    if not path.exists() and not path.is_symlink():
        return None
    if (any(part.is_symlink() for part in (path, *path.parents)) or not path.is_file()
            or path.stat().st_nlink != 1 or path.stat().st_size > 32 * 1024 * 1024):
        raise ValueError("维护记录不是有效的独立文件")
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("维护记录含重复字段")
            result[key] = value
        return result
    value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique)
    if not isinstance(value, dict):
        raise ValueError("维护记录格式不正确")
    return value


def _write(path, value):
    _directory(path.parent)
    temp = path.with_name("." + path.name + "." + uuid.uuid4().hex + ".tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, allow_nan=False, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
        _sync(path.parent)
    finally:
        temp.unlink(missing_ok=True)


def _marker(root):
    area = Path(root) / ".maintenance"
    if area.is_symlink():
        raise ValueError("维护目录不能是符号链接")
    marker = _read(area / "state.json")
    if marker and (marker.get("protocol") != PROTOCOL or marker.get("phase") not in {
            "preparing", "prepared", "recovery", "failed", "authorized", "activating", "completed"}):
        raise ValueError("维护记录版本或阶段不受支持")
    return marker


def require_startable(root):
    release_info()  # Validate immutable program declarations before opening SQLite.
    area = Path(root) / ".maintenance"
    marker = _marker(root)
    if marker and marker["phase"] != "completed":
        raise MaintenanceRequired("Alice 正在维护或等待恢复，请使用桌面的备份与恢复窗口")
    journal = _read(area / "restore.json")
    if journal and journal.get("phase") not in {"restored", "rolled_back"}:
        raise MaintenanceRequired("上次恢复尚未结束，请先核对或撤销未完成的恢复")
    fmt = _read(area / "format.json")
    if fmt and fmt.get("data_format") != FORMAT:
        raise MaintenanceRequired("当前版本不支持此数据格式，请安装匹配版本后恢复")


def _ignored(parts):
    return (parts[0] in _SKIP_ROOT or parts == ("data", ".alice-instance.lock")
            or any(p in {".DS_Store", "__pycache__"} for p in parts) or parts[0] in {"logs", "cache", "downloads"}
            or (parts[0] == "browser_sessions" and any(p in _BROWSER_CACHE for p in parts[1:])))


def _scan(base, *, exclude=True):
    rows = []
    for directory, dirs, files in os.walk(base, followlinks=False):
        parent = Path(directory)
        for name in sorted(dirs + files):
            path = parent / name
            rel = path.relative_to(base)
            if exclude and _ignored(rel.parts):
                if name in dirs:
                    dirs.remove(name)
                continue
            info = path.lstat()
            if stat.S_ISDIR(info.st_mode):
                rows.append({"path": rel.as_posix(), "kind": "directory", "mode": 0o700})
            elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                rows.append({"path": rel.as_posix(), "kind": "file", "size": info.st_size,
                             "mode": 0o700 if info.st_mode & 0o100 else 0o600, "sha256": _digest(path)})
            else:
                raise ValueError("用户目录含符号链接、硬链接或特殊文件，不能创建完整恢复点")
    return sorted(rows, key=lambda row: row["path"])


def _copy_file(source, target, mode=0o600):
    _directory(target.parent)
    src = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        info = os.fstat(src)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("备份来源不再是独立普通文件")
        dst = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
        with os.fdopen(dst, "wb") as output, os.fdopen(src, "rb", closefd=False) as input_file:
            shutil.copyfileobj(input_file, output, 1024 * 1024)
            output.flush()
            os.fsync(output.fileno())
    finally:
        os.close(src)


def _copy_inventory(source, target, rows):
    _directory(target)
    for row in rows:
        rel = Path(row["path"])
        if rel.is_absolute() or ".." in rel.parts:
            raise ValueError("恢复点含越界路径")
        if row["kind"] == "directory":
            _directory(target / rel)
        else:
            _copy_file(source / rel, target / rel, row["mode"])


def _durable_tree(path):
    directories = []
    for directory, _, files in os.walk(path):
        directories.append(Path(directory))
        for name in files:
            fd = os.open(Path(directory) / name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
    for directory in reversed(directories):
        _sync(directory)


def _sqlite_checks(profile):
    databases = []
    for row in _scan(profile, exclude=False):
        if row["kind"] != "file":
            continue
        path = profile / row["path"]
        if not path.exists() and path.name.endswith(("-wal", "-shm")):
            continue  # Closing a preceding staged SQLite database may remove sidecars.
        with path.open("rb") as stream:
            if stream.read(16) != b"SQLite format 3\0":
                continue
        connection = sqlite3.connect(path.as_uri() + "?mode=rw", uri=True, timeout=0)
        try:
            connection.execute("PRAGMA trusted_schema=OFF")
            if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                raise ValueError("恢复点数据库完整性核验失败")
            if connection.execute("PRAGMA foreign_key_check").fetchone():
                raise ValueError("恢复点数据库外键核验失败")
            if connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0] != 0:
                raise ValueError("恢复点数据库 WAL 无法收拢")
            schema = connection.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name").fetchall()
            databases.append({"path": row["path"], "user_version": connection.execute("PRAGMA user_version").fetchone()[0],
                              "schema_sha256": hashlib.sha256(json.dumps(schema, ensure_ascii=False).encode()).hexdigest()})
        finally:
            connection.close()
    return databases


def _summary(value):
    release = value.get("release")
    if (not isinstance(release, dict) or not isinstance(release.get("version"), str)
            or len(release["version"]) > 32 or not isinstance(release.get("build_id"), str)
            or len(release["build_id"]) > 160 or release.get("data_format") != FORMAT
            or type(value.get("file_count")) is not int or value["file_count"] < 0
            or type(value.get("bytes")) is not int or value["bytes"] < 0
            or not isinstance(value.get("created_at"), str) or len(value["created_at"]) > 64
            or value.get("credentials_included") is not True):
        raise ValueError("恢复点元数据无效")
    try:
        datetime.fromisoformat(value["created_at"])
    except ValueError:
        raise ValueError("恢复点时间格式无效") from None
    result = {key: value[key] for key in ("backup_id", "created_at", "file_count", "bytes", "credentials_included")}
    result["release"] = {key: release[key] for key in ("version", "build_id", "data_format")}
    return result


def _backup_path(root, backup_id):
    if not isinstance(backup_id, str) or not _ID.fullmatch(backup_id):
        raise ValueError("恢复点标识无效")
    folder = _area(root) / "backups" / backup_id
    if folder.is_symlink() or folder.parent.is_symlink():
        raise ValueError("恢复点目录不能是符号链接")
    return folder


def verify_backup(root, backup_id):
    folder = _backup_path(root, backup_id)
    value = _read(folder / "manifest.json")
    if (not value or value.get("protocol") != PROTOCOL or value.get("data_format") != FORMAT
            or value.get("backup_id") != backup_id or value.get("credentials_included") is not True):
        raise ValueError("恢复点缺失、格式不兼容或标识不匹配")
    _summary(value)
    profile = folder / "profile"
    if profile.is_symlink() or not profile.is_dir() or _scan(profile, exclude=False) != value.get("files"):
        raise ValueError("恢复点文件清单或摘要不匹配，拒绝恢复")
    return value


def _create_locked(root):
    area = _area(root)
    fmt = _read(area / "format.json")
    if fmt and fmt.get("data_format") != FORMAT:
        raise ValueError("此版本不能为不兼容的数据格式创建恢复点，请安装匹配版本")
    parent = _directory(area / "backups")
    backup_id = uuid.uuid4().hex
    temporary = _directory(parent / ("." + backup_id + ".partial"))
    rows = _scan(root)
    total = sum(row.get("size", 0) for row in rows)
    if shutil.disk_usage(area).free < total + 64 * 1024 * 1024:
        raise ValueError("磁盘空间不足，未创建恢复点")
    _copy_inventory(root, temporary / "profile", rows)
    if _scan(temporary / "profile", exclude=False) != rows or _scan(root) != rows:
        raise ValueError("备份期间数据发生变化，未发布恢复点")
    sqlite = _sqlite_checks(temporary / "profile")  # Only the private staged copy is opened.
    _durable_tree(temporary / "profile")
    files = _scan(temporary / "profile", exclude=False)
    value = {"protocol": PROTOCOL, "backup_id": backup_id, "created_at": _now(), "data_format": FORMAT,
             "release": release_info(), "credentials_included": True,
             "source_release": fmt.get("release") if fmt else None,
             "excluded": [".maintenance", "OS lock files", "logs", "cache", "downloads", "browser caches"],
             "files": files, "sqlite": sqlite, "file_count": sum(r["kind"] == "file" for r in files),
             "bytes": sum(r.get("size", 0) for r in files)}
    _write(temporary / "manifest.json", value)
    os.replace(temporary, parent / backup_id)
    _sync(parent)
    return _summary(verify_backup(root, backup_id))


def create_backup(root):
    with InstanceLock(Path(root) / "data", maintenance=True):
        return _create_locked(Path(root))


def mark_recovery(root):
    _write(_area(root) / "state.json", {"protocol": PROTOCOL, "phase": "recovery", "created_at": _now(),
                                      "source_release": release_info()})


def prepare_update(root):
    root = Path(root)
    with InstanceLock(root / "data", maintenance=True):
        journal = _read(_area(root) / "restore.json")
        if journal and journal.get("phase") not in {"restored", "rolled_back"}:
            raise ValueError("请先完成或撤销未完成的恢复")
        state = {"protocol": PROTOCOL, "phase": "preparing", "created_at": _now(), "source_release": release_info()}
        _write(_area(root) / "state.json", state)
        try:
            backup = _create_locked(root)
            state.update(phase="prepared", backup_id=backup["backup_id"])
            _write(_area(root) / "state.json", state)
            return backup
        except BaseException as exc:
            state.update(phase="failed", error=public_error(exc))
            _write(_area(root) / "state.json", state)
            raise


def status(root):
    root = Path(root)
    state = _marker(root)
    journal = _read(root / ".maintenance/restore.json")
    backups = []
    parent = root / ".maintenance/backups"
    if parent.is_symlink():
        raise ValueError("恢复点目录不能是符号链接")
    if parent.is_dir():
        for directory in parent.iterdir():
            if directory.is_dir() and _ID.fullmatch(directory.name) and not directory.is_symlink():
                try:
                    value = _read(directory / "manifest.json")
                    if value and value.get("backup_id") == directory.name and value.get("protocol") == PROTOCOL:
                        backups.append(_summary(value))
                except (ValueError, OSError, KeyError):
                    continue  # Never label an unreadable manifest as a valid backup.
    backups.sort(key=lambda value: value["created_at"], reverse=True)
    return {"release": release_info(), "phase": state["phase"] if state else "normal", "backups": backups[:20],
            "backup_count": len(backups), "incomplete_restore": bool(journal and journal.get("phase") not in {"restored", "rolled_back"}),
            "local_credentials": True, "network_updates": False,
            "error": str(state.get("error", ""))[:512] if state else ""}


def _fingerprint(path):
    if not path.exists() and not path.is_symlink():
        return None
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError("恢复路径不能是符号链接")
    value = _scan(path, exclude=False) if path.is_dir() else {"file": _digest(path)}
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _items(profile):
    result = set()
    for path in profile.iterdir():
        if _ignored((path.name,)):
            continue
        if path.name == "data" and path.is_dir() and not path.is_symlink():
            result.update("data/" + child.name for child in path.iterdir() if not _ignored(("data", child.name)))
        else:
            result.add(path.name)
    return result


def _restore_journal(root):
    value = _read(_area(root) / "restore.json")
    if not value or value.get("protocol") != PROTOCOL or not _ID.fullmatch(str(value.get("op_id", ""))):
        raise ValueError("恢复操作记录缺失或格式无效")
    if value.get("phase") not in {"replacing", "failed", "restored", "rolled_back"} or not isinstance(value.get("items"), list):
        raise ValueError("恢复操作阶段无效")
    if value.get("old_format") is not None and not isinstance(value["old_format"], dict):
        raise ValueError("恢复前格式记录无效")
    seen = set()
    for item in value["items"]:
        path = Path(item["path"])
        if (path.is_absolute() or ".." in path.parts or not path.parts or _ignored(path.parts)
                or len(path.parts) > 2 or (len(path.parts) == 2 and path.parts[0] != "data") or path.as_posix() == "data"
                or path.as_posix() in seen
                or any(item.get(key) is not None and not re.fullmatch(r"[a-f0-9]{64}", str(item[key])) for key in ("old", "new"))):
            raise ValueError("恢复操作路径无效")
        seen.add(path.as_posix())
    return value


def restore_backup(root, backup_id):
    root = Path(root)
    with InstanceLock(root / "data", maintenance=True):
        previous = _read(_area(root) / "restore.json")
        if previous and previous.get("phase") not in {"restored", "rolled_back"}:
            raise ValueError("请先撤销未完成的恢复；当前数据尚未替换")
        value = verify_backup(root, backup_id)
        if shutil.disk_usage(root).free < value["bytes"] + 64 * 1024 * 1024:
            raise ValueError("空间不足，未替换当前数据")
        mark_recovery(root)
        op_id = uuid.uuid4().hex
        work = _directory(_area(root) / "replaced" / op_id)
        old = _directory(work / "previous")
        candidate = work / "candidate"
        _copy_inventory(_backup_path(root, backup_id) / "profile", candidate, value["files"])
        if _scan(candidate, exclude=False) != value["files"]:
            raise ValueError("恢复暂存副本摘要不匹配，当前数据未替换")
        _sqlite_checks(candidate)
        _durable_tree(candidate)
        paths = sorted(_items(root) | _items(candidate))
        journal = {"protocol": PROTOCOL, "op_id": op_id, "backup_id": backup_id, "phase": "replacing", "created_at": _now(),
                   "old_format": _read(_area(root) / "format.json"),
                   "items": [{"path": path, "old": _fingerprint(root / path), "new": _fingerprint(candidate / path)} for path in paths]}
        _write(_area(root) / "restore.json", journal)
        try:
            for item in journal["items"]:
                path = item["path"]
                if _fingerprint(root / path) != item["old"]:
                    raise ValueError("当前数据已变化，停止恢复并保留现场")
                if _fingerprint(old / path) is not None or _fingerprint(candidate / path) != item["new"]:
                    raise ValueError("恢复暂存位置已变化，停止恢复并保留现场")
                if item["old"] is not None:
                    _directory((old / path).parent)
                    os.replace(root / path, old / path)
                    _sync((root / path).parent)
                    _sync((old / path).parent)
                if item["new"] is not None:
                    _directory((root / path).parent)
                    os.replace(candidate / path, root / path)
                    _sync((root / path).parent)
                    _sync((candidate / path).parent)
            for item in journal["items"]:
                if _fingerprint(root / item["path"]) != item["new"]:
                    raise ValueError("恢复后文件核验失败，请保留现场")
            _write(_area(root) / "format.json", {"data_format": FORMAT, "release": release_info()})
            journal["phase"] = "restored"
            _write(_area(root) / "restore.json", journal)
            return {"status": "restored", "backup": _summary(value), "previous_data_retained": True}
        except BaseException:
            journal["phase"] = "failed"
            _write(_area(root) / "restore.json", journal)
            raise


def rollback_restore(root):
    root = Path(root)
    with InstanceLock(root / "data", maintenance=True):
        value = _restore_journal(root)
        if value["phase"] in {"restored", "rolled_back"}:
            raise ValueError("此操作只撤销未完成的恢复，不能覆盖已继续使用的数据")
        work = _area(root) / "replaced" / value["op_id"]
        old, candidate = work / "previous", work / "candidate"
        plan = []
        for item in value["items"]:
            path = item["path"]
            actual, saved = _fingerprint(root / path), _fingerprint(old / path)
            if saved == item["old"] and saved is not None:
                if actual not in (None, item["new"]) or (actual is not None and (candidate / path).exists()):
                    raise ValueError("恢复现场已有其他改动，拒绝覆盖")
                plan.append((item, actual, saved))
            elif saved is None and actual == item["old"]:
                continue  # Old item was never moved, or a prior rollback already restored it.
            elif item["old"] is None and saved is None and actual in (None, item["new"]):
                if actual is not None and (candidate / path).exists():
                    raise ValueError("恢复暂存位置已有其他改动，拒绝覆盖")
                plan.append((item, actual, saved))
            else:
                raise ValueError("无法核对恢复前的数据，请保留现场人工恢复")
        for item, actual, saved in reversed(plan):
            path = item["path"]
            if _fingerprint(root / path) != actual or _fingerprint(old / path) != saved:
                raise ValueError("撤销期间现场已变化，停止操作并保留数据")
            if (root / path).exists():
                _directory((candidate / path).parent)
                os.replace(root / path, candidate / path)
                _sync((candidate / path).parent)
            if item["old"] is not None:
                _directory((root / path).parent)
                os.replace(old / path, root / path)
                _sync((old / path).parent)
            _sync((root / path).parent)
        if value.get("old_format") is None:
            (_area(root) / "format.json").unlink(missing_ok=True)
            _sync(_area(root))
        else:
            _write(_area(root) / "format.json", value["old_format"])
        value["phase"] = "rolled_back"
        _write(_area(root) / "restore.json", value)
        return {"status": "rolled_back"}


def authorize(root, nonce):
    if not isinstance(nonce, str) or not _ID.fullmatch(nonce):
        raise ValueError("启动身份无效")
    area = _area(root)
    journal = _read(area / "restore.json")
    if journal and journal.get("phase") not in {"restored", "rolled_back"}:
        raise ValueError("请先撤销未完成的恢复")
    fmt = _read(area / "format.json")
    if fmt and fmt.get("data_format") != FORMAT:
        raise ValueError("请先安装匹配数据格式的程序版本，尚未授权启动")
    state = _marker(root) or {"protocol": PROTOCOL, "created_at": _now()}
    if state.get("source_release") and state["source_release"] != release_info() and not state.get("backup_id"):
        raise ValueError("更新前恢复点尚未完成，不能启动不同版本；请先重试备份或安装原版本")
    if state.get("backup_id"):
        verify_backup(root, state["backup_id"])
    state.update(phase="authorized", nonce_sha256=hashlib.sha256(nonce.encode()).hexdigest(), target_release=release_info())
    _write(area / "state.json", state)
    return {"status": "authorized"}


def begin_activation(root, nonce):
    release_info()
    fmt = _read(Path(root) / ".maintenance/format.json")
    if fmt and fmt.get("data_format") != FORMAT:
        raise MaintenanceRequired("当前版本不支持此数据格式，请先安装匹配版本")
    state = _marker(root)
    if not state or state["phase"] == "completed":
        require_startable(root)
        return False
    if (state["phase"] != "authorized" or state.get("nonce_sha256") != hashlib.sha256(nonce.encode()).hexdigest()
            or state.get("target_release") != release_info()):
        raise MaintenanceRequired("更新准备或恢复尚未确认，请打开备份与恢复窗口")
    state.pop("nonce_sha256", None)
    state["phase"] = "activating"
    _write(_area(root) / "state.json", state)
    return True


def activation_ready(root):
    area = _area(root)
    state = _marker(root)
    _write(area / "format.json", {"data_format": FORMAT, "release": release_info()})
    if state and state["phase"] == "activating":
        state.update(phase="completed", completed_at=_now())
        _write(area / "state.json", state)


def command(root, action, *, backup_id=None, confirmed=False, nonce=None):
    root = Path(root)
    # CLI holds this lock for read-only status too, refusing to inspect an active Store.
    from .instance_lock import ProfileLease
    with ProfileLease(root):
        if action == "status":
            with InstanceLock(root / "data", maintenance=True):
                return status(root)
        if action == "verify":
            with InstanceLock(root / "data", maintenance=True):
                return {"status": "verified", "backup": _summary(verify_backup(root, backup_id))}
        if action == "backup":
            return {"status": "backed_up", "backup": create_backup(root)}
        if action in {"restore", "rollback", "authorize", "prepare"} and confirmed is not True:
            raise ValueError("此操作需要明确确认")
        if action == "restore":
            return restore_backup(root, backup_id)
        if action == "rollback":
            return rollback_restore(root)
        if action == "authorize":
            with InstanceLock(root / "data", maintenance=True):
                return authorize(root, nonce)
        if action == "prepare":
            return {"status": "prepared", "backup": prepare_update(root)}
        raise ValueError("未知维护操作")
