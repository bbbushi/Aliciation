"""命令行入口：python -m alice [命令]"""
from __future__ import annotations

import argparse
import json
import uuid
from datetime import datetime
from pathlib import Path

from . import config as appconfig
from . import consolidation, persona, persona_reset, retrieval, skills
from .config import Config, load
from .llm import make_client
from .store import Store


def _print_consolidation(summary: dict) -> None:
    if summary["to_warehouse"] or summary["promoted"] or summary["expired"] or summary["evicted"]:
        print(
            f"（记忆维护：{summary['to_warehouse']} 条进入仓库，"
            f"{len(summary['promoted'])} 条晋升长期，"
            f"{len(summary['expired'])} 条自然遗忘，"
            f"{len(summary['evicted'])} 条容量淘汰）"
        )


def cmd_memory(store: Store, layer: str | None) -> None:
    layers = [layer] if layer else ["active", "warehouse", "longterm"]
    for ly in layers:
        rows = store.memories_in_layer(ly)
        print(f"\n== {ly}（{len(rows)} 条）==")
        for m in rows[:50]:
            star = " ★" if m["salience"] else ""
            counts = f"提及{m['mention_count']}/使用{m['use_count']}"
            t = datetime.fromtimestamp(m["created_at"]).strftime("%m-%d")
            detail = f"：{m['content']}" if layer else ""
            print(f"- {t} {m['title']}{star}（{counts}）{detail}")
    tomb = store.conn.execute("SELECT COUNT(*) AS c FROM memory_tombstones").fetchone()["c"]
    print(f"\n墓碑（已真实遗忘）：{tomb} 条；技能：{store.count_skills()} 条")


def cmd_recall(store: Store, query: str) -> None:
    print(retrieval.tool_recall(store, query))
    print("（注：命中会累计这些记忆的使用次数）")


def _seed_wizard(store: Store, client, cfg: Config) -> None:
    print(f"首次运行：为你的 {cfg.persona.name} 选择人格底色（选 2~4 个，输入数字，如 1,3,5）")
    for i, t in enumerate(persona.TAGS, 1):
        print(f"{i:2d}. {t}", end="\n" if i % 5 == 0 else "   ")
    print()
    while True:
        raw = input("选择> ").strip()
        try:
            idx = [int(x) for x in raw.replace("，", ",").split(",")]
            if len(idx) != len(set(idx)):
                raise ValueError
            tags = [persona.TAGS[i - 1] for i in idx]
            if 2 <= len(tags) <= 4:
                break
        except (ValueError, IndexError):
            pass
        print("输入无效，请重新选择（例：1,3,5）")
    path = persona.seed_from_tags(store, client, cfg, tags)
    print(f"人格底色已生成：{path}")


def _run_maintenance_cmd(store: Store, cfg: Config, kind: str) -> None:
    """双维护显式命令（设计草案 §5）：先预览（不改数据）再创建任务并同步执行。"""
    from . import maintenance
    from .steps import drive
    preview = (maintenance.compact_preview(store, cfg) if kind == "compact"
               else maintenance.tidy_preview(store, cfg))
    print(json.dumps(preview, ensure_ascii=False, indent=2))
    client = make_client(cfg)
    task_id = store.enqueue_maintenance_task(
        kind, "explicit", f"{kind}:cli:{uuid.uuid4().hex[:8]}", "gui", {"reason": "CLI 显式命令"})
    if task_id is None:
        print("已有同类维护任务在排队，未重复创建（用 serve 的维护状态查看）。")
        return
    print(f"任务 {task_id} 执行中（模型调用约 {preview['estimated_model_calls']} 次）……")
    steps = (maintenance.compact_task_steps(store, client, cfg, task_id) if kind == "compact"
             else maintenance.tidy_task_steps(store, client, cfg, task_id))
    result = drive(steps)
    if kind == "compact" and result and result.get("opinion_processing") == "queued":
        result["opinions"] = drive(maintenance.compact_reflection_steps(store, client, cfg, task_id))
    print("结果：" + (json.dumps(result, ensure_ascii=False) if result else "未执行（任务已被认领或失效）"))


