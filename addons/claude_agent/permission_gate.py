"""质询文件袋的宿主侧 API（S5）：列挂起质询 / 写三层答案。

协议与 gate_server.py 对齐：gate-dir/<task>/<attempt> 下 *.pending.json 为挂起质询，
answers/<id>.json 为答案 {decision: allow_once|allow_task|deny}。
本模块只做参数校验与路径收敛（gate-dir 固定在 claude_policy 政策的工作区下），
文件 IO 由 GUI 请求线程在 service 单写者锁内完成（短步骤）。
"""
from __future__ import annotations

import json
import os
import uuid
import time
from pathlib import Path

ALLOWED_DECISIONS = ("allow_once", "allow_task", "deny")


def claude_policy_row(store) -> dict | None:
    """读 claude_policy（S1 独立表）。表不存在（未迁移）→ None。"""
    try:
        cur = store.conn.execute(
            "SELECT id, revision, enabled, native_sandbox_ack, workspace_root,"
            " max_turns, budget_usd_per_task, budget_usd_per_day FROM claude_policy WHERE id=1")
        cols = [d[0] for d in cur.description]
        row = cur.fetchone()
    except Exception:
        return None
    return dict(zip(cols, row)) if row else None


def claude_gate_dir(policy_row: dict | None) -> Path | None:
    if not policy_row:
        return None
    root = str(policy_row.get("workspace_root") or "").strip()
    if not root:
        return None
    return Path(root).resolve() / "gate"


def list_pending(gate_dir: Path) -> list[dict]:
    out = []
    if not gate_dir.is_dir():
        return out
    now = time.time()
    for pf in sorted(gate_dir.glob("*/*/*.pending.json")):
        try:
            req = json.loads(pf.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(req, dict):
            continue
        try:
            age = now - float(req['ts'])
            age_seconds = int(age)
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        if age >= 600:
            req["stale"] = True
        req["age_seconds"] = age_seconds
        out.append(req)
    return out


def answer(gate_dir: Path, payload: dict) -> dict:
    req_id = str(payload.get("id") or "")
    decision = payload.get("decision")
    if not req_id.replace("_", "").isalnum() or len(req_id) > 40 or ".." in req_id:
        raise ValueError("质询 ID 无效")
    if decision not in ALLOWED_DECISIONS:
        raise ValueError("decision 必须是 allow_once/allow_task/deny")
    task_id, attempt_id = payload.get('task_id'), payload.get('attempt_id')
    for identity in (task_id, attempt_id):
        if not isinstance(identity, str) or len(identity) != 32 or any(c not in '0123456789abcdef' for c in identity):
            raise ValueError('质询任务身份无效')
    target = gate_dir / task_id / attempt_id
    pending = target / f'{req_id}.pending.json'
    req = json.loads(pending.read_text(encoding='utf-8'))
    for key in ('task_id', 'attempt_id', 'epoch', 'policy_revision'):
        if req.get(key) != payload.get(key):
            raise ValueError('质询身份或授权版本已变化')
    if time.time() - float(req['ts']) >= 600:
        raise ValueError('质询已过期')
    answers = target / 'answers'
    answers.mkdir(parents=True, exist_ok=True)
    destination = answers / f'{req_id}.json'
    data = {'decision': decision, 'answered_at': time.time()}
    temporary = answers / (uuid.uuid4().hex + '.tmp')
    try:
        temporary.write_text(json.dumps(data), encoding='utf-8')
        try:
            os.link(temporary, destination)  # publish complete content; first answer wins
        except FileExistsError:
            previous = json.loads(destination.read_text(encoding='utf-8'))
            if previous.get('decision') != decision:
                raise ValueError('此质询已有不同答复')
    finally:
        temporary.unlink(missing_ok=True)
    return {'id': req_id, 'decision': decision, 'delivered': True}
