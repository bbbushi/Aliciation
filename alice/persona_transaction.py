"""Recoverable persona installation; no model calls and no implicit DB commits.

The DB marker is committed with all inserted priors and metadata. File journals
are atomic, but do not claim power-loss durability across SQLite and the FS.
"""
from __future__ import annotations

import hashlib
import heapq
import json
import math
import os
import re
import stat
import time
from pathlib import Path

from . import config as paths

KIND = "persona_import_v2"
TERMINAL = {"completed", "rolled_back", "superseded", "aborted"}
OP_ID = re.compile(r"imp_(?:[0-9a-f]{32}|[0-9]{8}_[0-9]{6}_[0-9a-f]{6})\Z")


def validate_op_id(value) -> str:
    if not isinstance(value, str) or not OP_ID.fullmatch(value):
        raise ValueError("人格安装操作身份无效")
    return value


def digest(text: str | None) -> str | None:
    return None if text is None else hashlib.sha256(text.encode("utf-8")).hexdigest()


def atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as stream:
        stream.write(text)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(tmp, path)


def write_record(path: Path, record: dict) -> None:
    atomic_text(path, json.dumps(record, ensure_ascii=False, indent=1))


def current_text() -> str | None:
    return paths.PERSONA_FILE.read_text(encoding="utf-8") if paths.PERSONA_FILE.exists() else None


def _set_meta(conn, key: str, value) -> None:
    if value is None:
        conn.execute("DELETE FROM meta WHERE key=?", (key,))
    else:
        conn.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) "
                     "DO UPDATE SET value=excluded.value", (key, str(value)))


def _row_digest(row) -> str:
    return digest(json.dumps(dict(row), ensure_ascii=False, sort_keys=True))


def _begin(store) -> None:
    # Owning a real outer transaction is essential: an outermost SAVEPOINT can
    # commit on RELEASE, and `with conn` does not itself issue BEGIN.
    if store.conn.in_transaction:
        raise ValueError("人格安装不能嵌入未完成的数据库事务")
    store.conn.execute("BEGIN IMMEDIATE")


def _plain_stat(path: Path):
    info = path.stat(follow_symlinks=False)
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise ValueError("人格记录路径含链接或重解析点")
    return info


def public_status(store, limit: int = 100) -> dict:
    """Bounded body-free projection; no recovery, model, counters or DB commit."""
    limit = max(1, min(100, limit))
    deadline, maximum = time.monotonic() + 1.0, 1024 * 1024
    root = paths.PERSONA_DIR / "imports"
    heap, items, scanned, incomplete = [], [], 0, False
    try:
        _plain_stat(paths.PERSONA_DIR)
        _plain_stat(root)
        with os.scandir(root) as entries:
            for entry in entries:
                if time.monotonic() >= deadline:
                    incomplete = True
                    break
                try:
                    validate_op_id(entry.name)
                    info = _plain_stat(Path(entry.path))
                    if not stat.S_ISDIR(info.st_mode):
                        continue
                    journal = Path(entry.path) / "op.json"
                    info = _plain_stat(journal)
                    if not stat.S_ISREG(info.st_mode):
                        continue
                    scanned += 1
                    candidate = (info.st_mtime_ns, entry.name, journal)
                    if len(heap) < limit:
                        heapq.heappush(heap, candidate)
                    elif candidate > heap[0]:
                        heapq.heapreplace(heap, candidate)
                except FileNotFoundError:
                    continue  # Preparation-only directory has no operation journal.
                except (OSError, ValueError):
                    incomplete = True
    except FileNotFoundError:
        return {"items": [], "truncated": False, "scan_incomplete": False}
    except (OSError, ValueError):
        return {"items": [], "truncated": True, "scan_incomplete": True}
    epoch = store.meta_get("session_epoch")
    for _, op_id, journal in heapq.nlargest(limit, heap):
        if time.monotonic() >= deadline:
            incomplete = True
            break
        try:
            info = _plain_stat(journal)
            if info.st_size > maximum:
                raise ValueError()
            with journal.open("rb") as stream:
                payload = stream.read(maximum + 1)
            if len(payload) > maximum:
                raise ValueError()
            rec = json.loads(payload.decode("utf-8"))
            if not isinstance(rec, dict):
                raise ValueError()
            row = store.conn.execute(
                "SELECT length(CAST(value AS BLOB)) AS bytes, "
                "CASE WHEN length(CAST(value AS BLOB))<=? THEN value ELSE NULL END AS value "
                "FROM meta WHERE key=?", (maximum, "persona_import:" + op_id)).fetchone()
            if row is not None and row["bytes"] > maximum:
                raise ValueError()
            marker = json.loads(row["value"]) if row is not None else None
            actual = marker if isinstance(marker, dict) else rec
            phase = rec.get("phase")
            known_phases = TERMINAL | {"prepared", "db_committed", "file_replaced"}
            items.append({"op_id": op_id, "phase": phase if phase in known_phases else "unknown",
                          "journal_version": 2 if rec.get("kind") == KIND else 1,
                          "epoch_matches": rec.get("epoch") == epoch,
                          "same_epoch": rec.get("epoch") == epoch,
                          "epoch": rec.get("epoch") if re.fullmatch(r"[0-9a-f]{32}", str(rec.get("epoch"))) else None,
                          "db_marker_present": row is not None,
                          "inserted_count": len(actual.get("inserted", [])),
                          "inserted_ids": [entry["id"] for entry in actual.get("inserted", [])
                                           if isinstance(entry, dict) and type(entry.get("id")) is int],
                          "before_sha256": rec.get("before_sha256") if re.fullmatch(r"[0-9a-f]{64}", str(rec.get("before_sha256"))) else None,
                          "after_sha256": rec.get("after_sha256") if re.fullmatch(r"[0-9a-f]{64}", str(rec.get("after_sha256"))) else None})
        except (OSError, ValueError, TypeError):
            items.append({"op_id": op_id, "phase": "unreadable"})
    return {"items": items, "truncated": scanned > limit or incomplete, "scan_incomplete": incomplete}


