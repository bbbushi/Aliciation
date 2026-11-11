"""Compatibility metadata; interaction guidance lives in constitution module 3."""
from .constitution import VERSION, metadata as _metadata

GUIDANCE = ""


def metadata():
    return {**_metadata(), "source": "constitution.json:3"}
