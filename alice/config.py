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
    # P1b 近位表达提醒（默认关；人工对照开关，效果未验不预填）
    expression_anchor: bool = False
    # 人格投影模式（20261107 注册表设计 §6/§8）：full=完整用途投影；registry=核心常驻+场景按需
    # （需快照带有效 assembly 且渠道 GUI；合同无效自动 full 回退并记录原因）
    expression_mode: str = "full"
    # 数据触发：每累计 N 条新记忆演化一次。安静则不变（无时间兜底，特性而非缺陷）
    evolve_every_memories: int = 15


@dataclass
class ExpressionConfig:
    """Explicit resource ceilings, never a request to silently select a prefix."""
    max_source_chars: int = 60_000
    max_source_units: int = 600
    max_items: int = 160
    max_links: int = 1200
    max_metadata_chars: int = 120_000
    max_projection_chars: int = 6000
    max_worker_chars: int = 1600
    max_anchor_chars: int = 500
    max_item_chars: int = 2400
    max_review_chars: int = 240_000
    # 人格注册表（20261107 设计 §6）：运行投影新增上限只能收紧冻结的完整表达资源约束；
    # 准备时冻结分类额度，运行配置扩大须重新明确准备
    max_core_chars: int = 2200
    max_registry_chars: int = 1600
    max_registry_bundles: int = 12
    max_scene_expanded_chars: int = 2400
    max_persona_load_calls: int = 2
    max_persona_loaded_bundles: int = 2

    def __post_init__(self):
        for name, low, high in (
            ("max_source_chars", 1000, 240_000), ("max_source_units", 10, 2400),
            ("max_items", 10, 640), ("max_links", 10, 4800),
            ("max_metadata_chars", 1000, 480_000), ("max_projection_chars", 500, 24_000),
            ("max_worker_chars", 200, 6000), ("max_anchor_chars", 100, 2000),
            ("max_item_chars", 100, 9600), ("max_review_chars", 2000, 960_000),
            ("max_core_chars", 100, 24000), ("max_registry_chars", 100, 24000),
            ("max_registry_bundles", 1, 64), ("max_scene_expanded_chars", 64, 24000),
            ("max_persona_load_calls", 1, 8), ("max_persona_loaded_bundles", 1, 64),
        ):
            value = getattr(self, name)
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f"expression.{name} 超出允许范围")


