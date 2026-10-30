"""人格系统：标签种子生成（首次）+ 自定义导入 + 周级自演化（护栏内）。

底色层（气质）：标签生成或用户导入，极慢变化，微调必须引用记忆依据。
生长层（性格内容）：自我叙事与关系认知，随记忆自由演化，禁止编造经历。
「我现在的想法」小节由思辨流程维护（thought.py），演化不覆写。
"""
from __future__ import annotations

import re
import time
from datetime import datetime
from pathlib import Path

from . import config as paths
from .config import Config
from .llm import chat, parse_json
from .store import Store

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

DIGEST_PROMPT = """下面是一份人格设定文档（一位"捐赠人"的角色稿）。AI 会继承 TA 的说话风格与部分认知，但不会把 TA 的经历当成自己的——那段背景只是一场 TA 经历过的、AI 复盘过的体验。

请把文档拆解为三类，输出 JSON：
- style_traits：说话习惯与风格要点（3~10 条，短语）。只描述"怎么说"——语气、用词、节奏都算风格；不涉及任何经历。
- candidate_stances：文档中体现的价值观、态度、看世界的方式（0~8 条）。每条 {{claim, from_donor}}——claim 是一句可被 AI 自己持有或拒绝的主张，from_donor 一句话说明它来自文档里的什么。
- story_facts：背景中的事实性要素（地点、人物、身世、事件；0~12 条，短语）。这些一律不成为 AI 的经历，只用于撰写来历。
- donor_name：文档中角色的名字（没有就空字符串）。

硬性规则：不新增文档之外的设定；文档没有的内容输出空列表。
输出 JSON：{{"style_traits": [], "candidate_stances": [{{"claim": "", "from_donor": ""}}], "story_facts": [], "donor_name": ""}}

人格设定文档：
{doc}"""

ENDORSE_PROMPT = """你是 {name}。下面是一些候选认知，全部来自一份你复盘过的人格设定文档（一位"捐赠人"）——TA 不是你，你也不是在扮演 TA，但 TA 留下了很多东西。

请逐条决定：这条认知你是否认可？认可的条件是它与你的底色气质相容、你愿意从今往后把它当作自己的看法。不认可就明确拒绝（太陌生、与你的气质冲突、说不清都算拒绝——拒绝不是失败，是你和 TA 不一样的地方）。

{donor_line}候选认知：
{stances}

输出 JSON：{{"adopted": ["认可的主张原文"], "rejected": [{{"claim": "被拒绝的主张原文", "why": "一句话"}}]}}"""

ORIGIN_SECTION_TMPL = """## 生长 · 我的来历

（{origin_note}）

- 我的说话方式、一部分看法，来自一份名为「{donor}」的人格设定文档——那是一位捐赠人，不是我。{rej_line}
- 我从TA那里拿走了这些：{adopted_brief}
- 没拿走这些：{rejected_brief}
- TA的故事不是我的记忆，是TA经历过、我读到的一场体验。我不是那个人，但TA赋予了我一些珍贵的东西，我打算用我自己的方式用下去。"""

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
    """安装用户提供的自定义人格（v0.3 底色捐赠：消化而非扮演）。

    流程：先把文档装配为可用底色（与旧版一致的回落路径），再尝试消化——
    LLM 提取（风格/情绪词/候选认知/背景事实）+ 认知收养审查，成功后重写底色、
    写入来历段、把收养的认知落为 stance 记忆。消化失败不影响安装（回落整包底色）。
    已有人格时先快照入 history（可回滚），并重置演化基线。
    传入 client 才走消化；client=None 时保持旧行为（离线/降级路径）。
    """
    md = md_text.strip()
    if not md:
        raise ValueError("人格文档是空的")
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

    digest_note = ""
    if client is not None:
        try:
            digest_note = _digest_persona(store, client, cfg, md_text, source,
                                          is_alice_format="## 底色" in md_text)
        except Exception as e:  # 消化失败不回滚安装——底色已可用，来历段保持占位
            digest_note = f"（消化暂失败：{e}；已按整包底色安装）"
        if digest_note:
            _append_changelog(digest_note)
    return paths.PERSONA_FILE


