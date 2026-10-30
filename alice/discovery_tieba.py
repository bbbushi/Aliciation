"""Anonymous, first-page Tieba protocol mapping; no I/O except injected GET.

``fetch_json(url)`` owns address/HTTP validation, JSON decoding, byte accounting,
and cancellation. Each public function calls it exactly once after validation.
No account, browser session, signature, retry, pagination, or HTML-page fallback
is implemented. Public source references and acceptance limits are documented in
docs/贴吧通道-参考与边界.md; these endpoints have no verified stability guarantee.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Callable
from urllib.parse import urlencode, urlsplit


_ORIGIN = "https://tieba.baidu.com"
_CODE_FIELDS = ("errno", "no", "error_code", "err_code")
_MESSAGE_FIELDS = ("error", "errmsg", "error_msg", "msg")
_VERIFICATION_MESSAGE = re.compile(r"验证码|安全验证|访问验证|人机验证")
_ASCII_LOWER = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")


def normalize_forums(value: object) -> list[str]:
    """Canonical exact forum names, not search terms or fuzzy/suffix matches."""
    if not isinstance(value, list):
        raise ValueError("贴吧范围须为吧名列表")
    result = []
    for raw in value:
        if not isinstance(raw, str):
            raise ValueError("吧名须为文字")
        name = unicodedata.normalize("NFC", raw.strip()).translate(_ASCII_LOWER)
        if (not name or len(name) > 128 or any(ord(c) < 32 or ord(c) == 127
                                               or unicodedata.category(c) in ("Cc", "Cs") for c in name)):
            raise ValueError("吧名须为不超过128字的非空名称，不含控制字符")
        if name not in result:
            result.append(name)
    return result


def options(value: object = None) -> dict:
    """Validate host-supplied policy independently of untrusted page fields."""
    if value is None:
        value = {}
    if not isinstance(value, dict) or set(value) - {"preferred_forums", "read_replies", "reply_limit"}:
        _fail("invalid_request", "贴吧阅读范围配置无效")
    try:
        forums = normalize_forums(value.get("preferred_forums", []))
    except ValueError:
        _fail("invalid_request", "贴吧优先吧名列表无效")
    replies, limit = value.get("read_replies", False), value.get("reply_limit", 20)
    if type(replies) is not bool or type(limit) is not int or not 0 < limit <= 9007199254740991:
        _fail("invalid_request", "贴吧回帖数量须为可准确表示的正整数")
    return {"preferred_forums": forums, "read_replies": replies, "reply_limit": limit}


def forum_name(value: object) -> str:
    """Missing or malformed origin labels never qualify for a restricted forum."""
    try:
        return normalize_forums([value])[0]
    except ValueError:
        return ""


def _search_forum(item: dict) -> str:
    direct = forum_name(item.get("forum_name"))
    info = item.get("forum_info")
    nested = forum_name(info.get("forum_name")) if isinstance(info, dict) else ""
    # Conflicting server metadata is not resolved by guessing.
    return "" if direct and nested and direct != nested else direct or nested


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
           trace: dict | None = None, tieba_options: dict | None = None) -> list[dict]:
    """One anonymous multsearch GET; snippets are transient discovery metadata."""
    from .discovery_sources import DiscoveryError, _plain, normalize_candidate, validate_query

    # Only fixed counts enter the shared trace. Its caller-owned platform,
    # provider and stage fields must survive, including on a partial failure.
    trace = {} if trace is None else trace
    trace.update(received=0, unsupported=0, duplicates=0, accepted=0, rejected=0, returned=0)

    def invalid_card(message: str) -> None:
        trace["rejected"] += 1
        _fail("tieba_response", message)

    policy = options(tieba_options)
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
        origin = _search_forum(item)
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
                "coverage": "search_snippet", "snippet": snippet, "forum_name": origin,
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
    preferred = set(policy["preferred_forums"])
    candidates.sort(key=lambda item: item.get("forum_name") not in preferred)
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


def check_forum(name: str, *, fetch_json: Callable[[str], object]) -> dict:
    """Resolve one explicitly requested forum and inspect its public first page.

    This is an existence/access check, not interest search or content collection.
    Only fixed identifiers and counts cross the worker boundary, never user data
    or the homepage's post titles, snippets, author profiles or control tokens.
    """
    requested = forum_name(name)
    if not requested:
        _fail("invalid_request", "请输入可检测的吧名")
    data = _data(fetch_json(_ORIGIN + "/mg/f/getFrsData?" + urlencode({
        "kw": requested, "pn": 1, "rn": 1,
    })), "forum_check")
    forum = data.get("forum")
    if not isinstance(forum, dict):
        _fail("tieba_forum_unconfirmed", "未取得可确认的吧信息")
    actual, identity = forum_name(forum.get("name")), _identifier(forum.get("id"))
    threads = data.get("thread_list")
    if not actual or not identity or not isinstance(threads, list):
        _fail("tieba_forum_unconfirmed", "贴吧返回的信息不足以确认该吧")
    seen = set()
    for thread in threads:
        if not isinstance(thread, dict):
            _fail("tieba_response", "吧首页帖子列表结构无效")
        if thread.get("is_live_ad") is True or _integer(thread.get("is_live_ad")) == 1:
            continue
        tid = _identifier(thread.get("tid")) or _identifier(thread.get("id"))
        if not tid or not isinstance(thread.get("title"), str) or not thread["title"].strip():
            _fail("tieba_response", "吧首页帖子信息无法确认")
        seen.add(tid)
    return {"requested": requested, "forum_name": actual, "forum_id": identity,
            "thread_count": len(seen),
            "status": "mismatch" if actual != requested else "ready" if seen else "empty"}


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


def read(candidate: dict, *, fetch_json: Callable[[str], object],
         tieba_options: dict | None = None, text_limit: int = 12000) -> dict:
    """One getPbData GET; explicit main floor plus opt-in top-level replies."""
    from .discovery_sources import _plain, normalize_candidate

    policy = options(tieba_options)
    candidate = normalize_candidate(candidate, "tieba")
    if type(text_limit) is not int or text_limit < 80:
        _fail("invalid_request", "贴吧文字读取额度无效")
    match = re.fullmatch(r"/p/([1-9][0-9]*)/?", urlsplit(candidate["url"]).path)
    if match is None:
        _fail("platform_scope", "贴吧读取仅支持帖子地址")
    tid = match[1]
    payload = fetch_json(_ORIGIN + "/mg/p/getPbData?" + urlencode({
        "kz": tid, "format": "json", "pn": 1,
        "rn": policy["reply_limit"] + 1 if policy["read_replies"] else 1,
    }))
    data = _data(payload, "read")
    _match_thread(payload, tid)
    _match_thread(data, tid)
    forum = data.get("forum")
    origin = forum_name(forum.get("name")) if isinstance(forum, dict) else ""
    if candidate.get("forum_name") and origin and candidate["forum_name"] != origin:
        _fail("source_mismatch", "帖子正文所属吧与搜索结果不一致")
    page = data.get("page")
    current_page = _integer(page.get("current_page")) if isinstance(page, dict) else None
    if (policy["read_replies"] and current_page != 1) or (current_page is not None and current_page != 1):
        _fail("tieba_response", "贴吧未返回可确认的帖子首页")
    thread = data.get("thread")
    if "thread" in data:
        if not isinstance(thread, dict):
            _fail("tieba_response", "贴吧帖子信息格式无效")
        _match_thread(thread, tid, include_id=True)
    posts = data.get("post_list")
    if not isinstance(posts, list):
        _fail("tieba_response", "贴吧正文响应缺少楼层列表")
    main_posts, replies = [], []
    seen_floors, seen_ids = set(), set()
    for post in posts:
        if not isinstance(post, dict) or _integer(post.get("floor")) is None:
            _fail("tieba_response", "贴吧楼层字段格式无效")
        floor = _integer(post["floor"])
        if floor < 1:
            _fail("tieba_response", "贴吧楼层编号无效")
        if floor == 1:
            main_posts.append(post)
        elif policy["read_replies"]:
            post_id = _identifier(post.get("id"))
            if not post_id:
                _fail("tieba_response", "贴吧回帖缺少可靠标识")
            if floor in seen_floors or post_id in seen_ids:
                continue
            _match_thread(post, tid)
            replies.append((floor, post))
            seen_floors.add(floor); seen_ids.add(post_id)
    if not main_posts:
        _fail("unreadable", "贴吧未返回可确认的一楼主帖，本轮未读取正文")
    if len(main_posts) != 1:
        _fail("tieba_response", "贴吧主帖楼层重复，无法确认正文")
    post = main_posts[0]
    _match_thread(post, tid)
    main_text = _body(post.get("content"))
    if not main_text and not policy["read_replies"]:
        _fail("tieba_no_text", "贴吧主帖未返回可读取文字，不读取图片或视频")
    title = candidate["title"]
    # Some valid responses omit thread.title. The first-floor post title, when
    # present, is validated independently; otherwise keep the candidate title.
    for source in (thread, post):
        if isinstance(source, dict) and "title" in source:
            if not isinstance(source["title"], str):
                _fail("tieba_response", "贴吧主帖标题格式无效")
            title = _plain(source["title"], 300) or title
    header = (f"【所属吧：{origin}】\n" if origin else "【所属吧未能确认】\n")
    header += ("【帖子首页文字节选；主帖与各楼回帖分别标注；不含楼中楼、图片或视频；不代表完整讨论或全吧意见】\n\n"
               if policy["read_replies"] else "【贴吧主帖文字节选；不含回帖、楼中楼及图片或视频内容】\n\n")
    main_label = header + "【主帖 · 1楼】\n"
    text = (main_label + (main_text or "（主帖没有可读取文字，未理解其中的媒体内容）"))[:text_limit]
    content_chars = min(len(main_text), max(0, text_limit - len(main_label)))
    count = 0
    for floor, reply in sorted(replies, key=lambda item: item[0]):
        if count >= policy["reply_limit"]:
            break
        if _identifier(reply.get("id")) == _identifier(post.get("id")):
            continue
        value = _body(reply.get("content"))
        if not value:
            continue
        label = f"\n\n【回帖 · {floor}楼（与主帖分开记录）】\n"
        remaining = text_limit - len(text) - len(label)
        if remaining <= 0:
            break
        text += label + value[:remaining]
        content_chars += min(len(value), remaining)
        count += 1
        if len(value) > remaining:
            break
    if not main_text and count == 0:
        _fail("tieba_no_text", "主帖及获准首页回帖均无可读取文字")
    return {"title": title, "text": text,
            "coverage": "forum_thread_excerpt" if count else "forum_main_post_excerpt",
            "tieba_forum": origin, "tieba_reply_count": count,
            "tieba_replies_requested": policy["read_replies"], "tieba_page": 1,
            "tieba_text_chars": content_chars}