@dataclass
class ContextConfig:
    recent_turns: int = 16
    transform_review_enabled: bool = True  # 显式带引文的文本变换：发布前一次角色/条件审查
    assembly_mode: str = "legacy"  # indexed 为 C1 人工试用；不自动改本机配置
    memory_candidate_limit: int = 12
    memory_catalogue_chars: int = 3600  # 本轮目录累计预算；初始最多使用1800
    memory_expanded_chars: int = 8000
    memory_tool_calls: int = 2
    # C2 跨轮工作集（context_keep/context_release；仅 indexed 模式挂载）
    working_set_enabled: bool = False
    working_set_max_items: int = 4
    working_set_max_chars: int = 4000
    working_set_ttl_turns: int = 12
    working_set_calls: int = 4
    history_enabled: bool = False  # C3普通旧原文；需indexed，不读归档/未标渠道旧数据
    history_max_chars: int = 8000
    history_calls: int = 2
    # C3(a) 有来源前情：移出取数窗的原文经模型生成一次性前情卡（默认关，人工验收后开）
    recap_enabled: bool = False
    recap_min_rows: int = 4
    recap_span_max_rows: int = 60
    recap_max_chars: int = 600
    recap_inject_count: int = 2
    recap_inject_chars: int = 1200
    # 上下文观测 SDK（20261107 补充设计）：元数据默认开；正文快照默认关（本机调试用）
    observer_enabled: bool = True
    observer_body_capture: bool = False
    # 前缀宪法精简与规则作用域（20261109 设计 §13）：legacy=改造前宪法+互动v1；
    # scoped=prefix_policy 五类块。首发默认 legacy；切换经配置文件，重启进程加载
    # （前缀代码不承诺热切换；运行行为以实际加载代码为准）
    prefix_policy_mode: str = "legacy"
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

    def __post_init__(self):
        if type(self.transform_review_enabled) is not bool:
            raise ValueError('context.transform_review_enabled 必须为布尔值')
        if self.assembly_mode not in ("legacy", "indexed"):
            raise ValueError("context.assembly_mode 必须为 legacy 或 indexed")
        if self.prefix_policy_mode not in ("legacy", "scoped"):
            raise ValueError("context.prefix_policy_mode 必须为 legacy 或 scoped")
        if type(self.working_set_enabled) is not bool:
            raise ValueError("context.working_set_enabled 必须为布尔值")
        if type(self.recap_enabled) is not bool:
            raise ValueError("context.recap_enabled 必须为布尔值")
        if type(self.history_enabled) is not bool:
            raise ValueError("context.history_enabled 必须为布尔值")
        for name, low, high in (("memory_candidate_limit", 1, 12),
                                ("memory_catalogue_chars", 512, 24000),
                                ("memory_expanded_chars", 1200, 64000),
                                ("memory_tool_calls", 0, 2),
                                ("working_set_max_items", 1, 8),
                                ("working_set_max_chars", 512, 16000),
                                ("working_set_ttl_turns", 1, 50),
                                ("working_set_calls", 0, 8),
                                ("history_max_chars", 1200, 64000),
                                ("history_calls", 0, 4),
                                ("recap_min_rows", 2, 100),
                                ("recap_span_max_rows", 2, 200),
                                ("recap_max_chars", 100, 2000),
                                ("recap_inject_count", 1, 4),
                                ("recap_inject_chars", 200, 8000),
                                ("recent_turns", 1, 1000),
                                ("total_budget_tokens", 0, 2000000),
                                ("reply_reserve_tokens", 0, 100000)):
            value = getattr(self, name)
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f"context.{name} 超出允许范围")
        if self.recap_min_rows > self.recap_span_max_rows:
            raise ValueError("recap_min_rows不能超过recap_span_max_rows")


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
class MaintenanceConfig:
    """双维护流程（对话压缩 compact + 记忆库整理 tidy，20261109 设计草案）。

    自动触发默认关（人工验收后再开）；显式命令不受这些开关限制。
    初值为工程起点，参数以真人使用与预算为准，不是效果结论。
    """
    # compact：自动触发（廉价检查排队，不在聊天路径同步执行）
    auto_compact_enabled: bool = False
    # R01：由 compact 异步替代旧轮末/退出同步提取（回复后只排队不等待；需 auto_compact）。
    # 关闭时（默认）旧提取路径原样保留；两模式共用「compact 提交推进 extract_cursor」的
    # 去重契约，同段原文不会被两条流程重复提为记忆。
    replaces_extraction: bool = False
    compact_pressure_rounds: int = 8        # 默认窗口外未压缩完整往返累计到该数即排队
    compact_min_interval_minutes: int = 45  # 距上次成功压缩的最小间隔
    compact_max_span_rounds: int = 12       # 单次压缩最多处理的完整往返数
    compact_state_max_chars: int = 800      # 接续状态正文上限
    # tidy：自动触发
    auto_tidy_enabled: bool = False
    tidy_interval_hours: int = 24           # 距上次成功整理的最小间隔
    tidy_min_new_memories: int = 12         # 新增记忆达到该数才值得排队
    tidy_duplicate_threshold: float = 0.72  # 同类记忆判重的文本重合度下限
    tidy_condense_min_chars: int = 600      # 长正文浓缩候选的最小长度
    tidy_max_candidates: int = 12           # 单次整理进入模型判读的候选上限
    tidy_rollback_days: int = 7             # 浓缩原文的回退窗口；到期清除不留永久正文日志
    runner_poll_seconds: int = 20           # 空闲执行器的轮询间隔（聊天优先，忙时跳过）

    def __post_init__(self):
        if type(self.auto_compact_enabled) is not bool or type(self.auto_tidy_enabled) is not bool:
            raise ValueError("maintenance 自动触发开关必须为布尔值")
        if type(self.replaces_extraction) is not bool:
            raise ValueError("maintenance.replaces_extraction 必须为布尔值")
        if self.replaces_extraction and not self.auto_compact_enabled:
            raise ValueError("maintenance.replaces_extraction 需同时启用 auto_compact_enabled，"
                             "否则既无同步提取也无异步压缩，记忆形成会整体停摆")
        for name, low, high in (("compact_pressure_rounds", 2, 200),
                                ("compact_min_interval_minutes", 5, 10080),
                                ("compact_max_span_rounds", 1, 100),
                                ("compact_state_max_chars", 200, 4000),
                                ("tidy_interval_hours", 1, 720),
                                ("tidy_min_new_memories", 1, 1000),
                                ("tidy_max_candidates", 2, 48),
                                ("tidy_rollback_days", 1, 90),
                                ("runner_poll_seconds", 5, 600)):
            value = getattr(self, name)
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f"maintenance.{name} 超出允许范围")
        for name, low, high in (("tidy_duplicate_threshold", 0.5, 0.98),
                                ("tidy_condense_min_chars", 200, 8000)):
            value = getattr(self, name)
            if type(value) is not int and type(value) is not float:
                raise ValueError(f"maintenance.{name} 必须为数值")
            if not low <= value <= high:
                raise ValueError(f"maintenance.{name} 超出允许范围")


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
class PluginsConfig:
    # 上下文观测插件（20261107 插件设计 §5）：固定页面开关，默认关；
    # 采集开关独立在 context.observer_*，插件开关不擅自改采集值
    context_inspector_enabled: bool = False


@dataclass
class Config:
    llm: LlmConfig = field(default_factory=LlmConfig)
    persona: PersonaConfig = field(default_factory=PersonaConfig)
    context: ContextConfig = field(default_factory=ContextConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    channels: ChannelsConfig = field(default_factory=ChannelsConfig)
    expression: ExpressionConfig = field(default_factory=ExpressionConfig)
    maintenance: MaintenanceConfig = field(default_factory=MaintenanceConfig)
    plugins: PluginsConfig = field(default_factory=PluginsConfig)


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
        expression=_build(ExpressionConfig, raw.get("expression", {})),
        maintenance=_build(MaintenanceConfig, raw.get("maintenance", {})),
        plugins=_build(PluginsConfig, raw.get("plugins", {})),
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
