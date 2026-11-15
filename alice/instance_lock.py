"""Lifetime OS lock shared by service and CLI writers; no PID-based reclamation."""
from __future__ import annotations

import errno
import os
from pathlib import Path
import stat
import threading

_guard = threading.RLock()
_owned: set[str] = set()
_profiles: dict[str, tuple[int, int]] = {}


def _lock_file(path: Path) -> int:
    fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or path.is_symlink() or info.st_nlink != 1:
            raise OSError("实例锁必须是独立普通文件")
        if os.name == "nt":
            import msvcrt
            if info.st_size == 0:
                os.write(fd, b"\0")
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except BaseException as exc:
        os.close(fd)
        if isinstance(exc, OSError) and exc.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
            raise InstanceBusy("此数据目录已有 Alice 实例，请先正常关闭后再操作") from None
        raise


class ProfileLease:
    """Stable outer lock retained across Store closure and offline maintenance.

    Reentrant only within this process; the inner data lock still permits one
    Store. Neither lock inode may be moved or replaced by a restore operation.
    """
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.key = os.path.normcase(str(self.root))
        self.active = False

    def acquire(self):
        with _guard:
            if self.active:
                raise RuntimeError("profile lease already acquired")
            current = _profiles.get(self.key)
            if current is None:
                self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
                current = (_lock_file(self.root / ".alice-profile.lock"), 0)
            _profiles[self.key] = (current[0], current[1] + 1)
            self.active = True
        return self

    def release(self):
        with _guard:
            if not self.active:
                return
            fd, count = _profiles[self.key]
            if count == 1:
                del _profiles[self.key]
                os.close(fd)
            else:
                _profiles[self.key] = (fd, count - 1)
            self.active = False

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *_exc):
        self.release()


class InstanceBusy(RuntimeError):
    pass


class InstanceLock:
    """Lock the canonical data directory, independent of port/configuration.

    The lock inode is never unlinked: deleting it permits a second writer to
    lock a different inode. OS locks, not file contents, establish ownership.
    """

    def __init__(self, data_dir: Path, *, maintenance: bool = False):
        if Path(data_dir).is_symlink():
            raise ValueError("数据目录不能是符号链接")
        self.directory = Path(data_dir).resolve()
        # Public ChatService(db_path=...) also supports standalone database dirs.
        self.profile_root = self.directory.parent if self.directory.name == "data" else self.directory
        self.path = self.directory / ".alice-instance.lock"
        self._key = os.path.normcase(str(self.directory))
        self._fd: int | None = None
        self._profile: ProfileLease | None = None
        self._maintenance = maintenance

    def acquire(self) -> InstanceLock:
        lease = ProfileLease(self.profile_root).acquire()
        try:
            if not self._maintenance:
                from .profile_maintenance import require_startable
                require_startable(lease.root)
            self._acquire_inner()
            self._profile = lease
            return self
        except BaseException:
            lease.release()
            raise

    def _acquire_inner(self):
        with _guard:
            if self._fd is not None or self._key in _owned:
                raise InstanceBusy("此数据目录已有 Alice 实例，请先正常关闭后再操作")
            self.directory.mkdir(parents=True, exist_ok=True)
            fd = _lock_file(self.path)
            self._fd = fd
            _owned.add(self._key)

    def release(self) -> None:
        with _guard:
            if self._fd is None:
                return
            fd, self._fd = self._fd, None
            try:
                # Closing the non-inheritable descriptor releases the OS lock.
                os.close(fd)
            finally:
                _owned.discard(self._key)
                if self._profile is not None:
                    self._profile.release()
                    self._profile = None

    def __enter__(self) -> InstanceLock:
        return self.acquire()

    def __exit__(self, *_exc) -> None:
        self.release()
