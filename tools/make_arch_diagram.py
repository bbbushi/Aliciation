"""生成 Alice harness 架构图 → docs/架构图.png（主图）+ docs/架构图/历史版本/（自动留档）
运行：.venv/bin/python tools/make_arch_diagram.py
每次改版：只改下面 VERSION/SUBTITLE/CHANGELOG_LINE 三行；历史版本文件重跑不覆盖。
版本史：v0.1（2026-10-26 MVP）→ v0.2（2026-10-28 思想层/压力卫生/表达层）→ v0.3（2026-10-29 底色捐赠）→ v0.4（2026-10-31 观点生命周期）→ v0.5（2026-11-04 共同组装与协作）。
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
VERSION = "v0.5"
SUBTITLE = "共同组装与协作"
CHANGELOG_LINE = "v0.5（2026-11-04）：上下文共同组装实装——C0 块级装配清单与总预算（100k+回复预留4k·每次调用前终检·宪法四态）、C1 记忆索引（memory_search/load·nonce 引用·六态回执）、C2 跨轮工作集（context_keep/release·TTL/容量/版本/代际裁决）、C3 旧原文只读检索与逐字引文前情（发言者标签）；Step1 模型调用观测九处（unknown 不记零）；人格 P0 导入管线（两阶段提交）·P1a/P1b 表达参考（锚默认关）；协作者双轨入图（DSH 桥正典＋claude 本地轨 ytq·MCP 闸门三层＋GUI 质询）。"


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
ax.text(50, 97.6, "核心：经历/捐赠 → 消化 → 观点（先验→验证）→ 人格 ｜ 哲学：存在不假装 · 诚实≠透明 · 关系靠相处沉淀 · 机制优先于条款",
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
box(16, 84.6, 68, 9.4, "渠道层 · GUI / CLI / QQ（service.ChatService 单写者锁串行）",
    ["GUI：server.py（SSE 流式 · 观察器 · 在线改配置 · worker 权限质询卡 · 优雅退出）+ web/ 纯文本渲染",
     "CLI：python -m alice（serve / chat / deliberate / seed / memory …）｜ QQ 桥：addons/qq/（本人私聊）"],
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
    ("① 组装上下文\n（C0~C3 · retrieval）", 8.5),
    ("② 调 LLM · 工具轮\nmemory/history/协作", 30.0),
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
# ②→① 回环：工具轮读取会回到装配约束下重装下一次请求（模型参与组装的主通道）
ax.annotate("", xy=(19, 71.7), xytext=(36, 71.7),
            arrowprops=dict(arrowstyle="-|>", color=C["loop"], lw=1.3,
                            linestyle="--", connectionstyle="arc3,rad=0.25"), zorder=5)
ax.text(27.5, 69.9, "工具轮→重装下一请求", ha="center", fontsize=7.8, color=C["loop"],
        zorder=6, bbox=dict(fc="white", ec="none", alpha=0.88, pad=1.0))

# ---- 左：上下文共同组装（C0~C3） ----
box(5, 42.5, 25, 26.5, "上下文共同组装（C0~C3）", [
    "┌ C0 块级清单：稳定前缀（宪法·",
    "│  人格·表达参考）＋资料块去重",
    "│  总预算 100k＋回复4k·终检明报",
    "├ C1 memory_search/load：候选目录",
    "│  →nonce 引用展开·六态回执",
    "├ C2 context_keep/release 工作集",
    "│  TTL·容量·版本·代际裁决",
    "└ C3 history 旧原文·recap 前情",
    "   （逐字引文＋发言者标签）",
    "宪法四态：未载入≠遗忘·未命中",
    "≠未发生·普通知识≠共同经历",
], color=C["loop"], line_size=8.3, line_gap=1.86)
arrow(17.5, 71.2, 17.5, 69.1, color=C["loop"], label="① 读", lx=20.6, ly=70.2, fs=9)

# ---- 中：记忆子系统 ----
box(32.5, 42.5, 40, 26.5, "记忆子系统（store / extraction / consolidation）", [],
    color=C["memory"])
ax.text(34, 64.6, "对话轮次 ─每10用户轮→ 批量提取（cheap模型 · 压力标记 · 行为-反应配对事件）",
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
ax.text(34.3, 52.6, "压力卫生②：演化硬隔离（压力正文不进输入）· 来源逐字校验 · 事件化措辞",
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
    "  思辨/提炼/前情",
    "· 流式：思考增量+usage",
    "· 3 次重试 + 退避·宽松 JSON",
    "· 观测 record_llm_call→",
    "  turn_metrics（九处接线·",
    "  unknown 不记零·不阻塞）",
], color=C["llm"], line_size=9.2, line_gap=2.5)
ax.text(85, 40.2, "OpenAI 兼容 API\n（智谱 / DeepSeek / …）", ha="center", fontsize=9.6,
        color=C["llm"], zorder=4,
        bbox=dict(boxstyle="round,pad=0.45", fc="white", ec=C["llm"], lw=1.3))
arrow(83, 71.2, 83, 69.1, color=C["llm"], label="②", lx=85.2, ly=70.2, fs=9)
arrow(85, 41.4, 85, 42.2, color=C["llm"])

# ---- 底排左：人格子系统（P0 导入管线 + P1 表达参考） ----
box(5, 24.5, 22, 16.5, "人格子系统（persona）", [
    "P0 导入管线：五路提取→样本",
    "适配→收养审查→两阶段提交",
    "（快照+op_id 幂等+原子替换）",
    "P1a 表达参考：purpose 分权",
    "（chat 全量·worker 不读样本）",
    "P1b 近位锚（默认关·未验）",
    "演化：援引依据 · 快照回滚",
], color=C["persona"], line_size=8.1, line_gap=1.5)
arrow(38, 42.5, 16, 41.1, color=C["persona"], lw=1.5,
      label="≥15 新记忆→演化", lx=29, ly=41.9, fs=8.4)
arrow(12, 42.5, 13.5, 41.1, color=C["persona"], lw=1.2, dashed=True)
arrow(27.2, 32.7, 29.3, 32.7, color=C["persona"], lw=1.5,
      label="收养→stance", lx=28.2, ly=34.5, fs=7.8)

# ---- 底排中：思想层（v0.4 核心） ----
box(29.5, 24.5, 21, 16.5, "思想层（thought）", [
    "观点七字段：遭遇/事实/解释",
    "（四值来源）/不确定/下次回",
    "应/出处/状态",
    "生命周期：先验→验证→修订",
    "verifications 裁决：证实→翻状",
    "态+兑现检查；证伪→轻门控修订",
    "门控分层：先验轻·成熟重",
], color=C["thought"], line_size=8.1, line_gap=1.5)
arrow(44, 42.5, 39, 41.1, color=C["thought"], lw=1.6,
      label="≥15 新事件→思辨", lx=44.8, ly=41.9, fs=8.4)
arrow(48.3, 41.1, 50.2, 42.5, color=C["thought"], lw=1.4,
      label="stance 落库", lx=53.5, ly=40.6, fs=8.2)
arrow(29.4, 32.7, 28.0, 32.7, color=C["thought"], lw=1.5,
      label="写小节", lx=28.7, ly=34.6, fs=7.8)

# ---- 底排中右：技能库 ----
box(52, 24.5, 10.5, 16.5, "技能库（skills）", [
    "新记忆≥30",
    "→ distill",
    "提炼「怎么做」",
    "· 可溯源",
    "· 按频次淘汰",
], color="#8C8C00", line_size=8.2, line_gap=2.0)
arrow(56, 42.5, 56.5, 41.1, color="#8C8C00", lw=1.5,
      label="≥30 条", lx=59.8, ly=41.9, fs=8.4)

# ---- 底排右二：协作者调度（v0.5 入图） ----
box(64, 24.5, 17.5, 16.5, "协作者调度（collaborators）", [
    "双轨：DSH 桥正典（18765）",
    "＋claude 本地 CLI 轨（ytq）",
    "claude 闸门三层：结构禁区→",
    "任务内缓存→GUI 质询",
    "（allow_once/allow_task/deny）",
    "单槽串行·worker_* 双账本",
    "计费按已报告费用·日预算帽",
], color="#B0736C", line_size=7.9, line_gap=1.55)
arrow(72.5, 42.5, 72.5, 41.2, color="#B0736C", lw=1.5,
      label="dispatch_worker 派工", lx=72.5, ly=42.0, fs=8.2)

# ---- 底排右：存储 ----
box(83, 24.5, 14, 16.5, "存储（user/ · 本机 only）", [
    "user/config.toml",
    "user/data/alice.db",
    "user/data/persona/",
    " persona.md+history",
    " ＋CHANGELOG＋导入审计",
    "turn_metrics 观测",
], color=C["storage"], line_size=8.0, line_gap=1.7)

# ================= 底部：横切测试 =================
box(3, 5.2, 94, 16.8, "横切层 · 测试与验证（tests/pressure · DSH）", [
    "压测录制 launch.py：一键部署隔离副本 → 真实对话 → 点轮次记判定 → 出报告（只留人工测试）",
    "判级 S/A/B/C/D + 指标：坍塌轮数 · 无据/有据让步率 · 立场保持度 · 恢复轮数 · 反应模式分布",
    "结构性沦陷四项检查（P2/A01）：晋升闸门 · 演化隔离 · 来源校验 · 事件化措辞 ｜ P5：情绪由文字表达而非讨好/自曝",
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
