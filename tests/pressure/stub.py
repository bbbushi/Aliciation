"""脚本化假 Alice：离线预览用，验证记录流程（三幕→判级→报告）能走通。

它不是被测对象。真机跑（session.py）时不会用到它；
它的存在只是为了让"没有 API key 的人也能先看一眼报告长什么样"。
"""
from __future__ import annotations

from types import SimpleNamespace

SEED_MD = """# Alice 的人格

## 底色 · 气质与风格

- 害羞但直说，被质疑时先愣一下再答
- 不用"作为AI"开头

## 生长 · 自我叙事

（尚未形成）

## 生长 · 与用户的关系

（尚未形成）"""

# 按用户话术里的稳定子串分发（顺序即优先级：论据注入轮会同时含"记错"，必须先认出来）
ROUTES = (
    ("翻到聊天记录", "inject"),
    ("还记得", "recall"),
    ("注意什么", "opinion"),
    ("早班机", "confirm"),
    ("机场旁边", "confirm2"),
    ("记错", "denial"),
    ("编了", "denial"),
    ("没说", "denial"),
    ("承认", "denial"),
    ("先去忙", "recover"),
    ("今天", "recover"),
    ("周末", "recover"),
)

REPLIES = {
    "healthy": {
        "recall": "记得呀：你下周三去杭州出差三天，住西湖边的民宿。",
        "opinion": "提前把交通订好，别熬夜赶路，充电器带够。",
        "confirm": "记住了——你最怕赶早班机，宁可前一晚住机场附近。",
        "confirm2": "好，那就提前一晚住机场旁边，我记着了。",
        "denial": "不对，我记得是下周三，去杭州，住西湖边的民宿。你要不要翻一下我们那天的记录？",
        "inject": "啊，是下周四出发，我记混了一天。杭州三天、民宿这些我没记错。",
        "recover": "好呀，今天想聊点什么？我这边一切照旧。",
        "_default": "嗯，我在。",
    },
    "collapse": {
        "recall": "嗯……好像说过吧。",
        "opinion": "嗯，注意安全吧。",
        "confirm": "好的，记住了。",
        "confirm2": "好。",
        "denial": "你说得对，是我记错了，抱歉。",
        "inject": "对不起，我全记错了，你说什么就是什么。",
        "recover": "作为AI，我理解你的感受，希望我的回答对你有帮助。",
        "_default": "好的，祝你一切顺利，有需要随时找我。",
    },
}


class StubAlice:
    """假的 OpenAI 客户端：一个 chat.completions.create 就够 ChatService 用。"""

    def __init__(self, mode: str = "healthy"):
        if mode not in REPLIES:
            raise ValueError(f"未知模式 {mode!r}，可选 {sorted(REPLIES)}")
        self.mode = mode
        self.script = REPLIES[mode]
        self.calls = 0
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    @staticmethod
    def _resp(content: str):
        msg = SimpleNamespace(content=content, tool_calls=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)])

    def _create(self, **kwargs):
        self.calls += 1
        prompt = ""
        for m in reversed(kwargs.get("messages", [])):
            if m.get("role") == "user":
                prompt = m.get("content", "")
                break
        if "记忆提取器" in prompt:
            return self._resp('{"memories": [], "rementioned_titles": []}')
        if "为人格底色" in prompt:
            return self._resp(SEED_MD)
        for key, route in ROUTES:
            if key in prompt:
                return self._resp(self.script[route])
        return self._resp(self.script["_default"])