def cmd_chat(store: Store, cfg: Config) -> None:
    summary = consolidation.run_if_due(store, cfg)
    if summary:
        _print_consolidation(summary)
    client = make_client(cfg)
    if persona.persona_md() is None:
        _seed_wizard(store, client, cfg)
    for watcher in (persona.evolve_if_due, skills.distill_if_due):
        due = watcher(store, client, cfg)
        if due:
            print(f"（{due}）")
    from .loop import run_chat

    run_chat(store, client, cfg)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="alice",
        description="Alice — 陪伴型 harness（单一永久会话 + 三层记忆 + 可生长人格）",
    )
    parser.add_argument("--config", default=None, help="config.toml 路径（默认 user/config.toml）")
    sub = parser.add_subparsers(dest="cmd")

    p_mem = sub.add_parser("memory", help="查看记忆库分层与统计")
    p_mem.add_argument("layer", nargs="?", choices=["active", "warehouse", "longterm"])
    p_recall = sub.add_parser("recall", help="手动检索仓库/长期记忆")
    p_recall.add_argument("query")
    sub.add_parser("consolidate", help="立即运行分层维护与生存过滤")
    sub.add_parser("compact", help="压缩一段已完成的普通对话为接续状态（双维护；先预览）")
    sub.add_parser("tidy", help="整理记忆库：去重合并与长正文浓缩（双维护；先预览）")
    sub.add_parser("tidy-rollback", help="回退最近一次整理批次的浓缩正文（窗口内）")
    sub.add_parser("evolve", help="立即运行人格演化")
    sub.add_parser("deliberate", help="立即运行思辨（从记忆消化观点）")
    p_seed = sub.add_parser("seed", help="安装自定义初始人格（markdown 文件）")
    p_seed.add_argument("file", help="人格文件路径，须含「## 底色」小节")
    sub.add_parser("distill", help="从记忆提炼技能")
    p_serve = sub.add_parser("serve", help="启动本地图形界面（浏览器）")
    p_serve.add_argument("--port", type=int, default=8800, help="监听端口（默认 8800）")
    p_serve.add_argument("--no-open", action="store_true", help="不自动打开浏览器")

    args = parser.parse_args()
    config_path = Path(args.config) if args.config else appconfig.USER_DIR / "config.toml"

    if args.cmd == "serve":
        from .server import run as run_server

        run_server(port=args.port, open_browser=not args.no_open,
                   cfg=load(config_path), config_path=config_path)
        return

    cfg = load(config_path)
    reset_record = persona_reset.before_open()
    store = Store(appconfig.DB_PATH)
    try:
        persona_reset.after_open(store, cfg, reset_record)
        if not store.meta_get("session_epoch"):
            store.meta_set("session_epoch", uuid.uuid4().hex)
        persona.recover_imports(store)
        persona.recover_growth(store)
        if args.cmd == "memory":
            cmd_memory(store, args.layer)
        elif args.cmd == "recall":
            cmd_recall(store, args.query)
        elif args.cmd == "consolidate":
            _print_consolidation(consolidation.run(store, cfg))
            print("分层维护完成。")
        elif args.cmd == "compact":
            _run_maintenance_cmd(store, cfg, "compact")
        elif args.cmd == "tidy":
            _run_maintenance_cmd(store, cfg, "tidy")
        elif args.cmd == "tidy-rollback":
            from . import maintenance

            print(json.dumps(maintenance.tidy_rollback(store, cfg), ensure_ascii=False))
        elif args.cmd == "evolve":
            print(persona.evolve(store, make_client(cfg), cfg))
        elif args.cmd == "deliberate":
            from . import thought

            print(thought.deliberate(store, make_client(cfg), cfg))
        elif args.cmd == "seed":
            md = Path(args.file).read_text(encoding="utf-8")
            print(f"人格已安装：{persona.install_persona(store, cfg, md, client=make_client(cfg))}")
        elif args.cmd == "distill":
            print(skills.distill(store, make_client(cfg), cfg))
        else:
            cmd_chat(store, cfg)
    finally:
        store.close()


if __name__ == "__main__":
    main()
