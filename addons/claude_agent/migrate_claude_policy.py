"""S1 数据迁移（claude-agent 分支限定，起服前一次性执行）。

设计修正说明：原设计 §五 说"worker_policy 单行 → 多行"，实查后发现其
CHECK(id=1) 是 schema 硬约束，SQLite 改约束需重建表——为不动 DSH 正典的表，
本迁移采用**代理行方案**：claude 的政策放独立表 claude_policy（含 enabled/
ack/budget/revision），原 worker_policy 一字不动。工具枚举名单化（S4）读两个源。
风险：零（不改 DSH 任何数据）；回退：DROP TABLE claude_policy 即可。

铁律：先跑本脚本再起服（数据层撞车教训）。幂等：重复执行跳过。
"""
from __future__ import annotations

import shutil
import sqlite3
import sys
import time
from pathlib import Path

DB = Path("user/data/alice.db")


def main() -> int:
    if not DB.is_file():
        print(f"未找到 {DB}（应从仓库根运行）")
        return 1
    backup = DB.with_name(f"alice.db.before-claude-policy-{time.strftime('%Y%m%d-%H%M%S')}.bak")
    shutil.copyfile(DB, backup)
    conn = sqlite3.connect(DB)
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS claude_policy (
      id INTEGER PRIMARY KEY CHECK(id=1),
      revision INTEGER NOT NULL DEFAULT 1,
      enabled INTEGER NOT NULL DEFAULT 0,
      native_sandbox_ack INTEGER NOT NULL DEFAULT 0,
      workspace_root TEXT NOT NULL DEFAULT '',
      max_turns INTEGER NOT NULL DEFAULT 12,
      budget_usd_per_task REAL NOT NULL DEFAULT 0.50,
      budget_usd_per_day REAL NOT NULL DEFAULT 2.00,
      updated_at INTEGER NOT NULL DEFAULT 0
    );
    """)
    row = conn.execute("SELECT id FROM claude_policy WHERE id=1").fetchone()
    if row is None:
        conn.execute("INSERT INTO claude_policy (id, updated_at) VALUES (1, strftime('%s','now'))")
        conn.commit()
        print(f"已建 claude_policy（默认禁用）。备份：{backup.name}")
    else:
        print("claude_policy 已存在，跳过（幂等）。")
    left = [n for (n,) in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '%policy%' ORDER BY name")]
    print("政策表现状：", ", ".join(left))
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
