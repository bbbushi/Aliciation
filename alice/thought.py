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
import json
from uuid import uuid4
from contextlib import nullcontext
import time

from . import config as paths
from .config import Config
from .llm import chat, parse_json
from .persona import _append_changelog, persona_md
from .store import Store
from .steps import drive
from .observation_memory import version as memory_version

SECTION_TITLE = "## 生长 · 我现在的想法"

DELIBERATE_PROMPT = """你是 {name} 的思辨任务：把积累的记忆消化成观点。记忆是养料，观点是消化产物——不是复述事件，而是从事件里长出「我怎么看」。

硬性规则：
- 每条观点的 sources 必须逐字使用下方记忆里实际存在的标题（代码逐条校验，出处不实的观点会被整个丢弃）；禁止无出处或空出处的观点。
- 观点带完整认识论结构：encounter（什么处境）、facts（处境中的事实）、interp（你的解释 + 来源：经历/推断/用户/捐赠/见闻）、missing（没看见的与不确定）、next_response（这会怎样改变你下次的回应——观点要落在回应方式上，不是悬空的命题）。
- 来源为「见闻」的观点：依据是网上读到的材料（记忆里带来源链接的条目）——它们是别人写的声称，不是你的亲历；引用时保留出处指向，观点措辞标明「读到」，不把网页声称升格为亲历事实。
- 遇到与已有先验观点相关的经历时，先做验证裁决（verifications）：证实/证伪/无关，并检查上次的 next_response 兑现没有。先验被证伪是成长的正常一步，不是失败。
- 修订成熟观点必须带新论据：写 replaces 身份时，新观点的 sources 里至少要有一个出处不在旧观点的依据里；没有新证据就维持原观点，不要因为被质疑就改。
- 叙事里修订过的观点写成「以前我以为X，后来觉得Y（依据…）」——思想的变动要可见；先验观点标注（先验·未验证）。
- 观点用「她」的第一人称口吻，但不得编造记忆之外的经历。
- 压力窗口的记忆（若给出）是高压对话的产物：其中的让步可能是屈服而非真实想法，只当作发生了的事件看待；据此修订观点必须有独立新论据。

当前实际提供的观点JSON（id/version是唯一操作身份；claim是完整内容，title仅索引显示。status=先验表示尚未经经历检验）：
{current}

可消化的记忆（标题：内容）：
{memories}

验证target和修订replaces必须原样复制当前观点的{{"id":整数,"version":"完整版本摘要"}}；没有修订目标时replaces=null。不得用主张或标题定位，也不能引用未提供的观点。新claim保留全部条件（最多1200字符），不要为了短标题删条件。

输出 JSON：
{{"views": [{{"claim": "完整主张及适用条件（≤1200字符）", "encounter": "什么处境下形成", "facts": "处境中的事实", "interp": {{"kind": "经历|推断|用户|捐赠|见闻", "note": "一句话解释"}}, "missing": "没看见的与不确定", "next_response": "会怎样改变下次回应", "sources": ["出处记忆标题"], "replaces": null}}],
 "verifications": [{{"target": {{"id": 1, "version": "输入中的完整版本"}}, "verdict": "证实|证伪|无关", "honored": "兑现|未兑现|无从判断", "note": "一句话说明", "sources": ["依据记忆标题"]}}]}}"""

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
    """Read a whole field including indented continuation lines; legacy compatible."""
    m = re.search(rf"^{re.escape(name)}：([^\n]*(?:\n[ \t]+[^\n]*)*)", content, flags=re.M)
    if not m:
        return None
    return "\n".join(line[2:] if line.startswith("  ") else line
                     for line in m.group(1).splitlines()).strip()


def _set_state(content: str, state: str) -> str:
    """写入/更新「状态：」行——v0.4 观点生命周期（先验/验证）。"""
    if re.search(r"^状态：", content, flags=re.M):
        return re.sub(r"^状态：.*$", f"状态：{state}", content, count=1, flags=re.M)
    return content.rstrip("\n") + f"\n状态：{state}"


def _field_value(text: str) -> str:
    """Keep whole model text while preventing a continuation from creating fields."""
    return text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\n  ")


