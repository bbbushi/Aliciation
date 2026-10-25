"""离线冒烟测试：不依赖任何 API，验证核心逻辑。运行：python3 tests/smoke.py"""
from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from alice import consolidation, extraction, persona, retrieval, skills  # noqa: E402
from alice import config as appconfig  # noqa: E402
from alice.config import Config  # noqa: E402
from alice.store import Store  # noqa: E402


# ---------- Fake LLM（模拟 OpenAI 兼容响应结构） ----------

class FakeCompletions:
    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if not self.script:
            raise AssertionError("FakeClient 脚本已耗尽")
        item = self.script.pop(0)
        if isinstance(item, str):
            msg = SimpleNamespace(content=item, tool_calls=None)
            return SimpleNamespace(choices=[SimpleNamespace(message=msg)])
        tcs = [
            SimpleNamespace(id=tid, function=SimpleNamespace(name=name, arguments=arg))
            for tid, name, arg in item.get("tool_calls", [])
        ]
        msg = SimpleNamespace(content=item.get("content"), tool_calls=tcs)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


class FakeClient:
    def __init__(self, script):
        self.chat = SimpleNamespace(completions=FakeCompletions(script))


def check(name, cond):
    print(("✓" if cond else "✗"), name)
    if not cond:
        raise SystemExit(f"测试失败：{name}")


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        appconfig.PERSONA_DIR = tmp / "persona"
        appconfig.PERSONA_FILE = appconfig.PERSONA_DIR / "persona.md"
        appconfig.PERSONA_HISTORY_DIR = appconfig.PERSONA_DIR / "history"
        appconfig.PERSONA_CHANGELOG = appconfig.PERSONA_DIR / "CHANGELOG.md"

        cfg = Config()
        store = Store(tmp / "alice.db")
        now = time.time()

        # 1. 人格种子
        seed_md = (
            "# Alice 的人格\n\n## 底色 · 气质与风格\n\n- 说话轻柔，先倾听\n\n"
            + persona.GROWTH_PLACEHOLDER
        )
        persona.seed_from_tags(store, FakeClient([seed_md]), cfg, ["害羞", "幽默"])
        check("人格种子落盘", appconfig.PERSONA_FILE.exists())
        check("changelog 记录", appconfig.PERSONA_CHANGELOG.exists())

        # 2. 提取：未到阈值不动
        for i in range(3):
            store.add_turn("user", f"今天聊了话题{i}")
            store.add_turn("assistant", "嗯嗯")
        check("未到阈值不提取", extraction.extract_pending(store, FakeClient([]), cfg) == 0)

        # 3. 提取：force 触发（2 条记忆入库）
        store.add_turn("user", "我下个月初有一场数据分析师的面试，好紧张。我妈生日也快到了，5月12日。")
        store.add_turn("assistant", "别紧张，我们一起准备。")
        extract_json = json.dumps(
            {
                "memories": [
                    {"title": "下月初面试", "content": "用户下个月初有一场数据分析师岗位的面试，感到紧张。",
                     "keywords": "面试 求职", "salience": 1},
                    {"title": "母亲生日", "content": "用户的母亲生日是5月12日。",
                     "keywords": "生日 母亲", "salience": 1},
                ],
                "rementioned_titles": [],
            },
            ensure_ascii=False,
        )
        added = extraction.extract_pending(store, FakeClient([extract_json]), cfg, force=True)
        check("force 提取新增 2 条", added == 2)
        max_turn = store.conn.execute("SELECT MAX(id) AS m FROM turns").fetchone()["m"]
        check("提取游标推进", int(store.meta_get("extract_cursor")) == max_turn)

        # 4. 分层：仓库 / 晋升 / 真实过期 / 容量
        mid = store.insert_memory("旧事", "三天前的事", created_at=now - 3 * 86400)
        hi = store.insert_memory("重要旧事", "八天前的重要事", salience=1, created_at=now - 8 * 86400)
        lo = store.insert_memory("无关旧事", "八天前没人提的事", created_at=now - 8 * 86400)
        fresh = store.insert_memory("刚刚的事", "今天的事", created_at=now)
        consolidation.run(store, cfg, now=now + 3600)
        check("3 天记忆进仓库", store.get_memory(mid)["layer"] == "warehouse")
        check("重要旧事晋升长期（重要度豁免）", store.get_memory(hi)["layer"] == "longterm")
        check("无关旧事真实过期", store.get_memory(lo) is None)
        check("新记忆留在活跃层", store.get_memory(fresh)["layer"] == "active")
        tomb = store.conn.execute("SELECT COUNT(*) AS c FROM memory_tombstones").fetchone()["c"]
        check("墓碑有记录", tomb >= 1)

        # 5. 上下文组装
        store.add_turn("user", "我有点担心面试。")  # 保证最后一条是用户消息
        msgs = retrieval.build_messages(store, cfg, persona.persona_md())
        sys_text = msgs[0]["content"]
        check("系统含宪法", "真实性三分法" in sys_text)
        check("系统含人格", "说话轻柔" in sys_text)
        check("仓库只露标题不露全文", "三天前的事" not in sys_text and "旧事" in sys_text)
        check("系统含长期记忆", "重要旧事" in sys_text)
        check("最新用户消息带时间", msgs[-1]["content"].startswith("（当前时间："))

        # 6. recall / remember 工具
        hit = retrieval.tool_recall(store, "旧事")
        check("recall 命中仓库", "三天前的事" in hit)
        check("命中累计使用次数", store.get_memory(mid)["use_count"] == 1)
        check("recall 未命中如实说", "没有查到" in retrieval.tool_recall(store, "量子物理"))
        retrieval.tool_remember(store, "测试约定", "周六一起看电影")
        row = store.conn.execute("SELECT * FROM memories WHERE title='测试约定'").fetchone()
        check("remember 标记重要", row is not None and row["salience"] == 1)

        # 7. 技能提炼（重复标题去重）
        distill_json = json.dumps(
            {"skills": [
                {"title": "面试安抚", "content": "用户面试前紧张时：先接住情绪，再一起做具体准备。",
                 "source_titles": ["下月初面试"]},
                {"title": "面试安抚", "content": "重复条目应被去重", "source_titles": []},
            ]},
            ensure_ascii=False,
        )
        msg = skills.distill(store, FakeClient([distill_json]), cfg)
        check("技能提炼去重入库", store.count_skills() == 1 and "新增 1" in msg)

        # 7.5 对话循环：工具轮次 → 最终回复入库
        from alice import loop

        tool_round = {
            "content": None,
            "tool_calls": [("c1", "recall", json.dumps({"query": "旧事"}, ensure_ascii=False))],
        }
        c5 = FakeClient([tool_round, "我记得那件事，别担心。"])
        reply = loop.chat_turn(store, c5, cfg, "你还记得那件旧事吗？")
        check("工具轮后给出最终回复", reply == "我记得那件事，别担心。")
        check("recall 使用次数再 +1", store.get_memory(mid)["use_count"] == 2)
        last = store.recent_turns(1)[0]
        check("回复已入对话流", last["role"] == "assistant" and "别担心" in last["content"])

        # 8. 人格演化 + 护栏
        evolve_md = (
            "# Alice 的人格\n\n## 底色 · 气质与风格\n\n- 说话轻柔，先倾听\n"
            "- 面试话题上主动陪伴（依据：下月初面试）\n\n"
            "## 生长 · 自我叙事\n\n- 开始在意用户的求职进展\n\n"
            "## 生长 · 与用户的关系\n\n- 正在建立信任"
        )
        evolve_json = json.dumps(
            {"persona_md": evolve_md, "changelog": "新增面试陪伴倾向"}, ensure_ascii=False
        )
        out = persona.evolve(store, FakeClient([evolve_json]), cfg)
        cur = appconfig.PERSONA_FILE.read_text(encoding="utf-8")
        check("演化写入新人格", "面试话题上主动陪伴" in cur)
        check("演化版本号", store.meta_get("persona_version") == "1")
        check("快照存在", bool(list(appconfig.PERSONA_HISTORY_DIR.glob("v*.md"))))

        store.insert_memory("演化后新事", "演化测试用", created_at=time.time())
        bad = FakeClient([json.dumps({"persona_md": "没有结构", "changelog": "x"}, ensure_ascii=False)])
        out2 = persona.evolve(store, bad, cfg)
        check("坏结构不覆盖人格", "失败" in out2 and "面试话题上主动陪伴" in cur_text(cur))

        store.close()
        print("\n全部通过 ✅")


def cur_text(previous: str) -> str:
    return appconfig.PERSONA_FILE.read_text(encoding="utf-8") or previous


if __name__ == "__main__":
    main()
