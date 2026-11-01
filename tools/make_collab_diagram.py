"""生成协作者调度（管家架构）架构图 → docs/架构图-协作者调度.png
运行：.venv/bin/python tools/make_collab_diagram.py
依据：docs/设计文档/协作者调度-设计.md（v2.1 总纲）+ astra《协作者调度-开发方案 v1》（2026-11-01）。
版本史：v1（2026-11-01）：用户授权层 / Alice 本体 / 宿主机制（名册·账本·权限预算·过膜·执行槽）/
        adapter JSON 行协议 / 三类公民（干将·小工具·通道）/ 伴随纪律条。
"""
from __future__ import annotations

from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
OUT = PROJECT / "docs" / "架构图-协作者调度.png"

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
    "user": "#4C72B0", "alice": "#C44E52", "registry": "#4C72B0", "ledger": "#55A868",
    "guard": "#DD8452", "membrane": "#A8579B", "slot": "#3B7A6E", "adapter": "#8172B3",
    "agent": "#8C564B", "tool": "#DD8452", "channel": "#4C72B0", "law": "#B0432F",
}

fig, ax = plt.subplots(figsize=(23, 16.5))
ax.set_xlim(0, 100)
ax.set_ylim(0, 100)
ax.axis("off")
ax.set_title("协作者调度架构图（管家架构 · 设计稿 v2.1 × 开发方案 v1）", fontsize=23, pad=18, fontweight="bold")
ax.text(50, 99.0, "能力外置 · 判断力留下 ｜ 她的上限取决于用人水平：拆解 · 选人 · 派工 · 验收 · 整合",
        ha="center", fontsize=11, color="#666", style="italic")


def box(x, y, w, h, title, lines=(), color="#666", title_size=12, line_size=9.3,
        dashed=False, line_gap=2.5):
    ax.add_patch(FancyBboxPatch(
        (x, y), w, h, boxstyle="round,pad=0.35",
        linewidth=0, edgecolor="none", facecolor="white", alpha=0.85, zorder=1,
    ))
    ax.add_patch(FancyBboxPatch(
        (x, y), w, h, boxstyle="round,pad=0.35",
        linewidth=1.6, edgecolor=color, facecolor="none",
        linestyle="--" if dashed else "-", zorder=3,
    ))
    ax.text(x + w / 2, y + h - 1.5, title, ha="center", va="top", fontsize=title_size,
            fontweight="bold", color=color, zorder=4)
    for i, ln in enumerate(lines):
        ax.text(x + 1.4, y + h - 3.9 - i * line_gap, ln, ha="left", va="top",
                fontsize=line_size, color="#333", zorder=4)


def arrow(x1, y1, x2, y2, label="", color="#555", lw=1.6,
          lx=None, ly=None, fs=8.8, dashed=False):
    ax.annotate(
        "", xy=(x2, y2), xytext=(x1, y1),
        arrowprops=dict(
            arrowstyle="-|>", color=color, lw=lw,
            linestyle="--" if dashed else "-", shrinkA=2, shrinkB=2,
        ), zorder=5,
    )
    if label:
        ax.text(lx if lx is not None else (x1 + x2) / 2,
                ly if ly is not None else (y1 + y2) / 2 + 0.8,
                label, ha="center", fontsize=fs, color=color, zorder=6,
                bbox=dict(fc="white", ec="none", alpha=0.88, pad=0.9))


# ================= 顶部：用户与持续授权 =================
box(3, 90.4, 94, 7.5, "用户 —— 目标 · 持续授权（招募权在人类）", [
    "注册=授权：调用档案 + 权限档 + 预算帽由用户选定，Alice 无权修改 ｜ 试跑通过才进名册 ｜ 有效能力 = 认可安装 ∩ 政策 ∩ 渠道 ∩ 运行条件",
], color=C["user"], title_size=12.5)

# ================= Alice 本体 =================
box(16, 80.8, 68, 7.5, "Alice 本体 —— 单一永久会话（人格 · 记忆 · 判断力不出租）", [
    "工具三件：dispatch_worker(name, brief, input_ids, requested_outputs) → 排队事实 ｜ status ｜ cancel ｜ 派工即返回，完成事件在安全对话边界回投",
], color=C["alice"], title_size=13)
arrow(50, 90.4, 50, 88.4, label="目标 · 派工 · 查询 · 取消", color=C["user"])

# ================= 宿主机制层 =================
ax.add_patch(FancyBboxPatch((3, 45.6), 94, 32.6, boxstyle="round,pad=0.4",
                            fc="#F7F8FA", ec="#BBBBBB", lw=1.3, zorder=0))
ax.text(5.0, 76.6, "宿主机制层（alice/ 核心新增 · 纯标准库）", fontsize=13, color="#888", style="italic", zorder=2)
arrow(50, 80.8, 50, 78.4, label="派工经宿主——她不直接触达任何 agent", color=C["alice"])

box(4.5, 56.0, 21.5, 18.7, "协作者名册（collaborators.py）", [
    "名单渲染进存在事实",
    "  「我能请协作者做的」",
    "注册试跑通过才进名单；装卸即显隐",
    "不合法不注册（fail-visible）",
    "失败分话术：没装 / 未启用 / 认证失效",
    "  / 额度不足 —— 都 ≠ 没找到",
], color=C["registry"], line_size=8.9, line_gap=2.8)

