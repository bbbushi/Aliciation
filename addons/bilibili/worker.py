"""One-operation, read-only Bilibili adapter using a mediated JSON-line protocol.

This process does not open network connections or application files. The host
owns permission, per-request accounting, HTTP validation, deadlines and killing
the child. See README.md for the exact wire contract and protocol references.
Only the host may satisfy http_request messages. No login, subtitle, playback,
write action, device activation, fallback endpoint or retry is implemented.
"""
from __future__ import annotations

import base64
import binascii
from datetime import datetime, timezone
import hashlib
from html import unescape
from html.parser import HTMLParser
import json
import re
import sys
import time
from urllib.parse import quote, urlencode, urlsplit


VERSION = 1
INITIAL_FRAME_BYTES = 65536
API_ORIGIN = "https://api.bilibili.com"
REFERER = "https://www.bilibili.com/"
BVID_PATTERN = re.compile(r"BV[0-9A-Za-z]{10}")
# The protocol's fixed permutation, independently implemented from the MIT
# reference linked and attributed in README.md; not from bilibili-api-python.
WBI_PERMUTATION = (
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52,
)
WBI_REMOVALS = str.maketrans("", "", "!'()*")


class AdapterError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def fail(code: str, message: str):
    raise AdapterError(code, message)


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _constant(_value):
    raise ValueError("non-finite number")


def decode_json(raw: bytes) -> dict:
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_object,
                           parse_constant=_constant)
    except (UnicodeError, ValueError, RecursionError):
        fail("invalid_response", "收到的 JSON 格式不受支持")
    if not isinstance(value, dict):
        fail("invalid_response", "收到的 JSON 顶层必须为对象")
    return value


def receive(max_bytes: int) -> dict:
    line = sys.stdin.buffer.readline(max_bytes + 1)
    if not line:
        fail("cancelled", "宿主已关闭请求通道")
    if len(line) > max_bytes or not line.endswith(b"\n"):
        fail("invalid_frame", "消息行不完整或超过本轮配置允许的大小")
    return decode_json(line)


def emit(value: dict):
    encoded = json.dumps(value, ensure_ascii=False, allow_nan=False,
                         separators=(",", ":")).encode("utf-8")
    sys.stdout.buffer.write(encoded + b"\n")
    sys.stdout.buffer.flush()


def positive_int(value: object, name: str, maximum: int = sys.maxsize) -> int:
    if type(value) is not int or not 1 <= value <= maximum:
        fail("invalid_input", name + " 必须是可表示的正整数")
    return value


def text(value: object, max_chars: int | None = None) -> str:
    if not isinstance(value, str):
        return ""
    value = "".join(char for char in value
                    if (char in "\n\t" or ord(char) >= 32)
                    and ord(char) != 127 and not 0xD800 <= ord(char) <= 0xDFFF)
    value = "\n".join(" ".join(line.split()) for line in value.splitlines()).strip()
    return value if max_chars is None else value[:max_chars]


class SearchText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str):
        self.parts.append(data)


def search_text(value: object, max_chars: int) -> str:
    if not isinstance(value, str):
        return ""
    parser = SearchText()
    try:
        parser.feed(value)
        parser.close()
    except (ValueError, RecursionError):
        return ""
    return text(unescape("".join(parser.parts)), max_chars)


def canonical_url(bvid: str) -> str:
    if not isinstance(bvid, str) or not BVID_PATTERN.fullmatch(bvid):
        fail("invalid_video", "视频编号格式不受支持")
    return "https://www.bilibili.com/video/" + bvid + "/"


def candidate_bvid(candidate: object) -> str:
    if not isinstance(candidate, dict) or candidate.get("platform") != "bilibili":
        fail("invalid_video", "读取只接受哔哩哔哩视频候选")
    url = candidate.get("url")
    if not isinstance(url, str):
        fail("invalid_video", "视频候选缺少规范地址")
    match = re.fullmatch(r"https://www\.bilibili\.com/video/(BV[0-9A-Za-z]{10})/?", url)
    if not match:
        fail("invalid_video", "读取只接受规范 BV 视频地址")
    return match.group(1)


