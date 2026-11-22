"""命令行入口：python -m alice [命令]"""
from __future__ import annotations

import argparse
import json
import uuid
from datetime import datetime
from pathlib import Path

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import Config
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
    from . import retrieval

    print(retrieval.tool_recall(store, query))
    print("（注：命中会累计这些记忆的使用次数）")


def _seed_wizard(service) -> None:
    from . import persona
    from .config import PERSONA_FILE

    print(f"首次运行：为你的 {service.cfg.persona.name} 选择人格底色（选 2~4 个，输入数字，如 1,3,5）")
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
    service.seed(tags)
    print(f"人格底色已生成：{PERSONA_FILE}")


def _run_maintenance_cmd(store: Store, cfg: Config, kind: str) -> None:
    """双维护显式命令（设计草案 §5）：先预览（不改数据）再创建任务并同步执行。"""
    from . import maintenance
    from .llm import make_client
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


def cmd_chat(cfg: Config, config_path: Path) -> None:
    from . import persona
    from .loop import run_chat
    from .service import ChatService

    service = ChatService(cfg, config_path=config_path, _foreground_cli=True)
    entered_chat = False
    try:
        if persona.persona_md() is None:
            _seed_wizard(service)
        for event in service.prepare():
            print(f"（{event}）")
        entered_chat = True
        run_chat(service)
    finally:
        if entered_chat and cfg.maintenance.replaces_extraction:
            print("（退出前跳过同步提取：compact 模式已启用，待下次空闲压缩）")
        service.close(extract=entered_chat)


def main(argv: list[str] | None = None) -> int | None:
    parser = argparse.ArgumentParser(
        prog="alice",
        description="Alice — 陪伴型 harness（单一永久会话 + 三层记忆 + 可生长人格）",
    )
    parser.add_argument("--config", default=None, help="config.toml 路径（默认 user/config.toml）")
    parser.add_argument("--user-dir", default=None, help="独立用户根目录（绝对路径；含配置、记忆和人格）")
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
    p_worker = sub.add_parser("discovery-worker", help="内容发现独立工作进程（固定 JSON 行协议）")
    p_worker.add_argument("--input-bytes", type=int, default=192 * 1024)
    p_desktop = sub.add_parser("desktop-host", help="桌面宿主专用私有管道入口")
    p_desktop.add_argument("--port", type=int, default=0)
    p_desktop.add_argument("--lab-session", action="store_true", help="仅在全新临时档案开放 Lab 私有管道")
    p_desktop.add_argument("--lab-test-controls", action="store_true", help="为隔离 Lab 实例放宽调度和额度")
    p_maintenance = sub.add_parser("maintenance", help="离线备份、更新准备与明确确认后的数据恢复")
    p_maintenance.add_argument("action", choices=["status", "backup", "verify", "prepare", "restore", "rollback", "authorize"])
    p_maintenance.add_argument("--backup-id")
    p_maintenance.add_argument("--confirm", action="store_true", help="明确确认维护或恢复操作")
    p_maintenance.add_argument("--launch-nonce", help="只授权此一次桌面启动的随机标识")

    args = parser.parse_args(argv)
    if args.cmd == "desktop-host" and args.lab_test_controls and not args.lab_session:
        parser.error("仅隔离 Lab 实例可使用测试策略")
    if args.cmd == "desktop-host" and args.lab_session:
        if args.user_dir is not None or args.config is not None:
            parser.error("Lab 会话只允许新的独立档案，不能指定已有档案或配置")
        from tempfile import mkdtemp
        args.user_dir = mkdtemp(prefix="alice-lab-")
    if args.cmd == "maintenance" and args.user_dir is None:
        parser.error("离线维护必须显式提供 --user-dir 绝对用户根目录")
    if args.cmd == "discovery-worker":
        # Workers consume an admitted payload only, never configuration or Store.
        from .discovery_worker import main as worker_main
        return worker_main(["--input-bytes", str(args.input_bytes)])
    from .runtime_paths import configure_user_root
    try:
        configure_user_root(args.user_dir)
    except (ValueError, RuntimeError) as exc:
        parser.error(str(exc))
    # Path selection must precede all imports which cache data/persona roots.
    from . import config as appconfig
    from .config import load
    from .instance_lock import InstanceBusy, InstanceLock

    config_path = Path(args.config) if args.config else appconfig.USER_DIR / "config.toml"

    if args.cmd == "maintenance":
        from .profile_maintenance import command, public_error
        try:
            result = command(appconfig.USER_DIR, args.action, backup_id=args.backup_id,
                             confirmed=args.confirm, nonce=args.launch_nonce)
            print(json.dumps(result, ensure_ascii=False))
            return 0
        except Exception as exc:
            # Never print database contents, credentials, or provider exceptions.
            print(json.dumps({"error": public_error(exc)}, ensure_ascii=False))
            return 1

    try:
        if args.cmd == "desktop-host":
            if args.config and config_path.resolve() != (appconfig.USER_DIR / "config.toml").resolve():
                parser.error("桌面模式配置必须位于选定用户根目录内")
            from .desktop_host import main as desktop_main
            from .autonomy_policy import configure_lab
            configure_lab(test_controls=args.lab_test_controls)
            return desktop_main(port=args.port, config_path=config_path, lab_session=args.lab_session)
        if args.cmd == "serve":
            from .server import run as run_server

            run_server(port=args.port, open_browser=not args.no_open,
                       cfg=load(config_path), config_path=config_path)
            return
        if args.cmd is None:
            cmd_chat(load(config_path), config_path)
            return
        with InstanceLock(appconfig.DATA_DIR):
            _run_command(args, config_path)
    except InstanceBusy as exc:
        parser.exit(1, str(exc) + "\n")
    except RuntimeError as exc:
        from .profile_maintenance import MaintenanceRequired
        if isinstance(exc, MaintenanceRequired):
            parser.exit(1, str(exc) + "\n")
        raise


def _run_command(args, config_path: Path) -> None:
    from . import config as appconfig
    from . import consolidation, persona, persona_mode_install, persona_reset, skills, thought
    from .config import load
    from .llm import make_client
    from .store import Store

    cfg = load(config_path)
    reset_record = persona_reset.before_open()
    mode_change = persona_mode_install.read_change(appconfig.DB_PATH, config_path)
    reset_cfg = persona_mode_install.reset_config(cfg, mode_change, reset_record)
    store = Store(appconfig.DB_PATH)
    try:
        persona_reset.after_open(store, reset_cfg, reset_record)
        if not store.meta_get("session_epoch"):
            store.meta_set("session_epoch", uuid.uuid4().hex)
        persona.recover_imports(store)
        persona_mode_install.finish(store, cfg, appconfig.DB_PATH, config_path, mode_change, discard_uncommitted=True)
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
            print(thought.deliberate(store, make_client(cfg), cfg))
        elif args.cmd == "seed":
            md = Path(args.file).read_text(encoding="utf-8")
            print(f"人格已安装：{persona.install_persona(store, cfg, md)}")
        elif args.cmd == "distill":
            print(skills.distill(store, make_client(cfg), cfg))
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
