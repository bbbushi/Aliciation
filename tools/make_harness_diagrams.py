"""生成代表性 harness 架构图 → docs/harness研究/架构图/*.png
运行：.venv/bin/python tools/make_harness_diagrams.py
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


def new_fig(title, w=13.5, h=8.8):
    fig, ax = plt.subplots(figsize=(w, h))
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 100)
    ax.axis("off")
    ax.set_title(title, fontsize=17, pad=12, fontweight="bold")
    return fig, ax


def box(ax, x, y, w, h, title, lines=(), color="#666", title_size=11.5,
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


def arrow(ax, x1, y1, x2, y2, label="", color="#555", lw=1.7,
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


# ═══════════════ 1. pi：极简分层 + 会话树 + 自扩展 ═══════════════

fig, ax = new_fig("pi —— 极简主义 harness：四层组件 · 会话树 · 自扩展")
PI = "#4C72B0"
box(ax, 6, 74, 52, 12, "pi-coding-agent（成品 CLI）",
    ["会话 · 斜杠命令 · 主题 · 项目上下文"], color="#55A868")
box(ax, 6, 59, 52, 12, "pi-agent-core（Agent 循环）",
    ["推理⇄工具⇄回喂 · 全程可中断（保留部分结果）· 消息队列 · 无 max-steps"], color="#DD8452")
box(ax, 6, 44, 52, 12, "pi-ai（LLM 抽象）",
    ["四种 API 风格归一 · 跨 provider 交接 · 工具校验 · 成本统计"], color="#8172B3")
box(ax, 6, 32, 52, 9, "pi-tui（终端 UI）",
    ["保留模式组件 · 差分渲染 · 无闪烁"], color="#9A9A9A")
for y in (74, 59, 44):
    arrow(ax, 32, y, 32, y - 3, color="#888")
ax.text(32, 27.5, "定位：一组搭你自己的 agent 的 SDK 组件（OpenClaw 即基于此）",
        ha="center", fontsize=10.5, style="italic", color="#666")

# 会话树
box(ax, 64, 55, 31, 31, "会话 = 可分支的树（干净 JSON）", [], color=PI)
ax.plot([70, 70, 78, 78], [80, 66, 66, 61], color=PI, lw=1.6, zorder=4)
ax.plot([70, 86], [80, 80], color=PI, lw=1.6, zorder=4)
ax.plot([86, 92], [80, 75], color=PI, lw=1.6, zorder=4)
for (x, y, t) in [(70, 81.5, "主线"), (78, 59.5, "支线任务\n（不污染主线）"),
                  (86, 81.5, "回溯"), (92, 73.5, "新分支")]:
    ax.text(x, y, t, ha="center", va="center", fontsize=8.8, color="#333", zorder=6,
            bbox=dict(fc="white", ec=PI, lw=0.8, boxstyle="round,pad=0.25"))
ax.text(79.5, 56.5, "可恢复 · 可导出 · 允许混用多 provider 消息",
        ha="center", fontsize=8.5, color="#666")

# 扩展 + 信条
box(ax, 64, 30, 31, 21, "扩展机制（TypeScript · 无 MCP）", [
    "· 热重载，agent 可现场给自己写扩展",
    "· 外部能力 = CLI 工具 + README",
    "  （模型按需自读自调，渐进披露）",
    "· 拒绝：MCP / 子代理 / 后台任务",
], color="#C44E52", line_size=8.8, line_gap=3.0)
ax.add_patch(FancyBboxPatch((14, 8), 72, 8, boxstyle="round,pad=0.4",
                            fc=PI, ec=PI, alpha=0.12, lw=1.5, zorder=2))
ax.text(50, 12, "信条：4 个工具（read/write/edit/bash）· 系统提示 + 工具定义 < 1000 token\n"
                "工具越少，行为越可预测 —— RL 训练的前沿模型不需要调教过的提示",
        ha="center", va="center", fontsize=10.5, color=PI, fontweight="bold", zorder=4)
fig.savefig(OUT / "pi.png", dpi=150, bbox_inches="tight", facecolor="white")
plt.close(fig)

# ═══════════════ 2. Claude Code：生产级功能主义 ═══════════════

fig, ax = new_fig("Claude Code —— 生产级功能主义：丰富能力面 × 多层控制")
CC = "#D97757"
steps = [("理解", 10, 62), ("计划 plan mode", 30, 62), ("执行（工具调用）", 52, 62), ("验证", 74, 62)]
for t, x, y in steps:
    ax.add_patch(FancyBboxPatch((x, y), 17, 9, boxstyle="round,pad=0.3",
                                fc=CC, ec=CC, alpha=0.15, lw=1.5, zorder=2))
    ax.text(x + 8.5, y + 4.5, t, ha="center", va="center", fontsize=11,
            fontweight="bold", color="#8A4B2B", zorder=4)
for i in range(3):
    x = steps[i][1] + 17
    arrow(ax, x, 66.5, x + 3, 66.5, color=CC)
arrow(ax, 82.5, 62, 20, 57, color=CC, dashed=True, lw=1.3)
ax.text(52, 58.5, "未通过 → 带反馈回到执行", ha="center", fontsize=9, color=CC)

box(ax, 6, 28, 26, 24, "工具面（大而全）", [
    "· read / edit / write / bash",
    "· grep / glob（渐进披露：",
    "  不预载代码库，按需搜索）",
    "· MCP 外接生态（任意服务器）",
], color="#55A868", line_size=9.2, line_gap=3.1)
box(ax, 36, 28, 28, 24, "安全与控制（多层门）", [
    "· Hooks：pre/post-tool 钩子",
    "· allow / ask / deny 三态权限",
    "· plan mode：方案经人确认再动手",
    "· Subagents：隔离子上下文",
], color="#C44E52", line_size=9.2, line_gap=3.1)
box(ax, 68, 28, 26, 24, "状态与扩展", [
    "· CLAUDE.md 项目记忆（外置）",
    "· PLAN.md / TODO.md 任务状态",
    "· compact 会话压缩",
    "· Skills：按需加载的能力包",
], color="#8172B3", line_size=9.2, line_gap=3.1)
arrow(ax, 19, 52, 15, 62, color="#55A868", lw=1.4)
arrow(ax, 50, 52, 45, 62, color="#C44E52", lw=1.4)
arrow(ax, 81, 52, 80, 62, color="#8172B3", lw=1.4)
ax.add_patch(FancyBboxPatch((14, 8), 72, 8, boxstyle="round,pad=0.4",
                            fc=CC, ec=CC, alpha=0.12, lw=1.5, zorder=2))
ax.text(50, 12, "信条：给前沿模型充分但受控的能力面 —— 与 pi 每一极相反，但内部自洽\n"
                "（行业影响：pre-tool hook 的 allow/ask/deny 已成 2026 安全范式）",
        ha="center", va="center", fontsize=10.5, color="#8A4B2B", fontweight="bold", zorder=4)
fig.savefig(OUT / "claude-code.png", dpi=150, bbox_inches="tight", facecolor="white")
plt.close(fig)

# ═══════════════ 3. SWE-agent：ACI 接口设计论 ═══════════════

fig, ax = new_fig("SWE-agent —— Agent-Computer Interface：接口即能力")
SW = "#DD8452"
box(ax, 30, 78, 40, 12, "LM Agent", ["推理 · 决策（ReAct 循环）"], color="#8172B3")
box(ax, 18, 48, 64, 22, "ACI —— Agent-Computer Interface（全部交互收窄于此瓶颈）", [
    "· 精简的文件查看 / 编辑命令（专为 LM 设计，非人类 shell）",
    "· 防呆编辑器：改完自动 lint，出错立即结构化反馈",
    "· 受限 shell + 护栏：接口设计本身决定 agent 成败",
], color=SW, line_size=9.5, line_gap=3.4)
box(ax, 26, 22, 48, 14, "执行沙箱", [
    "隔离环境：repo + 测试套件 + 依赖",
    "issue 进 → 修改代码 → 跑测试 → patch 出",
], color="#55A868", line_size=9.5, line_gap=3.2)
arrow(ax, 50, 78, 50, 70.4, color=SW, bidir=True)
arrow(ax, 50, 48, 50, 36.4, color="#55A868", bidir=True)
box(ax, 4, 44, 12, 30, "SWE-bench", [
    "真实",
    "GitHub",
    "issue",
    "基准：",
    "接口改动",
    "→ 回归",
    "→ 数据",
    "说话",
], color="#4C72B0", line_size=8.5, line_gap=3.0)
arrow(ax, 16, 59, 18, 59, color="#4C72B0", dashed=True)
ax.add_patch(FancyBboxPatch((14, 6), 72, 9, boxstyle="round,pad=0.4",
                            fc=SW, ec=SW, alpha=0.12, lw=1.5, zorder=2))
ax.text(50, 10.5, "核心发现：同一模型，仅改 ACI 设计（命令集 / 反馈格式 / 护栏），issue 解决率显著变化\n"
                  "「harness 是第一等设计对象」的学术定论来源（NeurIPS 2024）",
        ha="center", va="center", fontsize=10.5, color="#8A4B08", fontweight="bold", zorder=4)
fig.savefig(OUT / "swe-agent.png", dpi=150, bbox_inches="tight", facecolor="white")
plt.close(fig)

# ═══════════════ 4. OpenClaw：常驻个人助理 ═══════════════

fig, ax = new_fig("OpenClaw —— 常驻个人助理 harness（基于 pi SDK）")
OC = "#55A868"
chan = ["Telegram", "WhatsApp", "终端 / CLI", "…多渠道"]
for i, c in enumerate(chan):
    ax.add_patch(FancyBboxPatch((8 + i * 23, 82), 19, 7, boxstyle="round,pad=0.3",
                                fc="#4C72B0", ec="#4C72B0", alpha=0.15, lw=1.4, zorder=2))
    ax.text(8 + i * 23 + 9.5, 85.5, c, ha="center", va="center", fontsize=9.8,
            color="#2E4A73", zorder=4)
ax.text(50, 79.5, "渠道层（消息归一化）", ha="center", fontsize=10, color="#888", style="italic")
box(ax, 14, 58, 72, 17, "常驻网关（7×24 进程）", [
    "· 会话管理与路由（单用户长期状态） · 消息收发队列",
    "· 权限门：执行型操作需用户放行（allow/ask/deny）",
], color=OC, line_size=10, line_gap=3.6)
arrow(ax, 26, 82, 26, 75.4, color="#4C72B0")
arrow(ax, 74, 82, 74, 75.4, color="#4C72B0")
box(ax, 14, 36, 72, 17, "pi SDK 底座", [
    "· pi-ai（LLM 抽象 · 跨 provider） · pi-agent-core（循环 · 可中断）",
    "· 会话文件（可分支）+ 外置记忆 / 计划文件",
], color="#8172B3", line_size=10, line_gap=3.6)
arrow(ax, 50, 58, 50, 53.4, color=OC)
box(ax, 5, 10, 42, 20, "工具生态", [
    "· cron 定时任务（主动性来源）",
    "· 浏览器（获取信息）",
    "· 消息工具 · MCP 桥接",
], color="#DD8452", line_size=9.5, line_gap=3.4)
box(ax, 53, 10, 42, 20, "对 Alice 的参考点（二代半）", [
    "· 常驻网关 = 渠道层的成熟形态",
    "· cron 主动性 = 生产力语义；",
    "  Alice 需换成陪伴语义（分寸感）",
], color="#C44E52", line_size=9.5, line_gap=3.4, dashed=True)
arrow(ax, 26, 30, 26, 36, color="#DD8452", lw=1.4)
ax.text(50, 5.5, "定位：通用个人助理（生产力+生活）——与 Alice 同走「关系连续性」路线的最近亲",
        ha="center", fontsize=10.5, style="italic", color="#666")
fig.savefig(OUT / "openclaw.png", dpi=150, bbox_inches="tight", facecolor="white")
plt.close(fig)

print(f"已生成 4 张图 → {OUT}")
