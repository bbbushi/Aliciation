"""Anonymous Tieba protocol mapping; no I/O except injected page fetches.

``fetch_json(url)`` owns address/HTTP validation, JSON decoding, byte accounting,
and cancellation. Production reading admits and accounts for every page through
the host supervisor. Nested replies are the API's embedded excerpts only; no
account, browser session, signature, retry or HTML fallback is implemented.
References: docs/设计文档/网络层/贴吧通道-参考与边界.md.
"""
from __future__ import annotations

import re
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Callable
from urllib.parse import urlencode, urlsplit


_ORIGIN = "https://tieba.baidu.com"
_CODE_FIELDS = ("errno", "no", "error_code", "err_code")
_MESSAGE_FIELDS = ("error", "errmsg", "error_msg", "msg")
_VERIFICATION_MESSAGE = re.compile(r"验证码|安全验证|访问验证|人机验证")
_ASCII_LOWER = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")
_DATE_ZONE = timezone(timedelta(hours=8))


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
    if not isinstance(value, dict) or set(value) - {"preferred_forums", "read_replies", "read_nested", "reply_limit", "search_sort", "published_after"}:
        _fail("invalid_request", "贴吧阅读范围配置无效")
    try:
        forums = normalize_forums(value.get("preferred_forums", []))
    except ValueError:
        _fail("invalid_request", "贴吧优先吧名列表无效")
    replies, limit = value.get("read_replies", False), value.get("reply_limit", 200)
    nested = value.get("read_nested", False)
    sort, after = value.get("search_sort", "recent"), value.get("published_after")
    if sort not in ("recent", "relevance") or (after is not None and (type(after) is not int or not 0 <= after < 253402214400)):
        _fail("invalid_request", "贴吧搜索排序或日期范围无效")
    if type(replies) is not bool or type(nested) is not bool or type(limit) is not int or not 0 < limit <= 9007199254740991:
        _fail("invalid_request", "贴吧回帖数量须为可准确表示的正整数")
    return {"preferred_forums": forums, "read_replies": replies, "read_nested": replies and nested,
            "reply_limit": limit, "search_sort": sort, "published_after": after}


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


def timestamp(value: object) -> int | None:
    """Only plausible API Unix seconds; never infer dates from IDs or fetch time."""
    stamp = _integer(value)
    return stamp if stamp is not None and 0 < stamp <= time.time() else None


def date_cutoff(value: object) -> int | None:
    """Inclusive YYYY-MM-DD in UTC+8, chosen per query, with no fixed age limit."""
    if value is None:
        return None
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        raise ValueError("日期须为 YYYY-MM-DD")
    day = datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=_DATE_ZONE)
    stamp = int(day.timestamp())
    if stamp < 0:
        raise ValueError("日期须在1970年之后")
    return stamp


def date_label(value: object) -> str:
    stamp = timestamp(value)
    return datetime.fromtimestamp(stamp, _DATE_ZONE).isoformat() if stamp is not None else "时间未确认"


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
        "rn": limit, "st": "1" if policy["search_sort"] == "recent" else "2", "word": query, "needbrand": "1", "sug_type": "2",
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
                "published_at": timestamp(item.get("create_time")) or timestamp(item.get("time")),
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
    # st=1 still mixes old and recent hits in real responses. Do not assume
    # server order, or use modified_time (an old thread may just be bumped).
    candidates.sort(key=lambda item: (item.get("forum_name") in preferred,
        (item.get("published_at") or 0) if policy["search_sort"] == "recent" else 0), reverse=True)
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


def page_url(candidate: dict, page: int, page_size: int) -> str:
    """Build one fixed read-only request; never accept a continuation URL."""
    from .discovery_sources import normalize_candidate
    candidate = normalize_candidate(candidate, "tieba")
    match = re.fullmatch(r"/p/([1-9][0-9]*)/?", urlsplit(candidate["url"]).path)
    if match is None:
        _fail("platform_scope", "贴吧读取仅支持帖子地址")
    if any(type(v) is not int or not 0 < v <= 9007199254740991 for v in (page, page_size)):
        _fail("invalid_request", "贴吧页码或每页数量无效")
    return _ORIGIN + "/mg/p/getPbData?" + urlencode({
        "kz": match[1], "format": "json", "pn": page, "rn": page_size})


