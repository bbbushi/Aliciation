"""Optional Edge reader. Fixed search/read only; no Alice imports or model tools.

stdin: initial frame, go, and per-resource grants. stdout: ready/request/finished/
result. CDP Fetch pauses every request, including redirects, before admission.
The host kills this complete process tree on cancellation, timeout or shutdown.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
from html import unescape
from html.parser import HTMLParser
import json
import math
from pathlib import Path
import re
import sys
from urllib.parse import urlencode, urlsplit, urlunsplit


class Stop(Exception):
    pass


class TextExcerpt(HTMLParser):
    """Only text in the API's selected rich-content field; never loads HTML."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.hidden = [], []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        blocked = (tag in ("script", "style", "noscript", "iframe", "svg", "video", "audio", "object", "form")
                   or "hidden" in attrs or attrs.get("aria-hidden") == "true"
                   or bool(re.search(r"display\s*:\s*none|visibility\s*:\s*hidden", attrs.get("style", ""), re.I)))
        if self.hidden or blocked:
            if tag not in ("area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"):
                self.hidden.append(tag)
        elif tag in ("p", "div", "br", "li", "blockquote", "h1", "h2", "h3", "h4", "tr"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if self.hidden:
            if tag in self.hidden:
                del self.hidden[len(self.hidden) - 1 - self.hidden[::-1].index(tag):]
        elif tag in ("p", "div", "li", "blockquote", "h1", "h2", "h3", "h4", "tr"):
            self.parts.append("\n")

    def handle_data(self, value):
        if not self.hidden:
            self.parts.append(value)

    def text(self, value):
        self.feed(value)
        self.close()
        return "\n".join(line.strip() for line in "".join(self.parts).splitlines() if line.strip())


def emit(kind, **values):
    print(json.dumps({"version": 1, "type": kind, **values}, ensure_ascii=True, allow_nan=False), flush=True)


def receive():
    raw = sys.stdin.buffer.readline(65537)
    if not raw.endswith(b"\n") or len(raw) > 65536:
        raise Stop("browser_protocol")
    value = json.loads(raw)
    if not isinstance(value, dict) or value.get("version") != 1:
        raise Stop("browser_protocol")
    return value


def canonical(platform, url):
    if not isinstance(url, str):
        return None
    try:
        p = urlsplit(url)
        if p.scheme != "https" or p.port not in (None, 443) or p.username or p.password:
            return None
    except ValueError:
        return None
    if platform == "bilibili" and p.hostname == "www.bilibili.com" and re.fullmatch(r"/video/BV[0-9A-Za-z]{10}/?", p.path):
        return urlunsplit(("https", p.hostname, p.path.rstrip("/") + "/", "", ""))
    if platform == "zhihu" and p.hostname == "www.zhihu.com" and re.fullmatch(r"/question/\d+/answer/\d+/?", p.path):
        return urlunsplit(("https", p.hostname, p.path.rstrip("/"), "", ""))
    return None


def allowed(platform, url, method, resource):
    # Deliberately repeated by the stdlib host; this addon is not an authority.
    try:
        p = urlsplit(url)
        if p.scheme != "https" or p.port not in (None, 443) or p.username or p.password:
            return False
    except ValueError:
        return False
    if method not in ("GET", "HEAD") or resource not in ("Document", "Script", "Stylesheet", "XHR", "Fetch", "Other"):
        return False
    host = p.hostname or ""
    if platform == "bilibili":
        if host in ("www.bilibili.com", "search.bilibili.com"):
            return (p.path in ("/", "/all", "/video") or canonical(platform, url) is not None
                    or resource in ("Script", "Stylesheet"))
        if host == "api.bilibili.com":
            return p.path in ("/x/web-interface/nav", "/x/web-interface/view", "/x/frontend/finger/spi",
                              "/x/web-interface/wbi/search/type", "/x/web-interface/wbi/search/all/v2",
                              "/x/web-interface/search/type", "/x/web-interface/search/all/v2")
        return host.endswith(".hdslb.com") and resource in ("Script", "Stylesheet")
    if platform == "zhihu":
        if host == "www.zhihu.com":
            return p.path == "/" or bool(re.fullmatch(r"/api/v4/(?:search_v3|answers/\d+)", p.path))
        return host.endswith(".zhimg.com") and resource in ("Script", "Stylesheet")
    return False


SEARCH_JS = r"""() => {
 const selector = '.bili-video-card a[href*="/video/"], .video-item a[href*="/video/"]';
 return Array.from(document.querySelectorAll(selector)).filter(a => a.getClientRects().length).map(a => {
   const card = a.closest('.bili-video-card, .video-item');
   const title = card?.querySelector('h3')?.textContent;
   return {url:a.href, title:(title || a.getAttribute('title') || '').trim().slice(0,300)};
 }).filter(x => x.title);
}"""

GATE_JS = r"""() => {
 const visible = s => Array.from(document.querySelectorAll(s)).some(x => x.getClientRects().length);
 const title = document.title || '';
 if (/验证码|安全验证|访问验证|人机验证|Security Check|403 Forbidden|异常请求/.test(title)
     || visible('.geetest_panel, .geetest_holder, #captcha, .Captcha, .yidun_popup')) return 'verification_required';
 if (visible('.SignFlow, .Login-modal, .login-scan-wp') || /\/signin(?:\?|$)/.test(location.href)) return 'browser_login_required';
 return '';
}"""

READ_JS = r"""() => {
 const text = el => (el?.innerText || '').trim();
 return {title:text(document.querySelector('h1.video-title, h1.video-info-title, h1')),
   text:text(document.querySelector('.basic-desc-info-text, .desc-info-text')), coverage:'video_description_only'};
}"""


class Reader:
    def __init__(self, payload):
        self.p = payload
        self.platform = payload["platform"]
        self.serial = 0
        self.lock = asyncio.Lock()
        self.pending = {}
        self.network = {}
        self.tasks = set()
        self.failure = None
        self.closed = False

    def task(self, coroutine):
        task = asyncio.create_task(coroutine)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def paused(self, frame):
        request_id = frame["requestId"]
        try:
            if self.closed:
                return
            if "responseStatusCode" in frame or "responseErrorReason" in frame:
                entry = self.pending.get(request_id)
                headers = {h["name"].lower(): h["value"] for h in frame.get("responseHeaders", [])}
                length = headers.get("content-length", "")
                if entry and length.isdigit() and int(length) > self.p["response_bytes"]:
                    self.failure = "browser_response_limit"
                    await self.cdp.send("Fetch.failRequest", {"requestId": request_id, "errorReason": "Aborted"})
                    return
                if frame.get("frameId") == self.main_frame and frame.get("resourceType") == "Document" and frame.get("responseStatusCode", 200) in (401,403,412,429):
                    self.failure = "verification_required" if frame["responseStatusCode"] in (403,412) else "browser_login_required" if frame["responseStatusCode"] == 401 else "http_429"
                continuation = {"requestId": request_id}
                if frame.get("resourceType") == "Document" and frame.get("responseStatusCode"):
                    # Extra policy only on automated pages. Prevent unobserved
                    # worker/iframe/WS/media traffic; manual login is separate.
                    policy = ("sandbox allow-scripts allow-same-origin; worker-src 'none'; frame-src 'none'; object-src 'none'; media-src 'none'; form-action 'none'; "
                              + ("connect-src https://api.bilibili.com https://www.bilibili.com https://search.bilibili.com"
                                 if self.platform == "bilibili" else "connect-src https://www.zhihu.com https://zhuanlan.zhihu.com"))
                    continuation.update(responseCode=frame["responseStatusCode"],
                        responseHeaders=[*frame.get("responseHeaders", []), {"name":"Content-Security-Policy", "value":policy}])
                await self.cdp.send("Fetch.continueResponse", continuation)
                # Redirect bodies have no loadingFinished of their own.
                if entry and 300 <= frame.get("responseStatusCode", 0) < 400:
                    self.finished(request_id, None)
                return
            request, resource = frame["request"], frame.get("resourceType", "Other")
            if not allowed(self.platform, request["url"], request["method"], resource):
                await self.cdp.send("Fetch.failRequest", {"requestId": request_id, "errorReason": "BlockedByClient"})
                if resource == "Document" and frame.get("frameId") == self.main_frame:
                    self.failure = "browser_login_required" if "/signin" in request["url"] else "browser_scope"
                return
            async with self.lock:
                self.serial += 1
                identity = str(self.serial)
                emit("request", id=identity, url=request["url"], method=request["method"], resource=resource)
                grant = await asyncio.to_thread(receive)
                if grant.get("type") != "grant" or grant.get("id") != identity or grant.get("max_bytes") != self.p["response_bytes"]:
                    raise Stop("browser_protocol")
                self.pending[request_id] = identity
                if frame.get("networkId"):
                    self.network[frame["networkId"]] = request_id
            await self.cdp.send("Fetch.continueRequest", {"requestId": request_id})
        except BaseException as exc:
            if not self.closed:
                self.failure = str(exc) if isinstance(exc, Stop) else "browser_failed"

    def finished(self, request_id, amount):
        identity = self.pending.pop(request_id, None)
        if identity:
            emit("finished", id=identity, bytes=amount)

    def loading_finished(self, frame):
        request_id = self.network.pop(frame["requestId"], None)
        amount = int(frame.get("encodedDataLength", 0))
        if amount > self.p["response_bytes"]:
            self.failure = "browser_response_limit"
            amount = None
        if request_id:
            self.finished(request_id, amount)

    async def zhihu_json(self, page, endpoint):
        # Same-origin reads stay inside the dedicated browser's session. IDs
        # are parsed by Python because recent Zhihu IDs exceed JS safe ints.
        response = await page.evaluate("""async ({endpoint, maximum}) => {
            const response = await fetch(endpoint, {credentials:'same-origin'});
            if (!response.ok) return {status:response.status};
            const body = await response.text();
            return body.length <= maximum ? {status:response.status, body} : {too_large:true};
        }""", {"endpoint": endpoint, "maximum": self.p["response_bytes"]})
        if self.failure:
            raise Stop(self.failure)
        if response.get("too_large"):
            raise Stop("browser_response_limit")
        status = response.get("status")
        if status != 200:
            raise Stop("browser_login_required" if status == 401 else "verification_required"
                       if status in (403, 412) else "http_429" if status == 429 else "browser_content_unavailable")
        try:
            data = json.loads(response["body"])
        except (ValueError, KeyError):
            raise Stop("browser_content_unavailable") from None
        if not isinstance(data, dict) or data.get("error"):
            raise Stop("browser_content_unavailable")
        return data

    async def zhihu_search(self, page):
        # A short API slice can contain only unsupported result modules. Read
        # at least a normal page, then apply the user's candidate limit locally.
        endpoint = "/api/v4/search_v3?" + urlencode({"q": self.p["query"], "t": "general",
                                                     "offset": 0, "limit": max(20, self.p["limit"])})
        data = await self.zhihu_json(page, endpoint)
        if not isinstance(data.get("data"), list):
            raise Stop("browser_content_unavailable")
        candidates, seen = [], set()
        unsupported = duplicates = 0
        for item in data["data"]:
            if not isinstance(item, dict) or item.get("type") != "search_result":
                unsupported += 1
                continue
            obj = item.get("object")
            if not isinstance(obj, dict):
                raise Stop("browser_content_unavailable")
            identity, kind = str(obj.get("id", "")), obj.get("type")
            if kind != "answer":
                unsupported += 1
                continue
            if not re.fullmatch(r"[0-9]+", identity):
                raise Stop("browser_content_unavailable")
            question = obj.get("question") if isinstance(obj.get("question"), dict) else {}
            title = obj.get("title") or obj.get("name") or question.get("name") or question.get("title")
            if not isinstance(title, str) or not title.strip():
                raise Stop("browser_content_unavailable")
            question_id = str(question.get("id", ""))
            if not re.fullmatch(r"[0-9]+", question_id):
                raise Stop("browser_content_unavailable")
            url = f"https://www.zhihu.com/question/{question_id}/answer/{identity}"
            if url not in seen:
                candidates.append({"platform":"zhihu", "url":url,
                    "title":unescape(re.sub(r"<[^>]*>", "", title)).strip()[:300], "snippet":"", "coverage":"search_snippet"})
                seen.add(url)
            else:
                duplicates += 1
        returned = candidates[:self.p["limit"]]
        return {"candidates": returned, "diagnostic": {"kind":"search_results", "platform":"zhihu",
            "provider":"browser", "stage":"search", "received":len(data["data"]), "accepted":len(candidates),
            "returned":len(returned), "rejected":0, "duplicates":duplicates, "unsupported":unsupported}}

    async def zhihu_read(self, page):
        url = self.p["url"]
        path = urlsplit(url).path
        answer = re.fullmatch(r"/question/([0-9]+)/answer/([0-9]+)", path)
        if not answer:
            raise Stop("browser_scope")
        identity = answer[2]
        data = await self.zhihu_json(page, f"/api/v4/answers/{identity}?include=content,question,paid_info")
        if data.get("type") != "answer" or str(data.get("id", "")) != identity:
            raise Stop("source_mismatch")
        question = data.get("question") if isinstance(data.get("question"), dict) else {}
        if str(question.get("id", "")) != answer[1]:
            raise Stop("source_mismatch")
        if (any(data.get(key) for key in ("is_paid", "is_paid_content", "paid_info"))
                or data.get("can_read_content") is False):
            raise Stop("browser_content_unavailable")
        title = question.get("title") or question.get("name")
        content = data.get("content")
        if not isinstance(title, str) or not title.strip() or not isinstance(content, str):
            raise Stop("browser_content_unavailable")
        text = TextExcerpt().text(content)
        if len(text) < 80:
            raise Stop("browser_content_unavailable")
        label = "【知乎站内只读接口返回的指定正文节选；不含其他回答、评论、图片或付费内容】\n"
        text = label + text[:12000 - len(label)]
        return {"platform":"zhihu", "url":url, "title":TextExcerpt().text(title)[:300], "text":text,
                "coverage":"zhihu_answer_excerpt", "fetched_at":datetime.now(timezone.utc).isoformat(),
                "content_hash":hashlib.sha256(text.encode()).hexdigest()}

    async def run(self):
        from playwright.async_api import async_playwright, Error
        p = self.p
        profile = Path(p["profile"])
        profile.mkdir(parents=True, exist_ok=True)
        proxy = p.get("network_proxy")
        if proxy:
            parts = urlsplit(proxy)
            if parts.scheme != "http" or parts.hostname not in ("127.0.0.1", "localhost", "::1") or parts.username or parts.password:
                raise Stop("proxy_config")
        async with async_playwright() as driver:
            try:
                context = await driver.chromium.launch_persistent_context(str(profile), executable_path=p["executable"],
                    headless=not p.get("show_window", False), chromium_sandbox=True, service_workers="block", accept_downloads=False,
                    proxy={"server": proxy} if proxy else {"server": "direct://"},
                    args=["--disable-background-mode", "--autoplay-policy=user-gesture-required", "--start-minimized"])
            except Error as exc:
                message = str(exc).lower()
                raise Stop("browser_profile_busy" if any(v in message for v in (
                    "processsingleton", "profile appears to be in use", "user data directory is already in use",
                    "opening in existing browser session", "正在现有浏览器会话中打开")) else "browser_start_failed") from None
            try:
                async def close_socket(socket):
                    await socket.close()
                await context.route_web_socket("**/*", close_socket)
                page = context.pages[0] if context.pages else await context.new_page()
                context.on("page", lambda other: self.task(other.close()) if other != page else None)
                self.cdp = await context.new_cdp_session(page)
                self.main_frame = (await self.cdp.send("Page.getFrameTree"))["frameTree"]["frame"]["id"]
                await self.cdp.send("Network.enable")
                await self.cdp.send("Network.setCacheDisabled", {"cacheDisabled": True})
                self.cdp.on("Fetch.requestPaused", lambda event: self.task(self.paused(event)))
                self.cdp.on("Network.loadingFinished", self.loading_finished)
                self.cdp.on("Network.loadingFailed", lambda event: self.finished(self.network.pop(event["requestId"], ""), None))
                await self.cdp.send("Fetch.enable", {"patterns": [{"urlPattern":"*", "requestStage":"Request"}, {"urlPattern":"*", "requestStage":"Response"}]})
                url = p.get("url")
                if p["op"] == "search":
                    url = ("https://search.bilibili.com/all?" + urlencode({"keyword":p["query"]}) if self.platform == "bilibili"
                           else "https://www.zhihu.com/")
                elif canonical(self.platform, url) != url:
                    raise Stop("browser_scope")
                elif self.platform == "zhihu":
                    url = "https://www.zhihu.com/"
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=p["timeout_seconds"] * 1000)
                except Error:
                    raise Stop(self.failure or "browser_navigation_failed") from None
                end = asyncio.get_running_loop().time() + p["timeout_seconds"]
                while asyncio.get_running_loop().time() < end:
                    if self.failure:
                        raise Stop(self.failure)
                    gate = await page.evaluate(GATE_JS)
                    if gate:
                        raise Stop(gate)
                    if p["op"] == "search":
                        if self.platform == "zhihu":
                            return await self.zhihu_search(page)
                        rows = await page.evaluate(SEARCH_JS)
                        candidates, seen = [], set()
                        for row in rows:
                            target = canonical(self.platform, row.get("url"))
                            if target and target not in seen:
                                candidates.append({"platform":self.platform,"url":target,"title":row["title"],"snippet":"","coverage":"search_snippet"})
                                seen.add(target)
                        if candidates:
                            return {"candidates":candidates[:p["limit"]]}
                        empty = page.get_by_text(re.compile("没有找到相关|暂无搜索结果|未找到相关内容"))
                        if await empty.count() and await empty.first.is_visible():
                            return {"candidates":[]}
                    else:
                        if self.platform == "zhihu":
                            return await self.zhihu_read(page)
                        if canonical(self.platform, page.url) != url:
                            raise Stop("source_mismatch")
                        result = await page.evaluate(READ_JS)
                        if result and len(result.get("text", "")) >= 80:
                            label = "【仅实际视频简介文字节选；未观看视频、读取字幕或评论】\n"
                            content = label + result["text"][:12000-len(label)]
                            return {"platform":self.platform,"url":url,"title":result["title"][:300],"text":content,
                                    "coverage":result["coverage"],"fetched_at":datetime.now(timezone.utc).isoformat(),
                                    "content_hash":hashlib.sha256(content.encode()).hexdigest()}
                    await page.wait_for_timeout(250)
                raise Stop("browser_content_unavailable")
            finally:
                self.closed = True
                await context.close()
                for task in list(self.tasks):
                    task.cancel()
                for request_id in list(self.pending):
                    self.finished(request_id, None)


def main():
    try:
        p = receive()
        if p.get("platform") not in ("bilibili", "zhihu") or p.get("op") not in ("search", "read"):
            raise Stop("browser_scope")
        if type(p.get("show_window", False)) is not bool:
            raise Stop("browser_protocol")
        for key in ("response_bytes", "limit"):
            if type(p.get(key)) is not int or p[key] <= 0:
                raise Stop("browser_protocol")
        if (type(p.get("timeout_seconds")) not in (int, float)
                or not math.isfinite(p["timeout_seconds"]) or p["timeout_seconds"] <= 0):
            raise Stop("browser_protocol")
        if p["op"] == "search" and (not isinstance(p.get("query"), str) or not 1 <= len(p["query"]) <= 200):
            raise Stop("browser_protocol")
        emit("ready")
        if receive().get("type") != "go":
            raise Stop("browser_protocol")
        data = asyncio.run(Reader(p).run())
        emit("result", ok=True, data=data)
    except Stop as exc:
        emit("result", ok=False, code=str(exc))
    except BaseException:
        emit("result", ok=False, code="browser_failed")


if __name__ == "__main__":
    main()
