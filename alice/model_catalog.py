"""Explicit GUI-only model discovery for three fixed official providers."""
from __future__ import annotations

import json
import re
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

PROVIDERS = {
    "deepseek": ("https://api.deepseek.com", "https://api.deepseek.com/models"),
    "zhipu": ("https://open.bigmodel.cn/api/paas/v4/", "https://open.bigmodel.cn/api/v1/models"),
    "openai": ("https://api.openai.com/v1", "https://api.openai.com/v1/models"),
}


def provider_for(base_url: str) -> str:
    return next((name for name, (base, _) in PROVIDERS.items()
                 if base_url.rstrip("/").lower() == base.rstrip("/")), "custom")


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


def fetch_models(provider: str, key: str) -> list[str]:
    if not isinstance(key, str) or not 1 <= len(key) <= 4096 or any(not 33 <= ord(c) <= 126 for c in key):
        raise ValueError("API Key 格式无效，请检查是否多粘贴了空格或换行")
    request = Request(PROVIDERS[provider][1], headers={
        "Authorization": "Bearer " + key, "Accept": "application/json", "User-Agent": "Alice/0.1"})
    try:
        with build_opener(_NoRedirect).open(request, timeout=20) as response:
            raw = response.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise ValueError("供应商模型列表超过大小限制")
        value = json.loads(raw)
    except HTTPError as exc:
        message = {401: "API Key 无效", 403: "此密钥没有模型目录访问权限", 404: "供应商模型目录暂不可用",
                   429: "查询过于频繁，请稍后重试"}.get(exc.code, "模型目录请求失败，请稍后重试")
        exc.close()
        raise ValueError(message) from None
    except (URLError, OSError):
        raise ValueError("连接模型目录失败，请检查网络或代理后重试") from None
    except (UnicodeError, json.JSONDecodeError, RecursionError):
        raise ValueError("供应商模型目录未返回有效 JSON") from None
    if not isinstance(value, dict) or value.get("success") is False or value.get("error"):
        raise ValueError("模型目录查询被拒绝，请核对供应商与 API Key")
    rows = value.get("data", value.get("models"))
    if isinstance(rows, dict):
        rows = rows.get("models", rows.get("data"))
    if not isinstance(rows, list) or len(rows) > 10000:
        raise ValueError("供应商模型目录格式不兼容，请稍后重试")
    models = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        ident = row.get("slug", row.get("id")) if provider == "zhipu" else row.get("id")
        if not isinstance(ident, str) or not re.fullmatch(r"[A-Za-z0-9_.:/-]{1,256}", ident):
            continue
        name = ident.lower()
        prefix = {"deepseek": "deepseek-", "zhipu": "glm-", "openai": "gpt-"}[provider]
        if name.startswith(prefix) and not any(word in name for word in (
                "embedding", "image", "audio", "realtime", "transcribe", "tts", "asr", "ocr", "search", "codex")):
            models.add(ident)
    if not models:
        raise ValueError("目录中没有找到可选择的对话模型；请核对密钥权限后重新获取")
    return sorted(models, key=str.lower)
