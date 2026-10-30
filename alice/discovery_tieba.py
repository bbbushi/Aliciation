"""Anonymous, first-page Tieba protocol mapping; no I/O except injected GET.

``fetch_json(url)`` owns address/HTTP validation, JSON decoding, byte accounting,
and cancellation. Each public function calls it exactly once after validation.
No account, browser session, signature, retry, pagination, or HTML-page fallback
is implemented. Public source references and acceptance limits are documented in
docs/贴吧通道-参考与边界.md; these endpoints have no verified stability guarantee.
"""
from __future__ import annotations

import re
from typing import Callable
from urllib.parse import urlencode, urlsplit


_ORIGIN = "https://tieba.baidu.com"
_CODE_FIELDS = ("errno", "no", "error_code", "err_code")
_MESSAGE_FIELDS = ("error", "errmsg", "error_msg", "msg")
_VERIFICATION_MESSAGE = re.compile(r"验证码|安全验证|访问验证|人机验证")


def _fail(code: str, message: str, *, diagnostic: dict | None = None):
    # Sources imports this module lazily; keep the shared exception identity.
    from .discovery_sources import DiscoveryError
    raise DiscoveryError(code, message, diagnostic=diagnostic)


def _integer(value: object) -> int | None:
    if type(value) is int:
        return value
    if isinstance(value, str) and re.fullmatch(r"-?[0-9]{1,20}", value):
        return int(value)
    return None


def _identifier(value: object) -> str | None:
    if type(value) is int and value > 0:
        return str(value)
    if isinstance(value, str) and re.fullmatch(r"[1-9][0-9]*", value):
        return value
    return None


def _data(payload: object, stage: str) -> dict:
    """Classify control metadata, never the text of a post or search result."""
    diagnostic = {"platform": "tieba", "stage": stage, "api_code": None,
                  "verification_required": None}
    if not isinstance(payload, dict):
        _fail("tieba_response", "贴吧接口响应结构无效", diagnostic=diagnostic)
    scopes = [payload]
    if isinstance(payload.get("data"), dict):
        scopes.append(payload["data"])
    verification = None
    api_error = False
    selected_code = None
    for scope in scopes:
        for key in _CODE_FIELDS:
            if key in scope:
                code = _integer(scope[key])
                if code is None:
                    _fail("tieba_response", "贴吧接口状态码格式无效", diagnostic=diagnostic)
                # Prefer an actual nonzero code over another scope's success.
                # Never stringify unknown or oversized values into diagnostics.
                if selected_code is None or (selected_code == 0 and code != 0):
                    selected_code = code
                    diagnostic["api_code"] = code if -(2**31) <= code < 2**31 else None
                api_error = api_error or code != 0
        for key in _MESSAGE_FIELDS:
            if key not in scope or scope[key] is None:
                continue
            message = scope[key]
            if not isinstance(message, str):
                _fail("tieba_response", "贴吧接口状态说明格式无效")
            if _VERIFICATION_MESSAGE.search(message):
                verification = True
            if key == "error" and message.strip().lower() not in ("", "success"):
                api_error = True
        controls = [scope]
        if "anti" in scope and scope["anti"] is not None:
            if not isinstance(scope["anti"], dict):
                _fail("tieba_response", "贴吧接口验证状态格式无效")
            controls.append(scope["anti"])
        for control in controls:
            if "need_vcode" in control:
                flag = _integer(control["need_vcode"])
                if flag not in (0, 1):
                    _fail("tieba_response", "贴吧接口验证状态格式无效")
                if flag == 1:
                    verification = True
                elif verification is None:
                    verification = False
    diagnostic["verification_required"] = verification
    if verification:
        _fail("verification_required", "贴吧要求完成验证，本轮已停止", diagnostic=diagnostic)
    if api_error:
        # Check before data shape: real API errors often return data=null.
        _fail("tieba_api_error", "贴吧接口拒绝本次请求，本轮未取得材料", diagnostic=diagnostic)
    data = payload.get("data")
    if not isinstance(data, dict):
        _fail("tieba_response", "贴吧接口响应缺少数据对象")
    return data


def search(query: str, limit: int, *, fetch_json: Callable[[str], object],
           trace: dict | None = None) -> list[dict]:
    """One anonymous multsearch GET; snippets are transient discovery metadata."""
    from .discovery_sources import DiscoveryError, _plain, normalize_candidate, validate_query

    # Only fixed counts enter the shared trace. Its caller-owned platform,
    # provider and stage fields must survive, including on a partial failure.
    trace = {} if trace is None else trace
    trace.update(received=0, unsupported=0, duplicates=0, accepted=0, rejected=0, returned=0)

    def invalid_card(message: str) -> None:
        trace["rejected"] += 1
        _fail("tieba_response", message)

    query = validate_query(query)
    if type(limit) is not int or limit <= 0:
        _fail("invalid_request", "结果数量必须为正整数")
    url = _ORIGIN + "/mo/q/search/multsearch?" + urlencode({
        "rn": limit, "st": "1", "word": query, "needbrand": "1", "sug_type": "2",
        "pn": "1", "come_from": "search", "subapp_type": "pc", "_client_type": "20",
    })
    data = _data(fetch_json(url), "search")
    cards = data.get("card_list")
    if not isinstance(cards, list):
        _fail("tieba_response", "贴吧搜索响应缺少结果列表")
    trace["received"] = len(cards)
    candidates, seen = [], set()
    for card in cards:
        if not isinstance(card, dict):
            invalid_card("贴吧搜索结果卡片格式无效")
        # Other card kinds include forums/users/ads; they are not thread bodies.
        kinds = [card[key] for key in ("cardInfo", "cardStyle") if key in card]
        if any(not isinstance(kind, str) for kind in kinds):
            invalid_card("贴吧搜索结果类型格式无效")
        if not any(kind.lower() == "thread" for kind in kinds):
            trace["unsupported"] += 1
            continue
        item = card.get("data")
        if not isinstance(item, dict):
            invalid_card("贴吧搜索帖子字段无效")
        tid = _identifier(item.get("tid"))
        if tid is None or not isinstance(item.get("title"), str):
            invalid_card("贴吧搜索帖子标识或标题无效")
        snippet = item.get("content", "")
        if not isinstance(snippet, str):
            invalid_card("贴吧搜索摘要格式无效")
        title = _plain(item["title"], 300)
        if not title:
            invalid_card("贴吧搜索帖子缺少标题")
        try:
            candidate = normalize_candidate({
                "platform": "tieba", "url": f"{_ORIGIN}/p/{tid}", "title": title,
                "coverage": "search_snippet", "snippet": snippet,
            })
        except DiscoveryError:
            trace["rejected"] += 1
            continue
        if tid in seen:
            trace["duplicates"] += 1
            continue
        candidates.append(candidate)
        seen.add(tid)
        trace["accepted"] += 1
    result = candidates[:limit]
    trace["returned"] = len(result)
    return result


