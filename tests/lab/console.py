"""Run a human-controlled Alice Lab console over the public private-pipe protocol."""
from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import sys
import uuid
from datetime import datetime
from pathlib import Path

MODES = {"interest": "兴趣探索", "problem": "问题思考", "iteration": "自我迭代", "contact": "主动交流", "idle": "发呆"}
REASONS = {"weighted_rotation": "按权重抽签", "priority_content": "优先事项",
           "no_actionable_work": "无候选，未抽签", "activity_finish_rest": "模型提议休息，未抽签",
           "user_requested_rest": "用户安排休息，未抽签", "explicit_task": "明确指定任务，未抽签"}
ACTIONS = {"awake", "sleep", "run", "pause", "end_rest"}


def print_weights(snapshot):
    draw = snapshot.get("draw_weights")
    total = sum(draw.values()) if draw else 0
    parts = []
    for mode, label in MODES.items():
        item = snapshot.get("modes", {}).get(mode)
        if item is None:
            continue
        chance = f"（{100 * draw.get(mode, 0) / total:.1f}%）" if total else ""
        parts.append(f"{label} {item['weight']:g}{chance}")
    return " · ".join(parts) or "旧记录未留权重"


def print_schedule(schedule):
    current = schedule["current"]
    print("当前权重（百分比是普通抽签预览）：", print_weights(current))
    print(f"休息分数与 {current['dry_windows']} 个连续无进展窗口、"
          f"{current['blocked_modes']} 个受阻计数有关。")
    print("最近实际记录：")
    for run in schedule["records"][:8]:
        at = datetime.fromtimestamp(run["at"]).astimezone().strftime("%H:%M:%S")
        reason = REASONS.get(run.get("reason"), run.get("reason", "未知选择原因"))
        suffix = f" · {print_weights(run)}" if run.get("modes") or run.get("reason") == "weighted_rotation" else ""
        print(f"  {at} {MODES.get(run['activity'], run['activity'])} · {reason}{suffix}")


def exchange(process, session, op, **payload):
    request_id = uuid.uuid4().hex
    frame = {"protocol": 1, "instance_id": session["instance_id"], "nonce": session["nonce"],
             "request_id": request_id, "op": op, **payload}
    process.stdin.write(json.dumps(frame, ensure_ascii=False) + "\n")
    process.stdin.flush()
    raw = process.stdout.readline()
    if not raw:
        raise RuntimeError("测试实例已退出，操作结果未确认")
    reply = json.loads(raw)
    if any(reply.get(key) != frame[key] for key in ("protocol", "instance_id", "nonce", "request_id")):
        raise RuntimeError("控制回执不属于本次请求")
    return reply


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", type=Path, help="明确指定已支持 Lab 协议的打包后端；默认运行当前源码")
    args = parser.parse_args()
    command = [str(args.backend.resolve())] if args.backend else [sys.executable, "-m", "alice"]
    nonce = secrets.token_hex(16)
    with subprocess.Popen([*command, "desktop-host", "--lab-session"],
                          cwd=Path(__file__).resolve().parents[2], stdin=subprocess.PIPE,
                          stdout=subprocess.PIPE, encoding="utf-8",
                          env={**os.environ, "ALICE_DESKTOP_NONCE": nonce}) as process:
        try:
            raw = process.stdout.readline()
            session = json.loads(raw) if raw else {}
            if (session.get("nonce") != nonce or session.get("protocol") != 1
                    or session.get("event") != "ready" or session.get("lab_protocol") != "alice-lab/1"):
                raise RuntimeError("目标后端没有就绪或不支持独立测试协议")
            print(f"Alice {session['app_version']} · 测试档案：{session['lab_profile']}", flush=True)
            print(f"测试聊天与模型设置：{session['url']}", flush=True)
            print("命令：weights 查看权重；awake 开启后台并允许模型计费；"
                  "sleep 关闭后台；run 请求探索；pause 暂停；end_rest 结束休息；quit 退出。")
            while True:
                try:
                    action = input("Alice Lab > ").strip()
                except (EOFError, KeyboardInterrupt):
                    action = "quit"
                if action == "quit":
                    exchange(process, session, "shutdown", reason="quit")
                    break
                if action == "weights":
                    result = exchange(process, session, "lab_schedule")
                    if result.get("status") == "ok":
                        print_schedule(result["schedule"])
                    else:
                        print(result.get("error", "权重暂不可用"))
                elif action in ACTIONS:
                    result = exchange(process, session, "lab_control", action=action, params={})
                    print("操作已受理，之后可用 weights 核对记录。" if result.get("status") == "accepted"
                          else result.get("error", "操作不可用"))
                elif action:
                    print("未知命令。")
        except (RuntimeError, ValueError, OSError) as exc:
            print(f"Alice Lab：{exc}", file=sys.stderr)
            return 1
        finally:
            process.stdin.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
