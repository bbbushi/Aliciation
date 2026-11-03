"""配置加载：user/config.toml + 内置默认值。

user/ 是本机专属目录（config + 全部数据，gitignored）——换机器/交仓库时整个目录带走或删掉即可。
"""
from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
USER_DIR = PROJECT_ROOT / "user"
DATA_DIR = USER_DIR / "data"
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
    # C0 总预算（保守 token 估算，工程起点而非效果结论；0=关闭预算守卫）
    total_budget_tokens: int = 100_000
    reply_reserve_tokens: int = 4_000
    active_days: int = 2
    survival_days: int = 7
    survival_min_uses: int = 1
    warehouse_index_max_lines: int = 120
    active_full: int = 20
    longterm_top_k: int = 12
    longterm_capacity: int = 2000


@dataclass
class MemoryConfig:
    observation_value_review: bool = True  # 七天线复核已分级的新见闻；不延长来源许可
    interest_decay_days: int = 90  # 实施初值，可配置；只按真实用户依据衰减
    deliberate_early_high: bool = True
    observation_high_chars: int = 1200  # 建议细节量，不作机械截断；仍受已有资料容量限制
    observation_normal_chars: int = 600
    observation_low_chars: int = 180
    observation_condense_max_ratio: float = 0.8  # 浓缩后最多为原文的80%，不足时不写入
    extract_every_turns: int = 10
    distill_every_memories: int = 30  # 数据触发：每累计 N 条新记忆提炼一次技能
    deliberate_every_memories: int = 15  # 数据触发：每累计 N 条新事件记忆思辨一次（思想层）


@dataclass
class ChannelOwnerConfig:
    enabled: bool = False
    token_sha256: str = ""
    provider: str = "qq-official"
    app_id: str = ""
    sender_id: str = ""
    chat_type: str = "c2c"


@dataclass
class ChannelsConfig:
    enabled: bool = False
    protocol_version: int = 1
    qq_owner: ChannelOwnerConfig = field(default_factory=ChannelOwnerConfig)


def _channels(raw: dict) -> ChannelsConfig:
    if not isinstance(raw, dict) or set(raw) - {"enabled", "protocol_version", "qq_owner"}:
        raise ValueError("channels 配置字段无效")
    owner = raw.get("qq_owner", {})
    if not isinstance(owner, dict) or set(owner) - set(ChannelOwnerConfig.__dataclass_fields__):
        raise ValueError("channels.qq_owner 配置字段无效")
    result = ChannelsConfig(raw.get("enabled", False), raw.get("protocol_version", 1),
                            ChannelOwnerConfig(**owner))
    if type(result.enabled) is not bool or type(result.qq_owner.enabled) is not bool:
        raise ValueError("渠道 enabled 必须为布尔值")
    if type(result.protocol_version) is not int or result.protocol_version != 1:
        raise ValueError("不支持的渠道协议版本")
    o = result.qq_owner
    if result.enabled and o.enabled:
        if (o.provider != "qq-official" or o.chat_type != "c2c"
                or not isinstance(o.token_sha256, str)
                or not re.fullmatch(r"[0-9a-f]{64}", o.token_sha256)
                or any(not isinstance(v, str) or not v.strip() or len(v) > 256 or v == "*"
                       for v in (o.app_id, o.sender_id))):
            raise ValueError("QQ 渠道必须配置专用令牌哈希和唯一所有者身份")
    return result


@dataclass
class Config:
    llm: LlmConfig = field(default_factory=LlmConfig)
    persona: PersonaConfig = field(default_factory=PersonaConfig)
    context: ContextConfig = field(default_factory=ContextConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    channels: ChannelsConfig = field(default_factory=ChannelsConfig)


def _build(cls, raw: dict):
    fields = cls.__dataclass_fields__
    return cls(**{k: v for k, v in raw.items() if k in fields})


def load(path: Path | str | None = None) -> Config:
    p = Path(path) if path is not None else (USER_DIR / "config.toml")
    raw: dict = {}
    if p.exists():
        with open(p, "rb") as f:
            raw = tomllib.load(f)
    return Config(
        llm=_build(LlmConfig, raw.get("llm", {})),
        persona=_build(PersonaConfig, raw.get("persona", {})),
        context=_build(ContextConfig, raw.get("context", {})),
        memory=_build(MemoryConfig, raw.get("memory", {})),
        channels=_channels(raw.get("channels", {})),
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