def _match_thread(scope: dict, tid: str, *, include_id: bool = False) -> None:
    # A post's `id` is a reply ID, not a thread ID. Only thread objects use id.
    keys = ("tid", "thread_id", "kz", "id") if include_id else ("tid", "thread_id", "kz")
    for key in keys:
        if key not in scope:
            continue
        actual = _identifier(scope[key])
        if actual is None:
            _fail("tieba_response", "贴吧正文的帖子标识格式无效")
        if actual != tid:
            _fail("source_mismatch", "贴吧正文与候选帖子不匹配")


def _body(content: object) -> str:
    from .discovery_sources import _plain

    if isinstance(content, str):
        return _plain(content, len(content)).strip()
    if not isinstance(content, list):
        _fail("tieba_response", "贴吧主帖内容格式无效")
    fragments = []
    for part in content:
        if not isinstance(part, dict):
            _fail("tieba_response", "贴吧主帖内容片段格式无效")
        if "type" in part:
            kind = _integer(part["type"])
            if kind is None:
                _fail("tieba_response", "贴吧主帖内容类型格式无效")
            if kind not in (0, 1):
                # Emoji, images, voice and unknown media are not read as prose.
                continue
            # Type 0 is a plain-text run; type 1 can be a link label. Only its
            # text is retained, never a URL, link target or fetched destination.
            value = part.get("text")
        else:
            # Some public parsers support untyped text/c fragments as well.
            value = part.get("text", part.get("c"))
        if not isinstance(value, str):
            _fail("tieba_response", "贴吧主帖文字片段格式无效")
        fragments.append(value)
    markup = "".join(fragments)
    return _plain(markup, len(markup)).strip()


def read(candidate: dict, *, fetch_json: Callable[[str], object]) -> dict:
    """One getPbData GET, pn=1/rn=1; only an explicitly identified floor 1."""
    from .discovery_sources import _plain, normalize_candidate

    candidate = normalize_candidate(candidate, "tieba")
    match = re.fullmatch(r"/p/([1-9][0-9]*)/?", urlsplit(candidate["url"]).path)
    if match is None:
        _fail("platform_scope", "贴吧读取仅支持帖子地址")
    tid = match[1]
    payload = fetch_json(_ORIGIN + "/mg/p/getPbData?" + urlencode({
        "kz": tid, "format": "json", "pn": 1, "rn": 1,
    }))
    data = _data(payload, "read")
    _match_thread(payload, tid)
    _match_thread(data, tid)
    thread = data.get("thread")
    if "thread" in data:
        if not isinstance(thread, dict):
            _fail("tieba_response", "贴吧帖子信息格式无效")
        _match_thread(thread, tid, include_id=True)
    posts = data.get("post_list")
    if not isinstance(posts, list):
        _fail("tieba_response", "贴吧正文响应缺少楼层列表")
    main_posts = []
    for post in posts:
        if not isinstance(post, dict) or _integer(post.get("floor")) is None:
            _fail("tieba_response", "贴吧楼层字段格式无效")
        if _integer(post["floor"]) == 1:
            main_posts.append(post)
    if not main_posts:
        _fail("unreadable", "贴吧未返回可确认的一楼主帖，本轮未读取正文")
    if len(main_posts) != 1:
        _fail("tieba_response", "贴吧主帖楼层重复，无法确认正文")
    post = main_posts[0]
    _match_thread(post, tid)
    text = _body(post.get("content"))
    if not text:
        _fail("tieba_no_text", "贴吧主帖未返回可读取文字，不读取图片或视频")
    title = candidate["title"]
    # Some valid responses omit thread.title. The first-floor post title, when
    # present, is validated independently; otherwise keep the candidate title.
    for source in (thread, post):
        if isinstance(source, dict) and "title" in source:
            if not isinstance(source["title"], str):
                _fail("tieba_response", "贴吧主帖标题格式无效")
            title = _plain(source["title"], 300) or title
    return {"title": title,
            "text": "【贴吧主帖文字节选；不含回帖、楼中楼及图片或视频内容】\n\n" + text,
            "coverage": "forum_main_post_excerpt"}
