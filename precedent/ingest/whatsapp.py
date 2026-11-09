"""WhatsApp "Export chat" (.txt) parser.

Handles the formats Android and iOS produce across locales:
    09/11/2026, 10:49 - Sara: text
    [09/11/2026, 10:49:12] Sara: text
    11/9/26, 10:49 AM - Sara: text
    2026-11-09 10:49 - Sara: text
Multi-line messages are joined; system lines ("Sara added Ali") and media
placeholders are dropped. Day/month order is inferred from the file itself.
"""

from __future__ import annotations

import re
from datetime import datetime

from ..models import Message

_LINE = re.compile(
    r"^‎?\[?(?P<date>\d{1,4}[./-]\d{1,2}[./-]\d{1,4}),?\s+"
    r"(?P<time>\d{1,2}[:.]\d{2}(?:[:.]\d{2})?(?:\s?[  ]?[AaPp]\.?[Mm]\.?)?)\]?"
    r"\s*(?:-|–)?\s*(?P<rest>.*)$"
)
_AUTHOR = re.compile(r"^(?P<author>[^:]{1,60}):\s?(?P<text>.*)$", re.S)
# Byte-order mark and Unicode bidi controls (LRM, RLM, embeddings, isolates). Phones insert them
# around names and timestamps, especially in RTL (Urdu, Arabic) chats; they carry no meaning.
_INVISIBLE = re.compile("[\ufeff\u200e\u200f\u202a-\u202e\u2066-\u2069]")
_SKIP = ("<media omitted>", "image omitted", "video omitted", "audio omitted", "sticker omitted",
         "document omitted", "gif omitted", "this message was deleted", "you deleted this message",
         "messages and calls are end-to-end encrypted", "<this message was edited>")


def _infer_dayfirst(dates: list[str]) -> bool:
    for d in dates:
        parts = re.split(r"[./-]", d)
        if len(parts[0]) == 4:          # ISO-ish: year first
            continue
        a, b = int(parts[0]), int(parts[1])
        if a > 12:
            return True
        if b > 12:
            return False
    return True   # most of the world


def _parse_dt(date_s: str, time_s: str, dayfirst: bool) -> datetime:
    parts = [int(p) for p in re.split(r"[./-]", date_s)]
    if len(str(parts[0])) == 4 or parts[0] > 31:
        y, m, d = parts
    else:
        d, m, y = parts if dayfirst else (parts[1], parts[0], parts[2])
    if y < 100:
        y += 2000
    t = time_s.replace(" ", " ").replace(".", ":").strip().upper().replace("A:M:", "AM").replace("P:M:", "PM")
    t = t.replace("A:M", "AM").replace("P:M", "PM")
    pm = t.endswith("PM")
    am = t.endswith("AM")
    t = t.replace("AM", "").replace("PM", "").strip()
    hms = [int(x) for x in t.split(":")]
    hh, mm, ss = hms[0], hms[1], (hms[2] if len(hms) > 2 else 0)
    if pm and hh < 12:
        hh += 12
    if am and hh == 12:
        hh = 0
    return datetime(y, m, d, hh, mm, ss)


def parse_whatsapp(text: str, channel: str = "whatsapp", dayfirst: bool | None = None) -> list[Message]:
    raw: list[tuple[str, str, str]] = []   # (date, time, rest)
    for line in _INVISIBLE.sub("", text).splitlines():   # splitlines also handles \r\n exports
        m = _LINE.match(line)
        if m:
            raw.append((m["date"], m["time"], m["rest"]))
        elif raw:
            d, t, rest = raw[-1]
            raw[-1] = (d, t, rest + "\n" + line)
    if dayfirst is None:
        dayfirst = _infer_dayfirst([r[0] for r in raw])

    messages: list[Message] = []
    for n, (d, t, rest) in enumerate(raw, 1):
        am = _AUTHOR.match(rest)
        if not am:
            continue   # system message
        body = am["text"].strip()
        if not body or body.lower() in _SKIP or body.lower().endswith(" omitted"):
            continue
        try:
            ts = _parse_dt(d, t, dayfirst)
        except ValueError:
            continue
        messages.append(Message(id=f"{channel}:{n}", ts=ts.isoformat(), author=am["author"].strip(),
                                text=body, channel=channel))
    return messages
