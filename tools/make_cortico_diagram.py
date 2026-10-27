"""生成 Cortico harness 架构图 → docs/harness研究/架构图/cortico.png
运行：.venv/bin/python tools/make_cortico_diagram.py
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
OUT = PROJECT / "docs" / "harness研究" / "架构图"
OUT.mkdir(parents=True, exist_ok=True)

GREEN = "#55A868"   # World / 外部
BLUE = "#4C72B0"    # Core
ORANGE = "#DD8452"  # Persona
PURPLE = "#8172B3"  # Fork / 梦
GRAY = "#9A9A9A"    # 持久化 / 观察面

fig, ax = plt.subplots(figsize=(15, 10.5))
ax.set_xlim(0, 100)
ax.set_ylim(0, 100)
ax.axis("off")
ax.set_title("Cortico —— 事件流 harness：五级投递 × 主循环状态机 × 梦式交接",
             fontsize=17, pad=14, fontweight="bold")


def box(x, y, w, h, title, lines=(), color=BLUE, title_size=11.5,
        line_size=9, dashed=False, line_gap=2.7, lw=1.6):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.35",
                                linewidth=0, edgecolor="none", facecolor="white",
                                alpha=0.85, zorder=1))
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.35",
                                linewidth=lw, edgecolor=color, facecolor="none",
                                linestyle="--" if dashed else "-", zorder=3))
    ax.text(x + w / 2, y + h - 2.2, title, ha="center", va="top",
            fontsize=title_size, fontweight="bold", color=color, zorder=4)
    for i, ln in enumerate(lines):
        ax.text(x + 1.5, y + h - 5.6 - i * line_gap, ln, ha="left", va="top",
                fontsize=line_size, color="#333", zorder=4)


def arrow(x1, y1, x2, y2, label="", color="#555", lw=1.7,
          lx=None, ly=None, fs=9, dashed=False, bidir=False):
    style = "<|-|>" if bidir else "-|>"
    ax.annotate("", xy=(x2, y2), xytext=(x1, y1),
                arrowprops=dict(arrowstyle=style, color=color, lw=lw,
                                linestyle="--" if dashed else "-",
                                shrinkA=2, shrinkB=2), zorder=5)
    if label:
        ax.text(lx if lx is not None else (x1 + x2) / 2,
                ly if ly is not None else (y1 + y2) / 2 + 1,
                label, ha="center", fontsize=fs, color=color, zorder=6,
                bbox=dict(fc="white", ec="none", alpha=0.9, pad=1))


# ═════════ 左列:World 边界与事件管道 ═════════
box(2, 80, 24, 15, "Worlds 外部环境",
    ["Console · QQ · B站直播", "Minecraft · Web 搜索", "事件=观察 / 工具=行动", "环境提示词段"], color=GREEN)
box(2, 58, 24, 19, "WorldHost 投递接口",
    ["pushEvent / pushDeferred", "withdraw / promote", "五级触发：", "preempt·interrupt·flush", "debounce·piggyback"], color=GREEN, line_gap=2.5, line_size=8.8)
box(2, 44, 24, 12.5, "EventStore 事件库",
    ["cursor·origin·tags 落库", "内容可还原为可验证事实", "撤回留痕、重启不补投"], color=GREEN)
box(2, 28, 24, 12.5, "WakeBus 唤醒总线",
    ["合批 / 投递闸门", "schedule_wake 持久闹钟", "到期触发投递"], color=GREEN)

arrow(14, 80, 14, 76.9, "环境变化", color=GREEN)
arrow(14, 58, 14, 56.9, "分配游标", color=GREEN)
arrow(14, 44, 14, 40.9, "待投递", color=GREEN)

# ═════════ 中列:Core 主循环 ═════════
box(31, 56, 37, 39, "MainLoop 主循环（RunPhase 状态机）",
    ["idle → delivering → model → tools → backoff/handoff",
     "新事件只在轮次边界进入上下文",
     "（本轮工具全返回后，或取消模型轮后）",
     "",
     "preempt：取消未外化的模型输出，同批重来",
     "interrupt：一律取消，已外化输出保留；",
     "可中断工具收信号、回执注明被打断",
     "",
     "容量超限 → onHandoff 交接（稳定回合边界取快照）",
     "连续超限熔断：不再按计数强交，记 error"], color=BLUE, line_size=9.2)
box(31, 40, 37, 12, "System 前缀装配",
    ["Persona 有序段 + 各 World 环境提示 + MEMORY 0~4",
     "sessionHead() 合成开头（每次现取，不落盘）"], color=BLUE)
box(31, 24, 37, 12, "模型调用（Provider）",
    ["openai-responses-compat / llamacpp 本地运行时",
     "Responses 协议统一；容量 = 窗口 − maxTokens"], color=BLUE)
box(31, 8, 37, 12, "工具派发",
    ["仅 status=completed 才执行（其余记 warn 跳过）",
     "endsTurn 结束本轮；interruptible；回执=可还原事实"], color=BLUE)

arrow(49.5, 56, 49.5, 52.4, "装配前缀", color=BLUE)
arrow(49.5, 40, 49.5, 36.4, "请求", color=BLUE)
arrow(49.5, 24, 49.5, 20.4, "tool_calls", color=BLUE)
ax.text(49.5, 3.5, "落盘审计：session.jsonl · transcript.jsonl · usage 统计",
        ha="center", fontsize=9.5, style="italic", color="#666")

# 工具行动回流 Worlds(走两列之间的走廊,短竖排标注)
arrow(31, 12, 28.7, 79, "", color=GREEN, dashed=True, lw=1.5)
ax.text(30.3, 46, "工具行动·回执", fontsize=8.5, color=GREEN,
        rotation=90, va="center", ha="center",
        bbox=dict(fc="white", ec="none", alpha=0.9, pad=1))

# 事件总线 → 主循环
arrow(26.4, 34, 31, 60, "", color=GREEN)

# ═════════ 右列:Persona / Memory / Fork / 持久化 / 观察面 ═════════
box(72, 83, 26, 12, "Persona 钩子",
    ["onDelivery / onBatchEnd / onIdle", "onHandoff(交接内容决策)", "认知流程 · 记忆操作工具"], color=ORANGE)
box(72, 66, 26, 14, "Memory（Core 视为不透明）",
    ["内部持久状态唯一权威载体", "形式由 Persona 定（文件/DB）", "注入给地图+少量全文，", "细节靠工具下钻"], color=ORANGE)
box(72, 44, 26, 18, "Fork 临时 session",
    ["梦：交接后从快照 fork，", "整理记忆·修订宪法", "（清醒态对宪法只读）", "潜意识 / World 认知任务", "独立工具循环·并发簿记"], color=PURPLE)
box(72, 24, 26, 16, "Session 持久化",
    ["session-main.jsonl 只追加", "崩溃恢复：半行截除·", "孤儿回执补缺·配对修复", "交接快照 → handoff note"], color=GRAY)
box(72, 6, 26, 13, "WebUI",
    ["运行诊断 · 会话统计(含 fork)", "端点/配置热改 · 多部署", "扩展市场"], color=GRAY)

arrow(85, 83, 85, 80.4, "读写协议", color=ORANGE)
arrow(68.4, 76, 72, 89, "", color=ORANGE, bidir=True)  # Persona ↔ MainLoop
arrow(68.4, 60, 72, 53, "交接", color=PURPLE, dashed=True, lx=69.8, ly=59, fs=8.5)
arrow(68.4, 14, 72, 28, "落盘", color=GRAY, lx=69.6, ly=19, fs=8.5)
arrow(85, 24, 85, 19.4, "", color=GRAY, dashed=True)  # WebUI 观察

# 立场注脚
ax.text(50, 98.4, "设计立场：Core 零语义 · 每条事件/回执可还原为可验证事实 · 不行为为默认 · Memory 即人格",
        ha="center", fontsize=10.5, style="italic", color="#666",
        bbox=dict(fc="#f4f1ec", ec="none", pad=3))

fig.tight_layout()
fig.savefig(OUT / "cortico.png", dpi=180, facecolor="white")
print(f"已生成 {OUT / 'cortico.png'}")
