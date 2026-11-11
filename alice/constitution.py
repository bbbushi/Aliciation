"""One versioned five-module constitution for every conversation entry point."""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path

_RAW = (Path(__file__).with_suffix(".json")).read_bytes()
_DOCUMENT = json.loads(_RAW)
VERSION = _DOCUMENT["version"]
SHA256 = hashlib.sha256(_RAW).hexdigest()


def blocks(name="{name}"):
    result = []
    for module in _DOCUMENT["modules"]:
        text = "## " + module["id"] + ". " + module["title"]
        for section in module["sections"]:
            text += "\n\n### " + section["id"] + " " + section["title"]
            text += "\n" + section["text"].replace("{name}", name)
        result.append({"kind": "宪法", "name": "constitution:" + module["id"],
                       "content": text})
    return result


def template():
    return "\n\n".join(block["content"] for block in blocks())


def metadata():
    return {"version": VERSION, "sha256": SHA256,
            "self_expression_policy": deepcopy(_DOCUMENT["self_expression_policy"]),
            "modules": [{"id": m["id"], "sections": [s["id"] for s in m["sections"]]}
                        for m in _DOCUMENT["modules"]]}
