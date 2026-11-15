"""GUI-owned preparation archive recycling, independent of material semantics.

Moving an archive never installs/cancels a candidate or changes persona state.
Callers serialize through ChatService's control and Store locks.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re

from .persona_checkpoint import read_record

_TOKEN = re.compile(r"mat_[0-9a-f]{32}\Z")


def _revision(record):
    raw = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def _read(path, token):
    record = read_record(path)  # Rejects symlinks, non-files, invalid JSON and oversized records.
    if record.get("token") != token:
        raise ValueError("准备档案身份不一致，请刷新列表")
    return record


def _active(staged_path):
    if not staged_path.exists() and not staged_path.is_symlink():
        return None
    # An unreadable candidate must not accidentally remove its protected archive.
    return read_record(staged_path).get("token")


def status(result, root: Path, staged_path: Path):
    _check_root(root)
    active = _active(staged_path)
    for row in result.get("preparations", []):
        token = row.get("token")
        if not isinstance(token, str) or not _TOKEN.fullmatch(token):
            row.update(can_delete=False, delete_issue="档案身份无效")
            continue
        try:
            record = _read(root / (token + ".json"), token)
            row.update(archive_revision=_revision(record), can_delete=token != active,
                       delete_issue="当前候选，请先取消候选，再删除档案" if token == active else "")
        except (OSError, ValueError):
            row.update(can_delete=False, delete_issue="档案状态无法确认，请刷新列表")
    deleted = root / ".deleted"
    items = []
    if deleted.is_symlink():
        raise ValueError("已删除档案目录不能是链接")
    if deleted.exists() and not deleted.is_dir():
        raise ValueError("已删除档案位置不是目录，请核对本机文件")
    truncated = False
    if deleted.is_dir():
        files = sorted((p for p in deleted.glob("mat_*.json") if _TOKEN.fullmatch(p.stem)),
                       key=lambda p: (p.lstat().st_mtime_ns, p.name), reverse=True)
        truncated = len(files) > 100
        for path in files[:100]:
            try:
                record = _read(path, path.stem)
                items.append({"token": path.stem, "outcome": record.get("outcome"),
                              "created_at": record.get("created_at"), "archive_revision": _revision(record)})
            except (OSError, ValueError):
                continue
    result["deleted_preparations"] = items
    result["deleted_preparations_truncated"] = truncated
    return result


def move(payload, root: Path, staged_path: Path, *, restore=False):
    _check_root(root)
    token, expected = payload.get("token"), payload.get("archive_revision")
    if not isinstance(token, str) or not _TOKEN.fullmatch(token) or not isinstance(expected, str):
        raise ValueError("准备档案身份或版本无效，请刷新列表")
    if _active(staged_path) == token:
        raise ValueError("这是当前活动候选；请先取消候选，再删除准备档案")
    deleted = root / ".deleted"
    if deleted.is_symlink():
        raise ValueError("已删除档案目录不能是链接")
    if deleted.exists() and not deleted.is_dir():
        raise ValueError("已删除档案位置不是目录，请核对本机文件")
    source = (deleted if restore else root) / (token + ".json")
    target = (root if restore else deleted) / (token + ".json")
    try:
        record = _read(source, token)
    except FileNotFoundError:
        raise ValueError("档案已移动或不存在，请刷新列表后核对") from None
    if _revision(record) != expected:
        raise ValueError("准备档案已更新，请刷新列表核对后再操作")
    if target.exists() or target.is_symlink():
        raise ValueError("目标位置已有同名档案，未覆盖，请刷新列表")
    deleted.mkdir(mode=0o700, exist_ok=True)
    source.rename(target)
    # Promote moved records into the bounded recent lists without changing JSON.
    try:
        os.utime(target, None, follow_symlinks=False)
    except OSError:
        return {"restored" if restore else "deleted": True,
                "message": "档案已移动，但最近列表排序未更新；请刷新列表核对"}
    return {"restored" if restore else "deleted": True,
            "message": "准备档案已恢复，未自动启用候选" if restore else "准备档案已移到已删除档案，可恢复"}


def _check_root(root):
    for directory in (root, root.parent, root.parent.parent):
        if directory.is_symlink():
            raise ValueError("准备档案目录不能是链接")
