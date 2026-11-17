"""只修改 config.toml 的指定配置，校验其余语义与源文本保持不变。"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import tempfile
import tomllib
from pathlib import Path


class ConfigEditError(ValueError):
    """可安全展示给本地 GUI 的配置写入错误。"""


class ConfigConflict(ConfigEditError):
    """文件在读取后被其他编辑者改动。"""


_HEADER = re.compile(r"^[ \t]*\[[^\r\n]+\][ \t]*(?:#.*)?$")
_ASSIGN = re.compile(r"^(?P<prefix>[ \t]*)(?P<key>[A-Za-z0-9_-]+)(?P<eq>[ \t]*=[ \t]*)(?P<rest>.*)$")
_STRING_VALUE = re.compile(
    r'''^(?P<lead>[ \t]*)(?P<value>"(?:\\.|[^"\\])*"|'[^']*')(?P<tail>[ \t]*(?:#.*)?)$'''
)
_BOOL_VALUE = re.compile(r"^(?P<lead>[ \t]*)(?:true|false)(?P<tail>[ \t]*(?:#.*)?)$")


def _read(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return b""
    except OSError as exc:
        raise ConfigEditError("无法读取配置文件，请检查文件权限") from exc


def revision(path: Path) -> str:
    """原始文件摘要；空文件/文件不存在也有稳定版本号。"""
    return hashlib.sha256(_read(path)).hexdigest()


def _parse(data: bytes) -> tuple[str, dict]:
    try:
        text = data.decode("utf-8")
        parsed = tomllib.loads(text) if text.strip() else {}
    except (UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise ConfigEditError("配置文件不是有效的 UTF-8 TOML，请先修正文件") from exc
    return text, parsed


def _split_eol(line: str) -> tuple[str, str]:
    if line.endswith("\r\n"):
        return line[:-2], "\r\n"
    if line.endswith("\n"):
        return line[:-1], "\n"
    return line, ""


def _patched_text(text: str, old: dict, changes: dict[str, str | bool], *, section="llm") -> str:
    """定位普通配置表与单行标量键；不确定时拒绝，绝不重排配置。"""
    lines = text.splitlines(keepends=True)
    eol = "\r\n" if "\r\n" in text else "\n"
    table_header: int | None = None
    section_end: int | None = None
    in_table = False
    assignments: dict[str, int] = {}

    for index, line in enumerate(lines):
        body, _ = _split_eol(line)
        if _HEADER.fullmatch(body):
            if in_table and section_end is None:
                section_end = index
            in_table = bool(re.fullmatch(r"[ \t]*\[" + re.escape(section) + r"\][ \t]*(?:#.*)?", body))
            if in_table:
                if table_header is not None:
                    raise ConfigEditError(f"配置文件存在重复的 [{section}]，请先手工修正")
                table_header = index
            continue
        if not in_table:
            continue
        match = _ASSIGN.fullmatch(body)
        if match and match.group("key") in changes:
            key = match.group("key")
            if key in assignments:
                raise ConfigEditError(f"[{section}] 中有重复的配置项，请先手工修正")
            pattern = _BOOL_VALUE if type(changes[key]) is bool else _STRING_VALUE
            if not pattern.fullmatch(match.group("rest")):
                raise ConfigEditError(f"[{section}] 的 {key} 使用了暂不支持的写法，请先手工调整")
            assignments[key] = index

    existing = old.get(section, {})
    if not isinstance(existing, dict):
        raise ConfigEditError(f"[{section}] 不是配置表，请先手工修正")
    if table_header is None and section in old:
        raise ConfigEditError(f"请先把 [{section}] 改为普通 TOML 表后再用界面保存")
    for key in changes:
        if key in existing and key not in assignments:
            raise ConfigEditError(f"[{section}] 的 {key} 使用了暂不支持的写法，请先手工调整")

    for key, index in assignments.items():
        body, suffix = _split_eol(lines[index])
        match = _ASSIGN.fullmatch(body)
        assert match is not None
        pattern = _BOOL_VALUE if type(changes[key]) is bool else _STRING_VALUE
        value = pattern.fullmatch(match.group("rest"))
        assert value is not None
        lines[index] = (
            match.group("prefix") + key + match.group("eq") + value.group("lead")
            + json.dumps(changes[key], ensure_ascii=False) + value.group("tail") + suffix
        )

    missing = [key for key in changes if key not in assignments]
    if missing:
        added = [f"{key} = {json.dumps(changes[key], ensure_ascii=False)}{eol}" for key in missing]
        if table_header is None:
            if lines and not lines[-1].endswith("\n"):
                lines[-1] += eol
            if lines and lines[-1].strip():
                lines.append(eol)
            lines.append(f"[{section}]{eol}")
            lines.extend(added)
        else:
            position = section_end if section_end is not None else len(lines)
            if position and not lines[position - 1].endswith("\n"):
                lines[position - 1] += eol
            lines[position:position] = added
    return "".join(lines)


def replace_llm_values(path: Path, changes: dict[str, str], expected_revision: str) -> str:
    """校验语义不变后原子替换文件；失败时旧文件保持原样。"""
    allowed = {"base_url", "chat_model", "cheap_model", "api_key"}
    if not changes or not set(changes) <= allowed or not all(isinstance(v, str) for v in changes.values()):
        raise ConfigEditError("配置更新内容无效")
    return _replace_string_values(path, changes, expected_revision, section="llm")


def replace_persona_mode(path: Path, mode: str, expected_revision: str) -> str:
    """只改人格装配模式；保留其余字段、注释和凭据，校验后原子发布。"""
    if mode not in ("full", "registry"):
        raise ConfigEditError("人格装配模式无效")
    return _replace_string_values(path, {"expression_mode": mode}, expected_revision, section="persona")


def _replace_string_values(path: Path, changes: dict[str, str], expected_revision: str, *, section: str) -> str:
    source = _read(path)
    current = hashlib.sha256(source).hexdigest()
    if current != expected_revision:
        raise ConfigConflict("配置文件已被其他操作修改，请刷新设置后重试")
    text, old = _parse(source)
    patched = _patched_text(text, old, changes, section=section)
    try:
        output = patched.encode("utf-8")
    except UnicodeError as exc:
        raise ConfigEditError("配置含有无法保存的字符") from exc
    _, parsed = _parse(output)
    expected = copy.deepcopy(old)
    expected.setdefault(section, {}).update(changes)
    if parsed != expected:
        raise ConfigEditError("配置文件使用了复杂 TOML 写法，界面无法安全保留，请手工编辑")

    return _publish_config(path, output, expected_revision)


def replace_observation_settings(path: Path, *, enabled: bool, metadata: bool,
                                 body_capture: bool, expected_revision: str) -> str:
    """Atomically patch only the three observation booleans, preserving TOML."""
    if any(type(v) is not bool for v in (enabled, metadata, body_capture)):
        raise ConfigEditError("观测设置必须为布尔值")
    if body_capture and not metadata:
        raise ConfigEditError("正文采集需要元数据采集")
    source = _read(path)
    if hashlib.sha256(source).hexdigest() != expected_revision:
        raise ConfigConflict("配置文件已被其他操作修改，请刷新设置后重试")
    patched, old = _parse(source)
    changes = {"plugins": {"context_inspector_enabled": enabled},
               "context": {"observer_enabled": metadata, "observer_body_capture": body_capture}}
    expected = copy.deepcopy(old)
    for section, values in changes.items():
        patched = _patched_text(patched, _parse(patched.encode("utf-8"))[1], values, section=section)
        expected.setdefault(section, {}).update(values)
    output = patched.encode("utf-8")
    if _parse(output)[1] != expected:
        raise ConfigEditError("观测配置使用了复杂TOML写法，无法安全保留，请手工处理")
    return _publish_config(path, output, expected_revision)


def _publish_config(path: Path, output: bytes, expected_revision: str) -> str:
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", prefix="config.toml.", suffix=".tmp", dir=path.parent, delete=False
        ) as stream:
            temporary = Path(stream.name)
            os.chmod(temporary, 0o600)
            stream.write(output)
            stream.flush()
            os.fsync(stream.fileno())
        if revision(path) != expected_revision:
            raise ConfigConflict("配置文件已被其他操作修改，请刷新设置后重试")
        os.replace(temporary, path)
        temporary = None
    except ConfigConflict:
        raise
    except OSError as exc:
        raise ConfigEditError("保存配置失败，请检查文件权限和磁盘空间；原配置仍可使用") from exc
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
    return hashlib.sha256(output).hexdigest()


def replace_channels(path: Path, channels: dict, expected_revision: str) -> str:
    """Replace only ordinary channel tables; never rewrite model credentials."""
    from .config import _channels

    _channels(channels)
    source = _read(path)
    if hashlib.sha256(source).hexdigest() != expected_revision:
        raise ConfigConflict("配置文件已被其他操作修改，请刷新设置后重试")
    text, old = _parse(source)
    eol = "\r\n" if "\r\n" in text else "\n"
    kept = []
    removing = False
    for line in text.splitlines(keepends=True):
        body, _ = _split_eol(line)
        if _HEADER.fullmatch(body):
            removing = bool(re.fullmatch(r"[ \t]*\[channels(?:\.qq_owner)?\][ \t]*(?:#.*)?", body))
        if not removing:
            kept.append(line)
    patched = "".join(kept).rstrip("\r\n") + eol + eol
    patched += "[channels]" + eol
    for key in ("enabled", "protocol_version"):
        patched += f"{key} = {json.dumps(channels[key])}{eol}"
    patched += eol + "[channels.qq_owner]" + eol
    for key, value in channels["qq_owner"].items():
        patched += f"{key} = {json.dumps(value, ensure_ascii=False)}{eol}"
    output = patched.encode("utf-8")
    _, parsed = _parse(output)
    expected = copy.deepcopy(old)
    expected["channels"] = channels
    if parsed != expected:
        raise ConfigEditError("渠道配置使用了复杂 TOML 写法，无法安全保存，请先手工调整")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="wb", prefix="config.toml.", suffix=".tmp",
                                         dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            os.chmod(temporary, 0o600)
            stream.write(output)
            stream.flush()
            os.fsync(stream.fileno())
        if revision(path) != expected_revision:
            raise ConfigConflict("配置文件已被其他操作修改，请刷新设置后重试")
        os.replace(temporary, path)
        temporary = None
    except ConfigConflict:
        raise
    except OSError as exc:
        raise ConfigEditError("渠道配置未能保存，请检查文件权限和磁盘空间") from exc
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
    return hashlib.sha256(output).hexdigest()
