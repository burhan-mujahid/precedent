"""Find the "how do we handle X?" moments and the answers that followed.

With native threads (Slack) an episode is simply a thread whose root is a question.
Without them (WhatsApp) we take a question and the replies from *other people*
within a time window, stopping at the next unrelated question. That is a heuristic,
so it is tuned for recall: the review step is where precision comes from.
"""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import datetime, timedelta

from ..models import Episode, Message
from .redact import redact

# Questions that ask about *how the business runs*, not chit-chat.
_PROCESS_Q = re.compile(
    r"\b(how (do|should|can|did) (we|i|you)|what('?s| is| do| should) (the|we|i|our)|what do we do|"
    r"who (handles|deals|approves|is responsible|should)|do we|should (we|i)|can (we|i)|are we allowed|"
    r"is it ok(ay)?|what if|what happens (if|when)|where do (we|i)|which (one|supplier|account|template)|"
    r"policy|process|procedure|rule for|allowed to)\b",
    re.I,
)
# Urdu script and Roman Urdu cues (how / what / who / should / can / may we). They only count
# together with a question mark, so common words like "kya" do not turn chatter into questions.
_PROCESS_Q_UR = re.compile(
    r"(کیسے|کیا\s+کر|کیا\s+ہم|کون|چاہیے|سکتے|اجازت|طریقہ)|"
    r"\b(kaise|kaisay|kese|kya (karte|karein|karen|karna|hum|ham)|kon|kaun|chahiye|chahye|"
    r"(kar|de|le) sakte|sakte hain|ijazat|tareeqa|tarika)\b",
    re.I,
)
_QMARKS = ("?", "\u061f", "\uff1f")   # ASCII, Arabic/Urdu, full-width
_ACK = re.compile(r"^\W*(ok(ay)?|k|thanks?|thank you|thx|ty|noted|got it|perfect|great|cool|on it|will do|done|sure|"
                  r"theek|thik|ji|shukriya|ٹھیک|شکریہ|جی)\b|^\W+$", re.I)
_CHITCHAT = re.compile(r"\b(lunch|coffee|weekend|birthday|movie|how are you|you there|anyone free)\b", re.I)
# Unambiguous process wording. When present, chit-chat words do not veto the question
# ("how do we handle a birthday cake order?" is about the business, "should we get coffee?" is not).
_STRONG = re.compile(r"\b(how (do|should) we|what do we do|what('?s| is) (the|our) (process|policy|procedure|rule)|"
                     r"who (handles|approves|deals with)|policy|procedure)\b", re.I)


def is_question(text: str) -> bool:
    t = text.strip()
    if len(t) < 12 or (_CHITCHAT.search(t) and not _STRONG.search(t)):
        return False
    if not any(q in t for q in _QMARKS):
        return False
    return bool(_PROCESS_Q.search(t) or _PROCESS_Q_UR.search(t))


def _dt(m: Message) -> datetime:
    return datetime.fromisoformat(m.ts)


def build_episodes(messages: list[Message], window_hours: float = 12, max_replies: int = 8,
                   redact_pii: bool = True) -> list[Episode]:
    if redact_pii:
        messages = [replace(m, text=redact(m.text)) for m in messages]

    episodes: list[Episode] = []

    # 1) Native threads.
    threaded: dict[str, list[Message]] = {}
    for m in messages:
        if m.thread:
            threaded.setdefault(m.thread, []).append(m)
    for _tid, msgs in threaded.items():
        msgs.sort(key=lambda m: m.ts)
        root, answers = msgs[0], msgs[1:]
        if is_question(root.text) and any(r.author != root.author for r in answers):
            episodes.append(Episode(id=f"ep:{root.id}", channel=root.channel, question=root,
                                    replies=answers[:max_replies]))

    # 2) Flat chats: time window after each question.
    flat = [m for m in messages if not m.thread]
    window = timedelta(hours=window_hours)
    for i, q in enumerate(flat):
        if not is_question(q.text):
            continue
        replies: list[Message] = []
        for j in range(i + 1, len(flat)):   # index loop: slicing here made big exports quadratic
            m = flat[j]
            if _dt(m) - _dt(q) > window or len(replies) >= max_replies:
                break
            if replies and _dt(m) - _dt(replies[-1]) > timedelta(hours=2):
                break   # the conversation moved on
            if m.author == q.author and replies and _ACK.search(m.text.strip()) and len(m.text.split()) <= 4:
                break   # the asker acknowledged the answer: episode over
            if m.author != q.author and is_question(m.text) and not replies:
                # Someone else asked a new question before anyone answered this one.
                break
            if m.author == q.author and is_question(m.text):
                break
            replies.append(m)
        # An episode needs at least one answer from someone other than the asker.
        if any(r.author != q.author for r in replies):
            episodes.append(Episode(id=f"ep:{q.id}", channel=q.channel, question=q, replies=replies))
    return episodes


def episode_to_dict(ep: Episode) -> dict:
    return {"id": ep.id, "channel": ep.channel, "question": ep.question.to_dict(),
            "replies": [r.to_dict() for r in ep.replies]}


def episode_from_dict(d: dict) -> Episode:
    return Episode(id=d["id"], channel=d["channel"], question=Message.from_dict(d["question"]),
                   replies=[Message.from_dict(r) for r in d["replies"]])
