"""JSON capability definitions; public schemas and host routing share one source.

No user state, model calls, dynamic imports or executable JSON. Executors remain
the existing request-bound sessions, which recheck permissions at execution.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path
import re

_RAW = (Path(__file__).parent / "capabilities" / "commands.json").read_bytes()
_DOCUMENT = json.loads(_RAW)
VERSION = _DOCUMENT["version"]
SHA256 = hashlib.sha256(_RAW).hexdigest()
_EXECUTORS = {"ordinary", "search", "source_recall", "memory", "history",
              "workspace", "persona", "worker", "learning"}
_UNITS = {}
for _unit in _DOCUMENT["units"]:
    _command = _unit["command"]
    if (not isinstance(_command, str) or re.fullmatch(r"[a-z][a-z0-9_]*", _command) is None
            or _command in _UNITS or _unit["internal"]["executor"] not in _EXECUTORS
            or _unit["internal"]["operation"] != _command
            or not isinstance(_unit["summary"], str)
            or _unit["parameters"].get("type") != "object"):
        raise ValueError("invalid_capability_definition")
    _UNITS[_command] = _unit


def sdk_tool(command):
    unit = _UNITS[command]
    return {"type": "function", "function": {"name": command,
            "description": unit["summary"], "parameters": copy.deepcopy(unit["parameters"])}}


def sdk_group(executor):
    return [sdk_tool(name) for name, unit in _UNITS.items()
            if unit["internal"]["executor"] == executor]


def executor(command):
    unit = _UNITS.get(command)
    return unit["internal"]["executor"] if unit is not None else None


def metadata(tools=()):
    public = json.dumps(tools, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {"version": VERSION, "definition_sha256": SHA256,
            "public_sha256": hashlib.sha256(public.encode()).hexdigest(),
            "public_chars": len(public),
            "commands": [t["function"]["name"] for t in tools]}


def catalogue(tools):
    """Active commands carry their summary/schema in SDK tools, once only."""
    active = {t["function"]["name"] for t in tools}
    body = {"version": VERSION, "available": sorted(active),
            "not_exposed": [{"command": name, "summary": unit["summary"]}
                            for name, unit in _UNITS.items() if name not in active]}
    return "## 能力注册表\n" + json.dumps(body, ensure_ascii=False, separators=(",", ":"))


def _valid(value, schema):
    types = schema.get("type", [])
    types = [types] if isinstance(types, str) else types
    match = {"null": value is None, "boolean": type(value) is bool,
             "integer": type(value) is int,
             "number": type(value) is int or type(value) is float and math.isfinite(value),
             "string": isinstance(value, str), "array": isinstance(value, list),
             "object": isinstance(value, dict)}
    if types and not any(match.get(kind, False) for kind in types):
        return False
    if "enum" in schema and value not in schema["enum"]:
        return False
    if isinstance(value, dict):
        props = schema.get("properties", {})
        return (all(key in value for key in schema.get("required", []))
                and (schema.get("additionalProperties", True) or not set(value) - set(props))
                and all(_valid(v, props[k]) for k, v in value.items() if k in props))
    if isinstance(value, list):
        return (schema.get("minItems", 0) <= len(value) <= schema.get("maxItems", float("inf"))
                and all(_valid(v, schema.get("items", {})) for v in value))
    if isinstance(value, str):
        return schema.get("minLength", 0) <= len(value) <= schema.get("maxLength", float("inf"))
    if type(value) in (int, float):
        return ((type(value) is int or math.isfinite(value)) and schema.get("minimum", -float("inf")) <= value
                <= schema.get("maximum", float("inf")))
    return True


def validate_call(command, arguments, tools):
    """Check actual request-visible schema; execution still checks live grants."""
    schema = next((t["function"]["parameters"] for t in tools
                   if t["function"]["name"] == command), None)
    if schema is None:
        return {"error": "capability_not_available", "command": command}
    try:
        value = json.loads(arguments or "{}")
    except (ValueError, TypeError):
        return {"error": "invalid_arguments", "command": command}
    if not _valid(value, schema):
        return {"error": "invalid_arguments", "command": command}
    return None


def explicit_request(text, tools):
    """Only the actual current user message can carry /tool COMMAND [JSON].

    This binds intent, not permission, and never executes text as a tool call.
    """
    match = re.fullmatch(r"\s*/tool\s+([a-z][a-z0-9_]*)(?:\s+([\s\S]*))?\s*", text)
    if match is None:
        return None
    command, raw = match.groups()
    active = {t["function"]["name"] for t in tools}
    result = {"command": command, "available": command in active}
    if raw:
        try:
            arguments = json.loads(raw)
            if not isinstance(arguments, dict):
                raise ValueError("invalid_arguments")
            result["business_arguments"] = arguments
        except ValueError:
            result["argument_error"] = "invalid_json_object"
    return result
