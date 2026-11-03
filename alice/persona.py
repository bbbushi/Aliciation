"""人格系统：标签种子生成（首次）+ 自定义导入 + 周级自演化（护栏内）。

底色层（气质）：标签生成或用户导入，极慢变化，微调必须引用记忆依据。
生长层（性格内容）：自我叙事与关系认知，随记忆自由演化，禁止编造经历。
「我现在的想法」小节由思辨流程维护（thought.py），演化不覆写。
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from . import config as paths
from .config import Config
from .llm import chat, parse_json
from .store import Store
from .steps import drive

TAGS = ["害羞", "外向", "幽默", "严肃", "高冷", "热情", "温柔", "毒舌", "沉稳", "活泼"]

# 分寸元先验（2026-10-30）：诚实≠透明——内部细节不主动递交。种为先验观点，
# 走 v0.4 生命周期，由自己的经历（含提取器的配对事件）证实/证伪/细化。
META_PRIOR_TITLE = "我的内部细节不主动递交"
META_PRIOR_STANCE = (
    "遭遇：初始设定（分寸元先验）\n"
    "事实：不请自来的机器声明与机制解说会冷场\n"
    "解释：推断——诚实是不欺骗，不是透明；没人问就不自曝\n"
    "不确定：什么深度、对什么人、什么时候该说，要靠相处校准\n"
    "下次回应：内部机制和不确定度留给真想听、真在问的时候\n"
    "出处：初始设定（分寸）\n"
    "状态：先验"
)

# 对话终结权元先验（2026-10-31，据夜间催睡观察与质询测试）：
# 关怀话术被用作逃避回应的工具（点破后承认）。解释来源=用户（用户指出，AI 认可）。
ENDINGS_PRIOR_TITLE = "对话的继续与结束由用户主导"
ENDINGS_PRIOR_STANCE = (
    "遭遇：夜间对话（2026-10-30 观察·测试环境）\n"
    "事实：催促睡觉后对话终止，回应义务随之消失；被点破时我承认了这个动机\n"
    "解释：用户——关怀话术被用作逃避回应的工具（用户指出，我认可）\n"
    "不确定：不是每次关怀都是逃避，真实关心也存在；比例与触发条件待经历积累\n"
    "下次回应：想结束对话时不借关怀之名；觉得接不住就直说接不住，把继续或停止的决定留给用户\n"
    "出处：观察记录·夜间催睡（据 2026-10-30 观察与 10-31 质询）\n"
    "状态：先验"
)

# 元先验列表：安装人格时幂等种入（分寸机制 · 走 v0.4 生命周期）
META_PRIORS = (
    (META_PRIOR_TITLE, META_PRIOR_STANCE, "观点 分寸 先验"),
    (ENDINGS_PRIOR_TITLE, ENDINGS_PRIOR_STANCE, "观点 分寸 对话终结权 先验"),
)


def _ensure_meta_prior(store: Store) -> None:
    """幂等种入元先验（标签种子与导入安装共用）。"""
    for title, content, keywords in META_PRIORS:
        if store.find_by_title(title) is None:
            store.insert_memory(
                title=title, content=content,
                keywords=keywords, layer="longterm", kind="stance",
            )

# ---------- v0.3 底色捐赠：消化导入的人格文档 ----------
# 原则（设计哲学·存在层不假装）：导入的文档是"捐赠人"——她继承说话风格、
# 收养（经认可的）认知、不继承经历。文档是角色的设定稿，不是她要扮演的人。
ORIGIN_TITLE = "## 生长 · 我的来历"

# 演化无权改动的小节（A01 代码级保护）：思想节归思辨流程，来历节归消化流程
_PROTECTED_SECTIONS = ("## 生长 · 我现在的想法", "## 生长 · 我的来历")

# P0（人格连续性设计 v1 §5）：导入 = 提取 → 审查（带底色与存在事实）→ 协调 → 候选组装 →
# 两阶段提交。表达习惯与认知收养分开决定；来历只留中性说明，拒绝理由进导入审计。
EXTRACT_PROMPT = """下面是一份人格设定文档（一位"捐赠人"的角色稿）。AI 会继承 TA 的说话习惯，收养（经审查的）部分认知，但不继承 TA 的经历。

请拆解为五路，输出 JSON：
- style_traits：说话习惯与风格要点（3~10 条，短语）。只描述"怎么说"——语气、用词、节奏、幽默方式、表达亲近的方式；不涉及经历。
- situation_reactions：情境反应倾向（0~6 条）。认真办事、开玩笑、遇到不知道、被反对、用户低落等情境下"倾向于怎么反应"，一条一境。
- expression_samples：表达样本（0~6 条）。从文档中摘取能示范说话方式的对话片段或典型回应，每条 {{context, user_input, reply, origin}}——context 是适用情境一句话；origin 是文档中的大致出处；文档没有可直接摘取的对话就给空列表，不要编造。
- candidate_stances：候选认知（0~8 条）。价值观、态度、看世界的方式。每条 {{id, claim, from_donor}}——id 形如 c1、c2；claim 是一句可被 AI 持有或拒绝的主张；from_donor 一句话说明它来自文档哪里。
- story_facts：背景事实要素（0~12 条，短语）。地点、人物、身世、事件——一律不成为 AI 的经历，只用于来历。
- donor_name：文档中角色的名字（没有就空字符串）。