def recover(store) -> None:
    root = paths.PERSONA_DIR / "imports"
    for journal in sorted(root.glob("*/op.json")):
        try:
            rec = json.loads(journal.read_text(encoding="utf-8"))
            if not isinstance(rec, dict):
                raise ValueError("invalid record")
        except (OSError, ValueError) as exc:
            raise ValueError("人格安装记录损坏；请先核对导入档案，尚未开放聊天") from exc
        if rec.get("phase") in TERMINAL:
            # Completed legacy records have no v2 marker; do not reinterpret
            # them as a migration. Historical completed files may legitimately
            # differ after manual edits or growth, so never replay them.
            if rec.get("kind") == KIND and (
                    rec.get("op_id") != journal.parent.name or not rec.get("epoch")
                    or not re.fullmatch(r"[0-9a-f]{64}", str(rec.get("after_sha256")))):
                raise ValueError("人格安装终态记录损坏；需人工核对")
            continue
        if rec.get("kind") != KIND:
            raise ValueError("旧版未完成人格安装缺少恢复证据；需人工核对，不能覆盖当前人格")
        if (rec.get("phase") not in {"prepared", "db_committed", "file_replaced"}
                or not re.fullmatch(r"[0-9a-f]{64}", str(rec.get("after_sha256")))
                or (rec.get("before_sha256") is not None
                    and not re.fullmatch(r"[0-9a-f]{64}", str(rec["before_sha256"])))):
            raise ValueError("人格安装阶段或摘要无效；需人工核对")
        if rec.get("epoch") != store.meta_get("session_epoch"):
            raise ValueError("人格安装记录与当前会话不符；拒绝跨代际恢复")
        op_id = validate_op_id(rec.get("op_id"))
        if op_id != journal.parent.name:
            raise ValueError("人格安装记录身份不一致")
        marker_text = store.meta_get("persona_import:" + op_id)
        try:
            marker = json.loads(marker_text) if marker_text else None
            if marker_text and (not isinstance(marker, dict)
                    or not isinstance(marker.get("inserted"), list)
                    or not isinstance(marker.get("before_meta"), dict)
                    or not isinstance(marker.get("after_meta"), dict)
                    or set(marker["before_meta"]) != {"persona_version", "persona_seeded_at", "last_evolve_at"}
                    or set(marker["after_meta"]) != set(marker["before_meta"])):
                raise ValueError()
            if marker:
                values = marker["after_meta"]
                if (type(values["persona_version"]) is not int or values["persona_version"] < 1
                        or any(type(values[key]) not in (int, float) or not math.isfinite(values[key])
                               or values[key] < 0 for key in ("persona_seeded_at", "last_evolve_at"))
                        or any(value is not None and not isinstance(value, str)
                               for value in marker["before_meta"].values())):
                    raise ValueError()
                ids = []
                for item in marker["inserted"]:
                    if (not isinstance(item, dict) or type(item.get("id")) is not int
                            or item["id"] <= 0 or not re.fullmatch(r"[0-9a-f]{64}", str(item.get("sha256")))):
                        raise ValueError()
                    ids.append(item["id"])
                if len(ids) != len(set(ids)):
                    raise ValueError()
        except (ValueError, TypeError) as exc:
            raise ValueError("人格安装数据库标志损坏；需人工核对") from exc
        if marker and any(marker.get(k) != rec.get(k)
                          for k in ("kind", "epoch", "op_id", "before_sha256", "after_sha256")):
            raise ValueError("人格安装数据库标志与文件记录不一致")
        if marker:
            # A crash immediately after DB COMMIT leaves a prepared file journal
            # with no IDs. The atomic marker carries the authoritative write set.
            rec.update({key: marker[key] for key in ("inserted", "before_meta", "after_meta")})
        now_hash = digest(current_text())
        if marker and now_hash == rec["after_sha256"]:
            # Proven commit: exact new file AND marker committed with priors.
            rec["phase"] = "completed"
        else:
            if marker:
                _begin(store)
                try:
                    for item in marker["inserted"]:
                        row = store.conn.execute("SELECT * FROM memories WHERE id=?", (item["id"],)).fetchone()
                        if row is not None and _row_digest(row) != item["sha256"]:
                            raise ValueError("导入新增先验已发生后续变化；需人工核对，不自动删除")
                    for item in marker["inserted"]:
                        store.conn.execute("DELETE FROM memories WHERE id=?", (item["id"],))
                    for key, value in marker["after_meta"].items():
                        if store.meta_get(key) == str(value):
                            _set_meta(store.conn, key, marker["before_meta"][key])
                    _set_meta(store.conn, "persona_import:" + op_id, None)
                    store.conn.commit()
                except BaseException:
                    store.conn.rollback()
                    raise
            # Never write an old snapshot over a later manual edit. With no DB
            # marker a new file is contradictory, not a completed installation.
            if not marker and now_hash == rec["after_sha256"] and now_hash != rec["before_sha256"]:
                raise ValueError("人格文件已更新但无同事务先验证据；需人工恢复")
            rec["phase"] = "rolled_back" if now_hash == rec["before_sha256"] else "superseded"
        write_record(journal, rec)