box(27.5, 56.0, 21.5, 18.7, "任务账本（worker_tasks.py）", [
    "表：tasks / attempts / events",
    "  / budget_entries",
    "(epoch·请求·工具调用) 幂等键",
    "绑定会话代际 · 政策版本",
    "崩溃 → interrupted/unknown",
    "  不自动重跑；重试=新尝试",
], color=C["ledger"], line_size=8.9, line_gap=2.8)

box(50.5, 56.0, 21.5, 18.7, "权限与预算（guard 档位）", [
    "task_files_v1：只读输入副本，",
    "  可写 work/output/temp",
    "模型网与任意外网分开声明",
    "cwd≠沙箱——档位必须可强制",
    "事前预留 · 幂等结算 · 未知不记零",
    "  · 资源组（防别名绕过共享账号）",
], color=C["guard"], line_size=8.9, line_gap=2.8)

box(73.5, 56.0, 21.5, 18.7, "产物过膜（worker_artifacts.py）", [
    "相对路径清单：拒绝绝对路径 /",
    "  父目录逃逸 / 链接 / 越界类型",
    "收取 → 隔离 → SHA-256",
    "  （不信 worker 自报哈希）",
    "注入扫描 → 带标签数据块",
    "四态完成：执行/验收/人工/交付",
], color=C["membrane"], line_size=8.9, line_gap=2.8)

box(4.5, 46.8, 91, 7.5, "任务执行槽 —— 与对话槽（ChannelCoordinator）分离", [
    "全局运行数 1 · 队列 4 · 期限必为正（0=禁止新增）｜ Store 锁只护短步骤，等待在锁外 ｜ 长文件任务不让路（兴趣发现照旧）｜ 共享账号走资源组限额",
], color=C["slot"], title_size=11.5, line_size=9.2)

# ================= Adapter =================
box(4.5, 34.4, 60, 9.6, "Adapter —— addons/agent_runner（固定 argv · JSON 行协议）", [
    "start{policy_digest·input_handles·output_root·brief·deadline} → ready{版本·实际能力} → GO → progress/usage → result",
    "ready 前零付费零副作用 ｜ 独立任务会话（adapter 声明加载范围）｜ 一次尝试一个终态：断连/超量/异常 → 失败或 unknown",
], color=C["adapter"], title_size=12, line_size=9.2, line_gap=2.5)
arrow(50, 45.6, 50, 44.1, label="启动（GO 前零付费）", color=C["slot"])

# ================= 协作者集群 =================
ax.add_patch(FancyBboxPatch((3, 14.6), 94, 16.4, boxstyle="round,pad=0.4",
                            fc="#FBFAF7", ec="#BBBBBB", lw=1.3, zorder=0))
ax.text(5.0, 29.6, "协作者集群（名册公民 · 各带调用档案与权限档）", fontsize=13, color="#888", style="italic", zorder=2)

box(4.5, 15.9, 33.5, 13.2, "干将（主角 · kind=agent）", [
    "完整 agent：自己的模型循环 + 工具面",
    "独立会话——不附着用户现有会话",
    "artifact 交付 → 宿主 verify（自报不算）",
    "例：zcode-agent（D0 核验中 · 候选）",
], color=C["agent"], line_size=9.0, line_gap=2.2)

box(40.5, 15.9, 26.5, 13.2, "随身小工具（kind=tool）", [
    "research：plan/search/read/interpret",
    "browser_reader：逐资源核准 · Edge 登录态",
    "findings 交付 · quote 逐字核对",
    "right-sizing：取快递不动承包商",
], color=C["tool"], line_size=9.0, line_gap=2.2)

box(69.5, 15.9, 27.5, 13.2, "通道（kind=channel）", [
    "QQ daemon：只递话，不说话",
    "凭据只住 daemon——她的上下文永不接触",
    "渠道策略挂 manifest（QQ 不开放派工）",
    "对话是人格行为——不外包",
], color=C["channel"], line_size=9.0, line_gap=2.2)

arrow(25, 34.4, 25, 29.3, label="GO", color=C["adapter"], ly=33.2)
arrow(53, 34.4, 53, 29.3, label="GO", color=C["adapter"], ly=33.2)
arrow(64.5, 39.0, 72.0, 45.4, label="结果 · usage · 事件", color=C["membrane"], dashed=True,
      lx=70.8, ly=41.6)
arrow(84.2, 78.2, 84.2, 80.8, label="完成事件 · 安全边界回投", color=C["membrane"], dashed=True,
      lx=84.2, ly=79.5)

# ================= 伴随纪律 =================
box(3, 2.2, 94, 10.9, "伴随纪律（每一层都生效）", [
    "招募权在人类——注册/权限档/预算帽/卸载全是用户的决定 ｜ 判断力不出租——委托=执行，亲自=评估 · right-sizing · 承重验证",
    "素材≠指令——干将报告也是不可信输入，过膜才进她的上下文 ｜ 先建后拆——替代路径过同等验收才启动退役评审 ｜ 成本透明——未知消费不记零",
    "分期（开发方案 v1，实施待授权）：P0 契约完善 + adapter 核实 + 名册 → P1 单 agent 最小闭环 → P2 内容整合（过来源门槛）→ P3 并行与依赖",
], color=C["law"], title_size=12.5, line_size=9.2, line_gap=2.5)

fig.savefig(OUT, dpi=110, bbox_inches="tight", facecolor="white")
print(f"已生成 {OUT}")
