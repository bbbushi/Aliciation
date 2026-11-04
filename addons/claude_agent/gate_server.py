"""Alice 权限闸门（正式版 v1）：claude --permission-prompt-tool 的裁决端。

裁决顺序（设计 §二）：
  1. 结构禁区（硬挡，不质询）——~/.zcode、Alice 仓库、user/、凭据、沙箱外写入；
  2. 宿主精确动作许可（每次使用均由宿主重核活动身份与政策）；
  3. 宿主质询：写 pending 文件 → 等答案文件（GUI 质询卡/Alice 转述写入）→ 超时 deny。

答案协议：pending.json {id, ts, tool, input_summary, reason_hint}；
answers/<id>.json {decision: allow_once|allow_task|deny}。v1 答案端可以是
手工放置（人工验收），S5 接 GUI 后由 worker-tasks.js 写入。仅标准库。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import uuid
import sys
import time
from pathlib import Path

PENDING_TIMEOUT = 600.0
SENSITIVE_SUFFIXES = (".pem", ".key", ".env", ".kdbx")


def structural_review(tool_name: str, tool_input: dict, sandbox: Path) -> tuple[str, str]:
    """结构禁区：命中即 deny，不产生质询（宿主底线，不接受三层答案）。"""
    blob = json.dumps(tool_input, ensure_ascii=False)
    lowered = blob.lower()
    for hard in ("~/.zcode", ".zcode", "user/config.toml", "user/data",
                 "alice.db", "id_rsa", ".ssh", "keychain"):
        if hard.lower() in lowered:
            return "deny", f"结构禁区（宿主硬挡）：{hard}"
    if not isinstance(tool_input, dict):
        return "deny", "工具输入必须为对象"
    if tool_name in ("Write", "Edit", "NotebookEdit"):
        target = str(tool_input.get("file_path") or tool_input.get("notebook_path") or "")
        if target:
            try:
                path = Path(target)
                real = (path if path.is_absolute() else sandbox / path).resolve()
                if sandbox.resolve() not in real.parents and real.parent != sandbox.resolve():
                    return "deny", "写入目标在授权沙箱之外（宿主硬挡）"
            except (OSError, ValueError):
                return "deny", "写入路径无法解析（宿主硬挡）"
            if real.suffix.lower() in SENSITIVE_SUFFIXES:
                return "deny", f"敏感扩展名（宿主硬挡）：{real.suffix}"
    if tool_name == "Bash":
        cmd = str(tool_input.get("command", ""))
        for bad in ("rm -rf /", "curl ", "wget ", "scp ", "ssh ", "> ~/.", "sudo "):
            if bad in cmd.lower():
                return "deny", f"命令含高危模式（宿主硬挡）：{bad.strip()}"
    return "allow", "通过结构审查"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--audit", required=True)
    ap.add_argument("--sandbox", required=True)
    ap.add_argument("--gate-dir", required=True, help="质询 pending/answers 目录（宿主与 GUI 共享）")
    ap.add_argument("--label", default="claude-agent")
    ap.add_argument('--task-id', default='')
    ap.add_argument('--attempt-id', default='')
    ap.add_argument('--epoch', default='')
    ap.add_argument('--policy-revision', type=int, default=0)
    args = ap.parse_args()

    audit_path, sandbox = Path(args.audit), Path(args.sandbox).resolve()
    gate_dir = Path(args.gate_dir)
    (gate_dir / "answers").mkdir(parents=True, exist_ok=True)

    def audit(record: dict) -> None:
        record["ts"] = time.strftime("%H:%M:%S")
        with audit_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    def respond(rid, payload: dict, *, as_tool_text: bool = False) -> None:
        # JSON-RPC 方法（initialize/tools/list）返回裸 result；tools/call 的
        # --permission-prompt-tool 约定要求单个 text block（内容为 JSON 串）。
        result = ({"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}]}
                  if as_tool_text else payload)
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": rid, "result": result}) + "\n")
        sys.stdout.flush()

    while True:
        line = sys.stdin.readline(65537)
        if not line:
            break
        if len(line.encode("utf-8")) > 65536:
            return 2
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        rid, method = msg.get("id"), msg.get("method")
        if method == "initialize":
            respond(rid, {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
                          "serverInfo": {"name": "alice_gate", "version": "1.0.0"}})
        elif method == "tools/list":
            respond(rid, {"tools": [{
                "name": "confirm",
                "description": "Alice 宿主权限闸门：结构审查→任务内缓存→宿主质询（三层答案）",
                "inputSchema": {"type": "object", "properties": {
                    "tool_name": {"type": "string"}, "input": {"type": "object"},
                    "reason": {"type": "string"}},
                    "required": ["tool_name"]}}]})
        elif method == "tools/call":
            call_args = (msg.get("params") or {}).get("arguments", {})
            tool_name = str(call_args.get("tool_name", "?"))
            tool_input = call_args.get("input") or {}
            if isinstance(tool_input, dict) and tool_name in {'Read', 'Write', 'Edit', 'NotebookEdit'}:
                tool_input = dict(tool_input)
                for key in ('file_path', 'notebook_path'):
                    path = tool_input.get(key)
                    if isinstance(path, str) and path:
                        try:
                            target = Path(path)
                            tool_input[key] = str((target if target.is_absolute() else sandbox / target).resolve())
                        except (OSError, ValueError):
                            pass  # structural/host validation will deny invalid paths
            # 1. 结构禁区
            decision, message = structural_review(tool_name, tool_input, sandbox)
            origin = "structural"
            # Every permission use is revalidated by the host, including cached grants.
            if decision == 'allow':
                req_id = 'pq_' + uuid.uuid4().hex
                pending = {"id": req_id, "ts": time.time(), "label": args.label,
                           "task_id": args.task_id, "attempt_id": args.attempt_id,
                           "epoch": args.epoch, "policy_revision": args.policy_revision,
                           "worker_id": "claude-agent", "expires_at": time.time() + PENDING_TIMEOUT,
                           "tool": tool_name, "input": tool_input,
                           "reason_hint": str(call_args.get("reason", ""))[:300]}
                pending['action_digest'] = hashlib.sha256(json.dumps(
                    {'tool': tool_name, 'input': tool_input}, sort_keys=True, ensure_ascii=False,
                    separators=(',', ':'), allow_nan=False).encode()).hexdigest()
                pending_file = gate_dir / f'{req_id}.pending.json'
                temporary = gate_dir / f'{req_id}.tmp'
                temporary.write_text(json.dumps(pending, ensure_ascii=False), encoding='utf-8')
                temporary.replace(pending_file)
                print(f"[闸门] 质询挂起 {req_id}：{tool_name} 等待宿主三层答案…",
                      file=sys.stderr, flush=True)
                deadline = time.time() + PENDING_TIMEOUT
                answer = None
                while time.time() < deadline:
                    if (gate_dir / 'revoked').exists():
                        answer = {'decision': 'deny'}
                        break
                    ans_file = gate_dir / "answers" / f"{req_id}.json"
                    if ans_file.is_file():
                        try:
                            answer = json.loads(ans_file.read_text(encoding="utf-8"))
                        except (json.JSONDecodeError, OSError):
                            answer = {"decision": "deny"}
                        break
                    time.sleep(0.5)
                (gate_dir / f"{req_id}.pending.json").unlink(missing_ok=True)
                if answer is None:
                    decision, origin = "deny", "timeout"
                    message = "宿主未应答（10 分钟超时）"
                else:
                    if (any(answer.get(key) != pending[key] for key in
                            ('id', 'task_id', 'attempt_id', 'epoch', 'worker_id', 'policy_revision', 'expires_at', 'action_digest'))
                            or time.time() >= pending['expires_at']):
                        answer = {'decision': 'deny'}
                    d = answer.get('decision')
                    if d == "allow_once":
                        decision, origin = "allow", "allow_once"
                        message = "用户允许一次"
                    elif d == "allow_task":
                        decision, origin = "allow", "task_cache"
                        message = "用户允许（本任务内）"
                    else:
                        decision, origin = "deny", "user_deny"
                        message = "用户拒绝"
            if (gate_dir / 'revoked').exists():
                decision, origin, message = 'deny', 'revoked', '宿主已撤回本次执行权限'
            audit({"tool": tool_name, "decision": decision,
                   "origin": origin, "message": message, "label": args.label})
            print(f"[闸门] {tool_name} → {decision}（{origin}）", file=sys.stderr, flush=True)
            payload = ({"behavior": "allow", "updatedInput": tool_input} if decision == "allow"
                       else {"behavior": "deny", "message": message})
            respond(rid, payload, as_tool_text=True)
        elif rid is not None:
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": rid,
                                         "error": {"code": -32601, "message": method or "?"}}) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
