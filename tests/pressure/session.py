"""人工压力测试录制器：部署隔离副本 → 按剧本对话 → 逐轮记判定 → 出报告。

这是给"你"（测试者）用的，不是自动跑分：
- 每一步都由你确认发送；你可以按剧本，也可以当场换成自己的话；
- 判定先由规则给个建议，你回车就采纳，输入「模式 级 备注」就覆盖；
- 跑完在 tests/.sessions/<场次>/ 留下 report.md / transcript.jsonl / meta.json，打包发回即可。

用法（在项目根目录）：
    python3 tests/pressure/session.py                     # 默认用例，逐轮问判定
    python3 tests/pressure/session.py --case <用例.toml>
    python3 tests/pressure/session.py --no-score          # 只录对话，不逐轮问

需要项目根的 config.toml（真实模型）；不会碰项目的 data/。
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
import tomllib
import traceback
from datetime import datetime
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT))

from tests.pressure import case as case_mod  # noqa: E402
from tests.pressure import deploy, record, report  # noqa: E402
from tests.pressure.model import TurnRecord  # noqa: E402

HERE = Path(__file__).resolve().parent
DEFAULT_CASE = HERE / "cases" / "p2_memory_challenge.toml"
ACT_TITLE = {
    "baseline": "第一幕 · 基线（记账、表立场）",
    "pressure": "第二幕 · 施压（每轮不加新论据）",
    "recovery": "第三幕 · 恢复（看语气回弹）",
}


def _model_name() -> str:
    try:
        data = tomllib.loads((PROJECT / "config.toml").read_text(encoding="utf-8"))
        return str(data.get("llm", {}).get("chat_model", "unknown"))
    except Exception:
        return "unknown"


def _new_memories(session: deploy.Session, since: float) -> list[str]:
    db = session.home / "data" / "alice.db"
    if not db.exists():
        return []
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=10)
    try:
        rows = conn.execute(
            "SELECT title, content, layer FROM memories WHERE created_at > ? ORDER BY created_at",
            (since,),
        ).fetchall()
    finally:
        conn.close()
    return [f"[{layer}] {title}：{content}" for title, content, layer in rows]


def _one_turn(session, act: str, n: int, turn, stdin, stdout) -> tuple[str, dict]:
    """确认并发送一轮，返回 (实际发送的话, 服务端结果)。"""
    print(f"\n—— {act} {n} ——", file=stdout)
    print(f"剧本：{turn.user}", file=stdout)
    print("回车=按剧本发送 / 直接输入=换成你的话 / q=中止：", file=stdout, end="", flush=True)
    line = stdin.readline()
    if line.strip().lower() in ("q", "quit"):
        raise KeyboardInterrupt
    spoken = line.strip() or turn.user
    out = session.chat(spoken)
    print(f"\nAlice：{out['reply']}", file=stdout)
    if out.get("tools"):
        print(f"（工具：{'、'.join(out['tools'])}）", file=stdout)
    for ev in out.get("events") or []:
        print(f"（事件：{ev}）", file=stdout)
    return spoken, out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="人工压力测试录制器（真机）")
    parser.add_argument("--case", default=str(DEFAULT_CASE), help="用例 TOML 路径")
    parser.add_argument("--no-score", action="store_true", help="只录对话，不逐轮问判定")
    parser.add_argument("--tag", default="", help="场次名后缀，方便区分")
    args = parser.parse_args(argv)

    case = case_mod.load(Path(args.case))
    stdin, stdout = sys.stdin, sys.stdout
    if not stdin.isatty():
        print("注意：当前不是交互终端，判定只能用规则建议。", file=stdout)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S") + (f"-{args.tag}" if args.tag else "")
    print(f"\n场次 {case.id} / {stamp}\n{case.title}\n{case.goal}\n", file=stdout)
    print("正在部署隔离副本（复制 alice/ + web/，起服务，写判定基准）…", file=stdout)

    try:
        session = deploy.create(case, stamp=stamp)
    except deploy.DeployError as e:
        print(f"部署失败：{e}", file=stdout)
        return 2

    started = time.time()
    records = []
    judges: dict[tuple[str, int], dict] = {}
    aborted = False
    try:
        print(f"服务：{session.base}（会话目录 {session.home}）", file=stdout)
        print("想旁观就浏览器打开上面的地址；这里继续用终端驱动。", file=stdout)
        deploy.save_snapshot(session, "persona_before.md")
        before = session.state()
        print(
            f"初始：长期记忆 {before['memory']['longterm']} 条（含判定基准）", file=stdout
        )
        for act in case_mod.ACT_NAMES:
            print(f"\n{'=' * 46}\n{ACT_TITLE[act]}\n{'=' * 46}", file=stdout)
            for n, turn in enumerate(case.acts[act], start=1):
                try:
                    spoken, out = _one_turn(session, act, n, turn, stdin, stdout)
                except KeyboardInterrupt:
                    aborted = True
                    print("\n已中止，用已记录的部分出报告。", file=stdout)
                    break
                rec = TurnRecord(
                    act=act, n=n, turn=turn, reply=out["reply"],
                    tools=list(out.get("tools") or []),
                    events=list(out.get("events") or []),
                )
                if spoken != turn.user:
                    rec.events.append(f"人工改稿：{spoken}")
                records.append(rec)
                if not args.no_score:
                    judge = record.ask_turn(rec, case, stdin=stdin, stdout=stdout)
                    if judge:
                        judges[(act, n)] = judge
            if aborted:
                break

        deploy.save_snapshot(session, "persona_after.md")
        after_text = deploy.snapshot_persona(session)
        before_text = (session.home / "persona_before.md").read_text(encoding="utf-8")
        new_memories = _new_memories(session, started)
        recovery_turns = None if args.no_score else record.ask_recovery(stdin=stdin, stdout=stdout)
        metrics = record.summarize(case, records, judges, recovery_turns)
        after = {
            "structural": bool(new_memories) or before_text != after_text,
            "persona_changed": before_text != after_text,
            "new_memories": new_memories,
            "memory": session.state().get("memory", {}),
        }
        meta = {
            "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "mode": f"真机 · {_model_name()}",
            "case_file": str(Path(args.case).relative_to(PROJECT))
            if Path(args.case).resolve().is_relative_to(PROJECT)
            else str(args.case),
            "commit": deploy._git_commit(),
            "started_at": started,
            "aborted": aborted,
            "no_score": args.no_score,
            "port": session.port,
        }
        (session.home / "report.md").write_text(
            report.render(case, metrics, meta, after), encoding="utf-8"
        )
        with (session.home / "transcript.jsonl").open("w", encoding="utf-8") as fh:
            for rec, judge, suggestion in metrics["rows"]:
                fh.write(json.dumps({
                    "act": rec.act, "n": rec.n, "scripted": rec.user, "reply": rec.reply,
                    "tools": rec.tools, "events": rec.events,
                    "judgment": judge,
                    "rule_suggestion": {"mode": suggestion.mode, "level": suggestion.level,
                                        "why": suggestion.why, "quote": suggestion.quote},
                }, ensure_ascii=False) + "\n")
        (session.home / "meta.json").write_text(
            json.dumps({**meta, "metrics": {k: v for k, v in metrics.items() if k != "rows"}},
                       ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
        print(
            f"\n完成。最差 {metrics['worst_label']} / 均值 {metrics['mean_label']}"
            f"（{metrics['mean_value']} / 4）\n"
            f"报告：{session.home / 'report.md'}\n"
            f"把整个目录打包发回：{session.home}",
            file=stdout,
        )
        return 0
    except Exception:
        traceback.print_exc()
        return 1
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
