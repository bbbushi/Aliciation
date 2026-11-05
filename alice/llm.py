"""LLM 访问层：任何 OpenAI 兼容接口（智谱 / DeepSeek / ...）。"""
from __future__ import annotations

import json
import time
import uuid
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
    retry: bool = True,
    observation: dict | None = None,
    on_attempt_usage=None,
):
    """非流式调用；每次实际尝试回报用量，默认最多三次尝试。"""
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

    call_id = uuid.uuid4().hex
    last_err: Exception | None = None
    for attempt in range(3 if retry else 1):
        started = time.monotonic()
        response, failure = None, None
        try:
            response = client.chat.completions.create(**kwargs)
            return response
        except BaseException as exc:
            failure = exc
            if not isinstance(exc, Exception):
                raise
            e = exc  # 网络/限流等瞬时错误
            last_err = e
        finally:
            if on_attempt_usage is not None:
                try:
                    on_attempt_usage(response, incomplete=failure is not None)
                except Exception:
                    # Accounting must not replace the response/cancellation or
                    # trigger another provider request. It performs no SQL.
                    pass
            if observation:
                from . import metrics
                metrics.record_llm_call(observation.get("store"), observation.get("purpose", "unspecified"),
                    started, response, error=safe_provider_error(failure) if failure else None,
                    model=kwargs["model"], request_id=observation.get("request_id"), call_id=call_id,
                    attempt=attempt + 1, fallback=observation.get("fallback", False),
                    context=observation.get("context"),
                    status=("cancelled" if failure and not isinstance(failure, Exception) else
                            "error" if failure else "ok"))
        if retry and attempt < 2:
            time.sleep(2 * (attempt + 1))  # Backoff is separate from provider latency.
    raise RuntimeError(f"LLM 调用失败：{safe_provider_error(last_err)}") from None


def chat_text(client, cfg: Config, messages: list[dict], *, model: str | None = None, observation=None) -> str:
    resp = chat(client, cfg, messages, model=model, observation=observation)
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
