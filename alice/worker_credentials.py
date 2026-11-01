"""Explicit GUI provisioning of the dedicated, current-user DSH bridge secret.

Only ciphertext is stored. This is not protection from another process running
as the same Windows user. No model/DSH profile/ordinary Alice config is read.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager
import ctypes
import json
import os
from pathlib import Path
import re
import secrets
import stat
import tempfile

from .channels import ChannelError

FORMAT = 'alice-dsh-bridge-credential-v1'
SECRET_FORMAT = 'alice-dsh-bridge-secret-v1'
ENTROPY = b'Alice.DSH.Bridge.Credential.v1'
MAX_FILE = 8192
MAX_CIPHER = 4096
TOKEN = re.compile(r'[A-Za-z0-9_-]{43}\Z')
IDENTITY = re.compile(r'[0-9a-f]{64}\Z')


class CredentialError(ChannelError):
    def __init__(self, code='credential_unavailable'):
        super().__init__(409, code, {
            'credential_unsupported': '本机自动令牌目前支持 Windows；可使用高级手动配对。',
            'credential_busy': '另一项令牌设置正在进行，请稍后再试。',
            'credential_invalid': '已保存的令牌无法使用；请关闭授权后明确选择更换令牌。',
            'credential_unsafe_path': '令牌目录或文件类型不符合要求，未写入或读取令牌。',
        }.get(code, '本机令牌暂不可用，未显示凭据或原始系统错误。'))


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise CredentialError('credential_invalid')
        result[key] = value
    return result


def _json(raw):
    try:
        return json.loads(raw.decode('ascii'), object_pairs_hook=_unique_object)
    except (ValueError, UnicodeError, RecursionError):
        raise CredentialError('credential_invalid') from None


def _dpapi(raw: bytes, *, decrypt: bool) -> bytes:
    if os.name != 'nt':
        raise CredentialError('credential_unsupported')
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [('cbData', wintypes.DWORD), ('pbData', ctypes.POINTER(ctypes.c_ubyte))]

    data = ctypes.create_string_buffer(raw)
    entropy = ctypes.create_string_buffer(ENTROPY)
    source = Blob(len(raw), ctypes.cast(data, ctypes.POINTER(ctypes.c_ubyte)))
    optional = Blob(len(ENTROPY), ctypes.cast(entropy, ctypes.POINTER(ctypes.c_ubyte)))
    output = Blob()
    crypt = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    operation = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    operation.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p if decrypt else wintypes.LPCWSTR,
                          ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                          wintypes.DWORD, ctypes.POINTER(Blob)]
    operation.restype = wintypes.BOOL
    try:
        # CurrentUser is the absence of CRYPTPROTECT_LOCAL_MACHINE. Never prompt.
        if not operation(ctypes.byref(source), None, ctypes.byref(optional), None, None, 1,
                         ctypes.byref(output)):
            raise CredentialError('credential_invalid' if decrypt else 'credential_unavailable')
        if not output.pbData or not 0 < output.cbData <= (1024 if decrypt else MAX_CIPHER):
            raise CredentialError('credential_invalid')
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        ctypes.memset(data, 0, ctypes.sizeof(data))
        if output.pbData:
            ctypes.memset(output.pbData, 0, output.cbData)
            kernel.LocalFree(ctypes.cast(output.pbData, ctypes.c_void_p))


def _regular(info, *, directory=False):
    if (getattr(info, 'st_file_attributes', 0) & 0x400 or stat.S_ISLNK(info.st_mode)
            or (not stat.S_ISDIR(info.st_mode) if directory else
                not stat.S_ISREG(info.st_mode) or info.st_nlink != 1)):
        raise CredentialError('credential_unsafe_path')


class ManagedCredential:
    """Constructing or reading metadata never creates files or decrypts a secret."""
    def __init__(self):
        self._invalid_fingerprint = None

    def _directory(self, *, create=False):
        if os.name != 'nt':
            raise CredentialError('credential_unsupported')
        raw = os.environ.get('LOCALAPPDATA', '')
        if not re.match(r'^[A-Za-z]:[\\/]', raw) or '\x00' in raw:
            raise CredentialError('credential_unsafe_path')
        base = Path(raw)
        if not base.is_absolute():
            raise CredentialError('credential_unsafe_path')
        # Validate all existing ancestors, not just the final private directory.
        for part in reversed((base, *base.parents)):
            _regular(part.lstat(), directory=True)
        if os.path.normcase(str(base.resolve(strict=True))) != os.path.normcase(os.path.normpath(str(base))):
            raise CredentialError('credential_unsafe_path')
        directory = base
        for name in ('Alice', 'dsh-bridge'):
            directory /= name
            if create:
                directory.mkdir(mode=0o700, exist_ok=True)
            _regular(directory.lstat(), directory=True)
        return directory

    def _descriptor(self, directory):
        path = directory / 'credential.json'
        before = path.lstat()
        _regular(before)
        if not 0 < before.st_size <= MAX_FILE:
            raise CredentialError('credential_invalid')
        with path.open('rb') as stream:
            info = os.fstat(stream.fileno())
            _regular(info)
            if (before.st_dev, before.st_ino) != (info.st_dev, info.st_ino):
                raise CredentialError('credential_unsafe_path')
            raw = stream.read(MAX_FILE + 1)
        if len(raw) > MAX_FILE:
            raise CredentialError('credential_invalid')
        value = _json(raw)
        if (not isinstance(value, dict) or set(value) != {'format', 'protection', 'credential_id', 'ciphertext'}
                or value['format'] != FORMAT or value['protection'] != 'dpapi-current-user'
                or not isinstance(value['credential_id'], str) or not IDENTITY.fullmatch(value['credential_id'])
                or not isinstance(value['ciphertext'], str)):
            raise CredentialError('credential_invalid')
        try:
            cipher = base64.b64decode(value['ciphertext'], validate=True)
        except (ValueError, UnicodeError):
            raise CredentialError('credential_invalid') from None
        if (not 1 <= len(cipher) <= MAX_CIPHER
                or base64.b64encode(cipher).decode('ascii') != value['ciphertext']):
            raise CredentialError('credential_invalid')
        return value, cipher

    @staticmethod
    def _fingerprint(directory):
        info = (directory / 'credential.json').lstat()
        return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)

    def metadata(self):
        if os.name != 'nt':
            return {'supported': False, 'configured': False, 'state': 'unsupported'}
        configured = False
        try:
            directory = self._directory()
            # Invalid ordinary files are recoverable only by explicit rotation.
            info = (directory / 'credential.json').lstat()
            _regular(info)
            configured = True
            self._descriptor(directory)
            state = 'unavailable' if self._fingerprint(directory) == self._invalid_fingerprint else 'ready'
        except FileNotFoundError:
            state = 'missing'
        except (OSError, CredentialError):
            state = 'unavailable'
        return {'supported': True, 'configured': configured, 'state': state}

    @contextmanager
    def _guard(self, directory):
        """Stable sidecar, not the replaced data file; OS releases lock on crash."""
        from ctypes import wintypes

        class FileInfo(ctypes.Structure):
            _fields_ = [('attributes', wintypes.DWORD), ('created', wintypes.FILETIME),
                        ('accessed', wintypes.FILETIME), ('written', wintypes.FILETIME),
                        ('volume', wintypes.DWORD), ('size_high', wintypes.DWORD),
                        ('size_low', wintypes.DWORD), ('links', wintypes.DWORD),
                        ('index_high', wintypes.DWORD), ('index_low', wintypes.DWORD)]

        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                      wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        kernel.GetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.POINTER(FileInfo)]
        kernel.GetFileInformationByHandle.restype = wintypes.BOOL
        path = directory / 'credential.lock'
        # No sharing, OPEN_ALWAYS, FILE_FLAG_OPEN_REPARSE_POINT: no symlink follow.
        handle = kernel.CreateFileW(str(path), 0xC0000000, 0, None, 4, 0x00200000 | 0x80, None)
        if handle == ctypes.c_void_p(-1).value:
            raise CredentialError('credential_busy')
        try:
            info = FileInfo()
            if (not kernel.GetFileInformationByHandle(handle, ctypes.byref(info))
                    or info.attributes & (0x400 | 0x10) or info.links != 1):
                raise CredentialError('credential_unsafe_path')
            yield
        finally:
            kernel.CloseHandle(handle)

    def provision(self, operation_id, *, rotate=False):
        if not isinstance(operation_id, str) or not IDENTITY.fullmatch(operation_id):
            raise CredentialError('credential_invalid')
        try:
            directory = self._directory(create=True)
            with self._guard(directory):
                value, cipher = None, None
                try:
                    value, cipher = self._descriptor(directory)
                except FileNotFoundError:
                    pass
                except CredentialError as exc:
                    if not rotate or exc.code == 'credential_unsafe_path':
                        raise
                if value is not None and (not rotate or value['credential_id'] == operation_id):
                    try:
                        secret = _json(_dpapi(cipher, decrypt=True))
                        if (not isinstance(secret, dict) or set(secret) != {'format', 'credential_id', 'token'}
                                or secret['format'] != SECRET_FORMAT or secret['credential_id'] != value['credential_id']
                                or not isinstance(secret['token'], str) or not TOKEN.fullmatch(secret['token'])):
                            raise CredentialError('credential_invalid')
                        self._invalid_fingerprint = None
                        return secret['token']
                    except CredentialError:
                        self._invalid_fingerprint = self._fingerprint(directory)
                        raise
                token = secrets.token_urlsafe(32)
                plain = json.dumps({'format': SECRET_FORMAT, 'credential_id': operation_id, 'token': token},
                                   separators=(',', ':'), ensure_ascii=True).encode('ascii')
                protected = _dpapi(plain, decrypt=False)
                encoded = json.dumps({'format': FORMAT, 'protection': 'dpapi-current-user',
                                      'credential_id': operation_id, 'ciphertext': base64.b64encode(protected).decode('ascii')},
                                     separators=(',', ':'), ensure_ascii=True).encode('ascii')
                # Same-directory atomic replace; plaintext never enters the file.
                fd, temporary = tempfile.mkstemp(prefix='.credential-', suffix='.tmp', dir=directory)
                try:
                    with os.fdopen(fd, 'wb') as stream:
                        stream.write(encoded)
                        stream.flush()
                        os.fsync(stream.fileno())
                    if self._directory() != directory:
                        raise CredentialError('credential_unsafe_path')
                    target = directory / 'credential.json'
                    try:
                        _regular(target.lstat())
                    except FileNotFoundError:
                        pass
                    os.replace(temporary, target)
                finally:
                    Path(temporary).unlink(missing_ok=True)
                self._invalid_fingerprint = None
                return token
        except CredentialError:
            raise
        except (OSError, ValueError, TypeError):
            raise CredentialError() from None