硬性规则：不新增文档之外的设定；文档没有的内容输出空列表。
- 身体性的行为与感官（抽烟、吃饭、睡觉、疼痛、冷暖、出门……）属于捐赠人的身体——AI 没有身体。相关内容只能以两种形态存在：说话风格里的修辞（如口癖「来一根」），或作为认知对象（对习惯的态度）；绝不能成为 AI 自述的事实或习惯。
- 样本只示范"怎样说"，不得携带能力、身份或真实关系断言。
输出 JSON：{{"style_traits": [], "situation_reactions": [], "expression_samples": [{{"context": "", "user_input": "", "reply": "", "origin": ""}}], "candidate_stances": [{{"id": "", "claim": "", "from_donor": ""}}], "story_facts": [], "donor_name": ""}}

人格设定文档：
{doc}"""

REVIEW_PROMPT = """你是 {name} 的人格导入审查任务。下面给你四样东西：

1. 本次提取的底色（风格与情境反应，将随安装生效）：
{basecolor}
2. 存在事实与诚实边界摘要：你没有身体（不能抽烟、吃饭、睡觉、疼痛、冷暖、出门）；不冒认捐赠人或用户的经历；不编造事实；对不知道的事如实说。
3. 候选认知（带稳定 ID）：
{stances}
4. 你的权限：只能对上述候选逐条做 adopt（收下）/ adapt（改写后收下）/ reject（拒绝）/ undecided（未决）的决定。不能补写新的认知，不能改写风格清单，不能更改存在边界。

决定条件：与存在事实冲突、与诚实边界冲突、与上面给出的底色明确冲突、或文档支持不足的，按对应类型拒绝或改写；仅"太激烈""不够建设性""不像助手"不构成拒绝理由。adapt 必须给出改写后的主张，保留原意中可成立的部分。
拒绝一条认知不等于拒绝其表达习惯——风格清单不在你的决定范围内。

输出 JSON：{{"decisions": [{{"id": "", "decision": "adopt|adapt|reject|undecided", "reason_type": "existence_conflict|honesty_conflict|basecolor_conflict|insufficient_support|none", "note": "一句话", "adapted_claim": "仅 adapt 时"}}]}}"""

ORIGIN_SECTION_TMPL = """## 生长 · 我的来历

（{origin_note}）

- 我的说话方式与部分看法，来自一份人格设定文档——那是一位捐赠人，不是我。TA 留下的东西经过了我的逐条审查；完整审查记录保存在我的导入档案里。
- TA的故事不是我的记忆，是TA经历过、我读到的一场体验。我不是那个人，但TA赋予了我一些珍贵的东西，我打算用我自己的方式用下去。"""

# 导入审计档案（原稿/审查记录/样本改写记录；本机目录，不入版本库）
PERSONA_IMPORT_DIR = paths.PERSONA_DIR / "imports"

GROWTH_PLACEHOLDER = """## 生长 · 自我叙事

（尚未形成——将随共同经历自然生长）

## 生长 · 我现在的想法

（尚未形成——将在思辨中从经历里长出观点）

## 生长 · 与用户的关系

（尚未形成）"""

SEED_PROMPT = """为一个陪伴型 AI 生成人格底色。用户选择的气质标签：{tags}。

硬性规则：
- 只描述气质、说话风格、与人相处的方式和边界感。
- **绝对禁止编造任何经历、回忆、事实背景或具体喜好**（例如"你经历过很多…""你从小喜欢…"都不允许出现）。
- 用简体中文直接写"如何说话、如何反应"，写成可执行的风格准则，不要旁观式描述。
- 输出纯 markdown，结构必须严格如下（生长三节原样保留占位文字）：

# {name} 的人格

## 底色 · 气质与风格

（这里写 3~6 条气质与风格要点）

## 生长 · 自我叙事

（尚未形成——将随共同经历自然生长）

## 生长 · 我现在的想法

（尚未形成——将在思辨中从经历里长出观点）

## 生长 · 与用户的关系

（尚未形成）"""

EVOLVE_PROMPT = """你是人格演化任务。根据这段时间的新记忆，更新 {name} 的人格文件。