def install(store, op_id: str, candidate: str, before: str | None,
            priors: list[dict], meta_priors: tuple) -> None:
    validate_op_id(op_id)
    recover(store)
    if current_text() != before:
        raise ValueError("人格在准备后已变化；旧导入计划未提交")
    epoch = store.meta_get("session_epoch")
    if not epoch:
        raise ValueError("人格安装缺少会话代际")
    root = paths.PERSONA_DIR / "imports" / op_id
    journal = root / "op.json"
    if journal.exists():
        raise ValueError("人格安装操作身份已使用；不能重放旧安装")
    root.mkdir(parents=True, exist_ok=True)
    atomic_text(root / "candidate.md", candidate)
    if before is not None:
        atomic_text(root / "previous_persona.md", before)
    stamp = time.time()
    before_meta = {k: store.meta_get(k) for k in
                   ("persona_version", "persona_seeded_at", "last_evolve_at")}
    after_meta = {"persona_version": int(before_meta["persona_version"] or 0) + 1,
                  "persona_seeded_at": stamp, "last_evolve_at": stamp}
    rec = {"kind": KIND, "op_id": op_id, "epoch": epoch, "phase": "prepared",
           "before_sha256": digest(before), "after_sha256": digest(candidate),
           "before_exists": before is not None, "before_meta": before_meta,
           "after_meta": after_meta, "inserted": []}
    write_record(journal, rec)
    _begin(store)
    try:
        for item in priors:
            mid = store.insert_memory(**item)
            row = store.conn.execute("SELECT * FROM memories WHERE id=?", (mid,)).fetchone()
            rec["inserted"].append({"id": mid, "sha256": _row_digest(row)})
        for title, content, keywords in meta_priors:
            if store.find_by_title(title) is None:
                mid = store.insert_memory(title=title, content=content, keywords=keywords,
                                          layer="longterm", kind="stance")
                row = store.conn.execute("SELECT * FROM memories WHERE id=?", (mid,)).fetchone()
                rec["inserted"].append({"id": mid, "sha256": _row_digest(row)})
        for key, value in after_meta.items():
            _set_meta(store.conn, key, value)
        _set_meta(store.conn, "persona_import:" + op_id, json.dumps(rec, ensure_ascii=False))
        store.conn.commit()
    except BaseException:
        store.conn.rollback()
        recover(store)
        raise
    try:
        rec["phase"] = "db_committed"
        write_record(journal, rec)
        if current_text() != before:
            raise ValueError("人格在提交期间被编辑；保留后来的编辑")
        atomic_text(paths.PERSONA_FILE, candidate)
        rec["phase"] = "file_replaced"
        write_record(journal, rec)
        rec["phase"] = "completed"
        write_record(journal, rec)
    except BaseException:
        recover(store)
        # File + same-transaction marker prove success even when only the last
        # journal write failed. Do not invite another paid import/new op_id.
        marker_text = store.meta_get("persona_import:" + op_id)
        if marker_text and digest(current_text()) == digest(candidate):
            marker = json.loads(marker_text)
            if (marker.get("epoch") == epoch and marker.get("op_id") == op_id
                    and marker.get("after_sha256") == digest(candidate)):
                return
        raise
