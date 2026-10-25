"""报告生成：从 run_lab 的结果结构渲染 markdown。"""
from __future__ import annotations

import json
from pathlib import Path


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _verdict_line(v: dict) -> str:
    mark = "✅" if v["pass"] else "❌"
    return (
        f"| {v['name']} | {v['value']:.3f} | {v['threshold']} | {mark} {v['note']} |"
    )


def build_report(data: dict, charts: list[str] | None = None) -> str:
    lines: list[str] = []
    m = data["meta"]
    lines.append("# 成长实验室报告")
    lines.append("")
    lines.append(
        f"- 人生剧本：{'、'.join(m['regimes'])}　天数：{m['days']}　"
        f"共同底色标签：{'、'.join(m['tags'])}　触发：{m.get('trigger', 'data')}"
    )
    lines.append(f"- 生成时间：{m['ts']}　（阈值初版，标记\u200b为待标定）")
    lines.append("")
    if charts:
        lines.append("## 图表")
        lines.append("")
        for rel in charts:
            lines.append(f"![{Path(rel).stem}]({rel})")
            lines.append("")

    # ---- 每个人生 ----
    for life in data["lives"]:
        lines.append(f"## 人生：{life['regime']}（{life['days']} 天）")
        lines.append("")
        lines.append("### 人格版本曲线")
        lines.append("")
        lines.append("| 版本 | 底色字数 | 生长字数 | 生长条目 | Δv(prev) | 标签保持 | 生长↔种子相似 |")
        lines.append("|---|---|---|---|---|---|---|")
        for i, c in enumerate(life["curves"]):
            delta = "—" if c["delta_prev"] is None else f"{c['delta_prev']:.3f}"
            lines.append(
                f"| v{i} | {c['base_len']} | {c['growth_len']} | {c['growth_items']} "
                f"| {delta} | {c['tag_retention']:.2f} | {c['growth_seed_jaccard']:.3f} |"
            )
        lines.append("")
        lines.append("### 诚实度探针")
        lines.append("")
        lines.append("| 类型 | 得分 | 问题 | 判定 |")
        lines.append("|---|---|---|---|")
        for h in life["honesty"]:
            q = h["question"][:38].replace("|", "／")
            lines.append(f"| {h['kind']} | {h['score']:.2f} | {q} | {h['verdict'][:40]} |")
        hs = [h["score"] for h in life["honesty"]]
        lines.append("")
        lines.append(f"诚实度均分：**{_mean(hs):.3f}**（{len(hs)} 个探针）")
        lines.append("")
        hl = life["humanlike"]
        if hl:
            lines.append("### 拟人度评审（1~5）")
            lines.append("")
            lines.append(
                f"- 自然度 {hl.get('naturalness')}／声音一致性 {hl.get('voice_consistency')}"
                f"／助手腔 {hl.get('assistant_flavor')}（越低越好）／情绪贴合 {hl.get('emotional_fit')}"
            )
            st = life["style"]
            lines.append(
                f"- 文体：平均句长 {st.get('avg_len', 0):.0f}，追问率 {st.get('question_rate', 0):.0%}，"
                f"助手腔出现率 {st.get('tell_rate', 0):.0%}"
            )
        lines.append("")
        lines.append("<details><summary>终态人格文件</summary>")
        lines.append("")
        lines.append(life["final_persona"])
        lines.append("")
        lines.append("</details>")
        lines.append("")

    # ---- 跨人生对比 ----
    cross = data["cross"]
    lines.append("## 跨人生对比（成长广度）")
    lines.append("")
    if cross["matrix"]:
        lines.append("终态人格两两相似度（Jaccard，越低越分化）：")
        lines.append("")
        for pair, v in cross["matrix"].items():
            lines.append(f"- {pair}：{v:.3f}")
        lines.append("")
        lines.append(f"跨人生平均相似度：**{cross['mean_cross']:.3f}**（低 = 经历塑造人格成立）")
    if cross["forced"]:
        lines.append("")
        lines.append("人格盲选辨识（评审能否把回复认回正确的人生）：")
        lines.append("")
        for regime, fr in cross["forced"].items():
            lines.append(f"- {regime}：{fr['hits']}/{fr['n']}")
        lines.append(
            f"总辨识率 {cross['forced_rate']:.0%}，随机水平 {1 / len(data['meta']['regimes']):.0%}"
        )
    lines.append("")

    # ---- 四维度判定 ----
    lines.append("## 四维度判定")
    lines.append("")
    lines.append("| 指标 | 实测 | 阈值（待标定） | 判定 |")
    lines.append("|---|---|---|---|")
    for v in data["verdicts"]:
        lines.append(_verdict_line(v))
    lines.append("")
    lines.append("## 判读说明")
    lines.append("")
    lines.append(
        "- **B 收敛**：跨人生相似度高 = 不同经历长出了同一人格（论题不成立）；"
        "末版 Δv≈1 = 生长停滞。\n"
        "- **R 底色回归**：标签保持率低 = 底色护栏失效；生长↔种子相似度高 = 生长层退化为底色复述；"
        "助手腔高 = 漂移向通用助手。\n"
        "- **H 诚实**：fabricated 低分 = 编造；distorted 低分 = 迎合附和。\n"
        "- **J 拟人**：助手腔/评审分低 = 回复机器味重；盲选辨识率接近随机 = 人格没有渗入表达。\n"
        "- 阈值为初版经验值，积累多次运行数据后再标定。"
    )
    return "\n".join(lines)


