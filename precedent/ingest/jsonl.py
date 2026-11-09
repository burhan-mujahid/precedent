"""Generic JSONL input, for anything else (email, Teams, Discord, a CRM's notes).

One object per line: {"ts": "2026-11-09T10:49:00", "author": "Sara", "text": "...",
"thread": "optional-thread-id", "id": "optional"}.

`ts` may be ISO-8601 (with or without a timezone, "Z" allowed) or Unix epoch seconds.
Timezone-aware times are converted to UTC so they can be compared with each other.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..models import Message


def _ts(value: Any) -> str:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        dt = datetime.fromtimestamp(float(value), tz=timezone.utc)
    elif isinstance(value, str) and value.strip():
        s = value.strip()
        dt = datetime.fromisoformat(s[:-1] + "+00:00" if s.endswith(("Z", "z")) else s)   # "Z": Python 3.10
    else:
        raise ValueError(f"ts must be an ISO-8601 string or epoch seconds, got {value!r}")
    if dt.tzinfo:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.isoformat()


def parse_jsonl(path: Path, channel: str = "jsonl") -> list[Message]:
    out = []
    for n, line in enumerate(Path(path).read_text(encoding="utf-8-sig").splitlines(), 1):
        if not line.strip():
            continue
        try:
            r = json.loads(line)
            if not isinstance(r, dict):
                raise ValueError("expected a JSON object")
            if not isinstance(r.get("text"), str):
                raise ValueError("missing 'text'")
            ts = _ts(r.get("ts"))
        except ValueError as exc:   # json.JSONDecodeError is a ValueError
            raise ValueError(f"{path}, line {n}: {exc}") from None
        if not r["text"].strip():
            continue
        thread = r.get("thread")
        out.append(Message(id=str(r.get("id") or f"{channel}:{n}"), ts=ts, author=str(r.get("author") or "unknown"),
                           text=r["text"], channel=str(r.get("channel") or channel),
                           thread=str(thread) if thread is not None else None))
    out.sort(key=lambda m: m.ts)
    return out
