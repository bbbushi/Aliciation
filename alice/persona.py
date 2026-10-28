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
- 「生长 · 我现在的想法」：**由思辨流程维护，演化时原样保留，不得改写**（文件里没有这一节则不加）。
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


def seed_from_tags(store: Store, client, cfg: Config, tags: list[str]) -> Path:
    """由标签生成人格底色并落盘。"""
    prompt = SEED_PROMPT.format(tags="、".join(tags), name=cfg.persona.name)
    from .llm import chat_text

    md = chat_text(client, cfg, [{"role": "user", "content": prompt}])
    if "## 底色" not in md or "## 生长" not in md:
        # 结构不合规则退回纯模板，保证文件永远可用
        md = (
            f"# {cfg.persona.name} 的人格\n\n"
            f"## 底色 · 气质与风格\n\n（标签：{'、'.join(tags)}）\n\n{GROWTH_PLACEHOLDER}"
        )
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
    store.meta_set("persona_seeded_at", time.time())
    store.meta_set("last_evolve_at", time.time())  # 种子即基线，从现在起计演化周期
    return paths.PERSONA_FILE


def install_persona(store: Store, cfg: Config, md_text: str, source: str = "用户导入") -> Path:
    """安装用户提供的自定义人格。

    两种文档都收：
    1. Alice 格式（含「## 底色」小节）：直接安装；
    2. 角色提示词文档（无「## 底色」，如外部角色卡）：整份包进底色，标题用她的名字。
    缺失的生长小节自动补占位；用户自带的「我现在的想法」保留，思辨会在后续修订它。
    已有人格时先快照入 history（可回滚），并重置演化基线。
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
            f"（整份导入自{source}，以下为用户提供的人格设定）\n\n"
            f"{body.strip()}\n"
        )
    elif not md.startswith("# "):
        md = f"# {cfg.persona.name} 的人格\n\n{md}"
    for title, placeholder in (
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
    store.meta_set("persona_seeded_at", time.time())
    store.meta_set("last_evolve_at", time.time())  # 新基线：旧记忆不再立刻涌入演化
    return paths.PERSONA_FILE


def evolve(store: Store, client, cfg: Config) -> str:
    current = persona_md() or ""
    since = store.meta_get_time("last_evolve_at")
    new_memories = [
        f'{m["title"]}：{m["content"]}'
        for m in store.alive_memories()
        if m["created_at"] > since
    ][:100]
    if not new_memories:
        store.meta_set("last_evolve_at", time.time())
        return "没有新记忆，人格不变。"

    prompt = EVOLVE_PROMPT.format(
        name=cfg.persona.name,
        current=current,
        memories="\n".join("- " + s for s in new_memories),
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