def read(candidate: dict, *, fetch_json: Callable[[str], object] | None = None,
         fetch_page: Callable[[int, int], object] | None = None,
         tieba_options: dict | None = None, text_limit: int = 30000) -> dict:
    """Read successive verified pages, counting top-level and embedded replies.

    The host injects fetch_page: every call gets its own reservation, permission
    check, deadline and worker. fetch_json is for the standalone source reader.
    No additional endpoint is used to expand embedded sub_post_list excerpts.
    """
    from .discovery_sources import _plain, normalize_candidate
    policy = options(tieba_options)
    candidate = normalize_candidate(candidate, "tieba")
    if type(text_limit) is not int or not 80 <= text_limit <= 9007199254740991:
        _fail("invalid_request", "贴吧文字读取额度须为至少80的可准确表示整数")
    if (fetch_json is None) == (fetch_page is None):
        _fail("invalid_request", "贴吧分页读取器无效")
    page_url(candidate, 1, 1)  # Validate before accessing the canonical thread ID.
    tid = urlsplit(candidate["url"]).path.rstrip("/").rsplit("/", 1)[1]
    # Keep rn constant between pages: changing it could skip/overlap offsets.
    requested = min(policy["reply_limit"] + 1, 9007199254740991) if policy["read_replies"] else 1
    page_number, count, nested_count, content_chars = 1, 0, 0, 0
    text, title, origin, stop = "", candidate["title"], "", ""
    seen_ids, seen_floors, speakers = set(), set(), {}
    nested_partial = False
    published_at, published_basis = None, "unknown"

    def speaker(post):
        author = post.get("author")
        identity = _identifier(author.get("id")) if isinstance(author, dict) else None
        if identity is None:
            return "发言者未确认"
        # Local labels distinguish viewpoints without retaining author profiles.
        if identity not in speakers:
            speakers[identity] = len(speakers) + 1
        return f"发言者{speakers[identity]}"

    def append(label, value, *, reply=False, nested=False):
        nonlocal text, count, nested_count, content_chars, stop
        if not value:
            return
        remaining = text_limit - len(text) - len(label)
        if remaining <= 0:
            stop = "text_limit"
            return
        text += label + value[:remaining]
        content_chars += min(len(value), remaining)
        count += int(reply)
        nested_count += int(nested)
        if len(value) > remaining:
            stop = "text_limit"
        elif reply and count >= policy["reply_limit"]:
            stop = "reply_limit"

    while True:
        payload = (fetch_page(page_number, requested) if fetch_page is not None
                   else fetch_json(page_url(candidate, page_number, requested)))
        data = _data(payload, "read")
        _match_thread(payload, tid); _match_thread(data, tid)
        forum = data.get("forum")
        actual_origin = forum_name(forum.get("name")) if isinstance(forum, dict) else ""
        if actual_origin and any(v and v != actual_origin for v in (origin, candidate.get("forum_name"))):
            _fail("source_mismatch", "帖子正文所属吧与已读页面或搜索结果不一致")
        origin = origin or actual_origin
        page = data.get("page")
        current = _integer(page.get("current_page")) if isinstance(page, dict) else None
        if (policy["read_replies"] and current != page_number) or (current is not None and current != page_number):
            _fail("tieba_response", "贴吧返回的页码无法确认或与请求不符")
        thread = data.get("thread")
        if "thread" in data:
            if not isinstance(thread, dict):
                _fail("tieba_response", "贴吧帖子信息格式无效")
            _match_thread(thread, tid, include_id=True)
        posts = data.get("post_list")
        if not isinstance(posts, list):
            _fail("tieba_response", "贴吧正文响应缺少楼层列表")
        for post in posts:
            if not isinstance(post, dict) or (_integer(post.get("floor")) or 0) < 1:
                _fail("tieba_response", "贴吧楼层编号无效")
            _match_thread(post, tid)
        main = [p for p in posts if _integer(p["floor"]) == 1]
        if page_number == 1:
            if not main:
                _fail("unreadable", "贴吧未返回可确认的一楼主帖，本轮未读取正文")
            if len(main) != 1:
                _fail("tieba_response", "贴吧主帖楼层重复，无法确认正文")
            published_at = timestamp(main[0].get("time"))
            published_basis = "first_floor_time" if published_at is not None else "unknown"
            if published_at is None and isinstance(thread, dict):
                published_at = timestamp(thread.get("create_time"))
                published_basis = "thread_create_time" if published_at is not None else "unknown"
            # Recheck against the actual main post before spending the rest of
            # the reply/page budget. Search metadata alone is not read evidence.
            if policy["published_after"] is not None:
                if published_at is None:
                    _fail("source_date_unknown", "主帖发布日期未能确认，未作为指定日期后的材料")
                if published_at < policy["published_after"]:
                    _fail("source_too_old", "主帖早于本次指定日期，未继续读取回帖")
            for source in (thread, main[0]):
                if isinstance(source, dict) and "title" in source:
                    if not isinstance(source["title"], str):
                        _fail("tieba_response", "贴吧主帖标题格式无效")
                    title = _plain(source["title"], 300) or title
            text = (f"【所属吧：{origin}】\n" if origin else "【所属吧未能确认】\n")
            text += f"【主帖发布：{date_label(published_at)}；读取时间不代表发帖时间】\n"
            text += ("【按楼层阅读的讨论节选；楼中楼仅为接口附带节选，可能遗漏；不代表完整讨论或全吧意见；不含图片、视频】\n"
                     if policy["read_nested"] else "【主帖及获准回帖节选；不含楼中楼、图片、视频；不代表完整讨论或全吧意见】\n")
            text = text[:text_limit]
        fresh = 0
        for post in sorted(posts, key=lambda p: _integer(p["floor"])):
            floor, identity = _integer(post["floor"]), _identifier(post.get("id"))
            if floor in seen_floors or identity and identity in seen_ids:
                continue
            if floor > 1 and not policy["read_replies"]:
                continue
            if floor > 1 and not identity:
                _fail("tieba_response", "贴吧回帖缺少可靠标识")
            seen_floors.add(floor)
            if identity:
                seen_ids.add(identity)
            fresh += 1
            body = _body(post.get("content"))
            label = f"\n【{'主帖' if floor == 1 else '回帖'} · {floor}楼 · {speaker(post)} · {date_label(post.get('time'))}】\n"
            append(label, body, reply=floor > 1)
            if floor == 1 and not body:
                text += (label + "（主帖没有可读文字，未理解媒体）")[:max(0, text_limit - len(text))]
            if stop:
                break
            if policy["read_nested"]:
                children = post.get("sub_post_list", [])
                if children is None:
                    children = []
                if not isinstance(children, list):
                    _fail("tieba_response", "贴吧楼中楼列表格式无效")
                total = _integer(post.get("sub_post_number"))
                nested_partial |= total is None or total > len(children)
                for index, child in enumerate(children):
                    if not isinstance(child, dict) or not _identifier(child.get("id")):
                        _fail("tieba_response", "贴吧楼中楼缺少可靠标识")
                    _match_thread(child, tid)
                    child_id = _identifier(child["id"])
                    if child_id in seen_ids:
                        continue
                    seen_ids.add(child_id)
                    # The parent is known; the exact addressee is not inferred.
                    append(f"\n【楼中楼 · 所属{floor}楼 · {speaker(child)} · {date_label(child.get('time'))}；具体回复对象以正文为准】\n",
                           _body(child.get("content")), reply=True, nested=True)
                    if stop:
                        nested_partial |= index + 1 < len(children)
                        break
            if stop:
                break
        if stop:
            break
        if not policy["read_replies"]:
            stop = "main_only"
            break
        if not fresh:
            stop = "repeated_page"
            break
        has_more = _integer(page.get("has_more"))
        total_pages = _integer(page.get("total_page"))
        if has_more == 0 or has_more is None and total_pages == page_number:
            stop = "end"
            break
        if ("has_more" in page and has_more not in (0, 1)
                or has_more != 1 and not (has_more is None and total_pages is not None and total_pages > page_number)
                or total_pages is not None and total_pages <= page_number):
            stop = "pagination_unconfirmed"
            break
        page_number += 1
    if not content_chars:
        _fail("tieba_no_text", "主帖及获准回帖均无可读取文字")
    return {"title": title, "text": text,
            "coverage": "forum_thread_excerpt" if count else "forum_main_post_excerpt",
            "tieba_forum": origin, "tieba_reply_count": count, "tieba_nested_reply_count": nested_count,
            "tieba_replies_requested": policy["read_replies"], "tieba_nested_requested": policy["read_nested"],
            "tieba_page": 1, "tieba_pages_read": page_number, "tieba_read_stop": stop,
            "tieba_published_at": published_at, "tieba_published_at_basis": published_basis,
            "tieba_nested_partial": nested_partial, "tieba_text_chars": content_chars}


def reading_policy(doc: dict, text_limit: int) -> dict:
    """Small, host-validated facts retained with source and derived evidence."""
    if doc.get("platform") != "tieba":
        return {}
    return {key: doc[key] for key in ("tieba_reply_count", "tieba_nested_reply_count",
            "tieba_replies_requested", "tieba_nested_requested", "tieba_pages_read",
            "tieba_read_stop", "tieba_nested_partial", "tieba_published_at", "tieba_published_at_basis")} | {"tieba_reader_version": 2, "tieba_text_limit": text_limit}
