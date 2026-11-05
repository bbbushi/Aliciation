"""Reset archive journal. Fixed local paths only; startup runs before opening Store.

Incomplete moves roll back. Once a new database exists, ambiguous state blocks
startup rather than moving or deleting an unproven later edit. A completed
installation may be finalized from its DB marker, without a model call.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import uuid
from pathlib import Path

from . import config as paths
from .persona_transaction import atomic_text

NAMES = ("alice.db", "alice.db-wal", "alice.db-shm", "persona")


def journal_path():
    return paths.DATA_DIR / "reset-op.json"


def fingerprint(path):
    if not path.exists():
        return None
    if path.is_symlink() or getattr(path.stat(), "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
        raise ValueError("重置路径含链接，需人工核对")
    if path.is_file():
        return hashlib.sha256(path.read_bytes()).hexdigest()
    result = {}
    for entry in sorted(path.rglob("*")):
        if entry.is_symlink() or getattr(entry.stat(), "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
            raise ValueError("重置目录含链接，需人工核对")
        if entry.is_file():
            result[entry.relative_to(path).as_posix()] = hashlib.sha256(entry.read_bytes()).hexdigest()
    return result


def write(record):
    atomic_text(journal_path(), json.dumps(record, ensure_ascii=False))


def read():
    path = journal_path()
    if not path.exists():
        return None
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
        if (not isinstance(record, dict) or record.get("v") != 1 or not re.fullmatch(r"[0-9a-f]{32}", record["op_id"])
                or not re.fullmatch(r"[0-9]{8}-[0-9]{6}(?:-[0-9]+)?", record["archive"])
                or not isinstance(record.get("files"), dict) or set(record["files"]) != set(NAMES)
                or record.get("phase") not in {"moving", "archived", "installing", "completed", "rolled_back"}
                or any(not isinstance(record.get(key), str) or not re.fullmatch(r"[0-9a-f]{32}", record[key])
                       for key in ("old_epoch", "new_epoch"))):
            raise ValueError()
        plan = record.get("plan")
        if plan is not None:
            if (not isinstance(plan, dict) or not isinstance(plan.get("op_id"), str)
                    or not re.fullmatch(r"imp_(?:[0-9a-f]{32}|[0-9]{8}_[0-9]{6}_[0-9a-f]{6})", plan["op_id"])
                    or plan.get("epoch") != record["old_epoch"]
                    or not isinstance(plan.get("candidate_md"), str)
                    or not isinstance(plan.get("adopted"), list)):
                raise ValueError()
            from .persona import validate_import_plan
            validate_import_plan(plan)
            if record.get("plan_sha256") != hashlib.sha256(json.dumps(
                    plan, ensure_ascii=False, sort_keys=True).encode()).hexdigest():
                raise ValueError()
        return record
    except (ValueError, KeyError, TypeError) as exc:
        raise ValueError("重置操作日志损坏；请保留两版数据并人工恢复，尚未开放服务") from exc


def require_clear():
    record = read()
    if record and record["phase"] not in {"completed", "rolled_back"}:
        raise ValueError("上次重置尚未恢复；请重启完成恢复或按操作记录人工恢复，不能重新导入覆盖")


def begin(dest, epoch, plan):
    previous = read()
    if previous and previous["phase"] not in {"completed", "rolled_back"}:
        raise ValueError("上次重置尚未恢复")
    if previous:
        atomic_text(paths.DATA_DIR / "reset-history" / (previous["op_id"] + ".json"),
                    json.dumps(previous, ensure_ascii=False))
    record = {"v": 1, "op_id": uuid.uuid4().hex, "old_epoch": epoch,
              "new_epoch": uuid.uuid4().hex, "archive": dest.name,
              "phase": "moving", "plan": plan,
              "plan_sha256": hashlib.sha256(json.dumps(plan, ensure_ascii=False, sort_keys=True).encode()).hexdigest(),
              "files": {name: fingerprint(paths.DATA_DIR / name) for name in NAMES}}
    write(record)
    return record


def before_open():
    record = read()
    if not record or record["phase"] in {"completed", "rolled_back"}:
        return None
    dest = paths.DATA_DIR / "archive" / record["archive"]
    if record["phase"] == "moving":
        # Validate the entire set before restoring any path. No PID/path guessing.
        restores = []
        for name, expected in record["files"].items():
            src, saved = paths.DATA_DIR / name, dest / name
            if expected is None:
                if src.exists() or saved.exists():
                    raise ValueError("重置恢复遇到后来新增文件；请人工核对")
            elif src.exists() and not saved.exists() and fingerprint(src) == expected:
                continue
            elif not src.exists() and saved.exists() and fingerprint(saved) == expected:
                restores.append((saved, src))
            else:
                raise ValueError("归档两侧内容不明确；请保留现场人工恢复，尚未开放服务")
        for saved, src in restores:
            os.replace(saved, src)
        record["phase"] = "rolled_back"
        write(record)
        return None
    if record["phase"] not in {"archived", "installing"}:
        raise ValueError("重置阶段未知，尚未开放服务")
    # The old archive must remain exact before accepting any forward recovery.
    for name, expected in record["files"].items():
        if fingerprint(dest / name) != expected:
            raise ValueError("旧版归档已变化；不能自动完成重置")
    return record


def after_open(store, cfg, record):
    if record is None:
        return
    from . import persona
    epoch = store.meta_get("session_epoch")
    if epoch and epoch != record["new_epoch"]:
        raise ValueError("重置恢复发现不同代际数据库；拒绝覆盖")
    if not epoch:
        # Only a demonstrably fresh database can receive the recorded identity.
        if store.conn.execute("SELECT 1 FROM turns LIMIT 1").fetchone() or store.conn.execute("SELECT 1 FROM memories LIMIT 1").fetchone():
            raise ValueError("重置新库身份不明确；请人工恢复")
        store.meta_set("session_epoch", record["new_epoch"])
    plan = record.get("plan")
    if plan:
        persona.recover_imports(store)
        if not persona.import_plan_committed(store, plan):
            # No retry of a partly attempted installation. Its journal contains
            # precise recovery evidence; a later manual edit always wins.
            attempted = paths.PERSONA_DIR / "imports" / plan["op_id"] / "op.json"
            if attempted.exists() or paths.PERSONA_FILE.exists():
                raise ValueError("重置安装未完成；保留归档与导入记录，需人工恢复旧版")
            record["phase"] = "installing"
            write(record)
            persona.commit_import_plan(store, cfg, plan,
                archived_from=paths.DATA_DIR / "archive" / record["archive"], archived=True)
    elif paths.PERSONA_FILE.exists():
        raise ValueError("清空会话恢复发现后来的人格文件；请人工核对")
    record["phase"] = "completed"
    write(record)


def complete():
    record = read()
    if record and record["phase"] in {"archived", "installing"}:
        record["phase"] = "completed"
        write(record)
