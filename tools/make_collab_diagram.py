"""生成协作者调度实装架构图（P1 正典：DSH 桥路径）→ docs/架构图-协作者调度.png
运行：.venv/bin/python tools/make_collab_diagram.py
版本史：
  v1（2026-11-01）：设计稿 v2.1 × 开发方案 v1 的设计图（manifest/GO 门控/本地帧协议）。
  v2（2026-11-02）：**当前实装**架构图——撞车裁决后正典：dsh_agent 桥路径全链路
    （五工具/WorkerRuntime 准入与账本/DshBridge HTTP/Koishi 插件/DSH NEXT 专属会话）
    + 凭据 DPAPI + GUI 协作面板 + 留档 P3 本地轨（虚线）+ 边界与未开放如实标注。
    依据：main@PR#19 实装代码（3732ffe + PR#17/19）。
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
    "user": "#4C72B0", "alice": "#C44E52", "runtime": "#55A868", "ledger": "#3B7A6E",
    "gui": "#8172B3", "bridge": "#8C564B", "plugin": "#DD8452", "dsh": "#C44E52",
    "cred": "#4C72B0", "p3": "#7f7f7f", "ruler": "#A8579B", "law": "#B0432F",
}

fig, ax = plt.subplots(figsize=(23, 16.5))
ax.set_xlim(0, 100)
ax.set_ylim(0, 100)
ax.axis("off")
ax.set_title("协作者调度实装架构图（P1 · DSH 桥路径 · main@PR#19）", fontsize=23, pad=18, fontweight="bold")
ax.text(50, 99.3, "对话槽与任务槽分离 ｜ 判断力不出租：权限/账本/准入在宿主，派工与追问在 Alice，执行在 DSH",
        ha="center", fontsize=11, color="#666", style="italic")


def box(x, y, w, h, title, lines=(), color="#666", title_size=12, line_size=9.3,
        dashed=False, line_gap=2.2):
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


# ================= 主列（自上而下） =================
box(3, 89.8, 94, 8.7, "用户 —— GUI 授权与持续政策", [
    "「设置→DSH」生成独立随机令牌：Windows DPAPI 加密存专用凭据文件，明文不进网页/配置/数据库/聊天 ｜ 断开保留令牌与授权",
    "授权：工作目录 + 原生只读/工作区写模式 + 执行次数 + 单轮时限 ｜ 同范围后续派工不再逐次确认（权限收窄随时可做）",
], color=C["user"], title_size=12.5, line_size=9.2)

box(3, 78.4, 60, 8.7, "Alice 本体 —— 单一永久会话（对话槽 ChannelCoordinator 保留）", [
    "五工具（GUI 挂载）：dispatch_worker(name=\"dsh-agent\") ｜ worker_task_status ｜ worker_task_read ｜ send_worker_message 追问",
    "worker_refs 按渠道 scope 进上下文：只交付状态与不透明句柄，报告原文不直入永久会话（契约 9.1）",
], color=C["alice"], title_size=12.5, line_size=9.2)

box(3, 60.2, 60, 16.5, "WorkerCoordinator / WorkerRuntime —— 宿主机制层（service._workers）", [
    "_admit_locked 准入：政策 revision + epoch + worker_target 绑定请求与工具调用",
    "独立任务槽：1 执行 + 4 待办（_next_job_locked）｜ _supervise 监督线程 ｜ 取消意图 ≠ 已取消",
    "持久账本 worker_tasks.py：policy/tasks/messages/attempts/events/requests/reports/management_*",
    "四态完成：执行 / 验收(结构) / 人工 / 交付 ｜ 连接丢失保留 unknown，不重发不猜测",
    "追问/新建每次预留一次执行次数（取消不退）；次数/token 非金额硬帽，如实分标",
], color=C["runtime"], title_size=12.5, line_size=9.2, line_gap=2.5)

box(3, 49.4, 60, 8.7, "worker_transport.DshBridge —— HTTP 桥客户端", [
    "固定 127.0.0.1:18765 ｜ 协议 alice-dsh-plugin-bridge-v1 ｜ 独立令牌鉴权（32~256 位）",
    "不 import addon、不启动 DSH、不读取其个人认证 ｜ 超时 5s，桥不可达 → bridge_unavailable（uncertain）",
], color=C["bridge"], title_size=12, line_size=9.2)

box(3, 38.2, 60, 8.7, "bridge_plugin（Koishi/cordis 0.4.0）—— DSH 本机 bundle", [
    "无有效凭据不监听 ｜ hello + 专属任务 create/send/status/events/cancel/close ｜ 核对原生模式与 approval=never",
    "令牌经 DPAPI 凭据文件自动配对 ｜ 只操作插件自建会话，不改日常会话与全局默认",
], color=C["plugin"], title_size=12, line_size=9.2)

box(3, 25.6, 60, 10.9, "DSH NEXT —— 专属会话（被调度工人）", [
    "Desktop 2.0.21-next / Harness 0.2.1-rc.4 / Cordis 4.0.5（固定 D:/dshNEXT/DSH NEXT）",
    "权限档 dsh_native_v1：原生权限非 OS 沙箱——任务外读取/网络无法强隔离，用户 GUI 明确接受后才可派工",
    "本轮用授权目录相对文件名；DSH 自报文件 ≠ Alice 已收取或核验（文件 ID 导入/产物下载未开放）",
], color=C["dsh"], title_size=12.5, line_size=9.2)

# ================= 右列 =================
box(66, 79.9, 31, 8.7, "凭据（worker_credentials）", [
    "Windows CurrentUser DPAPI 加密，令牌仅由 DshBridge 出示鉴权",
    "重启免重授权；更换走高级设置",
], color=C["cred"], line_size=9.0)

box(66, 59.2, 31, 19.7, "GUI 协作面板（worker-tasks.js）", [
    "任务卡：状态/停止/结果与工作记录",
    "已读游标按站点/epoch/协作者保存，",
    "  跨标签单调同步、删除标记防迟到",
    "归档/恢复/删除同步设置",
    "  （默认关闭，删除禁用）",
    "正文/标题/凭据不入浏览器存储",
    "右侧固定面板 + 主区头像入口",
], color=C["gui"], line_size=9.0)

box(66, 44.8, 31, 13.1, "留档轨（P3 · 未接线）", [
    "本地 CLI 帧协议（git 历史 f3acba0）：",
    "  GO 门控 / 256KiB 帧上限 / 整树回收",
    "worker_artifacts.py + testbed 已进 main",
    "  ——待本地 CLI 干将（zcode 等）启用",
], color=C["p3"], line_size=9.0, dashed=True)

box(66, 34.8, 31, 8.7, "验收标尺（0 项通过·待人工）", [
    "契约 §六 + astra §5 三硬门槛",
    "对方 A01~A12 真实场景清单",
    "已有真实派工证据；产物未验收",
], color=C["ruler"], line_size=9.0)

# ================= 主链路箭头 =================
arrow(25, 89.8, 25, 88.4, color=C["user"])
arrow(25, 78.4, 25, 76.9, label="派工（只返回排队事实）", color=C["alice"])
arrow(25, 60.2, 25, 58.7, label="HTTP", color=C["runtime"])
arrow(25, 49.4, 25, 48.1, color=C["bridge"])
arrow(25, 38.2, 25, 36.9, color=C["plugin"])
arrow(63, 54, 66.3, 63, label="事件 / 回报", color=C["ledger"], dashed=True, lx=64.6, ly=58.5)
arrow(55, 75.5, 55, 78.3, label="worker_refs · 状态句柄", color=C["ledger"], dashed=True,
      lx=55, ly=76.9)

# ================= 底部：边界与未开放 =================
box(3, 10.6, 94, 13.1, "边界与未开放（如实标注）", [
    "未开放：文件 ID 导入 / 产物下载 / 跨任务来源转发 / 多任务并行 ｜ 单任务引用保留草稿、worker_target 绑定请求与 epoch",
    "边界：取消只针对自有会话（agent 空闲 ≠ 进程树全退）；明确关授权/改参清正文且 GUI 确认；同参恢复不撤销",
    "已保留：对话槽与单写者锁、GUI 自主检索、正式见闻、QQ 账本 v2、browser_reader ｜ 对话内检索 E11 bug（candidate/url）待修",
], color=C["law"], title_size=12.5, line_size=9.2, line_gap=2.5)

ax.text(50, 9.2, "待设计 P2：来源契约（worker 报告以「待核实建议」进见闻管线）｜ P3：本地 CLI 干将轨 ｜ 服务运行中（main@PR#19，数据层撞车已清）",
        ha="center", fontsize=9.5, color="#888", style="italic")

fig.savefig(OUT, dpi=110, bbox_inches="tight", facecolor="white")
print(f"已生成 {OUT}")
