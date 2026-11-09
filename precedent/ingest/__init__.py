"""Turn raw team chat into Q&A episodes.

The process a business actually runs on is rarely written down. It lives in the
moments where someone asks "how do we handle X?" and someone else answers. Ingest
finds those moments; mining turns them into draft rules.
"""

from __future__ import annotations

from pathlib import Path

from ..models import Message
from .episodes import build_episodes, is_question
from .jsonl import parse_jsonl
from .redact import redact
from .slack import parse_slack
from .whatsapp import parse_whatsapp

__all__ = ["load_messages", "build_episodes", "is_question", "redact"]


def load_messages(path: Path, fmt: str = "auto", channel: str | None = None,
                  dayfirst: bool | None = None) -> list[Message]:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(2, "No such file or directory", str(path))
    if fmt == "auto":
        if path.is_dir() or path.suffix == ".json":
            fmt = "slack"
        elif path.suffix == ".jsonl":
            fmt = "jsonl"
        else:
            fmt = "whatsapp"
    channel = channel or path.stem
    if fmt == "whatsapp":
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        return parse_whatsapp(text, channel=channel, dayfirst=dayfirst)
    if fmt == "slack":
        return parse_slack(path, channel=channel)
    if fmt == "jsonl":
        return parse_jsonl(path, channel=channel)
    raise ValueError(f"unknown format {fmt!r} (expected whatsapp | slack | jsonl)")
