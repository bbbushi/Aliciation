"""Worker 输入导入与产物收取：路径包含性、哈希、两阶段发布（契约 §四）。

目录布局：user/worker_tasks/<epoch>/{inputs/<input_id>, <task_id>/<attempt_id>/{input,work,output,collected}}
规则：包含性按 realpath 判定（不做字符串前缀比较）；拒绝符号链接与父目录逃逸；
worker 自报哈希不作为宿主确认；collected 由宿主独占写入。
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from pathlib import Path

ALLOWED_OUTPUT_EXTS = {".txt", ".md", ".csv", ".json", ".png", ".jpg", ".jpeg", ".svg"}
MAX_INPUT_BYTES = 64 * 1024 * 1024
MAX_COLLECT_BYTES = 256 * 1024 * 1024


class ArtifactError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def worker_root(base: Path) -> Path:
    return Path(base) / "worker_tasks"


def ensure_task_dirs(base: Path, epoch: str, task_id: str, attempt_id: str) -> dict[str, Path]:
    root = worker_root(base) / epoch / task_id / attempt_id
    dirs = {name: root / name for name in ("input", "work", "output", "collected")}
    for p in dirs.values():
        p.mkdir(parents=True, exist_ok=True)
    return dirs


# ---------- 输入 ----------

def stage_input(base: Path, epoch: str, *, display_name: str, media: str,
                data: bytes) -> tuple[str, str, dict]:
    """写暂存（临时名→正式名），返回 (input_id, 相对路径, 元数据)。最终文件名由宿主生成。"""
    if not data:
        raise ArtifactError("empty_input", "输入内容为空")
    if len(data) > MAX_INPUT_BYTES:
        raise ArtifactError("input_too_large", f"输入超过 {MAX_INPUT_BYTES} 字节上限")
    safe_media = media if media.replace("/", "").replace("-", "").isalnum() else "application/octet-stream"
    input_id = f"in_{uuid.uuid4().hex[:20]}"
    suffix = Path(display_name or "").suffix.lower()
    suffix = suffix if suffix in ALLOWED_OUTPUT_EXTS else ".bin"
    final_name = f"{input_id}{suffix}"  # 用户文件名只作展示，不作路径
    stage_dir = worker_root(base) / epoch / "inputs" / input_id
    stage_dir.mkdir(parents=True, exist_ok=True)
    tmp = stage_dir / (final_name + ".part")
    tmp.write_bytes(data)
    target = stage_dir / final_name
    os.replace(tmp, target)  # 同目录原子发布
    meta = {"sha256": sha256_file(target), "size": len(data), "media": safe_media,
            "display_name": (display_name or final_name)[:200], "rel_path": str(
                target.relative_to(worker_root(base)))}
    return input_id, meta["rel_path"], meta


def grant_inputs(base: Path, epoch: str, task_id: str, attempt_id: str,
                 input_rows: list[dict]) -> list[dict]:
    """派工时把获准输入复制进任务 input/；返回 worker 可见的 {input_id, file} 句柄。"""
    dirs = ensure_task_dirs(base, epoch, task_id, attempt_id)
    handles = []
    for row in input_rows:
        src = worker_root(base) / row["rel_path"]
        if not src.is_file():
            raise ArtifactError("input_missing", f"输入暂存缺失：{row['input_id']}")
        dst = dirs["input"] / f"{row['input_id']}{src.suffix}"
        shutil.copyfile(src, dst)
        if sha256_file(dst) != row["sha256"]:
            raise ArtifactError("input_corrupt", f"输入复制后哈希不符：{row['input_id']}")
        handles.append({"input_id": row["input_id"], "file": dst.name,
                        "display_name": row["display_name"]})
    return handles


# ---------- 产物收取 ----------

def _contained(output_root: Path, rel: str) -> Path:
    """相对路径 → realpath 包含性校验。字符串前缀比较不可靠（契约 §四）。

    符号链接检查只覆盖输出目录**以内**的路径段：根之上的祖先（如 macOS 的
    /tmp）是宿主文件系统自身形态，逃逸已由 realpath 包含性兜底。
    """
    if not isinstance(rel, str) or not rel.strip():
        raise ArtifactError("bad_path", "产物路径为空")
    p = Path(rel)
    if p.is_absolute() or p.drive or p.root:
        raise ArtifactError("bad_path", "产物必须是相对路径")
    if ".." in p.parts:
        raise ArtifactError("path_escape", "产物路径含父目录跳转")
    target = output_root / p
    real_target, real_root = target.resolve(), output_root.resolve()
    if os.path.commonpath([str(real_target), str(real_root)]) != str(real_root):
        raise ArtifactError("path_escape", "产物逃逸输出目录")
    cur = target
    while cur != output_root and cur != cur.parent:
        if cur.is_symlink():
            raise ArtifactError("symlink", "产物路径含符号链接")
        cur = cur.parent
    if target.exists() and target.is_symlink():
        raise ArtifactError("symlink", "产物路径含符号链接")
    return target


def collect_artifacts(base: Path, epoch: str, task_id: str, attempt_id: str,
                      rel_list: list) -> tuple[list[dict], list[dict]]:
    """收取产物：先校验清单，再复制进 collected/ 并计算宿主哈希。

    返回 (manifest, rejections)。两阶段：先写全量清单文件到 collected/.manifest.json，
    全部成功后发布——部分失败时已复制文件留在暂存命名下不交付。
    """
    dirs = ensure_task_dirs(base, epoch, task_id, attempt_id)
    output_root = dirs["output"]
    if not isinstance(rel_list, list) or not rel_list or len(rel_list) > 64:
        raise ArtifactError("bad_manifest", "产物清单必须是 1~64 项的列表")
    manifest, rejections = [], []
    staged: list[tuple[Path, Path, dict]] = []
    for rel in rel_list:
        try:
            target = _contained(output_root, rel)
            if not target.is_file():
                raise ArtifactError("not_file", "产物不是常规文件")
            size = target.stat().st_size
            if size > MAX_COLLECT_BYTES:
                raise ArtifactError("too_large", f"产物超过 {MAX_COLLECT_BYTES} 字节上限")
            if target.suffix.lower() not in ALLOWED_OUTPUT_EXTS:
                raise ArtifactError("bad_type", f"产物类型不在允许清单：{target.suffix}")
            digest = sha256_file(target)
            staged.append((target, dirs["collected"] / target.name,
                           {"rel_path": target.name, "sha256": digest, "size": size,
                            "media": _media_of(target.suffix)}))
        except ArtifactError as exc:
            rejections.append({"rel_path": str(rel)[:300], "code": exc.code, "message": exc.message})
    if not staged:
        if rejections:
            raise ArtifactError(rejections[0]["code"],
                                f"全部产物被拒；首因：{rejections[0]['message']}")
        raise ArtifactError("nothing_collectable", "产物清单为空")
    for src, dst, meta in staged:
        shutil.copyfile(src, dst)
        if sha256_file(dst) != meta["sha256"]:
            raise ArtifactError("collect_corrupt", f"收取后哈希不符：{meta['rel_path']}")
        manifest.append(meta)
    (dirs["collected"] / ".manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    return manifest, rejections


def _media_of(suffix: str) -> str:
    return {".txt": "text/plain", ".md": "text/markdown", ".csv": "text/csv",
            ".json": "application/json", ".png": "image/png", ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg", ".svg": "image/svg+xml"}.get(suffix, "application/octet-stream")
