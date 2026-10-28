"""LLM 访问层：任何 OpenAI 兼容接口（智谱 / DeepSeek / ...）。"""
from __future__ import annotations

import json
from typing import Any

from .config import Config, validate_llm


def make_client(cfg: Config):
    validate_llm(cfg)
    try:
        from openai import OpenAI  # 延迟导入：离线测试无需安装
    except ImportError as e:
        raise SystemExit(
            "缺少依赖 openai：请先运行  pip install -r requirements.txt"
            "（或使用 .venv/bin/python 运行）"
        ) from e

    # 超时收紧 + 关闭内置重试（外层 chat() 已带退避重试），避免单请求挂死拖垮长跑
    return OpenAI(
        base_url=cfg.llm.base_url, api_key=cfg.llm.resolved_key(),
        timeout=120.0, max_retries=0,
    )


def thinking_disabled(cfg: Config) -> bool:
    return cfg.llm.thinking == "off" or cfg.llm.disable_thinking


def safe_provider_error(exc: Exception) -> str:
    """只返回固定分类，供应商异常正文可能含 URL、请求内容或凭据。"""
    status = getattr(exc, "status_code", None)
    if status == 401:
        return "令牌无效或已过期"
    if status == 403:
        return "当前令牌没有访问权限"
    if status == 402:
        return "账户余额不足"
    if status == 404:
        return "模型或接口地址不存在"
    if status == 429:
        return "请求受限或账户额度不足"
    if status in (400, 422):
        return "供应商拒绝请求，请核对接口地址与模型名称"
    if isinstance(status, int) and status >= 500:
        return "供应商服务暂时不可用"
    name = type(exc).__name__
    if name in ("APITimeoutError", "TimeoutException", "ReadTimeout", "ConnectTimeout") or isinstance(exc, TimeoutError):
        return "连接超时，请检查网络后重试"
    if name in ("APIConnectionError", "ConnectError", "ConnectionError") or isinstance(exc, ConnectionError):
        return "无法连接接口地址，请检查网络与地址"
    return "模型请求失败，请检查接口地址、模型和账户状态"


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
        if thinking_disabled(cfg):  # 思考深度只作用于对话调用，不影响提取/评审
            kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}

    last_err: Exception | None = None
    for attempt in range(3):
        try:
            return client.chat.completions.create(**kwargs)
        except Exception as e:  # 网络/限流等瞬时错误
            last_err = e
            if attempt < 2:
                import time
                time.sleep(2 * (attempt + 1))  # 2s、4s 退避
    raise RuntimeError(f"LLM 调用失败：{safe_provider_error(last_err)}") from None


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