class Session:
    def __init__(self, response_bytes: int):
        self.response_bytes = response_bytes
        self.frame_bytes = response_bytes * 2 + INITIAL_FRAME_BYTES
        self.sequence = 0

    def get(self, purpose: str, path: str, query: str = "", cookie: str = "",
            *, anonymous_nav: bool = False) -> dict:
        self.sequence += 1
        request_id = str(self.sequence)
        headers = {"Accept": "application/json", "Referer": REFERER}
        if cookie:
            headers["Cookie"] = cookie
        emit({"version": VERSION, "type": "http_request", "id": request_id,
              "method": "GET", "url": API_ORIGIN + path + ("?" + query if query else ""),
              "headers": headers, "max_bytes": self.response_bytes, "purpose": purpose})
        response = receive(self.frame_bytes)
        if (type(response.get("version")) is not int or response["version"] != VERSION
                or response.get("type") != "http_response"
                or response.get("id") != request_id):
            fail("invalid_frame", "宿主响应与当前请求不匹配")
        if response.get("ok") is not True:
            fail("transport_denied", "宿主未完成本次网络请求，操作已停止")
        if type(response.get("status")) is not int or response["status"] != 200:
            fail("http_rejected", "哔哩哔哩未返回成功响应，操作已停止")
        encoded = response.get("body_base64")
        if not isinstance(encoded, str) or len(encoded) > ((self.response_bytes + 2) // 3) * 4:
            fail("response_too_large", "响应超过本轮配置的正文大小")
        try:
            raw = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error):
            fail("invalid_response", "宿主响应正文编码不受支持")
        if len(raw) > self.response_bytes:
            fail("response_too_large", "响应超过本轮配置的正文大小")
        envelope = decode_json(raw)
        code = envelope.get("code")
        if type(code) is not int:
            fail("invalid_response", "平台响应缺少有效的整数业务码")
        # Classify a real API error before inspecting data: error responses may
        # legitimately have data=null. Only nav permits anonymous code -101.
        if code != 0 and not (anonymous_nav and code == -101):
            fail("platform_rejected", "平台接口返回非零业务码，操作已停止")
        data = envelope.get("data")
        verification_required = False
        for container in (envelope, data):
            if not isinstance(container, dict):
                continue
            voucher = container.get("v_voucher")
            if voucher is None:
                continue
            if not isinstance(voucher, str):
                fail("invalid_response", "平台验证字段类型不符合约定")
            if voucher:
                verification_required = True
        if verification_required:
            fail("verification_required", "平台返回了验证要求，操作已停止")
        if not isinstance(data, dict):
            fail("invalid_response", "平台响应缺少预期资料字段")
        return data


def valid_device_cookie_value(value: object) -> bool:
    # RFC 6265 cookie-octet: opaque ASCII, including Base64 padding (+ / =).
    # Keep this predicate aligned with the host's independent validation.
    return (isinstance(value, str) and 1 <= len(value) <= 256
            and all(0x21 <= ord(char) <= 0x7E and char not in '\",;\\'
                    for char in value))


def device_cookie(data: dict) -> str:
    values = []
    for response_name, cookie_name in (("b_3", "buvid3"), ("b_4", "buvid4")):
        if response_name == "b_4" and response_name not in data:
            continue
        value = data.get(response_name)
        if not valid_device_cookie_value(value):
            fail("invalid_device", "未能解析平台返回的匿名设备标识")
        values.append(cookie_name + "=" + value)
    return "; ".join(values)


def wbi_key(value: object) -> str:
    if not isinstance(value, str):
        fail("invalid_signature_keys", "平台未提供可用的 WBI 口令")
    try:
        name = urlsplit(value).path.rsplit("/", 1)[-1]
    except ValueError:
        fail("invalid_signature_keys", "平台未提供可用的 WBI 口令")
    match = re.fullmatch(r"([0-9a-fA-F]{32})\.[A-Za-z0-9]+", name)
    if not match:
        fail("invalid_signature_keys", "平台未提供可用的 WBI 口令")
    # The image-looking URL is data only. Never fetch it or adopt its host.
    return match.group(1)


def signed_query(keyword: str, nav: dict) -> str:
    images = nav.get("wbi_img")
    if not isinstance(images, dict):
        fail("invalid_signature_keys", "平台未提供可用的 WBI 口令")
    key_material = wbi_key(images.get("img_url")) + wbi_key(images.get("sub_url"))
    mixin = "".join(key_material[index] for index in WBI_PERMUTATION[:32])
    parameters = {"keyword": keyword, "order": "totalrank", "page": "1",
                  "search_type": "video", "wts": str(int(time.time()))}
    # URL encoding uses %20 for spaces (encodeURIComponent semantics), then the
    # exact bytes of this query are signed and sent. MD5 is a wire requirement,
    # not a claim of cryptographic authentication or a secret held by Alice.
    query = urlencode(sorted(parameters.items()), quote_via=quote, safe="")
    signature = hashlib.md5((query + mixin).encode("utf-8"), usedforsecurity=False).hexdigest()
    return query + "&w_rid=" + signature


