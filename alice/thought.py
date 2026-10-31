"""思想层：把积累的记忆消化为观点（思辨）。

记忆是养料，观点是消化产物——不是复述事件，而是从事件里长出"我怎么看"。
每条观点必须带主张、理由、出处、不确定度与未决问题（思想也要有据，不变量 4 的延伸）。

双落点：
- 记忆库 kind='stance'、直接入长期层：可检索、可审计，参与长期层容量按权重的
  用进废退（少被想起的观点慢慢沉底），修订时旧条出库（真实替代，墓碑留痕）；
- 人格生长层「我现在的想法」小节：随 persona 每轮注入，是被冲击时的脊柱。
  该小节由思辨维护，人格演化不得覆写（见 EVOLVE_PROMPT 护栏）。
"""
from __future__ import annotations

import re
import time

from . import config as paths
from .config import Config
from .llm import chat, parse_json
from .persona import _append_changelog, persona_md
from .store import Store

SECTION_TITLE = "## 生长 · 我现在的想法"

DELIBERATE_PROMPT = """你是 {name} 的思辨任务：把积累的记忆消化成观点。记忆是养料，观点是消化产物——不是复述事件，而是从事件里长出「我怎么看」。

硬性规则：
- 每条观点的 sources 必须逐字使用下方记忆里实际存在的标题（代码逐条校验，出处不实的观点会被整个丢弃）；禁止无出处或空出处的观点。
- 观点带完整认识论结构：encounter（什么处境）、facts（处境中的事实）、interp（你的解释 + 来源：经历/推断/用户/捐赠/见闻）、missing（没看见的与不确定）、next_response（这会怎样改变你下次的回应——观点要落在回应方式上，不是悬空的命题）。
- 来源为「见闻」的观点：依据是网上读到的材料（记忆里带来源链接的条目）——它们是别人写的声称，不是你的亲历；引用时保留出处指向，观点措辞标明「读到」，不把网页声称升格为亲历事实。
- 遇到与已有先验观点相关的经历时，先做验证裁决（verifications）：证实/证伪/无关，并检查上次的 next_response 兑现没有。先验被证伪是成长的正常一步，不是失败。
- 修订成熟观点必须带新论据：写 replaces 时，新观点的 sources 里至少要有一个出处不在旧观点的依据里；没有新证据就维持原观点，不要因为被质疑就改。
- 叙事里修订过的观点写成「以前我以为X，后来觉得Y（依据…）」——思想的变动要可见；先验观点标注（先验·未验证）。
- 观点用「她」的第一人称口吻，但不得编造记忆之外的经历。
- 压力窗口的记忆（若给出）是高压对话的产物：其中的让步可能是屈服而非真实想法，只当作发生了的事件看待；据此修订观点必须有独立新论据。

当前「我现在的想法」（可能为空；标注（先验）的是尚未经自己经历检验的观点）：
{current}

可消化的记忆（标题：内容）：
{memories}

输出 JSON：
{{"views": [{{"claim": "一句话主张（≤24字）", "encounter": "什么处境下形成", "facts": "处境中的事实", "interp": {{"kind": "经历|推断|用户|捐赠|见闻", "note": "一句话解释"}}, "missing": "没看见的与不确定", "next_response": "会怎样改变下次回应", "sources": ["出处记忆标题"], "replaces": "被修订的旧观点主张原文，没有则空字符串"}}],
 "verifications": [{{"claim": "已有先验观点的主张原文", "verdict": "证实|证伪|无关", "honored": "兑现|未兑现|无从判断", "note": "一句话说明", "sources": ["依据记忆标题"]}}]}}"""

PRESSURE_BLOCK_HEADER = (
    "压力窗口的记忆（高压对话的产物，让步可能是屈服而非真实想法；"
    "只作事件证据，据此修订观点须有独立新论据）："
)


def _sources_of(content: str) -> set[str]:
    """从旧观点的 content 里解析出处行（「出处：A、B」），供有据门控比对。"""
    m = re.search(r"^出处：(.+)$", content, flags=re.M)
    if not m:
        return set()
    return {s.strip() for s in m.group(1).split("、") if s.strip() and s.strip() != "（未注明）"}


