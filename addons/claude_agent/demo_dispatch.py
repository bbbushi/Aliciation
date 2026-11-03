"""Demo：终端直调 claude CLI 的派工 + 权限质询闸门（P3 本地 CLI 轨的概念验证）。

流程：固定 argv 无头启动 claude（stream-json 双向）→ 模型遇到权限点发起
control_request（即「AI 向宿主质询」）→ 本驱动按策略裁决（allow/deny）并写
control_response → 模型继续或改路 → 收集 result/usage → 审计留痕。

裁决点 policy 此处来自命令行（allow/deny），真实接入时由「Alice 转述 + 用户
三层答案（允许一次/本任务内允许/拒绝）」替换——见 README 与 demo 记录文档。
仅标准库；凭据不过手（claude 用自己的登录与模型配置）。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path


def audit(path: Path, record: dict) -> None:
    record["ts"] = time.strftime("%H:%M:%S")
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(f"  [审计] {record['ts']} 质询工具={record.get('tool')} 决定={record.get('decision')}"
          f" 摘要={record.get('summary', '')[:80]}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--brief", required=True)
    ap.add_argument("--sandbox", required=True)
    ap.add_argument("--policy", choices=["allow", "deny"], default="allow")
    ap.add_argument("--label", default="demo")
    ap.add_argument("--max-turns", type=int, default=12)
    ap.add_argument("--timeout", type=float, default=180)
    args = ap.parse_args()

    sandbox = Path(args.sandbox).resolve()
    sandbox.mkdir(parents=True, exist_ok=True)
    audit_path = sandbox / "permission_audit.jsonl"
    audit_path.write_text("", encoding="utf-8")

    argv = [
        "claude", "-p", args.brief,
        "--output-format", "stream-json", "--input-format", "stream-json", "--verbose",
        "--permission-prompts", "host",      # 权限质询路由给宿主（本驱动）
        "--add-dir", str(sandbox),           # 上限：可见目录限定沙箱
        "--max-turns", str(args.max_turns),
    ]
    print(f"[启动] {' '.join(argv[:2])} ... 沙箱={sandbox} 策略={args.policy}")
    started = time.time()
    proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, cwd=str(sandbox))
    raw_dump = (os.environ.get("DEMO_RAW") and
                (sandbox / "raw_stream.jsonl").open("w", encoding="utf-8"))

    def send(frame: dict) -> None:
        if proc.stdin:
            proc.stdin.write(json.dumps(frame, ensure_ascii=False) + "\n")
            proc.stdin.flush()

    send({"type": "user", "message": {"role": "user",
          "content": [{"type": "text", "text": args.brief}]}})

    result, denied_count, allowed_count = None, 0, 0
    try:
        for line in proc.stdout:
            if raw_dump:
                raw_dump.write(line)
                raw_dump.flush()
            if time.time() - started > args.timeout:
                proc.kill()
                print("  [超时] 强制回收")
                break
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            kind = ev.get("type")
            if kind == "system" and ev.get("subtype") == "init":
                print(f"  [会话] 模型={ev.get('model')} 工具数={len(ev.get('tools', []))}")
            elif kind == "control_request":
                req = ev.get("request", {})
                tool = req.get("tool_name")
                inp = req.get("input", {})
                summary = json.dumps(inp, ensure_ascii=False)[:160]
                decision = args.policy
                audit(audit_path, {"tool": tool, "input": inp, "decision": decision,
                                   "summary": summary, "label": args.label})
                if decision == "allow":
                    allowed_count += 1
                    answer = {"behavior": "allow", "updatedInput": inp}
                else:
                    denied_count += 1
                    answer = {"behavior": "deny",
                              "message": "宿主拒绝：demo 策略为 deny（真实接入时这里返回用户/Alice 的三层答案）"}
                send({"type": "control_response",
                      "response": {"subtype": "success", "request_id": ev.get("request_id"),
                                   "response": answer}})
            elif kind == "result":
                result = ev
                break
    finally:
        if raw_dump:
            raw_dump.close()
        try:
            proc.stdin.close()
        except Exception:
            pass
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()

    if result is None:
        print("  [失败] 未收到 result 终态（超时或异常退出）")
        return 1
    print(f"  [结果] {str(result.get('result'))[:200]}")
    usage = result.get("usage", {})
    print(f"  [计费] 轮次={result.get('num_turns')} 输出token={usage.get('output_tokens')} "
          f"费用=${round(result.get('total_cost_usd') or 0, 4)}")
    print(f"  [质询] 允许={allowed_count} 拒绝={denied_count}（审计：{audit_path}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
