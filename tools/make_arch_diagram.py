"""生成 Alice harness 架构图 → docs/架构图.png
运行：.venv/bin/python tools/make_arch_diagram.py
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyBboxPatch  # noqa: E402

from tests.growth.charts import _setup_font  # noqa: E402

_setup_font()

C = {
    "channel": "#4C72B0", "loop": "#55A868", "memory": "#DD8452",
    "persona": "#C44E52", "llm": "#8172B3", "storage": "#9A9A9A",
    "lab": "#7f7f7f",
}

fig, ax = plt.subplots(figsize=(23, 16))
ax.set_xlim(0, 100)
ax.set_ylim(0, 100)
ax.axis("off")
ax.set_title("Alice Harness 架构图（v0.1 MVP）", fontsize=24, pad=16, fontweight="bold")


def box(x, y, w, h, title, lines=(), color="#666", title_size=13, line_size=10,
        dashed=False, line_gap=2.6):
    ax.add_patch(FancyBboxPatch(
        (x, y), w, h, boxstyle="round,pad=0.35",
        linewidth=0, edgecolor="none", facecolor="white", alpha=0.85, zorder=1,
    ))
    ax.add_patch(FancyBboxPatch(
        (x, y), w, h, boxstyle="round,pad=0.35",
        linewidth=1.6, edgecolor=color, facecolor="none",
        linestyle="--" if dashed else "-", zorder=3,
    ))
    ax.text(x + w / 2, y + h - 2.0, title, ha="center", va="top", fontsize=title_size,
            fontweight="bold", color=color, zorder=4)
    for i, ln in enumerate(lines):
        ax.text(x + 1.4, y + h - 5.4 - i * line_gap, ln, ha="left", va="top",
                fontsize=line_size, color="#333", zorder=4)


def arrow(x1, y1, x2, y2, label="", color="#555", lw=1.6,
          lx=None, ly=None, fs=9, dashed=False):
    ax.annotate(
        "", xy=(x2, y2), xytext=(x1, y1),
        arrowprops=dict(
            arrowstyle="-|>", color=color, lw=lw,
            linestyle="--" if dashed else "-", shrinkA=2, shrinkB=2,
        ), zorder=5,
    )
    if label:
        ax.text(lx if lx is not None else (x1 + x2) / 2,
                ly if ly is not None else (y1 + y2) / 2 + 0.9,
                label, ha="center", fontsize=fs, color=color, zorder=6,
                bbox=dict(fc="white", ec="none", alpha=0.88, pad=1.0))


# ================= 顶部：用户与渠道 =================
ax.text(50, 96.2, "用户", ha="center", fontsize=15, fontweight="bold",
        bbox=dict(boxstyle="circle,pad=0.45", fc="#EEE", ec="#666"))
box(27, 88.2, 46, 6.6, "渠道层 · CLI（alice/cli.py）",
    ["python -m alice ｜ memory ｜ recall ｜ consolidate ｜ evolve ｜ distill"],
    color=C["channel"], line_size=10)
arrow(50, 95.2, 50, 94.8, color=C["channel"])

# ================= 运行时核心容器 =================
ax.add_patch(FancyBboxPatch((3, 25), 94, 61, boxstyle="round,pad=0.4",
                            fc="#F7F8FA", ec="#BBBBBB", lw=1.3, zorder=0))
ax.text(5.0, 84.8, "运行时核心", fontsize=13, color="#888", style="italic", zorder=2)

# ---- 对话循环管线 ----
box(6, 74.2, 88, 9.2, "对话循环 loop.chat_turn —— 单一永久会话 = 每轮确定性重组上下文",
    color=C["loop"], title_size=14.5)
steps = [
    ("① 组装上下文\n（retrieval）", 8.5),
    ("② 调 LLM · 含工具轮\nrecall / remember", 30.0),
    ("③ 回复入库\n（store.add_turn）", 52.5),
    ("④ 触发器检查\n（数据量驱动）", 74.5),
]
for text, x in steps:
    ax.add_patch(FancyBboxPatch((x, 74.9), 17.0, 5.2, boxstyle="round,pad=0.25",
                                fc=C["loop"], ec="none", alpha=0.15, zorder=2))
    ax.text(x + 8.5, 77.5, text, ha="center", va="center", fontsize=10, zorder=4)
for i in range(3):
    x = steps[i][1] + 17.0
    arrow(x, 77.5, x + 4.5, 77.5, color=C["loop"], lw=1.5)
arrow(50, 88.2, 50, 83.5, color=C["channel"])

# ---- 左：上下文组装 ----
box(5, 45, 25, 26.5, "上下文组装（retrieval）", [
    "┌ 稳定前缀（吃 prompt 缓存）",
    "│ 1 宪法 · 三分法（不变）",
    "│ 2 人格文件 底色+生长（周级变）",
    "│ 3 记忆区（提取时变）：",
    "│    长期 top-k 全文·仓库索引·近期全文",
    "└ 4 近期原文 N 轮（每轮变）",
    "· 最新用户消息注入当前时间",
    "· 标注「未列出 = 不记得」",
], color=C["loop"], line_size=9.6, line_gap=2.5)
arrow(17.5, 74.2, 17.5, 71.6, color=C["loop"], label="① 读", lx=20.6, ly=73.2, fs=9)

# ---- 中：记忆子系统 ----
box(32.5, 45, 40, 26.5, "记忆子系统（store / extraction / consolidation）", [],
    color=C["memory"])
ax.text(34, 64.6, "对话轮次 ─每10用户轮·cheap模型→ 批量提取（含再提及计数）",
        ha="left", va="top", fontsize=9.6, color="#333", zorder=4)
layers = [
    ("活跃层 0~2天\n全文随取", 34.5, 0.30),
    ("仓库层 2~7天\n仅索引·按需 recall", 47.5, 0.22),
    ("长期层 幸存者\n检索 top-k 注入", 60.5, 0.30),
]
for text, x, alpha in layers:
    ax.add_patch(FancyBboxPatch((x, 58.4), 11.5, 6.0, boxstyle="round,pad=0.25",
                                fc=C["memory"], ec=C["memory"], alpha=alpha, lw=1.3, zorder=2))
    ax.text(x + 5.75, 61.4, text, ha="center", va="center", fontsize=9.3, zorder=4)
arrow(46.2, 61.4, 47.2, 61.4, color=C["memory"], lw=1.5, label="2天线", lx=46.8, ly=63.6, fs=8.8)
arrow(59.2, 61.4, 60.2, 61.4, color=C["memory"], lw=1.5, label="7天线", lx=59.8, ly=63.6, fs=8.8)
ax.text(52.5, 57.2, "七日线生存过滤：提及+使用≥1 或 重要度=1 → 晋升长期；否则物理删除",
        ha="center", va="top", fontsize=9.3, color="#8A4B08", zorder=4)
ax.text(34.3, 53.6, "recall 命中 → 使用次数+1（用进废退）· 长期层容量 2000 低分淘汰",
        ha="left", va="top", fontsize=9.3, color="#8A4B08", zorder=4)
ax.add_patch(FancyBboxPatch((42.5, 46.2), 21, 4.4, boxstyle="round,pad=0.25",
                            fc="#EEE", ec="#999", lw=1.2, zorder=2))
ax.text(53, 48.4, "墓碑（真实删除 · 仅存标题供审计）", ha="center", va="center",
        fontsize=9.3, color="#666", zorder=4)
arrow(50, 54.8, 50, 51.0, color="#999", lw=1.4, label="物理删除", lx=56.5, ly=53.2, fs=8.8)
arrow(76, 74.9, 72.3, 70.6, color=C["memory"], lw=1.4, dashed=True,
      label="remember 写入", lx=75.5, ly=72.4, fs=8.8)

# ---- 右：LLM 访问层 ----
box(74, 45, 22, 26.5, "LLM 访问层（llm / config）", [
    "· chat_model 对话/评审",
    "· cheap_model 提取/演化/提炼",
    "· 3 次重试 + 退避 · 120s 超时",
    "· disable_thinking（智谱）",
    "· 宽松 JSON 解析",
], color=C["llm"], line_size=9.6)
ax.text(85, 43.2, "OpenAI 兼容 API\n（智谱 / DeepSeek / …）", ha="center", fontsize=10,
        color=C["llm"], zorder=4,
        bbox=dict(boxstyle="round,pad=0.5", fc="white", ec=C["llm"], lw=1.3))
arrow(83, 74.2, 83, 71.6, color=C["llm"], label="②", lx=85.2, ly=73.2, fs=9)
arrow(85, 45.0, 85, 46.4, color=C["llm"])

# ---- 底排左：人格子系统 ----
box(5, 27.2, 40, 15.2, "人格子系统（persona）", [
    "标签种子 → 底色层（气质 · 极慢变）",
    "新记忆 ≥15 → 演化生长层：必须援引记忆依据",
    "护栏：结构校验 · 版本快照 · CHANGELOG",
    "· 坏输出不覆盖现有人格",
], color=C["persona"], line_size=9.6)
arrow(41, 45.0, 31, 42.5, color=C["persona"], lw=1.5,
      label="≥15 条新记忆 → evolve", lx=44.8, ly=44.2, fs=8.8)
arrow(17.5, 45.0, 23, 42.5, color=C["persona"], lw=1.2, dashed=True,
      label="注入上下文", lx=13.8, ly=44.2, fs=8.8)

# ---- 底排中：技能库 ----
box(48, 27.2, 24, 15.2, "技能库（skills）", [
    "新记忆 ≥30 → distill",
    "提炼「怎么做」 · 可溯源",
    "超额按使用频次淘汰",
], color="#8C8C00", line_size=9.6)
arrow(58, 45.0, 60, 42.5, color="#8C8C00", lw=1.5,
      label="≥30 条", lx=64.0, ly=44.2, fs=8.8)

# ---- 底排右：存储 ----
box(74.5, 27.2, 21.5, 15.2, "存储（本地 only）", [
    "data/alice.db",
    " 轮次·记忆·技能·墓碑·meta",
    "data/persona/persona.md",
    " + history/ + CHANGELOG",
], color=C["storage"], line_size=9.6)

# ================= 底部：横切测试 =================
box(3, 7.5, 94, 15.5, "横切层 · 测试与验证", [
    "离线冒烟 tests/smoke.py（27 项：分层 / 过滤 / 工具 / 演化护栏，FakeClient 零成本）　｜　成长实验室 tests/growth（平行人生 × 同底色，虚拟时钟压缩 21 天 → 1 小时）",
    "四维评估：B 成长广度（跨人生分化）· R 底色回归（标签保持 / 生长独立 / 助手腔）· H 诚实（伪造 / 真实 / 扭曲 / 知识边界探针）· J 拟人（量表 / 盲选辨识 / 文体）",
    "产出：report.md + report.html（图表内嵌）+ data.json（可离线重分析）+ lives/（每个人生的 DB 与人格快照）",
], color=C["lab"], dashed=True, line_size=9.8, line_gap=2.9)
arrow(50, 25.0, 50, 23.2, color=C["lab"], dashed=True)

fig.savefig(PROJECT / "docs" / "架构图.png", dpi=150, bbox_inches="tight",
            facecolor="white")
print("已生成：docs/架构图.png")