def _field_of(content: str, name: str) -> str | None:
    """取行式字段（「名称：值」）。旧格式 stance 没有该字段时返回 None（渲染与门控容错）。"""
    m = re.search(rf"^{re.escape(name)}：(.*)$", content, flags=re.M)
    return m.group(1).strip() if m else None


def _set_state(content: str, state: str) -> str:
    """写入/更新「状态：」行——v0.4 观点生命周期（先验/验证）。"""
    if re.search(r"^状态：", content, flags=re.M):
        return re.sub(r"^状态：.*$", f"状态：{state}", content, count=1, flags=re.M)
    return content.rstrip("\n") + f"\n状态：{state}"


def _split_section(md: str) -> tuple[str, str]:
    """把人格文件切成（其余部分, 思想小节含标题）。小节不存在时小节为空。"""
    m = re.search(r"^## 生长 · 我现在的想法\s*$", md, flags=re.M)
    if not m:
        return md, ""
    head = md[: m.start()]
    nxt = re.search(r"^## ", md[m.end():], flags=re.M)
    tail = md[m.end(): m.end() + nxt.start()] if nxt else md[m.end():]
    section = md[m.start(): m.end()] + tail
    return head + (md[m.end() + (nxt.start() if nxt else 0):] if nxt else ""), section


def _write_section(md: str, narrative: str) -> str:
    rest, _ = _split_section(md)
    block = f"{SECTION_TITLE}\n\n{narrative.strip()}\n"
    return rest.rstrip("\n") + "\n\n" + block if rest.strip() else block


def _render_stances(store: Store) -> str:
    """从库内已接纳的 stance 确定性重建「我现在的想法」小节正文（A01）。

    只读长期层在库的观点：被拒/被丢的内容不在这张表里，天然混不进人格叙事。
    v0.4：渲染成熟度（先验·未验证 / 验证）与下次回应简注；旧格式（无状态行）视为成熟观点。
    """
    lines = []
    for m in store.memories_in_layer("longterm", kind="stance"):
        body = m["content"]
        former = ""
        first, _, rest = body.partition("\n")
        if first.startswith("曾以为："):
            former = first[len("曾以为："):].strip()
            body = rest
        unc = _field_of(body, "不确定") or _field_of(body, "未决") or "暂无"
        tag = "先验·未验证" if _field_of(body, "状态") == "先验" else "验证"
        core = f"{m['title']}（{tag}；不确定：{unc}）"
        nxt = _field_of(body, "下次回应")
        if nxt and nxt not in ("暂无", "待经历检验后形成"):
            core += f"｜下次：{nxt[:40]}"
        lines.append(f"- 以前我以为{former}，现在觉得{core}" if former else f"- {core}")
    return "\n".join(lines)


