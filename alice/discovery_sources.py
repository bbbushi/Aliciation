"""Bounded, read-only discovery connectors. No configuration or memory is loaded.

The caller owns permission checks, accounting, retention and a subprocess deadline.
Each search/read operation makes at most one origin HTTP request, without
redirects or retries. Direct DNS results are checked before connection. Fixed
platform hosts may instead use an explicitly resolved local HTTP proxy; TLS
still verifies the original hostname. Local connectors and the Bilibili device
cookie capability remain separate from ordinary public page access.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import ipaddress
import json
import re
import socket
import ssl
import time
import xml.etree.ElementTree as ET
from urllib.parse import parse_qs, quote, unquote, urlencode, urlsplit, urlunsplit

from .discovery_network import NetworkRouteError, connect_via_proxy

MAX_SEARCH_BYTES = 256 * 1024
MAX_READ_BYTES = 1024 * 1024
MAX_MODEL_BYTES = 256 * 1024
MAX_TEXT_CHARS = 12000
MAX_RESULTS = 5
MAX_QUERY_CHARS = 500
MAX_HEADER_BYTES = 32768
MAX_URL_CHARS = 2048
PLATFORM_LABELS = {"wikipedia": "维基百科", "bilibili": "哔哩哔哩", "zhihu": "知乎", "tieba": "贴吧"}
_HOSTS = {
    "wikipedia": ("zh.wikipedia.org", "en.wikipedia.org"),
    "bilibili": ("www.bilibili.com",),
    "zhihu": ("www.zhihu.com", "zhuanlan.zhihu.com"),
    "tieba": ("tieba.baidu.com",),
}
_USER_AGENT = "AliceDiscovery/0.4 (+https://github.com/bbbushi/Aliciation)"
_DENIED_V4 = tuple(ipaddress.ip_network(n) for n in (
    "0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8",
    "169.254.0.0/16", "172.16.0.0/12", "192.0.0.0/24", "192.0.2.0/24",
    "192.88.99.0/24", "192.168.0.0/16", "198.18.0.0/15", "198.51.100.0/24", "203.0.113.0/24",
    "224.0.0.0/4", "240.0.0.0/4",
))
_DENIED_V6 = tuple(ipaddress.ip_network(n) for n in (
    "2001::/23", "2001:db8::/32", "2002::/16", "3ffe::/16", "3fff::/20",
))


class DiscoveryError(Exception):
    """Only fixed error codes/messages cross the worker boundary."""

    def __init__(self, code: str, message: str, *, diagnostic: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.diagnostic = diagnostic


def _fail(code: str, message: str):
    raise DiscoveryError(code, message)


@dataclass
class Usage:
    network_attempts: int = 0
    body_bytes: int = 0
    model_calls: int = 0
    input_tokens: int | None = None
    output_tokens: int | None = None


def platform_catalog() -> list[dict]:
    return [{"id": key, "label": label, "hosts": list(_HOSTS[key]),
             "providers": ["native", "bing_rss", "brave", "searxng"] if key in ("wikipedia", "tieba")
             else ["bing_rss", "brave", "searxng"],
             "coverage": "百科公开正文节选" if key == "wikipedia" else
             "公开视频简介或公开专栏文字；不观看视频" if key == "bilibili" else
             "帖子主楼文字节选，不含回复或楼中楼" if key == "tieba" else
             "实际可读取的指定问题、回答或文章正文"}
            for key, label in PLATFORM_LABELS.items()]


def _clean(value: object, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join("".join(c for c in value if c in "\n\t" or ord(c) >= 32)
                    .replace("\x7f", "").split())[:limit]


def _split_url(url: str, *, local: bool = False, max_chars: int = MAX_URL_CHARS):
    if (not isinstance(url, str) or not url or len(url) > max_chars
            or any(ord(c) <= 32 or ord(c) == 127 for c in url) or "\\" in url
            or re.search(r"%(?![0-9a-fA-F]{2})", url)):
        _fail("invalid_url", "地址格式不受支持")
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").encode("idna").decode("ascii").lower()
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except (ValueError, UnicodeError):
        _fail("invalid_url", "地址格式不受支持")
    is_local = host in ("localhost", "127.0.0.1", "::1")
    if (parts.username is not None or parts.password is not None or parts.fragment
            or not host or host.endswith(".") or not 1 <= port <= 65535
            or parts.scheme not in ("http", "https")):
        _fail("invalid_url", "地址格式不受支持")
    if local and is_local:
        pass
    elif parts.scheme != "https" or port != 443:
        _fail("blocked_address", "公开内容仅允许 HTTPS 默认端口")
    else:
        try:
            ipaddress.ip_address(host)
        except ValueError:
            if ("." not in host or not re.fullmatch(r"[a-z0-9.-]+", host)
                    or any(not label or label.startswith("-") or label.endswith("-")
                           for label in host.split("."))):
                _fail("blocked_address", "地址不在公开域名范围内")
        else:
            _fail("blocked_address", "公开内容不接受 IP 字面地址")
    if is_local and not local:
        _fail("blocked_address", "不允许访问本机地址")
    return parts, host, port, bool(local and is_local)


def validate_read_url(platform: str, url: str) -> str:
    """Pure validation for parent-side admission; DNS is checked in the worker."""
    parts, host, _, _ = _split_url(url)
    if not isinstance(platform, str) or platform not in _HOSTS or host not in _HOSTS[platform]:
        _fail("platform_scope", "内容地址不属于已选平台")
    path = unquote(parts.path)
    if any(ord(c) < 32 or ord(c) == 127 or c == "\\" for c in path):
        _fail("invalid_url", "内容路径不受支持")
    allowed = False
    if platform == "wikipedia":
        # Namespace pages and executable/index actions are outside this connector.
        allowed = path.startswith("/wiki/") and len(path) > 6 and ":" not in path[6:]
        if path == "/w/index.php":
            try:
                params = parse_qs(parts.query, strict_parsing=True)
            except ValueError:
                _fail("invalid_url", "百科页面参数格式无效")
            allowed = (set(params) == {"curid"} and len(params["curid"]) == 1
                       and bool(re.fullmatch(r"[1-9][0-9]{0,11}", params["curid"][0])))
        elif parts.query:
            allowed = False
    elif platform == "bilibili":
        allowed = bool(re.fullmatch(r"/video/(?:BV[0-9A-Za-z]{10}|av[0-9]+)/?", path)
                       or re.fullmatch(r"/read/cv[0-9]+/?", path))
    elif platform == "tieba":
        allowed = bool(re.fullmatch(r"/p/[1-9][0-9]{0,19}/?", path))
    elif platform == "zhihu" and host == "zhuanlan.zhihu.com":
        allowed = bool(re.fullmatch(r"/p/[0-9]+/?", path))
    elif platform == "zhihu":
        allowed = bool(re.fullmatch(r"/question/[0-9]+(?:/answer/[0-9]+)?/?", path))
    if not allowed:
        _fail("platform_scope", "该类页面不在本次公开内容范围内")
    path = quote(parts.path or "/", safe="/%:@!$&'()*+,;=-._~")
    # The resource identity is in the path; do not fetch tracking, sorting or
    # reply-page parameters as another source. Tieba always reads its main post.
    query = "" if platform in ("zhihu", "tieba") else quote(parts.query, safe="%=&;:+,/?@!$'()*-._~")
    if platform in ("zhihu", "tieba"):
        path = path.rstrip("/")
    return urlunsplit(("https", host, path, query, ""))


def normalize_candidate(candidate: dict, platform: str | None = None) -> dict:
    """Return only admitted source fields. Search snippets are transient only."""
    if not isinstance(candidate, dict):
        _fail("invalid_candidate", "候选资料格式无效")
    actual = platform or candidate.get("platform")
    if candidate.get("platform", actual) != actual:
        _fail("platform_scope", "候选平台不匹配")
    raw_url = candidate.get("url", "")
    # Older public search records can use HTTP/anchors for these same resources.
    # Upgrade only exact approved hosts; this never follows a redirect wrapper.
    if (isinstance(raw_url, str) and isinstance(actual, str) and actual in _HOSTS
            and not any(ord(c) <= 32 or ord(c) == 127 for c in raw_url) and "\\" not in raw_url):
        try:
            parts = urlsplit(raw_url)
            if (parts.hostname in _HOSTS[actual] and parts.username is None and parts.password is None
                    and parts.scheme in ("http", "https") and parts.port in (None, 80 if parts.scheme == "http" else 443)):
                raw_url = urlunsplit(("https", parts.hostname, parts.path, parts.query, ""))
        except ValueError:
            pass
    url = validate_read_url(actual, raw_url)
    result = {"platform": actual, "url": url,
              "title": _clean(candidate.get("title"), 300),
              "coverage": "search_snippet", "snippet": _plain(candidate.get("snippet", ""), 1200)}
    if actual == "tieba":
        from .discovery_tieba import forum_name, timestamp
        result["forum_name"] = forum_name(candidate.get("forum_name"))
        result["published_at"] = timestamp(candidate.get("published_at"))
    return result


def validate_query(query: str) -> str:
    if (not isinstance(query, str) or not query.strip() or len(query) > MAX_QUERY_CHARS
            or len(query.split()) > 70 or any(ord(c) < 32 or ord(c) == 127 for c in query)
            or "!" in query or re.search(r"(?:^|\s):", query)
            or re.search(r"(?:^|\s)(?:site|inurl|url|domain):", query, re.IGNORECASE)):
        _fail("invalid_query", "搜索词过长、为空或含搜索路由控制语法")
    return query.strip()


def _public_address(value: str) -> bool:
    try:
        addr = ipaddress.ip_address(value)
    except ValueError:
        return False
    if not addr.is_global or addr.is_multicast or addr.is_reserved:
        return False
    if addr.version == 4:
        return not any(addr in net for net in _DENIED_V4)
    return (addr in ipaddress.ip_network("2000::/3")
            and not any(addr in net for net in _DENIED_V6))


class _Reader:
    def __init__(self, sock, deadline: float):
        self.sock, self.deadline, self.buffer = sock, deadline, b""
        self.body_remaining: int | None = None
        self.usage: Usage | None = None

    def bound_body(self, limit: int, usage: Usage):
        # Header reads can already have received the first body bytes. Charge
        # them once here; subsequent socket reads include chunk framing too.
        if len(self.buffer) > limit:
            _fail("body_limit", "响应正文超过本次额度")
        self.body_remaining = limit - len(self.buffer)
        self.usage = usage
        usage.body_bytes += len(self.buffer)

    def _recv(self, size: int) -> bytes:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            _fail("timeout", "本阶段已超时")
        if self.body_remaining is not None:
            if self.body_remaining <= 0:
                _fail("body_limit", "响应正文达到本次额度")
            size = min(size, self.body_remaining)
        self.sock.settimeout(remaining)
        part = self.sock.recv(size)
        if self.body_remaining is not None:
            self.body_remaining -= len(part)
            self.usage.body_bytes += len(part)
        return part

    def line(self, limit: int = 8192) -> bytes:
        while b"\n" not in self.buffer:
            if len(self.buffer) >= limit:
                _fail("response_headers", "响应头或分块元数据过长")
            part = self._recv(min(4096, limit - len(self.buffer)))
            if not part:
                _fail("incomplete_response", "响应未完整返回")
            self.buffer += part
        line, self.buffer = self.buffer.split(b"\n", 1)
        if not line.endswith(b"\r") or len(line) + 1 > limit:
            _fail("response_headers", "响应行格式不受支持")
        return line + b"\n"

    def read(self, size: int) -> bytes:
        if self.buffer:
            part, self.buffer = self.buffer[:size], self.buffer[size:]
            return part
        return self._recv(min(size, 16384))

    def exact(self, size: int) -> bytes:
        result = bytearray()
        while len(result) < size:
            part = self.read(size - len(result))
            if not part:
                _fail("incomplete_response", "响应未完整返回")
            result.extend(part)
        return bytes(result)


def request_bytes(url: str, *, usage: Usage, timeout: float, limit: int,
                  method: str = "GET", body: bytes = b"", headers: dict | None = None,
                  allow_loopback: bool = False, bilibili_public_session: bool = False,
                  network_proxy: str = "") -> tuple[bytes, dict[str, str]]:
    """One bounded HTTP/1.1 exchange. Caller must kill the process at its deadline.

    Socket timeouts alone cannot bound system DNS or slowly delivered lines.
    Only model/connector callers may pass allow_loopback; pages never do.
    """
    # Connector queries may need percent-encoded Unicode; admitted page URLs
    # were already checked against the smaller MAX_URL_CHARS bound.
    parts, host, port, local = _split_url(url, local=allow_loopback, max_chars=8192)
    if not isinstance(network_proxy, str):
        _fail("proxy_config", "本阶段代理配置格式无效")
    if method not in ("GET", "POST") or len(body) > 128 * 1024:
        _fail("invalid_request", "请求格式不受支持")
    deadline = time.monotonic() + timeout
    usage.network_attempts += 1
    sock = None
    try:
        def time_left():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _fail("timeout", "本阶段已超时")
            return remaining

        if network_proxy:
            if parts.scheme != "https" or port != 443 or local:
                _fail("proxy_scope", "本机代理仅支持固定公开平台的 HTTPS 访问")
            try:
                sock = connect_via_proxy(network_proxy, host, deadline=deadline)
            except NetworkRouteError as exc:
                _fail(exc.code, exc.message)
        else:
            try:
                addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
            except socket.gaierror:
                _fail("dns_resolution", "无法解析目标域名")
            if not addresses or len(addresses) > 32:
                _fail("dns_invalid", "域名解析结果不受支持")
            for family, _, _, _, address in addresses:
                if family not in (socket.AF_INET, socket.AF_INET6):
                    _fail("dns_invalid", "域名解析返回不支持的地址类型")
                try:
                    ip = ipaddress.ip_address(address[0])
                except ValueError:
                    _fail("dns_invalid", "域名解析返回无效地址")
                if local:
                    if not ip.is_loopback:
                        _fail("dns_non_public", "本机连接器解析到非回环地址")
                elif not _public_address(str(ip)):
                    if ip.version == 4 and ip in ipaddress.ip_network("198.18.0.0/15"):
                        _fail("dns_fake_ip", "DNS 返回测试网段地址，可能来自代理 Fake-IP；直连通道不能使用该地址")
                    _fail("dns_non_public", "DNS 返回非公开地址；直连通道不能使用该解析结果")
            # Prefer IPv4; no retry or route fallback after a connection failure.
            family, socktype, proto, _, address = sorted(addresses, key=lambda a: a[0] != socket.AF_INET)[0]
            sock = socket.socket(family, socktype, proto)
            sock.settimeout(time_left())
            sock.connect(address)
        if parts.scheme == "https":
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            context.load_default_certs()
            context.set_alpn_protocols(["http/1.1"])
            sock.settimeout(time_left())
            sock = context.wrap_socket(sock, server_hostname=host)
        target = quote(parts.path or "/", safe="/%:@!$&'()*+,;=-._~")
        if parts.query:
            target += "?" + quote(parts.query, safe="%=&;:+,/?@!$'()*-._~")
        authority = f"[{host}]" if ":" in host else host
        if port != (443 if parts.scheme == "https" else 80):
            authority += f":{port}"
        request_headers = {"Host": authority, "User-Agent": _USER_AGENT,
                           "Accept-Encoding": "identity", "Connection": "close"}
        accepted_headers = {"accept", "content-type", "authorization", "x-subscription-token"}
        if bilibili_public_session:
            if host != "api.bilibili.com" or port != 443 or parts.scheme != "https":
                _fail("blocked_address", "B站设备会话只允许发送到固定接口域名")
            accepted_headers.update({"cookie", "referer"})
        for key, value in (headers or {}).items():
            if (key.lower() not in accepted_headers
                    or not isinstance(value, str) or len(value) > 8192
                    or any(ord(c) < 32 or ord(c) > 126 for c in value)):
                _fail("invalid_request", "请求头格式不受支持")
            request_headers[key] = value
        if method == "POST":
            request_headers["Content-Length"] = str(len(body))
        request = f"{method} {target} HTTP/1.1\r\n" + "".join(
            f"{key}: {value}\r\n" for key, value in request_headers.items()) + "\r\n"
        sock.settimeout(time_left())
        sock.sendall(request.encode("ascii") + body)
        reader = _Reader(sock, deadline)
        status_line = reader.line()
        match = re.fullmatch(rb"HTTP/1\.[01] ([0-9]{3})(?: [^\r\n]*)?\r\n", status_line)
        if not match:
            _fail("response_headers", "响应状态行不受支持")
        status = int(match[1])
        response_headers: dict[str, str] = {}
        header_bytes = len(status_line)
        for _ in range(101):
            line = reader.line()
            header_bytes += len(line)
            if header_bytes > MAX_HEADER_BYTES:
                _fail("response_headers", "响应头超过额度")
            if line == b"\r\n":
                break
            key, sep, value = line[:-2].partition(b":")
            if not sep or not re.fullmatch(rb"[!#$%&'*+.^_`|~0-9A-Za-z-]+", key):
                _fail("response_headers", "响应头格式不受支持")
            key = key.decode("ascii").lower()
            if key in response_headers and key in {"content-length", "transfer-encoding", "content-encoding", "content-type"}:
                _fail("response_headers", "响应存在重复的关键头")
            response_headers[key] = value.decode("latin-1").strip()
        else:
            _fail("response_headers", "响应头数量超过额度")
        reader.bound_body(limit, usage)
        if 300 <= status < 400:
            _fail("redirect_blocked", "平台要求跳转，本次未跟随")
        if status != 200:
            _fail(f"http_{status}", "平台或接口未返回可读取内容")
        if response_headers.get("content-encoding", "identity").lower() != "identity":
            _fail("compressed_response", "本阶段不接收压缩响应")
        transfer = response_headers.get("transfer-encoding", "").lower()
        length = response_headers.get("content-length")
        if transfer and (transfer != "chunked" or length is not None):
            _fail("response_headers", "响应传输格式不受支持")
        result = bytearray()

        def take(size: int):
            if size > limit - len(result):
                _fail("body_limit", "响应正文超过本次额度")
            while size:
                part = reader.read(min(size, 16384))
                if not part:
                    _fail("incomplete_response", "响应未完整返回")
                result.extend(part)
                size -= len(part)

        if transfer:
            metadata_bytes = 0
            for _ in range(8192):
                line = reader.line()
                metadata_bytes += len(line)
                if metadata_bytes > MAX_HEADER_BYTES:
                    _fail("body_limit", "分块元数据超过额度")
                chunk_size = line[:-2].split(b";", 1)[0]
                if not re.fullmatch(rb"[0-9a-fA-F]{1,8}", chunk_size):
                    _fail("response_headers", "分块格式不受支持")
                size = int(chunk_size, 16)
                if not size:
                    for _ in range(101):
                        trailer = reader.line()
                        metadata_bytes += len(trailer)
                        if metadata_bytes > MAX_HEADER_BYTES:
                            _fail("body_limit", "分块元数据超过额度")
                        if trailer == b"\r\n":
                            break
                    else:
                        _fail("response_headers", "响应尾部超过额度")
                    break
                take(size)
                if reader.exact(2) != b"\r\n":
                    _fail("response_headers", "分块格式不受支持")
            else:
                _fail("body_limit", "响应分块数量超过额度")
        elif length is not None:
            if not re.fullmatch(r"[0-9]{1,12}", length):
                _fail("response_headers", "正文长度格式不受支持")
            take(int(length))
        else:
            while len(result) < limit:
                part = reader.read(min(16384, limit - len(result)))
                if not part:
                    break
                result.extend(part)
            else:
                _fail("body_limit", "无长度响应达到本次额度")
        return bytes(result), response_headers
    finally:
        if sock is not None:
            sock.close()


def _json_body(body: bytes) -> dict:
    try:
        result = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeError, RecursionError):
        _fail("invalid_response", "接口未返回有效 JSON")
    if not isinstance(result, dict):
        _fail("invalid_response", "接口响应结构不受支持")
    return result


def _wiki_body(body: bytes) -> dict:
    result = _json_body(body)
    if "error" in result or "errors" in result:
        errors = [result["error"]] if "error" in result else result["errors"]
        if isinstance(errors, list) and any(isinstance(item, dict) and item.get("code") == "maxlag" for item in errors):
            _fail("wiki_maxlag", "维基百科当前繁忙，本轮已停止请求")
        _fail("wiki_api_error", "维基百科接口返回错误，本轮未取得材料")
    return result


class _TextParser(HTMLParser):
    _SKIP = {"script", "style", "nav", "footer", "aside", "noscript", "svg", "form", "button"}
    _VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self, limit: int):
        super().__init__(convert_charrefs=True)
        self.limit, self.parts, self.size = limit, [], 0
        self.skip: list[str] = []
        self.title_parts: list[str] = []
        self.in_title = False
        self.description = ""

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self.in_title = True
        if tag in self._SKIP:
            self.skip.append(tag)
        if tag == "meta" and not self.description:
            data = dict(attrs)
            if data.get("property", data.get("name", "")).lower() in ("og:description", "description"):
                self.description = _clean(data.get("content"), 4000)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False
        if tag in self.skip:
            del self.skip[len(self.skip) - 1 - self.skip[::-1].index(tag):]

    def handle_data(self, data):
        if self.in_title:
            if sum(map(len, self.title_parts)) < 600:
                self.title_parts.append(data[:600])
            return
        if not self.skip and self.size < self.limit:
            text = _clean(data, self.limit - self.size)
            if text:
                self.parts.append(text)
                self.size += len(text) + 1

    @property
    def text(self):
        return "\n".join(self.parts)[:self.limit]


def _plain(markup: object, limit: int) -> str:
    if not isinstance(markup, str):
        return ""
    parser = _TextParser(limit)
    parser.feed(markup)
    parser.close()
    return parser.text


def _decode(body: bytes, headers: dict) -> str:
    content_type = headers.get("content-type", "").lower()
    if content_type.split(";", 1)[0].strip() not in ("text/html", "application/xhtml+xml", "text/plain"):
        _fail("content_type", "该响应不是可读取的公开文字页面")
    match = re.search(r"charset\s*=\s*[\"']?([a-z0-9_-]+)", content_type)
    charset = match[1] if match else "utf-8"
    codecs = {"utf-8": "utf-8-sig", "utf8": "utf-8-sig", "gb18030": "gb18030",
              "gbk": "gb18030", "gb2312": "gb18030", "windows-1252": "cp1252"}
    if charset not in codecs:
        _fail("encoding", "页面字符编码暂不支持")
    try:
        return body.decode(codecs[charset])
    except UnicodeError:
        _fail("encoding", "页面文字解码失败")


def _rss_rows(body: bytes) -> list[dict]:
    """Parse only a real RSS feed; challenge/HTML pages are never empty results."""
    try:
        markup = body.decode("utf-8-sig")
        if re.search(r"<!\s*(?:DOCTYPE|ENTITY)\b", markup, re.IGNORECASE):
            _fail("search_response", "搜索响应含不支持的XML声明")
        root = ET.fromstring(markup)
    except (UnicodeError, ET.ParseError, RecursionError):
        _fail("search_response", "搜索服务未返回有效RSS，可能要求验证或暂不可用")
    if root.tag != "rss" or len(root.findall("channel")) != 1:
        _fail("search_response", "搜索服务没有返回预期的RSS列表")
    rows = []
    for item in root.find("channel").findall("item"):
        if len(item.findall("title")) != 1 or len(item.findall("link")) != 1:
            _fail("search_response", "搜索结果字段不完整")
        title, url = item.findtext("title"), item.findtext("link")
        if not isinstance(title, str) or not title.strip() or not isinstance(url, str) or not url.strip():
            _fail("search_response", "搜索结果缺少标题或来源链接")
        rows.append({"url": url.strip(), "title": title, "snippet": item.findtext("description", "")})
    return rows


def _tieba_fetch_json(url, *, usage, timeout, response_bytes, network_proxy):
    parts, host, port, local = _split_url(url, max_chars=8192)
    if (host != "tieba.baidu.com" or port != 443 or local
            or parts.path not in ("/mo/q/search/multsearch", "/mg/p/getPbData", "/mg/f/getFrsData")):
        _fail("platform_scope", "贴吧请求超出固定只读接口范围")
    body, _ = request_bytes(url, usage=usage, timeout=timeout, limit=response_bytes,
                            headers={"Accept": "application/json"}, network_proxy=network_proxy)
    try:
        return _json_body(body)
    except DiscoveryError:
        _fail("tieba_response", "贴吧未返回可用JSON，可能要求验证或登录")


def search(platform: str, query: str, provider: dict, *, usage: Usage,
           timeout: float = 15, limit: int = MAX_RESULTS, response_bytes: int = MAX_SEARCH_BYTES,
           network_proxy: str = "", trace: dict | None = None,
           tieba_options: dict | None = None) -> list[dict]:
    if not isinstance(platform, str) or platform not in _HOSTS or not isinstance(provider, dict):
        _fail("invalid_request", "平台或连接器无效")
    query = validate_query(query)
    if type(limit) is not int or limit <= 0:
        _fail("invalid_request", "结果数量必须为正整数")
    kind = provider.get("kind", "native")
    if platform == "tieba":
        from . import discovery_tieba
        tieba_options = discovery_tieba.options(tieba_options)
    trace = trace if trace is not None else {}
    trace.update(platform=platform, provider=kind, stage="search", received=0, accepted=0,
                 rejected=0, duplicates=0, unsupported=0, returned=0)
    rows = []
    if kind == "native" and platform == "tieba":
        from . import discovery_tieba
        return discovery_tieba.search(query, limit, trace=trace, tieba_options=tieba_options, fetch_json=lambda url: _tieba_fetch_json(
            url, usage=usage, timeout=timeout, response_bytes=response_bytes, network_proxy=network_proxy))
    elif kind == "native":
        if platform != "wikipedia":
            _fail("connector_unavailable", "该平台没有已实现的原生公开搜索接口")
        url = "https://zh.wikipedia.org/w/api.php?" + urlencode({
            "action": "query", "list": "search", "srsearch": query, "srnamespace": 0,
            # The endpoint itself permits at most 500 results per page.
            "srlimit": min(limit, 500), "srprop": "snippet", "format": "json", "formatversion": 2,
            "maxlag": 5})
        body, _ = request_bytes(url, usage=usage, timeout=timeout, limit=response_bytes, network_proxy=network_proxy)
        result = _wiki_body(body)
        query_result = result.get("query")
        if not isinstance(query_result, dict) or not isinstance(query_result.get("search"), list):
            _fail("wiki_response", "维基百科搜索响应缺少结果列表")
        for item in query_result["search"]:
            if (not isinstance(item, dict) or type(item.get("ns")) is not int
                    or type(item.get("pageid")) is not int or not isinstance(item.get("title"), str)
                    or not item["title"].strip()):
                _fail("wiki_response", "维基百科搜索结果字段不完整")
            pageid = item.get("pageid")
            if item.get("ns") == 0 and type(pageid) is int and 0 < pageid < 10**12:
                rows.append({"url": f"https://zh.wikipedia.org/w/index.php?curid={pageid}",
                             "title": item.get("title"), "snippet": item.get("snippet", "")})
            else:
                trace["unsupported"] += 1
    elif kind in ("bing_rss", "brave", "searxng"):
        # This is a provider hint, not an authorization gate: every result is filtered below.
        scope = {"wikipedia": "wikipedia.org", "bilibili": "bilibili.com",
                 "zhihu": "zhihu.com", "tieba": "tieba.baidu.com"}[platform]
        scoped_query = f"{query} site:{scope}"
        if kind == "bing_rss":
            url = "https://cn.bing.com/search?" + urlencode({"q": scoped_query, "format": "rss"})
            body, _ = request_bytes(url, usage=usage, timeout=timeout, limit=response_bytes,
                                    headers={"Accept": "application/rss+xml,application/xml,text/xml"},
                                    network_proxy=network_proxy)
            rows = _rss_rows(body)
        elif kind == "brave":
            key = provider.get("api_key")
            if not isinstance(key, str) or not key:
                _fail("provider_config", "尚未配置搜索服务凭据")
            url = "https://api.search.brave.com/res/v1/web/search?" + urlencode({
                # Brave's protocol limits each page to 20; not a daily Alice quota.
                "q": scoped_query, "count": min(limit, 20), "offset": 0, "result_filter": "web",
                "spellcheck": "false", "text_decorations": "false", "extra_snippets": "false"})
            body, _ = request_bytes(url, usage=usage, timeout=timeout, limit=response_bytes,
                                    headers={"X-Subscription-Token": key, "Accept": "application/json"},
                                    network_proxy=network_proxy)
            result = _json_body(body)
            rows = [{"url": r.get("url"), "title": r.get("title"), "snippet": r.get("description", "")}
                    if isinstance(r, dict) else None for r in result.get("web", {}).get("results", [])]
        else:
            endpoint = provider.get("endpoint", "")
            parts, _, _, _ = _split_url(endpoint, local=provider.get("allow_loopback") is True)
            engines = provider.get("engines")
            if (parts.path != "/search" or parts.query or parts.fragment
                    or not isinstance(engines, list) or not engines
                    or any(not isinstance(e, str) or not re.fullmatch(r"[a-zA-Z0-9 _-]{1,40}", e) for e in engines)):
                _fail("provider_config", "SearXNG 需要固定 /search 端点及有效引擎名称")
            data = urlencode({"q": scoped_query, "format": "json", "categories": "general",
                              "engines": ",".join(engines), "pageno": 1}).encode("ascii")
            # A user-configured SearXNG endpoint is not a fixed platform origin.
            # Its direct/local connector capability does not inherit this proxy.
            body, _ = request_bytes(endpoint, usage=usage, timeout=timeout, limit=response_bytes,
                                    method="POST", body=data,
                                    headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
                                    allow_loopback=provider.get("allow_loopback") is True)
            result = _json_body(body)
            rows = [{"url": r.get("url"), "title": r.get("title"), "snippet": r.get("content", "")}
                    if isinstance(r, dict) else None for r in result.get("results", [])]
    else:
        _fail("provider_config", "未知搜索连接器")
    candidates, seen = [], set()
    trace["received"] = len(rows) + trace["unsupported"]
    for row in rows:
        try:
            candidate = normalize_candidate(row, platform)
        except (DiscoveryError, ValueError):
            trace["rejected"] += 1
            continue
        if candidate["url"] not in seen:
            candidates.append(candidate)
            seen.add(candidate["url"])
        else:
            trace["duplicates"] += 1
    trace["accepted"] = len(candidates)
    trace["returned"] = min(len(candidates), limit)
    return candidates[:limit]


def check_tieba_forum(name: str, *, usage: Usage, timeout: float = 20,
                     response_bytes: int = MAX_READ_BYTES, network_proxy: str = "") -> dict:
    from . import discovery_tieba
    return discovery_tieba.check_forum(name, fetch_json=lambda target: _tieba_fetch_json(
        target, usage=usage, timeout=timeout, response_bytes=response_bytes, network_proxy=network_proxy))


def read_tieba_page(candidate: dict, page: int, page_size: int, *, usage: Usage,
                   timeout: float, response_bytes: int, network_proxy: str) -> dict:
    from .discovery_tieba import _data, page_url
    payload = _tieba_fetch_json(page_url(candidate, page, page_size), usage=usage,
        timeout=timeout, response_bytes=response_bytes, network_proxy=network_proxy)
    _data(payload, "read")  # Record API rejection/verification on this page's attempt.
    return payload


def finish_tieba_read(candidate: dict, parsed: dict) -> dict:
    """Apply the same evidence boundary to host-paged and standalone reads."""
    candidate = normalize_candidate(candidate, "tieba")
    if parsed.get("tieba_text_chars", 0) < 80:
        _fail("tieba_short_text", "贴吧获准范围内的文字不足，本轮未采纳；不以搜索摘要或媒体补足")
    return {**{key: value for key, value in parsed.items() if key != "tieba_text_chars"},
            "platform": "tieba", "url": candidate["url"],
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "content_hash": hashlib.sha256(parsed["text"].encode("utf-8")).hexdigest()}


def read(candidate: dict, *, usage: Usage, timeout: float = 20, response_bytes: int = MAX_READ_BYTES,
         network_proxy: str = "", tieba_options: dict | None = None, tieba_text_limit: int = 30000) -> dict:
    candidate = normalize_candidate(candidate)
    platform, url = candidate["platform"], candidate["url"]
    parts = urlsplit(url)
    title = candidate["title"]
    if platform == "wikipedia":
        params = {"action": "parse", "prop": "text", "format": "json", "formatversion": 2,
                  "disableeditsection": 1, "disabletoc": 1, "maxlag": 5}
        if parts.path == "/w/index.php":
            params["pageid"] = parse_qs(parts.query)["curid"][0]
        else:
            params["page"] = unquote(parts.path[6:]).replace("_", " ")
        body, _ = request_bytes(f"https://{parts.hostname}/w/api.php?" + urlencode(params),
                                usage=usage, timeout=timeout, limit=response_bytes, network_proxy=network_proxy)
        result = _wiki_body(body)
        parsed = result.get("parse")
        if (not isinstance(parsed, dict) or not isinstance(parsed.get("text"), str)
                or not isinstance(parsed.get("title"), str) or type(parsed.get("pageid")) is not int):
            _fail("wiki_response", "维基百科正文响应字段不完整")
        if "pageid" in params and str(parsed.get("pageid")) != params["pageid"]:
            _fail("source_mismatch", "百科正文与候选页不匹配")
        title = _clean(parsed.get("title"), 300) or title
        text = _plain(parsed.get("text"), MAX_TEXT_CHARS)
        coverage = "page_text_excerpt"
    elif platform == "tieba":
        from . import discovery_tieba
        parsed = discovery_tieba.read(candidate, tieba_options=tieba_options, text_limit=tieba_text_limit,
                                     fetch_json=lambda target: _tieba_fetch_json(
            target, usage=usage, timeout=timeout, response_bytes=response_bytes, network_proxy=network_proxy))
        return finish_tieba_read(candidate, parsed)
    else:
        body, headers = request_bytes(url, usage=usage, timeout=timeout, limit=response_bytes,
                                      headers={"Accept": "text/html,application/xhtml+xml,text/plain"},
                                      network_proxy=network_proxy)
        markup = _decode(body, headers)
        if platform == "zhihu":
            from . import discovery_zhihu
            parsed = discovery_zhihu.extract(candidate, markup)
            title, text, coverage = parsed["title"], parsed["text"], parsed["coverage"]
        else:
            parser = _TextParser(MAX_TEXT_CHARS)
            parser.feed(markup)
            parser.close()
            title = _clean(" ".join(parser.title_parts), 300) or title
            if platform == "bilibili" and parts.path.startswith("/video/"):
                text, coverage = parser.description, "video_description_only"
            else:
                text, coverage = parser.text, "static_page_excerpt"
                if len(text) < 160 and parser.description:
                    text, coverage = parser.description, "page_description_only"
    if len(text) < 80:
        _fail("unreadable", "未取得足够公开文字；可能需要登录、脚本或平台暂不支持")
    result = {"platform": platform, "url": url, "title": title, "text": text,
              "coverage": coverage, "fetched_at": datetime.now(timezone.utc).isoformat(),
              "content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest()}
    return result