def search(session: Session, request: dict, limit: int) -> dict:
    raw_query = request.get("query")
    if not isinstance(raw_query, str) or not raw_query.strip():
        fail("invalid_input", "搜索需要非空查询词")
    if any(ord(char) < 32 or ord(char) == 127 or 0xD800 <= ord(char) <= 0xDFFF
           for char in raw_query):
        fail("invalid_input", "搜索查询词含不受支持的控制字符")
    keyword = raw_query.translate(WBI_REMOVALS)
    if not keyword.strip():
        fail("invalid_input", "查询词经平台签名规则处理后为空")
    cookie = device_cookie(session.get("device", "/x/frontend/finger/spi"))
    nav = session.get("wbi_keys", "/x/web-interface/nav", cookie=cookie, anonymous_nav=True)
    result = session.get("search", "/x/web-interface/wbi/search/type",
                         signed_query(keyword, nav), cookie)
    rows = result.get("result")
    # A missing field is not a valid empty search result: risk-control payloads
    # can use HTTP 200/code 0 while omitting the requested data.
    if not isinstance(rows, list):
        fail("invalid_response", "平台未返回预期的视频搜索列表")
    candidates = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or row.get("type", "video") != "video":
            continue
        bvid = row.get("bvid")
        if not isinstance(bvid, str) or not BVID_PATTERN.fullmatch(bvid) or bvid in seen:
            continue
        title = search_text(row.get("title"), 300)
        if not title:
            continue
        seen.add(bvid)
        candidates.append({"platform": "bilibili", "url": canonical_url(bvid), "title": title,
                           "coverage": "search_snippet",
                           "snippet": search_text(row.get("description"), 1200)})
        if len(candidates) >= limit:
            break
    return {"candidates": candidates}


def read(session: Session, request: dict, text_chars: int) -> dict:
    bvid = candidate_bvid(request.get("candidate"))
    data = session.get("video_info", "/x/web-interface/view", urlencode({"bvid": bvid}))
    if data.get("bvid") != bvid:
        fail("source_mismatch", "平台返回的视频编号与请求不一致")
    title = text(data.get("title"), 300)
    description = text(data.get("desc"))
    if not title or not description or description in {"-", "--"}:
        fail("content_unavailable", "视频没有可用的公开文字简介；未用搜索摘要或元信息替代")
    owner = data.get("owner") if isinstance(data.get("owner"), dict) else {}
    lines = ["资料范围：以下仅为哔哩哔哩公开视频的接口元信息与文字简介。未获取视频画面、音频或字幕，不能据此声称已经观看视频或核实视频内的论证。",
             "视频标题：" + title, "视频编号：" + bvid]
    author = text(owner.get("name"), 300)
    if author:
        lines.append("发布者：" + author)
    published = data.get("pubdate")
    if type(published) is int and published > 0:
        try:
            lines.append("发布时间（UTC）：" + datetime.fromtimestamp(published, timezone.utc).isoformat())
        except (ValueError, OverflowError, OSError):
            pass
    duration = data.get("duration")
    if type(duration) is int and duration >= 0:
        lines.append("接口标注视频时长（秒）：" + str(duration))
    lines.append("公开文字简介：")
    prefix = "\n".join(lines) + "\n"
    remaining = text_chars - len(prefix)
    if remaining < 1:
        fail("text_limit", "正文长度配置不足以同时保留资料范围和实际简介")
    evidence_text = prefix + description[:remaining]
    return {"platform": "bilibili", "url": canonical_url(bvid), "title": title,
            "text": evidence_text, "coverage": "video_description_only",
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "content_hash": hashlib.sha256(evidence_text.encode("utf-8")).hexdigest()}


def run(request: dict) -> dict:
    if (type(request.get("version")) is not int or request["version"] != VERSION
            or request.get("op") not in ("search", "read")):
        fail("invalid_input", "只支持协议版本 1 的 search/read 操作")
    response_bytes = positive_int(request.get("response_bytes", 1024 * 1024), "response_bytes",
                                  (sys.maxsize - INITIAL_FRAME_BYTES) // 2)
    text_chars = positive_int(request.get("text_chars", 12000), "text_chars")
    limit = positive_int(request.get("limit", 5), "limit")
    session = Session(response_bytes)
    if request["op"] == "search":
        return search(session, request, limit)
    return read(session, request, text_chars)


def main():
    try:
        request = receive(INITIAL_FRAME_BYTES)
        result = {"version": VERSION, "type": "result", "ok": True, "data": run(request)}
    except AdapterError as exc:
        result = {"version": VERSION, "type": "result", "ok": False,
                  "error": {"code": exc.code, "message": exc.message}}
    except Exception:
        # Neither untrusted response bodies nor cookies belong in diagnostics.
        result = {"version": VERSION, "type": "result", "ok": False,
                  "error": {"code": "adapter_failed", "message": "B站适配操作未完成"}}
    try:
        emit(result)
    except (BrokenPipeError, OSError):
        pass


if __name__ == "__main__":
    main()
