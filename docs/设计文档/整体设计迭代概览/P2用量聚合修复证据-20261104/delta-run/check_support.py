"""One-session diagnostic support. All application imports come from a copied snapshot."""
from pathlib import Path
import hashlib
import json
import os
import shutil
import sys
import time
import traceback

RUN_ROOT = Path(__file__).resolve().parent
SOURCE = RUN_ROOT / 'source'
LIVE_REPO = Path('D:\\alice').resolve()

def isolated_workspace(suite, case='workspace'):
    target = RUN_ROOT / suite / case
    if target.exists():
        raise RuntimeError('Use a new unique fixture directory: ' + str(target))
    shutil.copytree(SOURCE, target)
    return target

def protect(workspace, *, allow_owned_processes=False):
    workspace = Path(workspace).resolve()
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(workspace))
    attempts = []
    def path(value):
        if not isinstance(value, (str, bytes, os.PathLike)):
            return None
        try:
            return Path(os.fsdecode(value)).resolve()
        except (ValueError, OSError):
            return None
    def check(value, write=False):
        if isinstance(value, (str, bytes, os.PathLike)) and os.fsdecode(value).lower() == os.devnull.lower():
            return  # OS null device used for harmless helper stdio, not a user file.
        p = path(value)
        if p is None:
            return
        if p == LIVE_REPO or LIVE_REPO in p.parents:
            attempts.append('live_repository_access')
            raise PermissionError('Live repository access is blocked in the isolated probe')
        if write and not (p == RUN_ROOT or RUN_ROOT in p.parents):
            attempts.append('write_outside_probe')
            raise PermissionError('Writes are limited to the probe root')
    def audit(event, args):
        if event.startswith('socket.'):
            attempts.append('network_blocked')
            raise PermissionError('Network is disabled for this probe')
        if event in ('subprocess.Popen', 'os.system', 'os.posix_spawn', 'os.spawn'):
            if not allow_owned_processes:
                attempts.append('process_blocked')
                raise PermissionError('Subprocesses are disabled for this suite')
            if event != 'subprocess.Popen':
                raise PermissionError('Only controlled Python Popen helpers are permitted')
            executable, argv = args[0], args[1]
            if isinstance(argv, str):
                import shlex
                argv = [part.strip('"') for part in shlex.split(argv, posix=False)]
            executable = executable or argv[0]
            if Path(executable).resolve() != Path(sys.executable).resolve() or '-c' in argv or '-m' in argv:
                raise PermissionError('Only this Python interpreter may be launched')
            script_paths = [path(x) for x in argv[1:] if isinstance(x, str) and x.endswith('.py')]
            if not script_paths or any(p is None or RUN_ROOT not in p.parents for p in script_paths):
                raise PermissionError('Subprocess script must belong to the probe root')
        if event == 'open':
            name, mode, flags = args
            write = bool((isinstance(mode, str) and any(c in mode for c in 'wax+')) or
                         (isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC)))
            check(name, write)
        elif event in ('os.listdir', 'os.scandir'):
            check(args[0])
        elif event in ('os.remove', 'os.rmdir', 'os.mkdir', 'os.chmod', 'os.utime', 'sqlite3.connect'):
            check(args[0], True)
        elif event in ('os.rename', 'os.link', 'os.symlink'):
            check(args[0], True); check(args[1], True)
    sys.addaudithook(audit)
    return attempts

class Results:
    def __init__(self, suite):
        self.suite, self.items = suite, []
    def check(self, name, callback):
        start = time.monotonic()
        try:
            detail = callback()
            result = {'name': name, 'status': 'passed', 'detail': detail}
        except Exception as exc:
            result = {'name': name, 'status': 'failed', 'exception': type(exc).__name__,
                      'message': str(exc), 'traceback': traceback.format_exc()}
        result['seconds'] = round(time.monotonic() - start, 4)
        self.items.append(result)
        print(json.dumps({k: result[k] for k in ('name','status','seconds')}, ensure_ascii=False), flush=True)
        return result
    def save(self, **extra):
        data = {'suite': self.suite, 'cases': self.items,
                'passed': sum(x['status']=='passed' for x in self.items),
                'failed': sum(x['status']=='failed' for x in self.items), **extra}
        target = RUN_ROOT / self.suite / 'results.json'
        target.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        return data