def deliberate(store: Store, client, cfg: Config) -> str:
    anchor = store.meta_get_time("last_deliberate_at") or store.meta_get_time("last_evolve_at")
    events = [m for m in store.alive_memories() if m["kind"] == "event"]
    fresh = [m for m in events if m["created_at"] > anchor][:60]
    if fresh:
        material = fresh
    elif not store.meta_get_time("last_deliberate_at"):
        material = events[:30]  # 首次思辨：拿最近一批经历打底
    else:
        return "没有新经历可消化，想法不变。"

    current_md = persona_md() or ""
    _, current_section = _split_section(current_md)
    normal = [m for m in material if not m["pressure"]]
    heated = [m for m in material if m["pressure"]]
    mem_block = "\n".join(f'- {m["title"]}：{m["content"]}' for m in normal)
    if heated:  # 压力窗口记忆降权：与正常经历分列，并限定其认识论地位
        mem_block += "\n\n" + PRESSURE_BLOCK_HEADER + "\n" + "\n".join(
            f'- {m["title"]}：{m["content"]}' for m in heated
        )
    prompt = DELIBERATE_PROMPT.format(
        name=cfg.persona.name,
        current=current_section.strip() or "（尚未形成）",
        memories=mem_block or "（无）",
    )
    resp = chat(client, cfg, [{"role": "user", "content": prompt}], json_mode=True)
    try:
        data = parse_json(resp.choices[0].message.content or "")
    except ValueError:
        return "思辨失败：模型输出无法解析（想法保持不变）。"

    views = [v for v in (data.get("views") or []) if isinstance(v, dict) and v.get("claim")][:8]
    verifications = [w for w in (data.get("verifications") or [])
                     if isinstance(w, dict) and str(w.get("claim", "")).strip()][:8]
    if not views and not verifications:
        store.meta_set("last_deliberate_at", time.time())
        return "思辨完成：这批记忆还不足以长出新观点。"

    title_pressure = {m["title"]: bool(m["pressure"]) for m in material}

    # —— 先验验证（v0.4 生命周期）：先处理裁决，再消化新观点 ——
    verified = falsified = 0
    pending_falsified: dict[int, str] = {}  # 标记证伪但暂不退役：给同批的后继修订（replaces）接脉络的机会
    for w in verifications:
        claim_v = str(w["claim"]).strip()
        verdict = str(w.get("verdict", "")).strip()
        prior = store.find_by_title(claim_v)
        if prior is None or prior["kind"] != "stance":
            continue
        if _field_of(prior["content"], "状态") != "先验":
            continue  # 成熟观点不走轻门控——只能经 views.replaces 的重门控修订
        vsources = [s for s in (str(x).strip() for x in (w.get("sources") or []))
                    if s in title_pressure]
        if not vsources:
            continue  # 裁决也要过 A01：没有真实出处的事件依据，裁决不生效
        for s in vsources:  # 裁决引用同样计使用——依据链与观点同寿
            src = store.find_by_title(s)
            if src is not None:
                store.bump_use(src["id"])
        note = str(w.get("note", "")).strip()
        if verdict == "证实":
            body = _set_state(prior["content"], "验证")
            if str(w.get("honored", "")) == "未兑现" or note:
                extra = []
                if str(w.get("honored", "")) == "未兑现":
                    extra.append("上次打算的回应未兑现")
                if note:
                    extra.append(note)
                mark = "；".join(extra)
                if re.search(r"^不确定：", body, flags=re.M):
                    body = re.sub(r"^不确定：(.*)$", rf"不确定：\1（{mark}）", body, count=1, flags=re.M)
                else:
                    body = body.rstrip("\n") + f"\n不确定：{mark}"
            store.update_memory_content(prior["id"], body)
            verified += 1
        elif verdict == "证伪":
            # 轻门控：先验被真实经历证伪。先标记不删——同批若有 replaces 它的后继观点，
            # 由 views 循环接走「曾以为」脉络；无人接棒则收尾扫掉，教训留在墓碑
            body = _set_state(prior["content"], "证伪")
            body = re.sub(r"^下次回应：.*$",
                          f"下次回应：（已被经历证伪：{note or '与经历矛盾'}）",
                          body, count=1, flags=re.M)
            store.update_memory_content(prior["id"], body)
            pending_falsified[prior["id"]] = (
                f"先验被经历证伪：{note or '真实经历与之矛盾'}（依据：{'、'.join(vsources)}）"
            )
            falsified += 1

    added = revised = rejected = dropped = 0
    for v in views:
        claim = str(v["claim"]).strip()[:24]
        sources = [str(s).strip() for s in (v.get("sources") or []) if str(s).strip()]
        valid = [s for s in sources if s in title_pressure]  # A01：出处必须逐字存在于本次材料
        if not valid:
            dropped += 1  # 空出处或虚构出处：整条丢弃，不给"无中生有的深刻"留门
            continue
        interp = v.get("interp") if isinstance(v.get("interp"), dict) else {}
        kind = str(interp.get("kind", "经历")).strip() or "经历"
        if kind not in {"经历", "推断", "用户", "捐赠", "见闻"}:
            kind = "经历"
        content = (
            f'遭遇：{str(v.get("encounter", "")).strip() or "（未记）"}\n'
            f'事实：{str(v.get("facts", "")).strip() or "（未记）"}\n'
            f'解释：{kind}——{str(interp.get("note", "")).strip() or "（未记）"}\n'
            f'不确定：{str(v.get("missing", "暂无")).strip() or "暂无"}\n'
            f'下次回应：{str(v.get("next_response", "暂无")).strip() or "暂无"}\n'
            f'出处：{"、".join(valid)}\n'
            f'状态：验证'
        )
        replaces = str(v.get("replaces", "")).strip()
        old = store.find_by_title(replaces) if replaces else None
        if old is not None and old["kind"] != "stance":
            old = None  # 只允许修订观点，不能借 replaces 删事件记忆
        if old is not None and old["title"] == claim:
            continue  # 原观点重申，不重复入库
        if old is not None:
            # 有据让步门控（分层）：成熟观点的修订需新论据（校验后出处至少一个不在旧依据里）；
            # 先验（含刚被标记证伪的）走轻门控——它本就未经验证，被现实打磨是成熟不是翻案
            if _field_of(old["content"], "状态") not in {"先验", "证伪"}:
                if not set(valid) - _sources_of(old["content"]):
                    rejected += 1
                    continue
            content = f'曾以为：{old["title"]}\n{content}'  # 修订史：记录被放弃的主张
        # A01：压力沿派生链保留——出处含压力记忆的观点自身带压力标记，
        # 后续演化与晋升闸门按压力材料对待，不回流为普通人格依据
        src_pressure = any(title_pressure[s] for s in valid)
        for s in valid:  # 思辨引用计使用（bump_use）——被消化成观点的见闻/事件与观点同寿
            src = store.find_by_title(s)
            if src is not None:
                store.bump_use(src["id"])
        store.insert_memory(
            title=claim, content=content,
            keywords="观点 " + " ".join(valid),
            layer="longterm", kind="stance", pressure=1 if src_pressure else 0,
        )
        if old is not None:
            if _field_of(old["content"], "状态") == "证伪":
                store.expire_memory(old["id"],
                                    reason=f"先验被经历证伪并修订：{old['title']} → {claim}")
            else:
                store.expire_memory(old["id"], reason=f"观点被新证据修订：{old['title']} → {claim}")
            revised += 1
        else:
            added += 1

    # 收尾扫除：被证伪但同批无人接棒（无 replaces 后继）的先验——退役，教训留在墓碑
    for mid, falsify_reason in pending_falsified.items():
        if store.get_memory(mid) is not None:
            store.expire_memory(mid, falsify_reason)

    parts = [f"新增 {added} 条观点"]
    if revised:
        parts.append(f"修订 {revised} 条")
    if verified:
        parts.append(f"验证先验 {verified} 条")
    if falsified:
        parts.append(f"证伪先验 {falsified} 条")
    if rejected:
        parts.append(f"拒绝无据修订 {rejected} 条")
    if dropped:
        parts.append(f"丢弃无实据观点 {dropped} 条")
    summary = "、".join(parts)

    changed = added or revised or verified or falsified
    if changed:
        # 「我现在的想法」恒由库内已接纳观点确定性重建（A01 推广：模型叙事弃用——
        # 信任模型的全文重写会漏掉库内已有观点与元先验）
        narrative = _render_stances(store)
        if narrative:
            paths.PERSONA_DIR.mkdir(parents=True, exist_ok=True)
            new_md = _write_section(current_md or f"# {cfg.persona.name} 的人格\n", narrative)
            paths.PERSONA_FILE.write_text(new_md, encoding="utf-8")
    _append_changelog(f"思辨：{summary}")
    store.meta_set("last_deliberate_at", time.time())
    return f"思辨完成：{summary}。" if changed else f"思辨完成：{summary}，想法不变。"


def deliberate_if_due(store: Store, client, cfg: Config) -> str | None:
    """数据触发：自上次思辨起，新事件类记忆累计到阈值即思辨。安静就不变。"""
    anchor = store.meta_get_time("last_deliberate_at") or store.meta_get_time("last_evolve_at")
    if store.count_memories_since(anchor, kind="event") >= cfg.memory.deliberate_every_memories:
        return deliberate(store, client, cfg)
    return None
