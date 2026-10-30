"""Extract one identified Zhihu entity from an already fetched public page.

No network, browser, script execution or account state is used here. Bootstrap
JSON is page data, not instructions. A missing entity never falls back to the
whole page, an excerpt, metadata, or the first answer in a question's feed.
"""
from __future__ import annotations

from html.parser import HTMLParser
import json
import re
from urllib.parse import unquote, urlsplit


def _fail(code: str, message: str):
    # discovery_sources imports this extractor only after fetching the page.
    from .discovery_sources import DiscoveryError
    raise DiscoveryError(code, message)


class _InitialDataParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.count = 0
        self.active = False
        self.complete = False
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag != "script":
            return
        ids = [value for name, value in attrs if name == "id"]
        if "js-initialData" not in ids:
            return
        if len(ids) != 1 or self.count:
            _fail("zhihu_response", "知乎页面初始化数据不唯一")
        self.count += 1
        self.active = True

    def handle_data(self, data):
        if self.active:
            self.parts.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self.active:
            self.active = False
            self.complete = True

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError()
        value[key] = item
    return value


def _invalid_constant(_value):
    raise ValueError()


def _object(parent: dict, key: str) -> dict:
    value = parent.get(key)
    if value is None:
        _fail("zhihu_unreadable", "本次公开页面未提供目标内容的正文数据")
    if not isinstance(value, dict):
        _fail("zhihu_response", "知乎页面正文数据结构不符合预期")
    return value


def _text_field(entity: dict, key: str) -> str:
    value = entity.get(key)
    if value is None:
        return ""
    if not isinstance(value, str):
        _fail("zhihu_response", "知乎正文或标题字段不是文字")
    return value


def _check_identity(entity: dict, identity: str, kind: str):
    # An exact map key already binds the entity. If the redundant id/type is
    # supplied, it must agree; do not infer identity from map iteration order.
    if "id" in entity:
        value = entity["id"]
        if type(value) not in (str, int):
            _fail("zhihu_response", "知乎内容编号格式不符合预期")
        if str(value) != identity:
            _fail("source_mismatch", "知乎返回的内容与所选链接不一致")
    if "type" in entity:
        if not isinstance(entity["type"], str):
            _fail("zhihu_response", "知乎内容类型格式不符合预期")
        if entity["type"] != kind:
            _fail("source_mismatch", "知乎返回的内容类型与所选链接不一致")


def extract(candidate: dict, markup: str) -> dict:
    """Return title/text/coverage for the URL's exact answer, article or question."""
    from .discovery_sources import MAX_TEXT_CHARS, _clean, _plain, validate_read_url

    if not isinstance(candidate, dict) or candidate.get("platform", "zhihu") != "zhihu":
        _fail("source_mismatch", "待读取内容不属于知乎")
    if not isinstance(markup, str):
        _fail("zhihu_response", "知乎页面文字格式不符合预期")
    parts = urlsplit(validate_read_url("zhihu", candidate.get("url", "")))
    path = unquote(parts.path)
    answer = re.fullmatch(r"/question/([0-9]+)/answer/([0-9]+)/?", path)
    article = re.fullmatch(r"/p/([0-9]+)/?", path) if parts.hostname == "zhuanlan.zhihu.com" else None
    question = re.fullmatch(r"/question/([0-9]+)/?", path)
    if answer:
        kind, identity, question_id = "answer", answer[2], answer[1]
    elif article:
        kind, identity, question_id = "article", article[1], None
    elif question:
        kind, identity, question_id = "question", question[1], question[1]
    else:
        _fail("source_mismatch", "知乎链接未指向可识别的回答、文章或问题")

    parser = _InitialDataParser()
    parser.feed(markup)
    parser.close()
    if not parser.count:
        _fail("zhihu_unreadable", "公开页面未提供可识别的知乎正文；可能需要登录或额外验证")
    if not parser.complete or parser.active:
        _fail("zhihu_response", "知乎页面初始化数据未完整返回")
    raw = "".join(parser.parts)
    if not raw.strip():
        _fail("zhihu_unreadable", "本次公开页面的知乎正文数据为空")
    try:
        initial = json.loads(raw, object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
    except (ValueError, RecursionError):
        _fail("zhihu_response", "知乎页面初始化数据不是有效的 JSON 对象")
    if not isinstance(initial, dict):
        _fail("zhihu_response", "知乎页面初始化数据结构不符合预期")
    entities = _object(_object(initial, "initialState"), "entities")
    collection = _object(entities, {"answer": "answers", "article": "articles", "question": "questions"}[kind])
    entity = _object(collection, identity)
    _check_identity(entity, identity, kind)

    fallback_title = _clean(candidate.get("title"), 300)
    if kind == "answer":
        parent = entity.get("question")
        if not isinstance(parent, dict) or type(parent.get("id")) not in (str, int):
            _fail("zhihu_response", "知乎回答未提供可核对的问题编号")
        if str(parent["id"]) != question_id:
            _fail("source_mismatch", "知乎回答所属问题与所选链接不一致")
        title = _clean(_text_field(parent, "title"), 300) or fallback_title or "知乎回答"
        text = _plain(_text_field(entity, "content"), MAX_TEXT_CHARS)
    elif kind == "article":
        title = _clean(_text_field(entity, "title"), 300) or fallback_title or "知乎文章"
        text = _plain(_text_field(entity, "content"), MAX_TEXT_CHARS)
    else:
        question_title = _text_field(entity, "title")
        title = _clean(question_title, 300) or fallback_title or "知乎问题"
        detail = _plain(_text_field(entity, "detail"), MAX_TEXT_CHARS)
        # The question's own title/detail are the evidence; answers are separate
        # sources, even when the same HTML happens to contain their entities.
        text = "\n".join(value for value in (_clean(question_title, MAX_TEXT_CHARS), detail) if value)[:MAX_TEXT_CHARS]
    if len(text) < 80:
        _fail("zhihu_unreadable", "未取得足够的目标知乎正文；不以搜索摘要或页面说明代替")
    return {"title": title, "text": text, "coverage": f"zhihu_{kind}_excerpt"}