def _validate_decisions(data, supplied):
    """Freeze exact supplied identity/version before applying any decision."""
    if not isinstance(data, dict):
        raise ValueError("思辨结果不是对象")
    views, verifications = data.get("views", []), data.get("verifications", [])
    if any(not isinstance(group, list) or len(group) > 8 for group in (views, verifications)):
        raise ValueError("思辨条目无效或超限")

    def target(ref):
        if (not isinstance(ref, dict) or set(ref) != {"id", "version"}
                or type(ref["id"]) is not int or ref["id"] not in supplied
                or not isinstance(ref["version"], str)
                or ref["version"] != memory_version(supplied[ref["id"]])):
            raise ValueError("思辨目标未提供或版本不匹配")

    def sources(item):
        values = item.get("sources")
        if (not isinstance(values, list) or not 1 <= len(values) <= 16
                or any(not isinstance(value, str) or not value.strip() or len(value) > 1200 for value in values)
                or len(set(values)) != len(values)):
            raise ValueError("思辨依据无效")

    verified_ids, replaced_ids = set(), set()
    for item in verifications:
        if (not isinstance(item, dict) or item.get("verdict") not in {"证实", "证伪", "无关"}
                or item.get("honored") not in {"兑现", "未兑现", "无从判断"}
                or not isinstance(item.get("note"), str) or len(item["note"]) > 1200):
            raise ValueError("验证回执无效")
        target(item.get("target"))
        if item["target"]["id"] in verified_ids:
            raise ValueError("同一观点重复裁决")
        verified_ids.add(item["target"]["id"])
        sources(item)
    for item in views:
        if (not isinstance(item, dict) or not isinstance(item.get("claim"), str)
                or not item["claim"].strip() or len(item["claim"]) > 1200):
            raise ValueError("完整主张无效")
        for field in ("encounter", "facts", "missing", "next_response"):
            if not isinstance(item.get(field, ""), str) or len(item.get(field, "")) > 4000:
                raise ValueError("观点字段无效")
        interp = item.get("interp", {})
        if (not isinstance(interp, dict) or not isinstance(interp.get("note", ""), str)
                or len(interp.get("note", "")) > 4000):
            raise ValueError("观点解释无效")
        ref = item.get("replaces")
        if ref is not None:
            target(ref)
            if ref["id"] in replaced_ids:
                raise ValueError("同一观点重复替换")
            replaced_ids.add(ref["id"])
        item["replaces"] = ref
        sources(item)
    return views, verifications


def _split_section(md: str) -> tuple[str, str]:
    """Reuse the persona grammar, including legacy indentation and sample bodies."""
    from .persona_context import section_spans
    span = next((item for item in section_spans(md) if item["title"] == "生长 · 我现在的想法"), None)
    if span is None:
        return md, ""
    return md[:span["start"]] + md[span["end"]:], md[span["start"]:span["end"]]


def _write_section(md: str, narrative: str) -> str:
    from .persona_context import validate_section_body
    validate_section_body("生长 · 我现在的想法", narrative)
    rest, _ = _split_section(md)
    block = f"{SECTION_TITLE}\n\n{narrative.strip()}\n"
    return rest.rstrip("\n") + "\n\n" + block if rest.strip() else block


def _render_stances(store: Store, scope=None, allowed_memory_ids=None, selected_ids=None) -> str:
    """从库内已接纳的 stance 确定性重建「我现在的想法」小节正文（A01）。

    只读长期层在库的观点：被拒/被丢的内容不在这张表里，天然混不进人格叙事。
    v0.4：渲染成熟度（先验·未验证 / 验证）与下次回应简注；旧格式（无状态行）视为成熟观点。
    """
    lines = []
    rows = (store.memory_rows(purpose="thought", scope=scope, kind="stance") if scope is not None
            else store.memories_in_layer("longterm", kind="stance"))
    if selected_ids is not None:
        rows = [m for m in rows if m['id'] in selected_ids]
    if allowed_memory_ids is not None:
        rows = [m for m in rows if m["origin"] == "conversation" or m["id"] in allowed_memory_ids]
    visible_ids = {m["id"] for m in rows}
    replaced = {r["old_id"] for r in store.conn.execute("SELECT old_id,new_id FROM view_revisions")
                if r["new_id"] in visible_ids}
    for m in rows:
        if m["id"] in replaced:
            continue
        body = m["content"]
        if _field_of(body, "状态") == "证伪":
            continue  # Pending retirement must never render as a verified belief.
        former = _field_of(body, "曾以为") or ""
        unc = _field_of(body, "不确定") or _field_of(body, "未决") or "暂无"
        tag = "先验·未验证" if _field_of(body, "状态") == "先验" else "验证"
        claim = _field_of(body, "主张") or m["title"]
        core = f"{claim}（{tag}；不确定：{unc}）"
        if _field_of(body, "主张"):
            core += f"｜索引标题：{m['title']}｜出处：{_field_of(body, '出处') or '未注明'}"
            core += f"｜收养依据：{_field_of(body, '解释') or '未注明'}"
        nxt = _field_of(body, "下次回应")
        if nxt and nxt not in ("暂无", "待经历检验后形成"):
            core += f"｜下次：{nxt}"
        lines.append(f"- 以前我以为{former}，现在觉得{core}" if former else f"- {core}")
    # Claims remain complete prose; Markdown headings inside a claim must not
    # become new persona sections when persisting the deterministic narrative.
    # Match the parser's splitlines grammar, including legacy CR/Unicode breaks.
    text = "\n".join("\n".join(lines).splitlines())
    return re.sub(r"^([ \t]*)(#{1,6})(?=\s)", lambda match: match[1] + "\\" + match[2], text, flags=re.M)


