"""Host bridge for the fixed, read-only Bilibili addon command.

The addon never receives network access from this API. Each requested HTTP hop
is validated here and separately executed by the discovery supervisor's worker.
Only ephemeral platform-issued device cookies can cross this protocol.
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import sys
import threading
import time
from urllib.parse import parse_qs, urlsplit


class TransportError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _valid_device_cookie_value(value):
    # RFC 6265 cookie-octet; independently enforced on both sides of stdio.
    return (isinstance(value, str) and 1 <= len(value) <= 256
            and all(0x21 <= ord(char) <= 0x7E and char not in '\",;\\'
                    for char in value))


def _valid_device_cookie_header(cookie):
    if not isinstance(cookie, str):
        return False
    pairs = cookie.split("; ")
    if len(pairs) not in (1, 2):
        return False
    for pair, expected_name in zip(pairs, ("buvid3", "buvid4")):
        name, separator, value = pair.partition("=")
        if name != expected_name or not separator or not _valid_device_cookie_value(value):
            return False
    return True


def _device_cookie_from_data(data):
    if not isinstance(data, dict):
        return None
    pairs = []
    for response_name, cookie_name in (("b_3", "buvid3"), ("b_4", "buvid4")):
        if response_name == "b_4" and response_name not in data:
            continue
        value = data.get(response_name)
        if not _valid_device_cookie_value(value):
            return None
        pairs.append(cookie_name + "=" + value)
    return "; ".join(pairs)


def validate_request(message, context):
    """Repeat this check in the isolated HTTP worker; no generic URL proxy."""
    required = {"version", "type", "id", "method", "url", "headers", "max_bytes", "purpose"}
    if not isinstance(message, dict) or set(message) != required:
        raise TransportError("bili_protocol")
    if (type(message["version"]) is not int or message["version"] != 1 or message["type"] != "http_request" or message["method"] != "GET"
            or not isinstance(message["id"], str) or not re.fullmatch(r"[1-9][0-9]*", message["id"])
            or type(message["max_bytes"]) is not int or message["max_bytes"] != context["response_bytes"]):
        raise TransportError("bili_protocol")
    url = message["url"]
    if not isinstance(url, str) or len(url) > 8192 or any(ord(c) < 33 or ord(c) > 126 for c in url):
        raise TransportError("bili_scope")
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.netloc != "api.bilibili.com" or parts.fragment:
        raise TransportError("bili_scope")
    paths = {"device": "/x/frontend/finger/spi", "wbi_keys": "/x/web-interface/nav",
             "search": "/x/web-interface/wbi/search/type", "video_info": "/x/web-interface/view"}
    purpose = message["purpose"]
    if not isinstance(purpose, str) or purpose not in paths or parts.path != paths[purpose]:
        raise TransportError("bili_scope")
    headers = message["headers"]
    if (not isinstance(headers, dict) or set(headers) - {"Accept", "Referer", "Cookie"}
            or headers.get("Accept") != "application/json" or headers.get("Referer") != "https://www.bilibili.com/"):
        raise TransportError("bili_scope")
    cookie = headers.get("Cookie")
    if (cookie is not None or purpose in ("wbi_keys", "search")) and not _valid_device_cookie_header(cookie):
        raise TransportError("bili_scope")
    query = parse_qs(parts.query, strict_parsing=True, keep_blank_values=True, max_num_fields=8)
    if any(len(values) != 1 for values in query.values()):
        raise TransportError("bili_scope")
    params = {key: value[0] for key, value in query.items()}
    if purpose in ("device", "wbi_keys"):
        if context["operation"] != "search" or params or purpose == "device" and cookie is not None:
            raise TransportError("bili_scope")
    elif purpose == "search":
        expected_keyword = context["query"].translate(str.maketrans("", "", "!'()*"))
        if (context["operation"] != "search" or set(params) != {"search_type", "keyword", "page", "order", "wts", "w_rid"}
                or params["search_type"] != "video" or params["keyword"] != expected_keyword
                or params["page"] != "1" or params["order"] != "totalrank"
                or not re.fullmatch(r"[0-9]{1,12}", params["wts"])
                or not re.fullmatch(r"[0-9a-f]{32}", params["w_rid"])):
            raise TransportError("bili_scope")
    else:
        candidate = context["candidate"]
        expected = re.fullmatch(r"https://www\.bilibili\.com/video/(BV[0-9A-Za-z]{10})/?", candidate["url"])
        if (context["operation"] != "read" or not expected or params != {"bvid": expected[1]} or cookie is not None):
            raise TransportError("bili_scope")
    return message


def run_adapter(operation, payload, *, response_bytes, deadline, perform_http, check_cancelled, unreaped):
    """Supervise parsing/signing only. perform_http supervises every actual hop."""
    command = Path(__file__).resolve().parent.parent / "addons" / "bilibili" / "worker.py"
    if not command.is_file():
        raise TransportError("bili_missing")
    context = {"operation": operation, "response_bytes": response_bytes, **payload}
    initial = {"version": 1, "op": operation, "response_bytes": response_bytes, "text_chars": 12000, **payload}
    frame_limit = response_bytes * 2 + 65536
    events, commands = queue.Queue(maxsize=4), queue.Queue(maxsize=2)
    stop = threading.Event()
    process = None
    threads = []
    env = {key: value for key, value in os.environ.items()
           if key.upper() in {"SYSTEMROOT", "WINDIR", "PATH", "PATHEXT", "TEMP", "TMP", "LANG", "LC_ALL"}}
    env["PYTHONIOENCODING"] = "utf-8"

    def send_event(event):
        while not stop.is_set():
            try:
                events.put(event, timeout=0.1)
                return
            except queue.Full:
                pass

    def read_lines():
        try:
            while not stop.is_set():
                raw = process.stdout.readline(frame_limit + 1)
                if not raw or len(raw) > frame_limit or not raw.endswith(b"\n"):
                    send_event(("error", None))
                    return
                value = json.loads(raw.decode("utf-8"), parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
                send_event(("message", value))
        except Exception:
            send_event(("error", None))

    def write_lines():
        try:
            while not stop.is_set():
                try:
                    value = commands.get(timeout=0.1)
                except queue.Empty:
                    continue
                raw = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8") + b"\n"
                if len(raw) > frame_limit:
                    raise ValueError()
                process.stdin.write(raw)
                process.stdin.flush()
        except Exception:
            send_event(("error", None))

    def send(value):
        while True:
            check_cancelled()
            if time.monotonic() >= deadline:
                raise TransportError("timeout")
            try:
                commands.put(value, timeout=0.1)
                return
            except queue.Full:
                pass

    try:
        check_cancelled()
        process = subprocess.Popen([sys.executable, str(command)], cwd=str(command.parent),
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   env=env, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        threads = [threading.Thread(target=read_lines, daemon=True, name="alice-bili-read"),
                   threading.Thread(target=write_lines, daemon=True, name="alice-bili-write")]
        for thread in threads:
            thread.start()
        send(initial)
        expected = ["device", "wbi_keys", "search"] if operation == "search" else ["video_info"]
        index, device_cookie = 0, None
        while True:
            check_cancelled()
            if time.monotonic() >= deadline:
                raise TransportError("timeout")
            try:
                kind, message = events.get(timeout=0.05)
            except queue.Empty:
                continue
            if kind == "error" or not isinstance(message, dict) or type(message.get("version")) is not int or message.get("version") != 1:
                raise TransportError("bili_protocol")
            if message.get("type") == "result":
                if message.get("ok") is not True:
                    error = message.get("error")
                    code = error.get("code", "bili_failed") if isinstance(error, dict) else "bili_protocol"
                    raise TransportError(code if isinstance(code, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,59}", code) else "bili_failed")
                if index != len(expected) or not isinstance(message.get("data"), dict):
                    raise TransportError("bili_protocol")
                return message["data"]
            validate_request(message, context)
            if index >= len(expected) or message["purpose"] != expected[index] or message["id"] != str(index + 1):
                raise TransportError("bili_protocol")
            if message["purpose"] in ("wbi_keys", "search") and message["headers"].get("Cookie") != device_cookie:
                raise TransportError("bili_scope")
            response = perform_http(message, context)
            if message["purpose"] == "device":
                body = json.loads(base64.b64decode(response["body_base64"], validate=True).decode("utf-8"))
                if not isinstance(body, dict):
                    raise TransportError("bili_protocol")
                values = body.get("data", {})
                if type(body.get("code")) is int and body["code"] == 0:
                    device_cookie = _device_cookie_from_data(values)
            index += 1
            send({"version": 1, "type": "http_response", "id": message["id"], "ok": True, **response})
    except (OSError, ValueError, KeyError, TypeError):
        raise TransportError("bili_protocol") from None
    finally:
        stop.set()
        if process is not None:
            if process.poll() is None:
                try:
                    process.kill()
                    process.wait(timeout=5)
                except (OSError, subprocess.TimeoutExpired):
                    unreaped(process)
            for thread in threads:
                thread.join(timeout=5)
                if thread.is_alive():
                    unreaped(process)
            if not any(thread.is_alive() for thread in threads):
                for stream in (process.stdin, process.stdout):
                    try:
                        stream.close()
                    except OSError:
                        pass