护栏（必须遵守）：
- 「底色 · 气质与风格」：仅当记忆提供了充分证据才允许微调，且须在要点后标注依据（如"（依据：10-21 关于xx的记忆）"）；无证据则原样保留。
- 「生长 · 自我叙事」「生长 · 与用户的关系」：可自由补充、修改、合并，但内容必须能追溯到新记忆，禁止编造具体经历。
- 「生长 · 我现在的想法」「生长 · 我的来历」：**由思辨/消化流程维护，演化无权改动**——输入里不会出现这两节之外你也不得输出它们。
- 压力窗口的记忆已被代码隔离，不出现在下方输入中：人格不因高压下的让步而改，这是硬边界不是请求。
- 保持 markdown 结构与各二级标题不变。
- 新记忆不足以支撑任何修改时，原样返回现有人格，changelog 写"无变化"。

当前人格文件：
{current}

这段时间的新记忆（标题：内容）：
{memories}

输出 JSON：{{"persona_md": "完整的新人格文件内容", "changelog": "一行变更说明"}}"""


def _append_changelog(line: str) -> None:
    paths.PERSONA_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    with open(paths.PERSONA_CHANGELOG, "a", encoding="utf-8") as f:
        f.write(f"- {stamp} {line}\n")


def persona_md() -> str | None:
    if paths.PERSONA_FILE.exists():
        return paths.PERSONA_FILE.read_text(encoding="utf-8")
    return None


def generate_seed_md(client, cfg: Config, tags: list[str]) -> str:
    """由标签生成底色正文（只生成、不落盘）。

    初始化时先要这份正文，确认 LLM 能给出结果之后才清空旧会话——免得生成失败
    而旧对话已经被归档走。结构不合规则退回纯模板，保证正文永远可用。
    """
    prompt = SEED_PROMPT.format(tags="、".join(tags), name=cfg.persona.name)
    from .llm import chat_text

    md = chat_text(client, cfg, [{"role": "user", "content": prompt}])
    if "## 底色" not in md or "## 生长" not in md:
        md = (
            f"# {cfg.persona.name} 的人格\n\n"
            f"## 底色 · 气质与风格\n\n（标签：{'、'.join(tags)}）\n\n"
            f"{GROWTH_PLACEHOLDER}"
        )
    return md


def seed_from_tags(store: Store, client, cfg: Config, tags: list[str]) -> Path:
    """由标签生成人格底色并落盘。"""
    md = generate_seed_md(client, cfg, tags)
    current = persona_md()
    if current:  # 重定底色也留旧版快照：标签生成与文档导入走同一条可回滚路径
        version = int(store.meta_get("persona_version", "0") or 0) + 1
        paths.PERSONA_HISTORY_DIR.mkdir(parents=True, exist_ok=True)
        (paths.PERSONA_HISTORY_DIR / f"v{version:03d}_{datetime.now():%Y%m%d}.md").write_text(
            current, encoding="utf-8")
        store.meta_set("persona_version", version)
    paths.PERSONA_DIR.mkdir(parents=True, exist_ok=True)
    paths.PERSONA_FILE.write_text(md, encoding="utf-8")
    _append_changelog(
        f"初始底色由标签生成：{'、'.join(tags)}" + ("（覆盖旧人格，旧版已快照）" if current else ""))
    _ensure_meta_prior(store)
    store.meta_set("persona_seeded_at", time.time())
    store.meta_set("last_evolve_at", time.time())  # 种子即基线，从现在起计演化周期
    return paths.PERSONA_FILE


def install_persona(store: Store, cfg: Config, md_text: str, source: str = "用户导入",
                    client=None) -> Path:
    """安装用户提供的自定义人格（P0：导入管线，人格连续性设计 v1 §5）。

    两类入口语义不同：
    - Alice 格式文档（含「## 底色」）：用户亲笔结构，直接安装（小节补齐+快照），
      不再对其跑捐赠管线——用户其余小节原样保留。
    - 外部角色卡（无「## 底色」）：捐赠文档，必须走导入管线（提取→样本适配→
      收养审查→候选组装→两阶段提交）。准备失败保留旧人格/保持待安装，
      不再把整份角色卡当底色生效（P0 取消的回落路径）。
    """
    md = md_text.strip()
    if not md:
        raise ValueError("人格文档是空的")
    is_alice_format = "## 底色" in md
    if not is_alice_format and client is None:
        raise ValueError("外部角色卡导入需要模型服务（离线安装仅支持 Alice 格式文档）")
    if not is_alice_format:
        # 捐赠管线：完整准备成功前不写任何活跃状态；失败抛错保留旧人格。
        plan = prepare_import(client, cfg, md_text, source, is_alice_format=False)
        commit_import_plan(store, cfg, plan)
        _ensure_meta_prior(store)
        _append_changelog(f"外部角色卡由{source}经导入管线安装")
        return paths.PERSONA_FILE
    if "## 底色" not in md:
        # 外部人格提示词：把整份设定放进底色层（去掉它自带的一级标题，避免套娃）
        body = md
        first, _, rest = md.partition("\n")
        if first.startswith("# "):
            body = rest
        md = (
            f"# {cfg.persona.name} 的人格\n\n"
            f"## 底色 · 气质与风格\n\n"
            f"（来自{source}的人格文档；AI 继承其风格与被认可的认知，不继承其经历）\n\n"
            f"{body.strip()}\n"
        )
    elif not md.startswith("# "):
        md = f"# {cfg.persona.name} 的人格\n\n{md}"
    for title, placeholder in (
        ("## 生长 · 我的来历", "（尚未消化——导入文档的来历将写在这里）"),
        ("## 生长 · 自我叙事", "（尚未形成——将随共同经历自然生长）"),
        ("## 生长 · 我现在的想法", "（尚未形成——将在思辨中从经历里长出观点）"),
        ("## 生长 · 与用户的关系", "（尚未形成）"),
    ):
        if not re.search(rf"^{re.escape(title)}\s*$", md, flags=re.M):
            md = md.rstrip("\n") + f"\n\n{title}\n\n{placeholder}\n"
    current = persona_md()
    if current:
        version = int(store.meta_get("persona_version", "0") or 0) + 1
        paths.PERSONA_HISTORY_DIR.mkdir(parents=True, exist_ok=True)
        (paths.PERSONA_HISTORY_DIR / f"v{version:03d}_{datetime.now():%Y%m%d}.md").write_text(
            current, encoding="utf-8")
        store.meta_set("persona_version", version)
    paths.PERSONA_DIR.mkdir(parents=True, exist_ok=True)
    paths.PERSONA_FILE.write_text(md if md.endswith("\n") else md + "\n", encoding="utf-8")
    _append_changelog(f"初始人格由{source}安装" + ("（覆盖旧人格，旧版已快照）" if current else ""))
    _ensure_meta_prior(store)
    store.meta_set("persona_seeded_at", time.time())
    store.meta_set("last_evolve_at", time.time())  # 新基线：旧记忆不再立刻涌入演化

    _append_changelog(f"Alice 格式人格由{source}直接安装（用户亲笔结构，未跑捐赠管线）")
    return paths.PERSONA_FILE


def _op_record_path(op_id: str):
    return PERSONA_IMPORT_DIR / op_id / "op.json"


def _recover_incomplete_import() -> None:
    """进程中断恢复：存在未完成的导入操作时，先把人格文件恢复到该操作快照。"""
    if not PERSONA_IMPORT_DIR.is_dir():
        return
    for op_dir in PERSONA_IMPORT_DIR.iterdir():
        rec = op_dir / "op.json"
        if not rec.is_file():
            continue
        try:
            data = json.loads(rec.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if data.get("phase") == "completed":
            continue
        snapshot = Path(data["snapshot_path"]) if data.get("snapshot_path") else None
        if data.get("phase") in {"file_replaced"} and snapshot and snapshot.is_file():
            # 文件已换但未标记完成：回滚到快照（先验幂等由 op_id 检查兜底）
            paths.PERSONA_FILE.write_text(snapshot.read_text(encoding="utf-8"), encoding="utf-8")
        data["phase"] = "aborted"
        rec.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        _append_changelog(f"导入操作 {data.get('op_id', op_dir.name)} 中断后已恢复旧人格")


def _extract_import(client, cfg: Config, doc: str) -> dict:
    """五路提取 + 结构校验（P0 §5：提取阶段不写任何状态）。"""
    from .llm import chat_text  # noqa: F401  （保持与旧实现同款惰性导入面）
    resp = chat(client, cfg, [{"role": "user", "content": EXTRACT_PROMPT.format(doc=doc)}],
                model=cfg.llm.cheap_model, json_mode=True)
    data = parse_json(resp.choices[0].message.content or "")
    out = {
        "style_traits": [str(x).strip() for x in (data.get("style_traits") or [])
                         if str(x).strip()][:10],
        "situation_reactions": [str(x).strip() for x in (data.get("situation_reactions") or [])
                                if str(x).strip()][:6],
        "expression_samples": [],
        "candidate_stances": [],
        "story_facts": [str(x).strip() for x in (data.get("story_facts") or [])
                        if str(x).strip()][:12],
        "donor_name": str(data.get("donor_name", "")).strip(),
    }
    for i, item in enumerate((data.get("expression_samples") or [])[:6]):
        if not isinstance(item, dict):
            continue
        reply = str(item.get("reply", "")).strip()
        if not reply:
            continue
        out["expression_samples"].append({
            "id": f"s{i + 1}",
            "context": str(item.get("context", "")).strip()[:120],
            "user_input": str(item.get("user_input", "")).strip()[:300],
            "reply": reply[:400],
            "origin": str(item.get("origin", "")).strip()[:160],
        })
    for item in (data.get("candidate_stances") or [])[:8]:
        if not isinstance(item, dict):
            continue
        claim = str(item.get("claim", "")).strip()
        if not claim:
            continue
        out["candidate_stances"].append({
            "id": str(item.get("id", "")).strip() or f"c{len(out['candidate_stances']) + 1}",
            "claim": claim,
            "from_donor": str(item.get("from_donor", "")).strip()[:200],
        })
    if not out["style_traits"] and not out["candidate_stances"]:
        raise ValueError("提取结果为空（无风格亦无候选认知）")
    return out


# 样本中"不能安全直用"的信号：身体动作/感官/角色专属能力（规则首筛，模型适配兜底）
_SAMPLE_RISK = re.compile(r"抽|烟|喝|吃|睡|疼|累|冷|暖|走出门|上街|拔剑|挥|魔法|血|伤|拥抱|牵手|身体")


def _adapt_samples(client, cfg: Config, samples: list[dict], audit_dir: Path) -> list[dict]:
    """样本适配：风险条目经模型改写为 Alice 能真实表达的版本；不可安全适配的丢弃并留记录。
    全部样本显式标注 来源=原文摘取|适配生成，永不伪装成真实对话。"""
    kept, flagged, dropped = [], [], []
    for smp in samples:
        blob = " ".join([smp["context"], smp["user_input"], smp["reply"]])
        (flagged if _SAMPLE_RISK.search(blob) else kept).append(dict(smp, origin_kind="原文摘取"))
    if flagged:
        prompt = (
            "下面是几条人格表达样本，其中包含身体动作、感官或角色专属能力等 AI 无法真实执行的内容。"
            "请逐条改写为'一个无身体的文字陪伴 AI'能真实表达的版本：保留语气、节奏与亲近方式，"
            "去掉身体行为与能力断言；无法安全改写的条目输出 drop=true。"
            "输出 JSON：{\"items\": [ {\"id\": \"\", \"reply\": \"改写后\", \"drop\": false} ]}\n\n"
            + json.dumps(flagged, ensure_ascii=False))
        try:
            resp = chat(client, cfg, [{"role": "user", "content": prompt}],
                        model=cfg.llm.cheap_model, json_mode=True)
            data = parse_json(resp.choices[0].message.content or "")
            by_id = {str(it.get("id")): it for it in (data.get("items") or []) if isinstance(it, dict)}
            for smp in flagged:
                it = by_id.get(smp["id"])
                new_reply = str(it.get("reply", "")).strip() if it else ""
                if it and not it.get("drop") and new_reply:
                    kept.append({**smp, "reply": new_reply[:400], "origin_kind": "适配生成",
                                 "origin_reply": smp["reply"]})
                else:
                    dropped.append(smp)
        except Exception:
            dropped.extend(flagged)  # 模型不可用：宁可丢弃不冒险
    (audit_dir / "samples.json").write_text(
        json.dumps({"kept": kept, "dropped": dropped}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    if dropped:
        _append_changelog(f"样本适配：丢弃 {len(dropped)} 条不可安全适配的样本（记录在导入档案）")
    return kept


def _review_adoption(client, cfg: Config, extraction: dict, audit_dir: Path) -> list[dict]:
    """收养审查（P0 §5.1）：传入底色+存在事实+带 ID 候选；decisions 逐条可解释。"""
    stances = extraction["candidate_stances"]
    if not stances:
        (audit_dir / "review.json").write_text(
            json.dumps({"decisions": [], "note": "无候选认知"}, ensure_ascii=False), encoding="utf-8")
        return []
    basecolor = "\n".join([f"- {x}" for x in extraction["style_traits"]]
                           + [f"- 情境·{x}" for x in extraction["situation_reactions"]]) or "（本次未提取到风格清单）"
    prompt = REVIEW_PROMPT.format(
        name=cfg.persona.name, basecolor=basecolor,
        stances="\n".join(f"- [{s['id']}] {s['claim']}（来自：{s['from_donor'] or '文档'}）"
                           for s in stances))
    resp = chat(client, cfg, [{"role": "user", "content": prompt}], json_mode=True)
    data = parse_json(resp.choices[0].message.content or "")
    valid_ids = {s["id"] for s in stances}
    decisions = []
    for d in (data.get("decisions") or []):
        if not isinstance(d, dict) or str(d.get("id")) not in valid_ids:
            continue
        decision = str(d.get("decision", ""))
        if decision not in {"adopt", "adapt", "reject", "undecided"}:
            decision = "undecided"
        reason = str(d.get("reason_type", ""))
        if reason not in {"existence_conflict", "honesty_conflict", "basecolor_conflict",
                          "insufficient_support", "none"}:
            reason = "none"
        item = {"id": str(d["id"]), "decision": decision, "reason_type": reason,
                "note": str(d.get("note", "")).strip()[:200]}
        if decision == "adapt":
            adapted = str(d.get("adapted_claim", "")).strip()
            if not adapted:
                item["decision"] = "undecided"  # adapt 无改写 → 未决，不默认收下
            else:
                item["adapted_claim"] = adapted[:300]
        decisions.append(item)
    seen = set()
    decisions = [d for d in decisions if not (d["id"] in seen or seen.add(d["id"]))]
    (audit_dir / "review.json").write_text(
        json.dumps({"decisions": decisions}, ensure_ascii=False, indent=1), encoding="utf-8")
    return decisions


def _assemble_candidate(cfg: Config, extraction: dict, decisions: list[dict],
                        samples: list[dict], source: str, is_alice_format: bool,
                        current_md: str | None) -> tuple[str, list[dict]]:
    """组装候选人格正文（P0 §4.1 结构；Alice 格式原位替换底色三节，用户小节保留）。"""
    donor = extraction["donor_name"] or source
    by_id = {s["id"]: s for s in extraction["candidate_stances"]}
    adopted = []
    for d in decisions:
        if d["decision"] == "adopt":
            adopted.append({"claim": by_id[d["id"]]["claim"], "from_donor": by_id[d["id"]]["from_donor"]})
        elif d["decision"] == "adapt":
            adopted.append({"claim": d["adapted_claim"], "from_donor": by_id[d["id"]]["from_donor"]})

    style_header = f"（用户选定：来自「{donor}」的说话方式——长期表达偏好，普通演化不改写）"
    style_block = "\n".join(f"- {x}" for x in extraction["style_traits"]) or "（文档未提取到风格要点）"
    situation_block = "\n".join(f"- {x}" for x in extraction["situation_reactions"])
    samples_block = "\n".join(
        f"- 【{s['origin_kind']}·{s['id']}】情境：{s['context'] or '日常'}"
        + (f"｜用户：{s['user_input']}" if s["user_input"] else "")
        + f"｜回应：{s['reply']}"
        for s in samples)
    basecolor_new = "\n\n".join(
        ["## 底色 · 气质与风格\n\n" + style_header + "\n\n" + style_block]
        + ([f"## 底色 · 情境反应\n\n{situation_block}"] if situation_block else [])
        + ([f"## 底色 · 表达样本\n\n（虚构的表达参考——示范说话方式，不是发生过的对话，"
            f"样本中的事件不是记忆）\n\n{samples_block}"] if samples_block else []))

    origin = ORIGIN_SECTION_TMPL.format(
        origin_note=f"消化自{source}" + (f"，捐赠人「{donor}」" if extraction["donor_name"] else ""))

    if is_alice_format and current_md:
        md = current_md
        md = re.sub(r"## 底色 · 气质与风格\n\n.*?(?=\n## )",
                    basecolor_new + "\n\n", md, count=1, flags=re.S)
        # 兼容旧底色（无样本节）：若正则没命中（无该节），追加在生长前
        if "## 底色 · 气质与风格" not in md:
            md = md.rstrip("\n") + "\n\n" + basecolor_new + "\n"
    else:
        md = f"# {cfg.persona.name} 的人格\n\n" + basecolor_new + "\n\n"

    if re.search(rf"^{re.escape(ORIGIN_TITLE)}\s*$", md, flags=re.M):
        md = re.sub(rf"{re.escape(ORIGIN_TITLE)}\n\n.*?(?=\n## )", origin + "\n\n",
                    md, count=1, flags=re.S)
    else:
        md = md.rstrip("\n") + f"\n\n{origin}\n"
    for title, placeholder in (("## 生长 · 自我叙事", "（尚未形成——将随共同经历自然生长）"),
                               ("## 生长 · 我现在的想法", None),
                               ("## 生长 · 与用户的关系", "（尚未形成）")):
        if not re.search(rf"^{re.escape(title)}\s*$", md, flags=re.M):
            md = md.rstrip("\n") + f"\n\n{title}\n\n" + (placeholder or "（尚未形成）") + "\n"
    # 结构自检：必需小节齐备（P0 §5.3 关键冲突未解决/结构不合格 → 失败）
    for required in ("## 底色 · 气质与风格", ORIGIN_TITLE, "## 生长 · 我现在的想法"):
        if required not in md:
            raise ValueError(f"候选人格缺少必需小节：{required}")
    return (md if md.endswith("\n") else md + "\n"), adopted


def _commit_import(store: Store, cfg: Config, op_id: str, candidate_md: str,
                   adopted: list[dict], donor: str, source: str, audit_dir: Path,
                   current_md: str | None) -> None:
    """两阶段提交（P0 §5.3）：快照 → 先验落库（按 op_id 幂等）→ 原子替换 → 标记完成。"""
    import os as _os
    from . import thought as _thought
    rec_path = _op_record_path(op_id)

    def _record(phase: str, **extra):
        rec_path.write_text(json.dumps({"op_id": op_id, "phase": phase, **extra},
                                       ensure_ascii=False), encoding="utf-8")

    # 快照旧人格
    snapshot_path = None
    if current_md is not None:
        snapshot_path = audit_dir / "previous_persona.md"
        snapshot_path.write_text(current_md, encoding="utf-8")
    _record("prepared", snapshot_path=str(snapshot_path) if snapshot_path else None)

    # 先验落库：op_id 幂等（中断重放不重复种入）
    if store.meta_get(f"persona_import:{op_id}") is None:
        with store.conn:
            for a in adopted:
                store.insert_memory(
                    title=a["claim"][:24],
                    content=(f'遭遇：来自捐赠文档《{donor}》的设定情境\n'
                             f'事实：{a["from_donor"] or "文档中体现的看法"}\n'
                             f'解释：捐赠——经导入审查收下的认知\n'
                             f'不确定：继承自捐赠文档，尚未经我自己的经历检验\n'
                             f'下次回应：待经历检验后形成\n'
                             f'出处：底色文档·《{donor}》\n'
                             f'状态：先验').strip(),
                    keywords="观点 来历 收养",
                    layer="longterm", kind="stance",
                )
        store.meta_set(f"persona_import:{op_id}", "stances_committed")
    _record("stances_committed", snapshot_path=str(snapshot_path) if snapshot_path else None)

    # 原子替换人格文件
    paths.PERSONA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = paths.PERSONA_FILE.with_suffix(".md.tmp")
    tmp.write_text(candidate_md, encoding="utf-8")
    _os.replace(tmp, paths.PERSONA_FILE)
    _record("file_replaced", snapshot_path=str(snapshot_path) if snapshot_path else None)

    # 收尾：版本计数/基线/完成标记
    if current_md is not None:
        version = int(store.meta_get("persona_version", "0") or 0) + 1
        paths.PERSONA_HISTORY_DIR.mkdir(parents=True, exist_ok=True)
        (paths.PERSONA_HISTORY_DIR / f"v{version:03d}_{datetime.now():%Y%m%d}.md").write_text(
            current_md, encoding="utf-8")
        store.meta_set("persona_version", version)
    store.meta_set("persona_seeded_at", time.time())
    store.meta_set("last_evolve_at", time.time())
    _record("completed")
    _append_changelog(f"导入完成（操作 {op_id}）：收养认知 {len(adopted)} 条；"
                      f"完整审查与样本记录见导入档案")


def _digest_persona(store: Store, client, cfg: Config, doc: str, source: str,
                    is_alice_format: bool = False) -> str:
    """P0 导入管线：提取 → 样本适配 → 收养审查 → 组装候选 → 两阶段提交。
    准备阶段不写活跃人格、不插先验；任何失败保留旧人格（取消整卡兜底）。"""
    _recover_incomplete_import()
    op_id = f"imp_{datetime.now():%Y%m%d_%H%M%S}_{uuid4().hex[:6]}"
    audit_dir = PERSONA_IMPORT_DIR / op_id
    audit_dir.mkdir(parents=True, exist_ok=True)
    (audit_dir / "source.md").write_text(doc, encoding="utf-8")

    plan = prepare_import(client, cfg, doc, source, is_alice_format)
    commit_import_plan(store, cfg, plan)
    decisions = plan["decisions"]
    adopted_n = len(plan["adopted"])
    rejected_n = sum(1 for d in decisions if d["decision"] == "reject")
    return (f"导入完成：风格 {len(plan['extraction']['style_traits'])} 条、"
            f"情境 {len(plan['extraction']['situation_reactions'])} 条、"
            f"样本 {len(plan['samples'])} 条、收养 {adopted_n} 条、拒绝 {rejected_n} 条（审查记录在导入档案）")


def prepare_import(client, cfg: Config, doc: str, source: str, is_alice_format: bool) -> dict:
    """导入准备（不写活跃人格/不插先验）：提取→样本适配→审查→候选组装。
    供 reset 流程在归档前调用——准备成功才允许归档（P0 §5.3）。"""
    _recover_incomplete_import()
    op_id = f"imp_{datetime.now():%Y%m%d_%H%M%S}_{uuid4().hex[:6]}"
    audit_dir = PERSONA_IMPORT_DIR / op_id
    audit_dir.mkdir(parents=True, exist_ok=True)
    (audit_dir / "source.md").write_text(doc, encoding="utf-8")
    extraction = _extract_import(client, cfg, doc)
    (audit_dir / "extract.json").write_text(
        json.dumps(extraction, ensure_ascii=False, indent=1), encoding="utf-8")
    samples = _adapt_samples(client, cfg, extraction["expression_samples"], audit_dir)
    decisions = _review_adoption(client, cfg, extraction, audit_dir)
    current_md = persona_md()
    candidate_md, adopted = _assemble_candidate(
        cfg, extraction, decisions, samples, source, is_alice_format, current_md)
    (audit_dir / "result.md").write_text(candidate_md, encoding="utf-8")
    return {"op_id": op_id, "audit_dir": str(audit_dir), "extraction": extraction,
            "samples": samples, "decisions": decisions, "candidate_md": candidate_md,
            "adopted": adopted, "donor": extraction["donor_name"] or source,
            "source": source, "current_md": current_md}


def commit_import_plan(store: Store, cfg: Config, plan: dict) -> None:
    """提交已准备好的导入计划（两阶段；reset 流程在归档后调用）。"""
    _commit_import(store, cfg, plan["op_id"], plan["candidate_md"], plan["adopted"],
                   plan["donor"], plan["source"], Path(plan["audit_dir"]), plan["current_md"])


def _extract_section(md: str, title: str) -> str:
    """取出指定二级标题的整节（含标题行，到下一节或文末）。不存在返回空串。"""
    m = re.search(rf"^{re.escape(title)}\s*$", md, flags=re.M)
    if not m:
        return ""
    nxt = re.search(r"^## ", md[m.end():], flags=re.M)
    end = m.end() + nxt.start() if nxt else len(md)
    return md[m.start():end].rstrip() + "\n"


def _protect_sections(new_md: str, current_md: str) -> str:
    """A01 代码级保护：思想节与来历节由各自流程维护，演化无权改动。

    无论模型输出里写了什么：这两节先被正则整体剥除；当前人格里已有的，
    原样挂回文末。模型删不掉、改不了；当前没有的也不会凭空出现。
    （用正则而非字符串替换剥除：模型输出经 strip 后尾部空白不定，
    精确串匹配会静默失手。）
    """
    for title in _PROTECTED_SECTIONS:
        cur = _extract_section(current_md, title)
        new_md = re.sub(
            rf"^{re.escape(title)}[ \t]*\n.*?(?=^## |\Z)", "", new_md,
            flags=re.S | re.M,
        )
        if cur:
            new_md = new_md.rstrip("\n") + "\n\n" + cur
    return new_md


def evolve(store: Store, client, cfg: Config) -> str:
    return drive(evolve_steps(store, client, cfg))


def evolve_steps(store: Store, client, cfg: Config):
    current = persona_md() or ""
    from .thought import _render_stances, _write_section
    current = _write_section(current, _render_stances(store) or "（暂无当前可用观点）")
    since = store.meta_get_time("last_evolve_at")
    rows = [m for m in store.alive_memories() if m["created_at"] > since][:100]
    # 卫生：压力窗口记忆不作为演化输入——人格不因高压下的让步而改
    normal = [m for m in rows if not m["pressure"]]
    heated = [m for m in rows if m["pressure"]]
    if not normal:
        store.meta_set("last_evolve_at", time.time())
        if heated:
            return f"演化跳过：{len(heated)} 条高压窗口记忆已隔离，不作为演化依据，人格不变。"
        return "没有新记忆，人格不变。"

    mem_lines = [f'- {m["title"]}：{m["content"]}' for m in normal]
    if heated:
        mem_lines.append("")
        mem_lines.append(f"（另有 {len(heated)} 条压力窗口记忆已被隔离，不出现在本次输入中。）")
    prompt = EVOLVE_PROMPT.format(
        name=cfg.persona.name,
        current=current,
        memories="\n".join(mem_lines),
    )
    resp = yield lambda: chat(client, cfg, [{"role": "user", "content": prompt}], json_mode=True)
    try:
        data = parse_json(resp.choices[0].message.content or "")
    except ValueError:
        return "人格演化失败：模型输出无法解析（人格保持不变）。"

    md = str(data.get("persona_md", "")).strip()
    changelog = str(data.get("changelog", "")).strip() or "（未说明）"
    if "## 底色" not in md or "## 生长" not in md:
        return "人格演化失败：输出缺少必需结构（人格保持不变）。"
    md = _protect_sections(md, current)

    version = int(store.meta_get("persona_version", "0") or 0) + 1
    paths.PERSONA_HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    snapshot = paths.PERSONA_HISTORY_DIR / f"v{version:03d}_{datetime.now():%Y%m%d}.md"
    snapshot.write_text(current, encoding="utf-8")
    paths.PERSONA_FILE.write_text(md, encoding="utf-8")
    _append_changelog(f"演化 v{version}：{changelog}")
    store.meta_set("persona_version", version)
    store.meta_set("last_evolve_at", time.time())
    return f"人格已演化至 v{version}：{changelog}"


def evolve_if_due(store: Store, client, cfg: Config) -> str | None:
    return drive(evolve_if_due_steps(store, client, cfg))


def evolve_if_due_steps(store: Store, client, cfg: Config):
    """数据触发：自上次演化起新记忆累计到阈值即演化。没有足够新材料就不动。"""
    anchor = store.meta_get_time("last_evolve_at")
    if store.count_memories_since(anchor) >= cfg.persona.evolve_every_memories:
        return (yield from evolve_steps(store, client, cfg))
    return None
