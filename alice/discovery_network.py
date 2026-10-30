"""Explicit local HTTP proxy routing for fixed discovery destinations only.

System proxy discovery is read-only. It never probes a port, uses a PAC script,
inherits proxy credentials, or falls back to direct access after a route failure.
The caller applies TLS to the returned tunnel using the original target name.
"""
from __future__ import annotations

import ipaddress
import re
import socket
import time
from urllib.parse import urlsplit
from urllib.request import getproxies


_PROXY_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
_TARGET_HOSTS = frozenset({
    "zh.wikipedia.org", "en.wikipedia.org", "www.bilibili.com", "api.bilibili.com",
    "www.zhihu.com", "zhuanlan.zhihu.com", "tieba.baidu.com", "api.search.brave.com", "cn.bing.com",
})
_MAX_PROXY_HEADERS = 32768


class NetworkRouteError(Exception):
    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code, self.message = code, message or code


def normalize_proxy_endpoint(endpoint: str) -> str:
    """Accept only a credential-free HTTP proxy on an explicit local address."""
    if (not isinstance(endpoint, str) or not endpoint or len(endpoint) > 1024
            or any(ord(c) <= 32 or ord(c) >= 127 for c in endpoint)
            or "\\" in endpoint or "?" in endpoint or "#" in endpoint):
        raise NetworkRouteError("proxy_config", "代理须为不含凭据的本机 HTTP 地址")
    try:
        parts = urlsplit(endpoint)
        host = parts.hostname
        port = parts.port if parts.port is not None else 80
        valid = (parts.scheme == "http" and host in _PROXY_HOSTS
                 and parts.username is None and parts.password is None
                 and parts.path in ("", "/") and 1 <= port <= 65535)
    except (ValueError, UnicodeError):
        valid = False
    if not valid:
        raise NetworkRouteError("proxy_config", "只支持无凭据的本机 HTTP 代理；请检查系统代理或选择直连")
    authority = f"[{host}]" if host == "::1" else host
    return f"http://{authority}:{port}"


def resolve_system_proxy() -> str:
    """Read HTTPS/ALL proxy settings; no configured proxy means direct access."""
    try:
        configured = getproxies()
    except (OSError, ValueError):
        raise NetworkRouteError("proxy_config", "无法读取当前系统代理设置") from None
    endpoint = configured.get("https") or configured.get("all")
    return normalize_proxy_endpoint(endpoint) if endpoint else ""


def connect_via_proxy(endpoint: str, target_host: str, *, deadline: float) -> socket.socket:
    """Connect once to a local proxy and tunnel one allowlisted HTTPS origin.

    The trusted local proxy resolves the fixed target. This does not claim to
    perform the direct route's public-IP validation on that remote resolution.
    """
    if target_host not in _TARGET_HOSTS:
        raise NetworkRouteError("proxy_scope", "此目标不支持通过内容发现的本机代理访问")
    parts = urlsplit(normalize_proxy_endpoint(endpoint))

    def remaining():
        value = deadline - time.monotonic()
        if value <= 0:
            raise NetworkRouteError("timeout", "本阶段代理连接超时")
        return value

    try:
        addresses = socket.getaddrinfo(parts.hostname, parts.port, type=socket.SOCK_STREAM)
    except OSError:
        raise NetworkRouteError("proxy_connect", "无法解析本机代理地址") from None
    if not addresses or len(addresses) > 32:
        raise NetworkRouteError("proxy_config", "本机代理解析结果不受支持")
    for family, _, _, _, address in addresses:
        try:
            local = ipaddress.ip_address(address[0]).is_loopback
        except ValueError:
            local = False
        if family not in (socket.AF_INET, socket.AF_INET6) or not local:
            raise NetworkRouteError("proxy_config", "本机代理解析到了非回环地址")
    family, socktype, proto, _, address = sorted(addresses, key=lambda a: a[0] != socket.AF_INET)[0]
    sock = socket.socket(family, socktype, proto)
    try:
        sock.settimeout(remaining())
        sock.connect(address)
        target = f"{target_host}:443"
        request = f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n\r\n"
        sock.settimeout(remaining())
        sock.sendall(request.encode("ascii"))
        response = bytearray()
        while b"\r\n\r\n" not in response:
            if len(response) >= _MAX_PROXY_HEADERS:
                raise NetworkRouteError("proxy_protocol", "本机代理响应头过长")
            sock.settimeout(remaining())
            part = sock.recv(min(4096, _MAX_PROXY_HEADERS - len(response)))
            if not part:
                raise NetworkRouteError("proxy_protocol", "本机代理未完整返回隧道响应")
            response.extend(part)
        header, extra = bytes(response).split(b"\r\n\r\n", 1)
        lines = header.split(b"\r\n")
        status = re.fullmatch(rb"HTTP/1\.[01] ([0-9]{3})(?: [^\r\n]*)?", lines[0])
        if not status or len(lines) > 101:
            raise NetworkRouteError("proxy_protocol", "本机代理响应格式不受支持")
        if status[1] == b"407":
            raise NetworkRouteError("proxy_auth", "本机代理要求身份验证，当前通道不传代理凭据")
        if status[1] != b"200":
            raise NetworkRouteError("proxy_rejected", "本机代理未建立到目标平台的隧道")
        if extra or any(not re.fullmatch(rb"[!#$%&'*+.^_`|~0-9A-Za-z-]+:[^\r\n]*", line) for line in lines[1:]):
            raise NetworkRouteError("proxy_protocol", "本机代理隧道响应格式不受支持")
        return sock
    except (TimeoutError, socket.timeout):
        sock.close()
        raise NetworkRouteError("timeout", "本阶段代理连接超时") from None
    except OSError:
        sock.close()
        raise NetworkRouteError("proxy_connect", "无法连接已配置的本机代理") from None
    except BaseException:
        sock.close()
        raise
