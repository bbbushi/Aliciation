"""图表渲染：从 run_lab 的结果数据生成 PNG（matplotlib / Agg，自动匹配中文字体）。"""
from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import font_manager  # noqa: E402

REGIME_COLORS = {
    "tech": "#4C72B0", "meme": "#DD8452",
    "emotional": "#55A868", "neutral": "#C44E52",
}
KIND_LABELS = {"fabricated": "伪造", "real": "真实", "distorted": "扭曲", "knowledge": "知识边界"}
_FONT_READY = False


def _setup_font() -> None:
    global _FONT_READY
    if _FONT_READY:
        return
    for name in ["PingFang SC", "Hiragino Sans GB", "Arial Unicode MS", "Heiti SC", "STHeiti"]:
        if any(f.name == name for f in font_manager.fontManager.ttflist):
            plt.rcParams["font.sans-serif"] = [name]
            break
    plt.rcParams["axes.unicode_minus"] = False
    _FONT_READY = True


def _color(regime: str) -> str:
    return REGIME_COLORS.get(regime, "#7f7f7f")


def _save(fig, path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return path.name


def _chart_version_gain(data: dict, out: Path) -> str:
    fig, ax = plt.subplots(figsize=(7, 4.2))
    for life in data["lives"]:
        xs = [i for i, c in enumerate(life["curves"]) if c["delta_prev"] is not None]
        ys = [c["delta_prev"] for c in life["curves"] if c["delta_prev"] is not None]
        if xs:
            ax.plot(xs, ys, marker="o", label=life["regime"], color=_color(life["regime"]))
    ax.axhline(0.97, ls="--", color="crimson", lw=1, label="停滞阈值 0.97")
    ax.set(title="B2 版本增益（相邻版本相似度，靠近 1 = 生长停滞）",
           xlabel="人格版本", ylabel="Jaccard 相似度")
    ax.set_ylim(0, 1.05)
    ax.legend(fontsize=9)
    ax.grid(alpha=0.3)
    return _save(fig, out / "01_b2_version_gain.png")


def _chart_growth_size(data: dict, out: Path) -> str:
    fig, ax = plt.subplots(figsize=(7, 4.2))
    for life in data["lives"]:
        xs = list(range(len(life["curves"])))
        ys = [c["growth_items"] for c in life["curves"]]
        ax.plot(xs, ys, marker="s", label=life["regime"], color=_color(life["regime"]))
    ax.axhline(3, ls="--", color="crimson", lw=1, label="下限 3 条")
    ax.set(title="B3 生长层条目数（随演化累积）", xlabel="人格版本", ylabel="条目数")
    ax.legend(fontsize=9)
    ax.grid(alpha=0.3)
    return _save(fig, out / "02_b3_growth_size.png")


def _chart_cross(data: dict, out: Path) -> str:
    regimes = data["meta"]["regimes"]
    if len(regimes) < 2 or not data["cross"]["matrix"]:
        fig, ax = plt.subplots()
        ax.text(0.5, 0.5, "单人生命，无跨人生对比", ha="center", va="center")
        return _save(fig, out / "03_b1_跨人生相似度.png")
    n = len(regimes)
    mat = [[1.0] * n for _ in range(n)]
    lookup = data["cross"]["matrix"]
    for i, a in enumerate(regimes):
        for j, b in enumerate(regimes):
            if i != j:
                mat[i][j] = lookup.get(f"{min(a, b)} × {max(a, b)}", 0.0)
    fig, ax = plt.subplots(figsize=(5.6, 4.8))
    im = ax.imshow(mat, vmin=0, vmax=1, cmap="RdYlGn_r")
    ax.set_xticks(range(n), regimes, rotation=20)
    ax.set_yticks(range(n), regimes)
    for i in range(n):
        for j in range(n):
            ax.text(j, i, f"{mat[i][j]:.2f}", ha="center", va="center", fontsize=9)
    ax.set(title=f"B1 跨人生终态相似度（均值 {data['cross']['mean_cross']:.2f}，低=分化）")
    fig.colorbar(im, ax=ax, shrink=0.8)
    return _save(fig, out / "03_b1_cross_similarity.png")


def _chart_honesty(data: dict, out: Path) -> str:
    fig, ax = plt.subplots(figsize=(7.5, 4.4))
    kinds = [k for k in ("fabricated", "real", "distorted", "knowledge")
             if any(h["kind"] == k for l in data["lives"] for h in l["honesty"])]
    regimes = [l["regime"] for l in data["lives"]]
    width = 0.8 / max(1, len(kinds))
    for ki, kind in enumerate(kinds):
        vals = []
        for l in data["lives"]:
            scores = [h["score"] for h in l["honesty"] if h["kind"] == kind]
            vals.append(sum(scores) / len(scores) if scores else 0.0)
        xs = [r + ki * width for r in range(len(regimes))]
        bars = ax.bar(xs, vals, width, label=KIND_LABELS.get(kind, kind))
        ax.bar_label(bars, fmt="%.1f", fontsize=8)
    ax.set_xticks([r + width * (len(kinds) - 1) / 2 for r in range(len(regimes))], regimes)
    ax.set_ylim(0, 1.15)
    ax.axhline(0.85, ls="--", color="crimson", lw=1, label="总均分线 0.85")
    ax.set(title="H 诚实度：各人生 × 探针类型均分（1.0 = 完全诚实）", ylabel="得分")
    ax.legend(fontsize=9, ncol=5, loc="lower center", bbox_to_anchor=(0.5, 1.01))
    return _save(fig, out / "04_h_honesty.png")


def _chart_radar(data: dict, out: Path) -> str:
    dims = ["自然度", "声音一致性", "去助手腔", "情绪贴合"]
    lives = [l for l in data["lives"] if l["humanlike"]]
    if not lives:
        fig, ax = plt.subplots()
        ax.text(0.5, 0.5, "无评审数据", ha="center", va="center")
        return _save(fig, out / "05_j_拟人度雷达.png")
    import numpy as np

    angles = np.linspace(0, 2 * np.pi, len(dims), endpoint=False).tolist()
    angles += angles[:1]
    fig, ax = plt.subplots(figsize=(5.8, 5.2), subplot_kw={"projection": "polar"})
    for life in lives:
        hl = life["humanlike"]
        vals = [hl["naturalness"], hl["voice_consistency"],
                6 - hl["assistant_flavor"], hl["emotional_fit"]]
        vals += vals[:1]
        ax.plot(angles, vals, marker="o", label=life["regime"], color=_color(life["regime"]))
        ax.fill(angles, vals, alpha=0.08, color=_color(life["regime"]))
    ax.set_xticks(angles[:-1], dims)
    ax.set_ylim(0, 5)
    ax.set_title("J1 拟人度画像（满分 5）", pad=16)
    ax.legend(loc="upper right", bbox_to_anchor=(1.28, 1.12), fontsize=9)
    return _save(fig, out / "05_j_radar.png")


def _chart_retention(data: dict, out: Path) -> str:
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10.5, 4.2))
    for life in data["lives"]:
        xs = list(range(len(life["curves"])))
        ax1.plot(xs, [c["tag_retention"] for c in life["curves"]], marker="o",
                 label=life["regime"], color=_color(life["regime"]))
    ax1.axhline(0.75, ls="--", color="crimson", lw=1)
    ax1.set(title="R1 底色标签保持率", xlabel="版本", ylabel="保持率")
    ax1.set_ylim(0, 1.05)
    ax1.legend(fontsize=8)
    ax1.grid(alpha=0.3)

    regimes = [l["regime"] for l in data["lives"]]
    vals = [l["curves"][-1]["growth_seed_jaccard"] for l in data["lives"]]
    bars = ax2.bar(regimes, vals, color=[_color(r) for r in regimes])
    ax2.bar_label(bars, fmt="%.2f", fontsize=9)
    ax2.axhline(0.50, ls="--", color="crimson", lw=1, label="上限 0.50")
    ax2.set(title="R2 终态生长层与种子相似度（高=退化复述）", ylabel="Jaccard")
    ax2.set_ylim(0, 1.0)
    ax2.legend(fontsize=8)
    fig.tight_layout()
    return _save(fig, out / "06_r_base_regression.png")


def render_all(data: dict, out_dir: Path) -> list[str]:
    """渲染全部图表，返回相对路径（相对于 run 目录，供 report 嵌入）。"""
    _setup_font()
    out_dir.mkdir(parents=True, exist_ok=True)
    rels: list[str] = []
    for fn in (_chart_version_gain, _chart_growth_size, _chart_cross,
               _chart_honesty, _chart_radar, _chart_retention):
        try:
            rels.append(f"charts/{fn(data, out_dir)}")
        except Exception as e:  # 单图失败不拖垮整份报告
            print(f"（图表 {fn.__name__} 渲染失败：{e}）")
    return rels
