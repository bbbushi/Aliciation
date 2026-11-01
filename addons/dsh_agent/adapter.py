"""Read-only DSH installation discovery, separate from runtime pairing.

The selected integration is the DSH plugin bridge. This metadata reader neither
loads that plugin nor connects to DSH; it never provides an execution endpoint.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import struct
import sys

PROTOCOL = "alice-dsh-adapter-v1"
INSTALL_ROOT = Path("D:/dshNEXT/DSH NEXT")
MAX_REQUEST_BYTES = 8192
MAX_HEADER_BYTES = 16 * 1024 * 1024
MAX_METADATA_BYTES = 128 * 1024


def _asar_json(archive: Path, name: str) -> dict:
    """Read one bounded, installation-owned metadata file without extraction."""
    with archive.open("rb") as stream:
        prefix = stream.read(16)
        if len(prefix) != 16:
            raise ValueError("invalid_asar_header")
        _, header_size, _, json_size = struct.unpack("<4I", prefix)
        if not 0 < json_size <= MAX_HEADER_BYTES or header_size < json_size + 8:
            raise ValueError("invalid_asar_header")
        node = json.loads(stream.read(json_size).decode("utf-8"))
        for component in name.split("/"):
            node = node["files"][component]
        size = node.get("size")
        offset = node.get("offset")
        if node.get("unpacked") or node.get("link"):
            raise ValueError("unsupported_asar_metadata")
        if type(size) is not int or not 0 < size <= MAX_METADATA_BYTES:
            raise ValueError("invalid_asar_metadata_size")
        if not isinstance(offset, str) or not offset.isdecimal():
            raise ValueError("invalid_asar_metadata_offset")
        start = 8 + header_size + int(offset)
        if start + size > archive.stat().st_size:
            raise ValueError("invalid_asar_metadata_range")
        stream.seek(start)
        value = json.loads(stream.read(size).decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("invalid_asar_metadata")
        return value


def describe() -> dict:
    result = {
        "protocol": PROTOCOL,
        "name": "dsh-agent",
        "ready": False,
        "executable": False,
        "status": "unverified_installation",
        "reason": "installation_not_verified",
        "integration_direction": "dsh-plugin-bridge",
        "transport": "installation-metadata",
        "target_transport": "authenticated-loopback-http",
        "bridge_connected": None,
        "permission_profile": "runtime_not_queried",
        "permission_enforced": False,
        "cost": {"kind": "unknown", "hard_limit": False},
        "blockers": ["runtime_not_queried"],
    }
    archive = INSTALL_ROOT / "resources" / "app.asar"
    if not (INSTALL_ROOT / "DSH NEXT.exe").is_file() or not archive.is_file():
        return result
    try:
        desktop = _asar_json(archive, "package.json")
        runtime = _asar_json(archive, "node_modules/@deepseek-ai/dsh/package.json")
        cordis = _asar_json(archive, "node_modules/@deepseek-ai/cordis/package.json")
    except (OSError, ValueError, KeyError, TypeError, struct.error):
        result["reason"] = "installation_metadata_unreadable"
        return result
    # Never treat a changed executable/package as the build that was reviewed.
    identities = (desktop.get("name"), desktop.get("version"),
                  runtime.get("name"), runtime.get("version"),
                  cordis.get("name"), cordis.get("version"))
    expected = ("dsh-desktop-next", "2.0.21-next", "@deepseek-ai/dsh", "0.2.1-rc.4",
                "@deepseek-ai/cordis", "4.0.5")
    if identities != expected:
        result["reason"] = "installation_version_changed"
        return result
    result.update({
        "installation_verified": True,
        "desktop_name": "dsh-desktop-next",
        "desktop_version": "2.0.21-next",
        "runtime_version": "0.2.1-rc.4",
        "cordis_version": "4.0.5",
        "status": "metadata_only",
        "reason": "runtime_not_queried",
    })
    return result


def handle(request: dict) -> dict:
    identity = request.get("id")
    if not isinstance(identity, str) or not 1 <= len(identity) <= 100:
        return {"ok": False, "error": "invalid_request_id"}
    if set(request) - {"id", "op"}:
        return {"id": identity, "ok": False, "error": "unsupported_fields"}
    if request.get("op") == "describe":
        return {"id": identity, "ok": True, "result": describe()}
    # No browser/agent-supplied URL, command, token, profile or launch flag is
    # accepted. There is intentionally no override, even for an enabled policy.
    return {"id": identity, "ok": False, "error": "metadata_only",
            "termination_confirmed": False}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--describe", action="store_true")
    parser.add_argument("--serve", action="store_true")
    args = parser.parse_args()
    if args.describe == args.serve:
        parser.error("choose exactly one of --describe or --serve")
    if args.describe:
        print(json.dumps(describe(), ensure_ascii=True))
        return 0
    while True:
        raw = sys.stdin.buffer.readline(MAX_REQUEST_BYTES + 1)
        if not raw:
            return 0
        if len(raw) > MAX_REQUEST_BYTES or not raw.endswith(b"\n"):
            print(json.dumps({"ok": False, "error": "invalid_frame"}), flush=True)
            return 2
        try:
            request = json.loads(raw)
            if not isinstance(request, dict):
                raise ValueError("invalid request")
            response = handle(request)
        except (ValueError, UnicodeError):
            response = {"ok": False, "error": "invalid_request"}
        print(json.dumps(response, ensure_ascii=True), flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
