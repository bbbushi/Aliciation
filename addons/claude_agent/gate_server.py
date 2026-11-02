"""Alice 权限闸门：最小 MCP stdio server，作为 claude 的 --permission-prompt-tool。

模型每次需要权限时调用本服务的 confirm 工具（带上工具名与输入），本服务按策略
裁决并返回 {behavior: allow|deny}；每次质询写审计（jsonl）并打印到 stderr——
真实接入时，裁决点替换为「Alice 转述 + 用户三层答案」。仅标准库。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

STRUCTURAL_DENY_PREFIXES = ()  # 结构禁区示例：(str(Path.home() / ".zcode"),)


def decide(policy: str, tool_name: str, tool_input: dict) -> tuple[str, str]:
    blob = json.dumps(tool_input, ensure_ascii=False)
    for bad in STRUCTURAL_DENY_PREFIXES:
        if bad and bad in blob:
            return "deny", f"结构禁区（宿主硬挡，不接受质询答案）：{bad}"
    if policy == "allow":
        return "allow", "demo 策略放行"
    return "deny", "demo 策略拒绝：真实接入时此处返回用户/Alice 的三层答案（允许一次/本任务内允许/拒绝）"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--policy", choices=["allow", "deny"], default="deny")
    ap.add_argument("--label", default="demo")
    ap.add_argument("--audit", required=True)
    args = ap.parse_args()
    audit_path = Path(args.audit)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        rid, method = msg.get("id"), msg.get("method")
        if method == "initialize":
            out = {"jsonrpc": "2.0", "id": rid, "result": {
                "protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
                "serverInfo": {"name": "alice_gate", "version": "0.1.0"}}}
        elif method == "tools/list":
            out = {"jsonrpc": "2.0", "id": rid, "result": {"tools": [{
                "name": "confirm",
                "description": "Alice 宿主权限闸门：对模型的工具使用质询返回 allow/deny",
                "inputSchema": {"type": "object", "properties": {
                    "tool_name": {"type": "string"},
                    "input": {"type": "object"},
                    "reason": {"type": "string"}},
                    "required": ["tool_name"]}}]}}
        elif method == "tools/call":
            call_args = (msg.get("params") or {}).get("arguments", {})
            tool_name = str(call_args.get("tool_name", "?"))
            tool_input = call_args.get("input") or {}
            decision, message = decide(args.policy, tool_name, tool_input)
            rec = {"ts": time.strftime("%H:%M:%S"), "label": args.label, "tool": tool_name,
                   "input": tool_input, "decision": decision, "message": message}
            with audit_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            print(f"[闸门] 质询：模型想用 {tool_name} → {json.dumps(tool_input, ensure_ascii=False)[:120]}"
                  f" ⇒ 裁决：{decision}（{message[:60]}）", file=sys.stderr, flush=True)
            payload = ({"behavior": "allow", "updatedInput": tool_input} if decision == "allow"
                       else {"behavior": "deny", "message": message})
            # --permission-prompt-tool 约定：工具返回单个 text block，内容为 JSON 串
            # {"behavior": "allow", "updatedInput": {...}} 或 {"behavior": "deny", "message": "..."}
            out = {"jsonrpc": "2.0", "id": rid, "result": {
                "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}]}}
        elif rid is not None:
            out = {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": method or "?"}}
        else:
            continue  # 通知类：无需应答
        sys.stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
