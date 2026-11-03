"""生成 QQ 桥接入架构图 → wiki/harness研究/架构图/qq-bridge.png
运行：.venv/bin/python tools/make_qq_bridge_diagram.py
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


def _setup_font() -> None:
    for name in ["PingFang SC", "Hiragino Sans GB", "Arial Unicode MS", "Heiti SC", "STHeiti"]:
        if any(f.name == name for f in font_manager.fontManager.ttflist):
            plt.rcParams["font.sans-serif"] = [name]
            break
    plt.rcParams["axes.unicode_minus"] = False


_setup_font()

C = {
    "qq": "#1EBAFC", "bridge": "#4C72B0", "alice": "#55A868",
    "store": "#DD8452", "safety": "#C44E52", "note": "#666",
}

fig, ax = plt.subplots(figsize=(21, 13))
ax.set_xlim(0, 100)
ax.set_ylim(0, 82)
ax.axis("off")
ax.set_title("Alice QQ 接入 · 实现架构（v1 · 官方机器人 / 单所有者 / C2C 纯文字）",
             fontsize=21, pad=18, fontweight="bold")


def box(x, y, w, h, title, lines=(), color="#666", title_size=12.5, line_size=9,
        dashed=False, line_gap=2.4):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.35",
                 linewidth=0, facecolor="white", alpha=0.9, zorder=1))
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.35",
                 linewidth=1.6, edgecolor=color, facecolor="none",
                 linestyle="--" if dashed else "-", zorder=3))
    ax.text(x + w / 2, y + h - 1.8, title, ha="center", va="top", fontsize=title_size,
            fontweight="bold", color=color, zorder=4)
    for i, ln in enumerate(lines):
        ax.text(x + 1.2, y + h - 4.8 - i * line_gap, ln, ha="left", va="top",
                fontsize=line_size, color="#333", zorder=4)


def arrow(x1, y1, x2, y2, label="", color="#555", lw=1.6, lx=None, ly=None, fs=8.5, dashed=False):
    ax.annotate("", xy=(x2, y2), xytext=(x1, y1),
                arrowprops=dict(arrowstyle="-|>", color=color, lw=lw,
                                linestyle="--" if dashed else "-", shrinkA=2, shrinkB=2), zorder=5)
    if label:
        ax.text(lx if lx is not None else (x1 + x2) / 2,
                ly if ly is not None else (y1 + y2) / 2 + 0.9,
                label, ha="center", fontsize=fs, color=color, zorder=6,
                bbox=dict(fc="white", ec="none", alpha=0.9, pad=1.0))


# ============ 左：腾讯官方服务 ============
box(2, 38, 17, 26, "腾讯 QQ 官方服务", [
    "api.bot.qq.com",
    "· WebSocket 事件",
    "  C2C_MESSAGE_CREATE",
    "· POST /v2/users/{openid}",
    "  /messages（被动回复）",
    "· 扫码授权（第三方机器人）",
], color=C["qq"], line_size=8.8, line_gap=2.5)

# ============ 中：QQ 桥（独立进程） ============
box(23, 26, 34, 50, "QQ 桥  addons/qq（Node ≥22.19 · 独立进程）", [], color=C["bridge"])
ax.text(40, 71.9, "不监听公网 · 凭据不出本机 · runtime-lease 防双开", ha="center",
        fontsize=8.6, color=C["note"], style="italic", zorder=4)

# 两个入口
ax.add_patch(FancyBboxPatch((24.5, 62.5), 31, 7.2, boxstyle="round,pad=0.25",
             fc=C["bridge"], ec="none", alpha=0.12, zorder=2))
ax.text(25.7, 67.6, "web-control.mjs —— 网页控制进程（推荐）", fontsize=9.6,
        fontweight="bold", color=C["bridge"], zorder=4)
ax.text(25.7, 64.8, "JSONL 协议 · 安装组件 · 扫码 · 配对 · 启停", fontsize=8.2, color="#333", zorder=4)

ax.add_patch(FancyBboxPatch((24.5, 54.5), 31, 6.4, boxstyle="round,pad=0.25",
             fc=C["bridge"], ec="none", alpha=0.12, zorder=2))
ax.text(25.7, 58.9, "main.mjs —— 终端备用入口", fontsize=9.6,
        fontweight="bold", color=C["bridge"], zorder=4)
ax.text(25.7, 56.2, "scan / pair / config / run · 机器凭据 0600", fontsize=8.2, color="#333", zorder=4)

# bridge.mjs 核心状态机
ax.add_patch(FancyBboxPatch((24.5, 41.5), 31, 11.5, boxstyle="round,pad=0.25",
             fc=C["bridge"], ec="none", alpha=0.2, zorder=2))
ax.text(25.7, 50.8, "bridge.mjs —— 核心状态机", fontsize=10, fontweight="bold",
        color=C["bridge"], zorder=4)
steps = [
    "1. receive 收入校验（本人/±5min/幂等/纯文字）",
    "2. 排队 Ledger（deadline=消息时间+被动窗口）",
    "3. submit 提交 Alice（token · 六字段 · 幂等）",
    "4. 轮询 result（freezePlan 分段冻结 · 删 text）",
    "5. 逐段发送（report→QQ POST→回执上报）",
]
for i, t in enumerate(steps):
    ax.text(25.7, 48.4 - i * 2.0, t, fontsize=8.4, color="#333", zorder=4)

box(24.5, 32.0, 31, 6.2, "支撑：Ledger 原子账本 · SafeLog 脱敏 · reply-plan 分段",
    [], color="#8C8C00", title_size=8.6)

# ============ 右：Alice 服务 ============
box(62, 26, 36, 50, "Alice 服务（Python · 本机回环）", [], color=C["alice"])

ax.add_patch(FancyBboxPatch((63.5, 60), 33, 12.5, boxstyle="round,pad=0.25",
             fc=C["alice"], ec="none", alpha=0.12, zorder=2))
ax.text(64.7, 70.2, "server.py —— /api/channels/v1/*", fontsize=10, fontweight="bold",
        color=C["alice"], zorder=4)
for i, ln in enumerate([
    "machine token 鉴权（HMAC 比对 SHA256）",
    "渠道协议 v1 · protocol_version=1",
    "GUI 请求同协议归一（gui_request）",
    "管理操作只在网页 · 凭据不回显",
]):
    ax.text(64.7, 67.4 - i * 2.2, "· " + ln, fontsize=8.2, color="#333", zorder=4)

ax.add_patch(FancyBboxPatch((63.5, 45.5), 33, 12, boxstyle="round,pad=0.25",
             fc=C["safety"], ec="none", alpha=0.1, zorder=2))
ax.text(64.7, 55.2, "ChannelCoordinator —— 准入与状态机", fontsize=10,
        fontweight="bold", color=C["safety"], zorder=4)
for i, ln in enumerate([
    "session_epoch 纪元（重置即换，旧纪元 409）",
    "reserve 单槽预订（与 GUI _lock 互斥）",
    "source_key 幂等（重复投递不重复生成）",
    "时间窗 ±300s · 六字段校验 · 仅本人 C2C",
    "delivery 回执机（不可回退·confirmed 须真 ID）",
]):
    ax.text(64.7, 52.5 - i * 2.1, "· " + ln, fontsize=8.2, color="#333", zorder=4)

ax.add_patch(FancyBboxPatch((63.5, 28), 33, 15, boxstyle="round,pad=0.25",
             fc=C["store"], ec="none", alpha=0.1, zorder=2))
ax.text(64.7, 40.8, "同一个 Alice —— ChatService（单写者锁）", fontsize=10,
        fontweight="bold", color=C["store"], zorder=4)
for i, ln in enumerate([
    "channel_requests / turns / deliveries 三表",
    "QQ 轮次与 GUI 共用：记忆·思辨·演化 watcher",
    "压力卫生 · A01 出处校验 · 分寸机制照常生效",
    "回复生成 → reply_hash 冻结（账本）",
    "来源撤销/归档 → 未发分段停止",
]):
    ax.text(64.7, 38.1 - i * 2.1, "· " + ln, fontsize=8.2, color="#333", zorder=4)

# ============ 连接箭头 ============
arrow(19.2, 56, 22.8, 56, "WS 事件 /", color=C["qq"], lx=21, ly=59.5, fs=8)
arrow(22.8, 50, 19.2, 50, "HTTP 发消息", color=C["qq"], lx=21, ly=46.8, fs=8)
arrow(57.2, 54, 61.8, 54, "submit /\nresult", color=C["bridge"], lx=59.5, ly=57.5, fs=8)
arrow(61.8, 44, 57.2, 44, "delivery\n回执", color=C["bridge"], lx=59.5, ly=40.6, fs=8)
arrow(79.5, 26, 79.5, 23.2, color=C["store"], lw=1.4)
ax.text(80.3, 24.2, "SQLite（user/data/alice.db）", fontsize=8.2, color=C["store"], zorder=4)

# ============ 底部：发送纪律 ============
box(2, 4, 96, 15.5, "发送纪律（bridge.mjs 的安全内核）", [
    "QQ 实际发送前三重检查：时钟未回拨 · 被动窗口预算足 · 纪元与绑定仍有效；任何一项不过即 retry_wait/expire，不发",
    "频控全局共享：429 → not_before 全局推迟（改记录无法绕过）· 401 → 令牌刷新一次 · 每段最多 3 次尝试",
    "结果不明不自动重发：send_unknown 保守计账，完整回复保留在本机网页 · platform_confirmed 必须带真实消息 ID（回执=事实，不=权限）",
    "被动回复窗口耗尽 → expired，不转主动消息 · epoch 变化 → quarantined 隔离 · 账本写失败 → 立即停机",
], color=C["safety"], dashed=True, line_size=9.2, line_gap=2.9)
arrow(50, 26, 50, 19.8, color=C["safety"], dashed=True, lw=1.4)

fig.savefig(PROJECT / "wiki" / "harness研究" / "架构图" / "qq-bridge.png",
            dpi=150, bbox_inches="tight", facecolor="white")
print("已生成：wiki/harness研究/架构图/qq-bridge.png")
