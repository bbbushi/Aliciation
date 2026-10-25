"""LLM 访问层：任何 OpenAI 兼容接口（智谱 / DeepSeek / ...）。"""
from __future__ import annotations

import json
from typing import Any

from .config import Config, validate_llm


def make_client(cfg: Config):
    validate_llm(cfg)
    from openai import OpenAI  # 延迟导入：离线测试无需安装

    # 超时收紧 + 关闭内置重试（外层 chat() 已带退避重试），避免单请求挂死拖垮长跑
    return OpenAI(
        base_url=cfg.llm.base_url, api_key=cfg.llm.resolved_key(),
        timeout=120.0, max_retries=0,
    )


def chat(
    client,
    cfg: Config,
    messages: list[dict],
    *,
    model: str | None = None,
    tools: list[dict] | None = None,
    json_mode: bool = False,
):
    """非流式调用，失败自动重试一次。"""
    kwargs: dict[str, Any] = {
        "model": model or cfg.llm.chat_model,
        "messages": messages,
        "temperature": 0.8,
    }
    if tools:
        kwargs["tools"] = tools
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    if cfg.llm.disable_thinking and "bigmodel" in (cfg.llm.base_url or ""):
        kwargs["extra_body"] = {"thinking": {"type": "disabled"}}

    last_err: Exception | None = None
    for attempt in range(3):
        try:
            return client.chat.completions.create(**kwargs)
        except Exception as e:  # 网络/限流等瞬时错误
            last_err = e
            if attempt < 2:
                import time
                time.sleep(2 * (attempt + 1))  # 2s、4s 退避
    raise RuntimeError(f"LLM 调用失败：{last_err}") from last_err


def chat_text(client, cfg: Config, messages: list[dict], *, model: str | None = None) -> str:
    resp = chat(client, cfg, messages, model=model)
    return (resp.choices[0].message.content or "").strip()


def parse_json(text: str) -> dict:
    """宽松 JSON 解析：容忍代码围栏与前后杂文本。"""
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    if start >= 0:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start : i + 1])
                    except json.JSONDecodeError:
                        break
    raise ValueError(f"无法从模型输出解析 JSON：{text[:200]}")
