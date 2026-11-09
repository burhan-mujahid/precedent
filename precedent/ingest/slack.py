"""Slack workspace export parser.

Accepts either the export root (channel folders of daily JSON files), a single channel
folder, or a single JSON file. Native threads (thread_ts) are preserved, which makes
episode building exact instead of time-window based.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from ..models import Message

_MENTION = re.compile(r"<@([A-Z0-9]+)>")
_LINK = re.compile(r"<(https?://[^|>]+)(?:\|([^>]+))?>")


def _users(root: Path) -> dict[str, str]:
    path = root / "users.json"
    if not path.exists():
        return {}
    users = json.loads(path.read_text(encoding="utf-8"))
    return {u["id"]: (u.get("real_name") or u.get("name") or u["id"]) for u in users}


def _clean(text: str, users: dict[str, str]) -> str:
    text = _MENTION.sub(lambda m: "@" + users.get(m[1], m[1]), text)
    text = _LINK.sub(lambda m: m[2] or m[1], text)
    return text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").strip()


def _files(path: Path) -> list[tuple[str, Path]]:
    if path.is_file():
        return [(path.parent.name, path)]
    channel_dirs = [d for d in path.iterdir() if d.is_dir() and any(d.glob("*.json"))]
    if channel_dirs:   # export root: one folder per channel
        return [(d.name, f) for d in sorted(channel_dirs) for f in sorted(d.glob("*.json"))]
    return [(path.name, f) for f in sorted(path.glob("*.json")) if f.name not in ("users.json", "channels.json")]


def parse_slack(path: Path, channel: str | None = None) -> list[Message]:
    path = Path(path)
    root = path if path.is_dir() else path.parent
    users = _users(root) or _users(root.parent)
    out: list[Message] = []
    for chan, f in _files(path):
        try:
            records = json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if not isinstance(records, list):
            continue
        for r in records:
            if not isinstance(r, dict) or "ts" not in r:
                continue
            if r.get("type") != "message" or r.get("subtype") in ("channel_join", "channel_leave", "bot_message"):
                continue
            text = _clean(r.get("text", ""), users)
            if not text:
                continue
            ts = datetime.fromtimestamp(float(r["ts"]), tz=timezone.utc).replace(tzinfo=None)
            user = r.get("user", "unknown")
            author = str((r.get("user_profile") or {}).get("real_name") or users.get(user, user))
            name = channel if (channel and path.is_file()) else chan
            out.append(Message(id=f"{name}:{r['ts']}", ts=ts.isoformat(), author=author, text=text,
                               channel=name, thread=r.get("thread_ts")))
    out.sort(key=lambda m: m.ts)
    return out