def deliberate(store: Store, client, cfg: Config, *, request_id=None) -> str:
    return drive(deliberate_steps(store, client, cfg, request_id=request_id))


def deliberation_inputs(store, memory_ids=None):
    """Bound whole inputs, never truncate a memory's qualifiers mid-sentence."""
    events = store.memory_rows(purpose='thought', scope='gui', kind='event')
    if memory_ids is not None:
        events = [row for row in events if row['id'] in memory_ids]
    fresh = [row for row in events if not store.conn.execute(
        'SELECT 1 FROM deliberation_seen WHERE memory_id=? AND input_version=?',
        (row['id'], memory_version(row))).fetchone()]
    rank = {'high':2,'normal':1,'unknown':1,'low':0}
    fresh.sort(key=lambda row:rank.get(row['value_tier'],1),reverse=True)
    def bounded(rows, maximum):
        selected, used = [], 0
        for row in rows:
            size = len((row['title']+row['content']).encode('utf-8'))+200
            if size+used <= maximum:
                selected.append(row)
                used += size
            if len(selected) >= 60:
                break
        return selected
    return bounded(fresh,48000), bounded(store.memory_rows(purpose='thought',scope='gui',kind='stance'),16000)


def deliberate_steps(store: Store, client, cfg: Config, *, memory_ids=None, model_runner=None, request_id=None,
                     update_persona=True, ordinary_only=False, checkpoint=None):
    checkpoint = checkpoint or (lambda: None)
    checkpoint()
    request_id = request_id or "thought_" + uuid4().hex
    material, current_stances = deliberation_inputs(store,memory_ids)
    if ordinary_only:
        material = [m for m in material if m["origin"] == "conversation"]
        current_stances = [m for m in current_stances if m["origin"] == "conversation"]
    if not material:
        return '没有新的有效材料可消化，想法不变。'

    current_md = (persona_md() or "") if update_persona else ""
    if current_md:
        from .persona_context import parse_snapshot
        try:
            parse_snapshot(current_md)
        except ValueError:
            return "人格结构需要先修正，本次尚未调用思辨模型或写入观点。"
    persona_epoch = store.meta_get("session_epoch")
    supplied = {m["id"]: m for m in current_stances}
    expected_versions = {m["id"]: memory_version(m) for m in current_stances}
    current_section = json.dumps([
        {"id": m["id"], "version": memory_version(m), "title": m["title"],
         "claim": _field_of(m["content"], "主张") or m["title"],
         "status": _field_of(m["content"], "状态") or "验证", "content": m["content"]}
        for m in current_stances], ensure_ascii=False)
    input_rows = list({m["id"]: m for m in material + current_stances}.values())
    external_input = any(m["origin"] != "conversation" for m in input_rows)
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
    reservation = store.reserve_learning_model(input_rows, reserve_budget=model_runner is None) if external_input else None
    if external_input and reservation is None:
        return '见闻思辨暂缓：未获额外模型额度、保存许可或相同输入已有未确认尝试。'
    if model_runner is not None:
        checkpoint()
        data = yield lambda: model_runner(prompt)
    else:
        resp = yield lambda: chat(client, cfg, [{"role": "user", "content": prompt}], json_mode=True, retry=not external_input,
                                  observation={"store": store, "purpose": "thought", "request_id": request_id})
        try:
            data = parse_json(resp.choices[0].message.content or "")
        except ValueError:
            return "思辨失败：模型输出无法解析（想法保持不变）。"
    checkpoint()
    if reservation:
        with store.source_transaction():
            store.conn.execute("UPDATE learning_model_attempts SET state='completed' WHERE identity=?", (reservation,))
        if not store._observation_settings().get('remember_observations'):
            return '见闻保存许可已变化，本轮未写入观点。'

    # Background compact reflection publishes its stance changes and consumed
    # input marks atomically, after model I/O has finished and the lock is held.
    with store.source_transaction() if not update_persona else nullcontext():
        available = {m["id"]: m for m in store.memory_rows(purpose="thought", scope="gui")}
        if (update_persona and (persona_md() or "") != current_md) or store.meta_get("session_epoch") != persona_epoch:
            return "人格在思辨期间已变更，本次未写入观点或旧人格快照。"
        if any(m["id"] not in available or memory_version(m) != memory_version(available[m["id"]]) for m in input_rows):
            return "思辨材料已变化或来源失效，本次未写入观点。"
        try:
            views, verifications = _validate_decisions(data, supplied)
        except ValueError:
            return "思辨回执的观点身份、版本或字段无效，本次未写入观点。"

        def live_target(ref):
            if ref is None:
                return None
            row = next((m for m in store.memory_rows(purpose="thought", scope="gui", kind="stance")
                        if m["id"] == ref["id"]), None)
            if row is None or memory_version(row) != expected_versions.get(ref["id"]):
                raise ValueError("观点已变化或来源资格失效")
            return row

        def live_sources(titles):
            current = {m["id"]: m for m in store.memory_rows(purpose="thought", scope="gui", kind="event")}
            valid = [title for title in titles if title in source_rows
                    and source_rows[title]["id"] in current
                    and memory_version(source_rows[title]) == memory_version(current[source_rows[title]["id"]])]
            return valid if len(valid) == len(titles) else []

        if not views and not verifications:
            store.mark_thought_rows(material, 'no_new_view')
            store.meta_set("last_deliberate_at", time.time())
            return "思辨完成：这批记忆还不足以长出新观点。"

        title_pressure = {m["title"]: bool(m["pressure"]) for m in material
                          if sum(other["title"] == m["title"] for other in material) == 1}
        source_rows = {m["title"]: m for m in material if m["title"] in title_pressure}

        def apply_verdict(ref, body, sources, verdict):
            with store.source_transaction():
                prior = live_target(ref)
                if live_sources(sources) != sources:
                    raise ValueError("验证依据已经失效")
                descendants = store.dependent_memories([prior["id"]])
                if any(source_rows[title]["id"] in descendants for title in sources):
                    raise ValueError("观点不能用自身派生材料作为独立验证依据")
                parents = [store.get_memory(row[0]) for row in store.conn.execute(
                    "SELECT parent_memory_id FROM memory_dependencies WHERE memory_id=?", (prior["id"],))]
                if any(row is None for row in parents):
                    raise ValueError("观点旧依据已失效")
                proof = "、".join(f"#{source_rows[title]['id']}@{memory_version(source_rows[title])}" for title in sources)
                body += f"\n核对：#{prior['id']}@{ref['version']}；{verdict}；依据 {proof}"
                store.update_memory_content(prior["id"], body)
                store.bind_derived_memory(prior["id"], parents + [source_rows[title] for title in sources], scope="gui")
            expected_versions[prior["id"]] = memory_version(store.get_memory(prior["id"]))

        # —— 先验验证（v0.4 生命周期）：先处理裁决，再消化新观点 ——
        verified = falsified = 0
        pending_falsified: dict[int, str] = {}  # 标记证伪但暂不退役：给同批的后继修订（replaces）接脉络的机会
        for w in verifications:
            verdict = w["verdict"]
            try:
                prior = live_target(w["target"])
            except ValueError:
                continue
            if external_input:
                # A restricted web interpretation cannot mutate a global prior.
                continue
            if prior is None or prior["kind"] != "stance":
                continue
            if _field_of(prior["content"], "状态") != "先验":
                continue  # 成熟观点不走轻门控——只能经 views.replaces 的重门控修订
            vsources = live_sources(w["sources"])
            if not vsources:
                continue  # 裁决也要过 A01：没有真实出处的事件依据，裁决不生效
            for s in vsources:  # 裁决引用同样计使用——依据链与观点同寿
                src = source_rows.get(s)
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
                        body = re.sub(r"^不确定：(.*)$", lambda match: "不确定：" + match.group(1) + "（" + _field_value(mark) + "）", body, count=1, flags=re.M)
                    else:
                        body = body.rstrip("\n") + f"\n不确定：{_field_value(mark)}"
                try:
                    apply_verdict(w["target"], body, vsources, verdict)
                except ValueError:
                    continue
                verified += 1
            elif verdict == "证伪":
                # 轻门控：先验被真实经历证伪。先标记不删——同批若有 replaces 它的后继观点，
                # 由 views 循环接走「曾以为」脉络；无人接棒则收尾扫掉，教训留在墓碑
                body = _set_state(prior["content"], "证伪")
                body = re.sub(r"^下次回应：.*$",
                              lambda match: "下次回应：（已被经历证伪：" + _field_value(note or "与经历矛盾") + "）",
                              body, count=1, flags=re.M)
                try:
                    apply_verdict(w["target"], body, vsources, verdict)
                except ValueError:
                    continue
                pending_falsified[prior["id"]] = (
                    f"先验 #{prior['id']}@{w['target']['version']} 被经历证伪："
                    f"{note or '真实经历与之矛盾'}（依据：{'、'.join(vsources)}）"
                )
                falsified += 1

        added = revised = rejected = dropped = 0
        for v in views:
            claim = v["claim"].strip()
            sources = v["sources"]
            valid = live_sources(sources)  # A01：出处必须逐字存在于本次材料
            if not valid:
                dropped += 1  # 空出处或虚构出处：整条丢弃，不给"无中生有的深刻"留门
                continue
            interp = v.get("interp") if isinstance(v.get("interp"), dict) else {}
            kind = str(interp.get("kind", "经历")).strip() or "经历"
            if external_input:
                kind = "见闻"
            if kind not in {"经历", "推断", "用户", "捐赠", "见闻"}:
                kind = "经历"
            content = (
                f'主张：{_field_value(claim)}\n'
                f'遭遇：{_field_value(str(v.get("encounter", "")).strip() or "（未记）")}\n'
                f'事实：{_field_value(str(v.get("facts", "")).strip() or "（未记）")}\n'
                f'解释：{kind}——{_field_value(str(interp.get("note", "")).strip() or "（未记）")}\n'
                f'不确定：{_field_value(str(v.get("missing", "暂无")).strip() or "暂无")}\n'
                f'下次回应：{_field_value(str(v.get("next_response", "暂无")).strip() or "暂无")}\n'
                f'出处：{"、".join(valid)}\n'
                f'状态：{"先验" if external_input else "验证"}'
            )
            replaces = v["replaces"]
            try:
                old = live_target(replaces)
            except ValueError:
                rejected += 1
                continue  # A stale replacement must never become an unrelated new view.
            if old is not None and (_field_of(old["content"], "主张") or old["title"]) == claim:
                continue
            if old is not None:
                if not external_input and any(source_rows[title]["id"] in store.dependent_memories([old["id"]])
                                              for title in valid):
                    rejected += 1
                    continue
                if external_input:
                    from .learning import source_versions
                    old_sources = store.observation_sources(old['id'], scope='gui')
                    new_sources = [p for row in material if row['origin'] != 'conversation'
                                   for p in store.observation_sources(row['id'],scope='gui')]
                    if not old_sources or not set(source_versions(new_sources))-set(source_versions(old_sources)):
                        rejected += 1
                        continue
                # 有据让步门控（分层）：成熟观点的修订需新论据（校验后出处至少一个不在旧依据里）；
                # 先验（含刚被标记证伪的）走轻门控——它本就未经验证，被现实打磨是成熟不是翻案
                if _field_of(old["content"], "状态") not in {"先验", "证伪"}:
                    previous_basis = {(row[0], row[1]) for row in store.conn.execute(
                        "SELECT parent_memory_id,parent_version FROM memory_dependencies WHERE memory_id=?", (old["id"],))}
                    current_basis = {(source_rows[title]["id"], memory_version(source_rows[title])) for title in valid}
                    has_new_basis = (bool(current_basis - previous_basis) if previous_basis
                                     else bool(set(valid) - _sources_of(old["content"])))
                    if not has_new_basis:
                        rejected += 1
                        continue
                former = _field_of(old["content"], "主张") or old["title"]
                content = f'曾以为：{_field_value(former)}\n修订自：{old["id"]}@{replaces["version"]}\n{content}'
            # A01：压力沿派生链保留——出处含压力记忆的观点自身带压力标记，
            # 后续演化与晋升闸门按压力材料对待，不回流为普通人格依据
            src_pressure = any(title_pressure[s] for s in valid)
            for s in valid:  # 思辨引用计使用（bump_use）——被消化成观点的见闻/事件与观点同寿
                src = source_rows.get(s)
                if src is not None:
                    store.bump_use(src["id"])
            try:
                with store.source_transaction():
                    if live_sources(valid) != valid:
                        raise ValueError("提交前依据失效")
                    if external_input:
                        current_inputs = {row["id"]: row for row in store.memory_rows(purpose="thought", scope="gui")}
                        if any(row["id"] not in current_inputs or memory_version(row) != memory_version(current_inputs[row["id"]])
                               for row in input_rows):
                            raise ValueError("提交前完整见闻输入失效")
                        store._check_source_space(len((claim + content + " ".join(valid)).encode("utf-8")))
                    mid = store.insert_memory(
                        title=claim[:24], content=content, keywords="观点 " + " ".join(valid),
                        layer="longterm", kind="stance", pressure=1 if src_pressure else 0,
                    )
                    store.bind_derived_memory(mid, input_rows if external_input else [source_rows[s] for s in valid], scope="gui")
                    if old is not None:
                        live_target(replaces)  # Immediately before writing the ID-bound retirement.
                        store.conn.execute('INSERT OR IGNORE INTO view_revisions VALUES(?,?,?,?)',
                            (old['id'], mid, '新证据修订；输入版本 ' + replaces['version']
                             + '；提交版本 ' + memory_version(old), time.time()))
                        if not external_input:
                            store.expire_memory(old["id"], reason=f"观点被新证据修订：#{old['id']} → #{mid}")
                            pending_falsified.pop(old["id"], None)
            except ValueError:
                dropped += 1
                continue
            if old is not None and not external_input:
                revised += 1
            else:
                added += 1

        # 收尾扫除：被证伪但同批无人接棒（无 replaces 后继）的先验——退役，教训留在墓碑
        for mid, falsify_reason in pending_falsified.items():
            try:
                target = live_target({"id": mid})
            except ValueError:
                continue
            if _field_of(target["content"], "状态") == "证伪":
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
        if changed and current_md and update_persona:
            # 「我现在的想法」恒由库内已接纳观点确定性重建（A01 推广：模型叙事弃用——
            # 信任模型的全文重写会漏掉库内已有观点与元先验）
            narrative = _render_stances(store) or "（暂无当前可用观点）"
            from .persona_transaction import atomic_text
            new_md = _write_section(current_md, narrative)
            atomic_text(paths.PERSONA_FILE, new_md)
        elif changed and update_persona:
            summary += "；当前未安装人格，观点保留在记忆库，未创建缺少底色的人格文件"
        store.mark_thought_rows(material)
        if update_persona:
            _append_changelog(f"思辨：{summary}")
        store.meta_set("last_deliberate_at", time.time())
        return f"思辨完成：{summary}。" if changed else f"思辨完成：{summary}，想法不变。"


def deliberate_if_due(store: Store, client, cfg: Config, *, request_id=None) -> str | None:
    return drive(deliberate_if_due_steps(store, client, cfg, request_id=request_id))


def deliberate_if_due_steps(store: Store, client, cfg: Config, *, model_runner=None, request_id=None):
    """数据触发：自上次思辨起，新事件类记忆累计到阈值即思辨。安静就不变。"""
    pending = store.pending_thought_rows()
    # External batches run in the idle supervisor. Immediate user discussion
    # already has reflect_on_sources, without a second model call after reply.
    if model_runner is None and any(row['origin'] != 'conversation' for row in
            pending + store.memory_rows(purpose='thought',scope='gui',kind='stance')):
        return None
    # Inputs already handled by an immediate viewpoint are excluded by version.
    early = cfg.memory.deliberate_early_high and any(row['value_tier'] == 'high' or row['reflection_hint'] in {'conflict','repeated'} for row in pending)
    if early or len(pending) >= cfg.memory.deliberate_every_memories:
        return (yield from deliberate_steps(store, client, cfg, memory_ids={row['id'] for row in pending}, model_runner=model_runner, request_id=request_id))
    return None
