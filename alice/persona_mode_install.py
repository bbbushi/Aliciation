"""Publish an import's configured mode only after the persona commit is proven."""
from __future__ import annotations

import copy
import json
import os
import re

from .config_edit import ConfigConflict, persona_mode_revision, replace_persona_mode, revision
from .persona_transaction import KIND, current_text, digest, validate_op_id, write_record


def journal_path(database):
    return database.with_name(database.name + ".material-mode.json")


def _config_identity(config_path):
    return digest(os.path.normcase(str(config_path.resolve())))


def read_change(database, config_path):
    path = journal_path(database)
    if not path.exists():
        return None
    try:
        if path.is_symlink() or path.stat().st_size > 4096:
            raise ValueError()
        change = json.loads(path.read_text(encoding="utf-8"))
        validate_op_id(change["op_id"])
        if (change["mode"] not in {"full", "registry"}
                or not re.fullmatch(r"[a-f0-9]{32}", change["epoch"])
                or any(not re.fullmatch(r"[a-f0-9]{64}", change[key]) for key in
                       ("config", "before", "after", "persona"))):
            raise ValueError()
        # Previous journals recorded only whole-file hashes. A mode change at
        # that time could only have come from the other supported mode.
        change.setdefault("before_mode", "full" if change["mode"] == "registry" else "registry")
        if change["before_mode"] not in {"full", "registry"}:
            raise ValueError()
    except (OSError, TypeError, ValueError, KeyError) as exc:
        raise ValueError("人格装配恢复记录损坏，请先核对档案") from exc
    if change["config"] != _config_identity(config_path):
        raise ValueError("请使用安装人格时选定的配置文件完成装配恢复")
    return change


def stage(database, config_path, expected_revision, plan):
    path = journal_path(database)
    if path.exists():
        raise ValueError("上次人格装配尚未恢复")
    mode = plan["material_mode"]
    current, before_mode = persona_mode_revision(config_path)
    if current != expected_revision:
        raise ConfigConflict("配置文件已被其他操作修改，请刷新设置后重试")
    after = current if before_mode == mode else replace_persona_mode(config_path, mode, current, publish=False)
    change = {"op_id": plan["op_id"], "epoch": plan["epoch"], "mode": mode,
              "config": _config_identity(config_path), "before": current, "before_mode": before_mode,
              "after": after, "persona": digest(plan["candidate_md"])}
    write_record(path, change)
    return change


def candidate_config(cfg, change):
    if change is None:
        return cfg
    candidate = copy.deepcopy(cfg)
    candidate.persona.expression_mode = change["mode"]
    return candidate


def reset_config(cfg, change, reset):
    if change is None or reset is None:
        return cfg
    plan = reset.get("plan") or {}
    if (plan.get("op_id"), plan.get("epoch"), plan.get("material_mode"), digest(plan.get("candidate_md"))) != (
            change["op_id"], change["epoch"], change["mode"], change["persona"]):
        raise ValueError("人格装配恢复记录与归档计划不符，请先核对档案")
    return candidate_config(cfg, change)


def finish(store, cfg, database, config_path, change, *, discard_uncommitted=False):
    if change is None:
        return None
    path = journal_path(database)
    epoch = store.meta_get("session_epoch")
    marker = store.meta_get("persona_import:" + change["op_id"])
    if marker is None and discard_uncommitted and epoch == change["epoch"]:
        path.unlink(missing_ok=True)  # The original profile is active and no install committed.
        return None
    saved = json.loads(marker) if marker is not None else {}
    if (not isinstance(saved, dict)
            or (saved.get("kind"), saved.get("epoch"), saved.get("op_id"), saved.get("after_sha256")) != (
                KIND, epoch, change["op_id"], change["persona"])
            or digest(current_text()) != change["persona"]):
        raise ValueError("人格安装与装配记录未能相互确认，请先核对档案")

    # Keep the running session consistent with its installed persona even
    # when disk publication fails. A restart verifies the same proof again.
    mode = change["mode"]
    cfg.persona.expression_mode = mode
    current, configured_mode = persona_mode_revision(config_path)
    if configured_mode != mode:
        if configured_mode != change["before_mode"]:
            raise ConfigConflict(f'人格已安装，请将 persona.expression_mode 设置为 "{mode}" 后重试恢复。')
        current = replace_persona_mode(config_path, mode, current)
    elif revision(config_path) != current:
        raise ConfigConflict("核对装配模式时配置又发生变化，请重试恢复")
    path.unlink(missing_ok=True)
    return current
