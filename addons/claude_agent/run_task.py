"""claude-agent 端到端 runner（S4/S5 验收用）：ClaudeLocalTransport + 正式闸门。

用法（分支内人工验收 / 自动自检）：
  python addons/claude_agent/run_task.py --brief "..." --sandbox <dir> \
      [--max-turns 8] [--deadline 300] [--auto-answer allow_once|deny]
--auto-answer：后台线程监视 gate-dir 的 pending 文件并按固定策略写答案——
模拟 GUI 质询卡（C03）；不传则人工放置 answers/<id>.json（真人工流）。
产物收取复用 main 的 worker_artifacts.collect_artifacts。
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from addons.claude_agent.local_transport import ClaudeLocalTransport  # noqa: E402


def auto_responder(gate_dir: Path, decision: str, stop: threading.Event) -> None:
    answers = gate_dir / "answers"
    answers.mkdir(parents=True, exist_ok=True)
    seen: set[str] = set()
    while not stop.is_set():
        for pf in gate_dir.glob("*.pending.json"):
            try:
                req = json.loads(pf.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            rid = req.get("id")
            if rid and rid not in seen:
                seen.add(rid)
                time.sleep(1.5)  # 模拟用户反应延迟
                (answers / f"{rid}.json").write_text(
                    json.dumps({"decision": decision}, ensure_ascii=False), encoding="utf-8")
                print(f"  [GUI模拟] 质询 {rid} → {decision}")
        stop.wait(0.5)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--brief", required=True)
    ap.add_argument("--sandbox", required=True)
    ap.add_argument("--max-turns", type=int, default=10)
    ap.add_argument("--deadline", type=float, default=300)
    ap.add_argument("--auto-answer", choices=["allow_once", "deny", "none"], default="none")
    args = ap.parse_args()

    sandbox = Path(args.sandbox).resolve()
    gate_dir = sandbox / "gate"
    gate_dir.mkdir(parents=True, exist_ok=True)
    audit_path = sandbox / "permission_audit.jsonl"
    mcp_cfg = gate_dir / "mcp.json"
    mcp_cfg.write_text(json.dumps({"mcpServers": {"alice_gate": {
        "command": sys.executable,
        "args": [str(REPO / "addons/claude_agent/gate_server.py"),
                 "--audit", str(audit_path), "--sandbox", str(sandbox),
                 "--gate-dir", str(gate_dir), "--label", "run_task"]}}}),
        encoding="utf-8")

    stop = threading.Event()
    if args.auto_answer != "none":
        threading.Thread(target=auto_responder, args=(gate_dir, args.auto_answer, stop),
                         daemon=True).start()

    events: list[tuple[str, dict]] = []
    transport = ClaudeLocalTransport(brief=args.brief, sandbox=sandbox,
                                     gate_mcp_config=mcp_cfg, max_turns=args.max_turns,
                                     on_event=lambda t, p: events.append((t, p)),
                                     hard_deadline=args.deadline)
    print(f"[派工] 沙箱={sandbox} 自动答案={args.auto_answer}")
    transport.start()
    while transport.terminal() is None:
        time.sleep(0.3)
    stop.set()
    term = transport.terminal()

    if term and term["frame"] == "result":
        payload = term["payload"]
        print(f"  [结果] {str(payload.get('result'))[:160]}")
        usage = payload.get("usage", {})
        print(f"  [计费] turns={payload.get('num_turns')} "
              f"out_tok={usage.get('output_tokens')} cost=${round(payload.get('total_cost_usd') or 0, 4)}")
    else:
        print(f"  [终态] {term}")

    # 产物收取（main 件复用）
    try:
        from alice.worker_artifacts import collect_artifacts
        manifest, rejects = collect_artifacts(sandbox.parent, sandbox.parent.name,
                                              sandbox.name, "attempt_demo",
                                              [p.name for p in sandbox.glob("output/*")] and
                                              [f"output/{p.name}" for p in sandbox.glob("output/*")])
        print(f"  [收取] {len(manifest)} 项产物，拒绝 {len(rejects)} 项")
    except Exception as exc:
        print(f"  [收取] 跳过（{type(exc).__name__}: {exc}）——本 runner 的沙箱即工作目录")

    print(f"  [审计] {audit_path}（{sum(1 for _ in audit_path.open()) if audit_path.exists() else 0} 条）")
    return 0 if term and term.get("frame") == "result" else 1


if __name__ == "__main__":
    raise SystemExit(main())