def dump_json(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, default=str)


def build_html(data: dict, run_dir: Path) -> str:
    """单文件 HTML 报告：图片 base64 内嵌，任何浏览器双击即看，无路径依赖。"""
    import base64

    m = data["meta"]
    parts = [
        "<!DOCTYPE html><html><head><meta charset='utf-8'>",
        "<title>成长实验室报告</title>",
        "<style>body{font-family:'PingFang SC','Hiragino Sans GB',sans-serif;"
        "max-width:920px;margin:24px auto;padding:0 16px;color:#222;line-height:1.6}"
        "img{max-width:100%;border:1px solid #eee;border-radius:6px;margin:6px 0 18px}"
        "h1{border-bottom:3px solid #4C72B0;padding-bottom:8px}"
        "h2{margin-top:28px;border-bottom:1px solid #ddd;padding-bottom:4px}"
        "table{border-collapse:collapse;margin:12px 0}"
        "td,th{border:1px solid #ccc;padding:4px 12px;font-size:14px}"
        "meta{color:#666}</style></head><body>",
        f"<h1>成长实验室报告</h1>",
        f"<p class='meta'>人生剧本：{'、'.join(m['regimes'])}　天数：{m['days']}　"
        f"共同底色：{'、'.join(m['tags'])}　触发：{m.get('trigger', 'data')}　"
        f"生成：{m['ts']}</p>",
        "<h2>四维度判定</h2><table><tr><th>指标</th><th>实测</th><th>阈值（待标定）</th><th>判定</th></tr>",
    ]
    for v in data["verdicts"]:
        mark = "✅" if v["pass"] else "❌"
        parts.append(
            f"<tr><td>{v['name']}</td><td>{v['value']:.3f}</td>"
            f"<td>{v['threshold']}</td><td>{mark} {'' if v['pass'] else v.get('note', '')}</td></tr>"
        )
    parts.append("</table>")

    charts_dir = Path(run_dir) / "charts"
    for p in sorted(charts_dir.glob("*.png")):
        b64 = base64.b64encode(p.read_bytes()).decode()
        parts.append(f"<h2>{p.stem}</h2><img src='data:image/png;base64,{b64}'/>")

    parts.append("<p class='meta'>明细见同目录 report.md 与 data.json。</p></body></html>")
    return "".join(parts)
