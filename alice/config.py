"""配置加载：项目根目录 config.toml + 内置默认值。"""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
DB_PATH = DATA_DIR / "alice.db"
PERSONA_DIR = DATA_DIR / "persona"
PERSONA_FILE = PERSONA_DIR / "persona.md"
PERSONA_HISTORY_DIR = PERSONA_DIR / "history"
PERSONA_CHANGELOG = PERSONA_DIR / "CHANGELOG.md"


@dataclass
class LlmConfig:
    base_url: str = ""
    chat_model: str = ""
    cheap_model: str = ""
    api_key_env: str = "ALICE_API_KEY"
    api_key: str = ""  # 直配优先；留空则回落到 api_key_env 指定的环境变量
    thinking: str = "on"  # 对话思考过程：on/off（off 显著省输入 token；仅作用于对话调用）
    disable_thinking: bool = False  # 兼容旧开关，与 thinking="off" 等效

    def resolved_key(self) -> str:
        return self.api_key or os.environ.get(self.api_key_env, "")


@dataclass
class PersonaConfig:
    name: str = "Alice"
    # 数据触发：每累计 N 条新记忆演化一次。安静则不变（无时间兜底，特性而非缺陷）
    evolve_every_memories: int = 15


@dataclass
class ContextConfig:
    recent_turns: int = 16
    active_days: int = 2
    survival_days: int = 7
    survival_min_uses: int = 1
    warehouse_index_max_lines: int = 120
    active_full: int = 20
    longterm_top_k: int = 12
    longterm_capacity: int = 2000


@dataclass
class MemoryConfig:
    extract_every_turns: int = 10
    distill_every_memories: int = 30  # 数据触发：每累计 N 条新记忆提炼一次技能
    deliberate_every_memories: int = 15  # 数据触发：每累计 N 条新事件记忆思辨一次（思想层）


@dataclass
class Config:
    llm: LlmConfig = field(default_factory=LlmConfig)
    persona: PersonaConfig = field(default_factory=PersonaConfig)
    context: ContextConfig = field(default_factory=ContextConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)


def _build(cls, raw: dict):
    fields = cls.__dataclass_fields__
    return cls(**{k: v for k, v in raw.items() if k in fields})


def load(path: Path | None = None) -> Config:
    p = path or (PROJECT_ROOT / "config.toml")
    raw: dict = {}
    if p.exists():
        with open(p, "rb") as f:
            raw = tomllib.load(f)
    return Config(
        llm=_build(LlmConfig, raw.get("llm", {})),
        persona=_build(PersonaConfig, raw.get("persona", {})),
        context=_build(ContextConfig, raw.get("context", {})),
        memory=_build(MemoryConfig, raw.get("memory", {})),
    )


def validate_llm(cfg: Config) -> None:
    problems = []
    if not cfg.llm.base_url:
        problems.append("config.toml [llm] base_url 未配置（可参考 config.example.toml）")
    if not cfg.llm.chat_model:
        problems.append("config.toml [llm] chat_model 未配置")
    if not cfg.llm.cheap_model:
        problems.append("config.toml [llm] cheap_model 未配置")
    if not cfg.llm.resolved_key():
        problems.append(
            f"API Key 未设置：在 config.toml [llm] 填 api_key = \"...\"，"
            f"或 export {cfg.llm.api_key_env}=..."
        )
    if problems:
        raise SystemExit("配置不完整：\n  " + "\n  ".join(problems))
