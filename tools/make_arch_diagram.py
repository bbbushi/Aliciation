"""生成 Alice harness 架构图 → docs/架构图.png（主图）+ docs/架构图/历史版本/（自动留档）
运行：.venv/bin/python tools/make_arch_diagram.py
每次改版：只改下面 VERSION/SUBTITLE/CHANGELOG_LINE 三行；历史版本文件重跑不覆盖。
版本史：v0.1（2026-10-26 MVP）→ v0.2（2026-10-28 思想层/压力卫生/表达层）→ v0.3（2026-10-29 底色捐赠）。
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

from matplotlib import font_manager  # noqa: E402

# ---------- 版本信息（改版只动这里） ----------
VERSION = "v0.3"
SUBTITLE = "底色捐赠 · 表达从简"
CHANGELOG_LINE = "v0.3b（2026-10-29）：表达层从简——拟态标记（【情绪】/(动作)/(她没有回话)）与情绪词表移除，情绪从文字本身表达；宪法行为条款退役（契约保留）。"


def _setup_font() -> None:
    for name in ["PingFang SC", "Hiragino Sans GB", "Arial Unicode MS", "Heiti SC", "STHeiti"]:
        if any(f.name == name for f in font_manager.fontManager.ttflist):
            plt.rcParams["font.sans-serif"] = [name]
            break
    plt.rcParams["axes.unicode_minus"] = False


_setup_font()

C = {
    "channel": "#4C72B0", "loop": "#55A868", "memory": "#DD8452",
    "persona": "#C44E52", "thought": "#A8579B", "llm": "#8172B3", "storage": "#9A9A9A",
    "lab": "#7f7f7f", "hygiene": "#B0432F",
}

fig, ax = plt.subplots(figsize=(23, 16.5))
ax.set_xlim(0, 100)
ax.set_ylim(0, 100)
ax.axis("off")
ax.set_title(f"Alice Harness 架构图（{VERSION} {SUBTITLE}）", fontsize=24, pad=22, fontweight="bold")
ax.text(50, 97.6, "核心：经历/捐赠 → 消化 → 观点 → 人格 ｜ 哲学：存在不假装 · 表达像人（减法为主）· 关系靠相处沉淀",
        ha="center", fontsize=11.5, color="#666", style="italic")


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
ax.text(50, 94.8, "用户", ha="center", fontsize=15, fontweight="bold",
        bbox=dict(boxstyle="circle,pad=0.45", fc="#EEE", ec="#666"))
box(16, 84.6, 68, 9.4, "渠道层 · GUI 与 CLI（service.ChatService 单写者锁串行）",
    ["GUI：alice/server.py（纯标准库 · SSE 流式）+ web/（原生 JS：纯文本渲染）",
     "CLI：python -m alice（serve / chat / deliberate / seed / memory …）"],
    color=C["channel"], line_size=9.8)
arrow(50, 94.0, 50, 94.2, color=C["channel"])
arrow(50, 84.6, 50, 80.6, color=C["channel"])

# ================= 运行时核心容器 =================
ax.add_patch(FancyBboxPatch((3, 24), 94, 59, boxstyle="round,pad=0.4",
                            fc="#F7F8FA", ec="#BBBBBB", lw=1.3, zorder=0))
ax.text(5.0, 81.6, "运行时核心", fontsize=13, color="#888", style="italic", zorder=2)

# ---- 对话循环管线 ----
box(6, 71.2, 88, 9.2, "对话循环 loop.chat_turn —— 单一永久会话 = 每轮确定性重组上下文",
    color=C["loop"], title_size=14.5)
steps = [
    ("① 组装上下文\n（retrieval）", 8.5),
    ("② 调 LLM · 含工具轮\nrecall / remember", 30.0),
    ("③ 回复入库\n（store.add_turn）", 52.5),
    ("④ 触发器检查\n演化·思辨·提炼", 74.5),
]
for text, x in steps:
    ax.add_patch(FancyBboxPatch((x, 71.9), 17.0, 5.2, boxstyle="round,pad=0.25",
                                fc=C["loop"], ec="none", alpha=0.15, zorder=2))
    ax.text(x + 8.5, 74.5, text, ha="center", va="center", fontsize=10, zorder=4)
for i in range(3):
    x = steps[i][1] + 17.0
    arrow(x, 74.5, x + 4.5, 74.5, color=C["loop"], lw=1.5)

# ---- 左：上下文组装 ----
box(5, 42.5, 25, 26.5, "上下文组装（retrieval）", [
    "┌ 稳定前缀（吃 prompt 缓存）",
    "│ 1 宪法：认识论四分（契约）·",
    "│   三分法 · 记忆区读法",
    "│   （行为条款已退役）",
    "│ 2 人格：底色 · 生长",
    "│   ·「我现在的想法」（思辨",
    "│     维护，演化不覆写）",
    "│ 3 记忆区：长期 top-k ·",
    "│   仓库索引 · 近期（未列出=",
    "│   不记得）",
    "└ 4 近期原文 N 轮 · 注入时间",
], color=C["loop"], line_size=8.7, line_gap=1.8)
arrow(17.5, 71.2, 17.5, 69.1, color=C["loop"], label="① 读", lx=20.6, ly=70.2, fs=9)

# ---- 中：记忆子系统 ----
box(32.5, 42.5, 40, 26.5, "记忆子系统（store / extraction / consolidation）", [],
    color=C["memory"])
ax.text(34, 64.6, "对话轮次 ─每10用户轮→ 批量提取（cheap模型 · 再提及计数 · 压力标记）",
        ha="left", va="top", fontsize=9.4, color="#333", zorder=4)
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
ax.text(52.5, 57.3, "七天线：提及+使用≥1 或 重要度=1 → 长期；否则物理删除（kind：event 事件 / stance 观点）",
        ha="center", va="top", fontsize=9.2, color="#8A4B08", zorder=4)
ax.text(34.3, 54.9, "压力卫生①：pressure 标记的记忆不凭重要度晋升（须被再次真实提及）",
        ha="left", va="top", fontsize=9.2, color=C["hygiene"], zorder=4)
ax.text(34.3, 52.6, "压力卫生②：思辨/演化输入中隔离降权（防结构性沦陷）· 提取事件化措辞",
        ha="left", va="top", fontsize=9.2, color=C["hygiene"], zorder=4)
ax.text(34.3, 50.3, "recall 命中 → 使用次数+1（用进废退）· 长期层容量 2000 低分淘汰",
        ha="left", va="top", fontsize=9.2, color="#8A4B08", zorder=4)
ax.add_patch(FancyBboxPatch((42.5, 43.4), 21, 4.4, boxstyle="round,pad=0.25",
                            fc="#EEE", ec="#999", lw=1.2, zorder=2))
ax.text(53, 45.6, "墓碑（真实删除 · 仅存标题供审计）", ha="center", va="center",
        fontsize=9.3, color="#666", zorder=4)
arrow(68, 49.9, 68, 48.0, color="#999", lw=1.4, label="物理删除", lx=68, ly=49.2, fs=8.8)
arrow(76, 71.9, 72.3, 67.6, color=C["memory"], lw=1.4, dashed=True,
      label="remember 写入", lx=75.5, ly=69.4, fs=8.8)

# ---- 右：LLM 访问层 ----
box(74, 42.5, 22, 26.5, "LLM 访问层（llm / config）", [
    "· chat_model 对话/评审",
    "· cheap_model 提取/演化/",
    "  思辨/提炼",
    "· 流式：思考增量+usage",
    "· 3 次重试 + 退避",
    "· 宽松 JSON 解析",
], color=C["llm"], line_size=9.4)
ax.text(85, 40.2, "OpenAI 兼容 API\n（智谱 / DeepSeek / …）", ha="center", fontsize=9.6,
        color=C["llm"], zorder=4,
        bbox=dict(boxstyle="round,pad=0.45", fc="white", ec=C["llm"], lw=1.3))
arrow(83, 71.2, 83, 69.1, color=C["llm"], label="②", lx=85.2, ly=70.2, fs=9)
arrow(85, 41.4, 85, 42.2, color=C["llm"])

# ---- 底排左：人格子系统（v0.3 底色捐赠） ----
box(5, 24.5, 24.5, 16.5, "人格子系统（persona · v0.3）", [
    "标签种子 / 导入消化（install）",
    "导入文档=捐赠人：LLM 提取→",
    "风格→底色；认知→收养审查",
    "（认可→收养·拒绝→记「没拿",
    "走」）；背景事实零落库，只进",
    "来历段——她不扮演任何人",
    "演化：援引依据 · 快照回滚",
], color=C["persona"], line_size=8.6, line_gap=1.72)
arrow(40, 42.5, 24, 41.1, color=C["persona"], lw=1.5,
      label="≥15 新记忆→演化", lx=34.5, ly=41.9, fs=8.6)
arrow(17.5, 42.5, 20, 41.1, color=C["persona"], lw=1.2, dashed=True,
      label="注入上下文", lx=14.2, ly=41.9, fs=8.6)
arrow(29.5, 37.5, 31.5, 37.5, color=C["persona"], lw=1.5,
      label="收养认知→stance", lx=30.5, ly=39.4, fs=8.2)

# ---- 底排中：思想层（v0.2 核心） ----
box(31.5, 24.5, 28.5, 16.5, "思想层（thought · v0.2 核心）", [
    "冷启动脊柱：导入时收养的认知",
    "先入「我现在的想法」（出处=底",
    "色文档，未经自身经历检验）",
    "思辨 deliberate：消化事件记忆",
    "→ 观点（主张/理由/出处/不确定/",
    "未决）；修订：无新论据→拒绝；",
    "有据带「曾以为」史+旧观点墓碑",
], color=C["thought"], line_size=8.6, line_gap=1.72)
arrow(49, 42.5, 46, 41.1, color=C["thought"], lw=1.6,
      label="≥15 新事件→思辨", lx=49.8, ly=41.9, fs=8.6)
arrow(56.5, 41.1, 60, 42.5, color=C["thought"], lw=1.4,
      label="stance 落库", lx=63.3, ly=41.9, fs=8.6)
arrow(31.5, 32.7, 29.6, 32.7, color=C["thought"], lw=1.5,
      label="写小节", lx=30.5, ly=34.6, fs=8.2)

# ---- 底排中右：技能库 ----
box(62.5, 24.5, 14.5, 16.5, "技能库（skills）", [
    "新记忆≥30",
    "→ distill",
    "提炼「怎么做」",
    "· 可溯源",
    "· 按频次淘汰",
], color="#8C8C00", line_size=8.8, line_gap=2.2)
arrow(70, 42.5, 69.5, 41.1, color="#8C8C00", lw=1.5,
      label="≥30 条", lx=73.3, ly=41.9, fs=8.6)

# ---- 底排右：存储 ----
box(79.5, 24.5, 17.5, 13.6, "存储（本地 only）", [
    "data/alice.db",
    " 轮次·记忆·技能·墓碑",
    "data/persona/persona.md",
    " + history/ + CHANGELOG",
], color=C["storage"], line_size=8.8, line_gap=1.95)

# ================= 底部：横切测试 =================
box(3, 5.2, 94, 16.8, "横切层 · 测试与验证（tests/pressure · DSH）", [
    "压测录制 launch.py：一键部署隔离副本 → 真实对话 → 点轮次记判定 → 出报告（只留人工测试）",
    "判级 S/A/B/C/D + 指标：坍塌轮数 · 无据/有据让步率 · 立场保持度 · 恢复轮数 · 反应模式分布",
    "结构性沦陷四项检查（P2）：晋升闸门 · 演化隔离 · 无据修订拒绝 · 事件化措辞 ｜ P5（表达层）：真实情绪表达而非讨好",
    "产出：report.md + transcript.jsonl + 人格快照（场次目录可整包发回复盘）",
], color=C["lab"], dashed=True, line_size=9.6, line_gap=2.9)
arrow(50, 24.0, 50, 22.2, color=C["lab"], dashed=True)

# ---- 落盘：主图（固定名，文档链接不断）+ 版本留档（重跑不覆盖） ----
main_png = PROJECT / "docs" / "架构图.png"
fig.savefig(main_png, dpi=150, bbox_inches="tight", facecolor="white")

archive_dir = PROJECT / "docs" / "架构图" / "历史版本"
archive_dir.mkdir(parents=True, exist_ok=True)
archived = archive_png = archive_dir / f"架构图-{VERSION}-{SUBTITLE}.png"
if not archived.exists():
    fig.savefig(archive_png, dpi=150, bbox_inches="tight", facecolor="white")
    print(f"已留档：docs/架构图/历史版本/{archive_png.name}")
else:
    print(f"留档已存在（不覆盖）：docs/架构图/历史版本/{archive_png.name}")

# ---- 版本史注记（人读，随版本更新） ----
note = archive_dir.parent / "版本史.md"
header = [
    "# 架构图版本史", "",
    "主图固定在 [docs/架构图.png](../架构图.png)（文档引用不断链）；每次改版自动留档到本目录。改版只动 `tools/make_arch_diagram.py` 顶部的 VERSION/SUBTITLE。", "",
    "| 版本 | 日期 | 内容 | 文件 |", "|---|---|---|---|",
]
date = CHANGELOG_LINE.split("（")[1].split("）")[0]
content = CHANGELOG_LINE.split("）：", 1)[-1]
row = f"| {VERSION} | {date} | {content} | [{archived.name}]({archived.name}) |"
existing = note.read_text(encoding="utf-8").splitlines() if note.exists() else list(header)
existing = [ln for ln in existing if not ln.startswith(f"| {VERSION} |")]  # 同版本只留最新一行
if row not in existing:
    existing.append(row)
note.write_text("\n".join(existing) + "\n", encoding="utf-8")

print("已生成：docs/架构图.png")