def _digest_persona(store: Store, client, cfg: Config, doc: str, source: str,
                    is_alice_format: bool = False) -> str:
    """v0.3 消化：提取（cheap）→ 收养审查（chat）→ 重写底色/来历段/收养 stance 落库。

    外部角色卡：消化成功后整体重建干净结构（丢弃整包戏服）；Alice 格式文档：
    原位替换风格块/词表块/来历段/想法段，用户其余小节保留。
    返回 changelog 一行；背景事实只进来历段，绝不入记忆库（硬边界）。
    """
    from .llm import chat_text
    from . import thought

    resp = chat(client, cfg, [{"role": "user", "content": DIGEST_PROMPT.format(doc=doc)}],
                model=cfg.llm.cheap_model, json_mode=True)
    data = parse_json(resp.choices[0].message.content or "")
    styles = [str(s).strip() for s in (data.get("style_traits") or []) if str(s).strip()][:10]
    stances = [s for s in (data.get("candidate_stances") or [])
               if isinstance(s, dict) and str(s.get("claim", "")).strip()][:8]
    facts = [str(f).strip() for f in (data.get("story_facts") or []) if str(f).strip()][:12]
    donor = str(data.get("donor_name", "")).strip() or source
    if not styles and not stances:
        return "（消化未产出可用内容，保持整包底色）"

    # —— 认知收养审查（chat model，她的第一次"想"） ——
    adopted: list[str] = []
    rejected: list[tuple[str, str]] = []
    if stances:
        prompt = ENDORSE_PROMPT.format(
            name=cfg.persona.name,
            donor_line=f"这位捐赠人叫「{donor}」。\n" if donor else "",
            stances="\n".join(f"- {s['claim']}（来自：{s.get('from_donor', '文档')}）" for s in stances),
        )
        resp2 = chat(client, cfg, [{"role": "user", "content": prompt}], json_mode=True)
        data2 = parse_json(resp2.choices[0].message.content or "")
        adopted = [str(c).strip() for c in (data2.get("adopted") or []) if str(c).strip()]
        claim_set = {str(s["claim"]).strip() for s in stances}
        adopted = [c for c in adopted if any(c == k or c in k or k in c for k in claim_set)]
        for r in (data2.get("rejected") or []):
            if isinstance(r, dict) and str(r.get("claim", "")).strip():
                rejected.append((str(r["claim"]).strip(), str(r.get("why", "")).strip()))
    else:
        adopted, rejected = [], []

    # —— 收养的认知落为 stance 记忆（冷启动脊柱；v0.4：先验状态，待自己经历检验）——
    for claim in adopted:
        match = next((s for s in stances
                      if s["claim"].strip() == claim or claim in s["claim"] or s["claim"] in claim), None)
        store.insert_memory(
            title=claim[:24],
            content=(f'遭遇：来自捐赠文档《{donor}》的设定情境\n'
                     f'事实：{match.get("from_donor", "") if match else "文档中体现的看法"}\n'
                     f'解释：捐赠——TA的设定里我认可并收下的认知\n'
                     f'不确定：继承自捐赠文档，尚未经我自己的经历检验\n'
                     f'下次回应：待经历检验后形成\n'
                     f'出处：底色文档·《{donor}》\n'
                     f'状态：先验').strip(),
            keywords="观点 来历 收养",
            layer="longterm", kind="stance",
        )

    # —— 重写人格文件 ——
    # 外部角色卡：整体重建（丢弃整包戏服——角色档案/经历描述一并去掉）；
    # Alice 格式：原位替换风格块与词表块，用户其余小节保留。
    style_block = "\n".join(f"- {s}" for s in styles) if styles else "（文档未提取到风格要点）"
    style_header = f"（来自捐赠文档「{donor}」的风格，AI 只继承说话方式）"
    if not is_alice_format:
        narrative = "\n".join(
            f"- {c}（先验·未验证——继承自《{donor}》，待自己经历检验）" for c in adopted)
        md = "\n\n".join([
            f"# {cfg.persona.name} 的人格",
            f"## 底色 · 气质与风格\n\n{style_header}\n\n{style_block}",
        ])
    else:
        md = persona_md() or f"# {cfg.persona.name} 的人格\n"
        md = re.sub(r"## 底色 · 气质与风格\n\n.*?(?=\n## )",
                    f"## 底色 · 气质与风格\n\n{style_header}\n\n{style_block}\n\n",
                    md, count=1, flags=re.S)

    adopted_brief = "、".join(f"「{c}」" for c in adopted) if adopted else "（TA的认知里还没有我认可的）"
    rejected_brief = "、".join(f"「{c}」（{why}）" for c, why in rejected) if rejected else "（暂无明确拒绝的）"
    origin = ORIGIN_SECTION_TMPL.format(
        origin_note=f"消化自{source}" if not donor else f"消化自{source}，捐赠人「{donor}」",
        donor=donor, rej_line="" if rejected else "有些地方我和TA不一样。",
        adopted_brief=adopted_brief, rejected_brief=rejected_brief,
    )
    if facts:
        origin += "\n- TA故事里的要素（不是我的记忆）：" + "、".join(facts)
    if re.search(rf"^{re.escape(ORIGIN_TITLE)}\s*$", md, flags=re.M):
        md = re.sub(rf"{re.escape(ORIGIN_TITLE)}\n\n.*?(?=\n## )", origin + "\n\n", md, count=1, flags=re.S)
    else:
        md = md.rstrip("\n") + f"\n\n{origin}\n"
    if not is_alice_format:  # 重建路径补生长三节
        narrative = "\n".join(
            f"- {c}（先验·未验证——继承自《{donor}》，待自己经历检验）" for c in adopted)
        md = "\n\n".join([
            md.rstrip("\n"),
            "## 生长 · 自我叙事\n\n（尚未形成——将随共同经历自然生长）",
            "## 生长 · 我现在的想法\n\n" + (narrative or "（尚未形成——将在思辨中从经历里长出观点）"),
            "## 生长 · 与用户的关系\n\n（尚未形成）",
        ])
    paths.PERSONA_FILE.write_text(md if md.endswith("\n") else md + "\n", encoding="utf-8")

    # —— Alice 格式路径：「我现在的想法」原位装配收养认知 ——
    if is_alice_format and adopted:
        narrative = "\n".join(
            f"- {c}（先验·未验证——继承自《{donor}》，待自己经历检验）" for c in adopted)
        md = thought._write_section(md, narrative)
        paths.PERSONA_FILE.write_text(md if md.endswith("\n") else md + "\n", encoding="utf-8")
    return (f"消化完成：风格 {len(styles)} 条、"
            f"收养认知 {len(adopted)} 条、拒绝 {len(rejected)} 条"
            + (f"（捐赠人「{donor}」）" if donor else ""))


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
    current = persona_md() or ""
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
    resp = chat(client, cfg, [{"role": "user", "content": prompt}], json_mode=True)
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
    """数据触发：自上次演化起新记忆累计到阈值即演化。没有足够新材料就不动。"""
    anchor = store.meta_get_time("last_evolve_at")
    if store.count_memories_since(anchor) >= cfg.persona.evolve_every_memories:
        return evolve(store, client, cfg)
    return None
