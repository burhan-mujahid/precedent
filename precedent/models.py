"""Core data model.

Everything Precedent stores is one of these records. They are plain dataclasses that
round-trip through dicts, so the on-disk formats (YAML for things humans edit,
JSONL for append-only logs) stay boring and diffable.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
from dataclasses import asdict, dataclass, field, fields
from datetime import date, datetime, timedelta, timezone
from typing import Any, TypeVar, overload

RISK_LEVELS = ("low", "medium", "high")
RULE_STATUSES = ("draft", "approved", "deprecated")

# Outcomes a resolver can return. Anything other than ANSWER means a human is involved.
ANSWER = "answer"
ESCALATE = "escalate"
NEEDS_APPROVAL = "needs_approval"

# Outcomes of the execution gate (guard.check), called while an action runs and after it is done.
CONTINUE = "continue"
HALT = "halt"
STAGES = ("during", "after")

# Why a running action was halted.
HALT_REASONS = (
    "unknown_decision",  # the agent is acting on a decision id the rulebook never issued
    "halted",            # this decision or its run was already halted; halts are sticky
    "not_authorized",    # the decision was escalate: the action should never have started
    "no_sign_off",       # needs_approval, but no person has signed it off
    "already_done",      # the action was already checked after Done: one decision, one action
    "expired",           # the decision (or its sign-off) is older than runtime.decision_ttl_minutes
    "rule_changed",      # a cited rule was edited or retired after the decision was made
    "out_of_scope",      # the step uses an amount the decision did not authorise
    "external",          # a person or an outside probe halted it
)

# Why an agent was not allowed to act on its own. Every one of these opens a gap.
GAP_REASONS = (
    "no_coverage",      # nothing in the rulebook is about this situation
    "low_confidence",   # rules exist but the match is weak for this risk tier
    "conflict",         # two approved rules disagree
    "model_unsure",     # the model said the rules do not settle it
    "ungrounded",       # the model cited rules it was not given
    "human_override",   # a person overrode a decision after the fact
    "reasoner_error",   # the model call failed (outage, quota, bad reply): fail closed
    "halted",           # a running action was halted (out of scope, or by a person or probe)
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


@overload
def iso(dt: datetime | date) -> str: ...
@overload
def iso(dt: None) -> None: ...
def iso(dt: datetime | date | None) -> str | None:
    return dt.isoformat() if dt else None


_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")


def valid_id(value: object) -> bool:
    """Ids become file names, so anything that could escape a directory ('../x', 'a/b') is refused."""
    return isinstance(value, str) and bool(_SAFE_ID.fullmatch(value)) and ".." not in value


def new_id(prefix: str) -> str:
    """Sortable, collision-resistant id: P-20261109T1103-a3f9c41b07."""
    return f"{prefix}-{utcnow().strftime('%Y%m%dT%H%M')}-{secrets.token_hex(5)}"


_T = TypeVar("_T")


def _from_dict(cls: type[_T], data: dict[str, Any]) -> _T:
    known = {f.name for f in fields(cls)}  # type: ignore[arg-type]
    return cls(**{k: v for k, v in data.items() if k in known})


@dataclass
class Message:
    """One chat message, normalised from any source."""

    id: str
    ts: str               # ISO-8601
    author: str
    text: str
    channel: str = ""
    thread: str | None = None   # native thread id when the source has one (Slack)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Message:
        return _from_dict(cls, d)


@dataclass
class Episode:
    """A question and the replies that answered it: the unit we mine rules from."""

    id: str
    channel: str
    question: Message
    replies: list[Message] = field(default_factory=list)

    @property
    def messages(self) -> list[Message]:
        return [self.question, *self.replies]


@dataclass
class Rule:
    """A single operating rule: in situation WHEN, do THEN, because WHY."""

    id: str
    title: str
    when: str
    then: str
    why: str = ""
    exceptions: list[str] = field(default_factory=list)
    risk: str = "low"
    status: str = "draft"
    owner: str = ""
    approved_by: str = ""          # who approved this version
    tags: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    version: int = 1
    created: str = ""
    updated: str = ""
    review_by: str = ""

    def __post_init__(self) -> None:
        # Hand-edited YAML: an unquoted 2026-12-01 loads as a date, a bare number as an int.
        for name in ("created", "updated", "review_by"):
            value = getattr(self, name)
            if isinstance(value, (date, datetime)):
                setattr(self, name, value.isoformat()[:10])
            elif value is None:
                setattr(self, name, "")
        for name in ("title", "when", "then", "why", "owner", "approved_by"):
            value = getattr(self, name)
            if value is None:
                setattr(self, name, "")
            elif not isinstance(value, str):
                setattr(self, name, str(value))
        for name in ("exceptions", "tags", "sources"):
            value = getattr(self, name)
            if value is None:
                setattr(self, name, [])
            elif isinstance(value, str):
                setattr(self, name, [value])
        for name in ("created", "updated", "review_by"):
            value = getattr(self, name)
            if value:
                try:
                    date.fromisoformat(value[:10])
                except ValueError:
                    raise ValueError(f"rule {self.id}: {name} must be a date like 2026-12-31, got {value!r}") from None
        if not valid_id(self.id):
            raise ValueError(f"rule id {self.id!r} must be letters, digits, '-', '_' or '.' (it becomes a file name)")
        if self.risk not in RISK_LEVELS:
            raise ValueError(f"rule {self.id}: risk must be one of {RISK_LEVELS}, got {self.risk!r}")
        if self.status not in RULE_STATUSES:
            raise ValueError(f"rule {self.id}: status must be one of {RULE_STATUSES}, got {self.status!r}")
        if not self.when.strip() or not self.then.strip():
            raise ValueError(f"rule {self.id}: 'when' and 'then' are required")

    def touch(self, review_days: int) -> None:
        today = utcnow().date()
        self.updated = today.isoformat()
        self.created = self.created or self.updated
        self.review_by = (today + timedelta(days=review_days)).isoformat()

    def is_stale(self, today: date | None = None) -> bool:
        if not self.review_by:
            return False
        return date.fromisoformat(self.review_by) < (today or utcnow().date())

    def search_text(self) -> str:
        return " ".join([self.title, self.when, self.then, " ".join(self.tags), " ".join(self.exceptions)])

    def fingerprint(self) -> str:
        """Short hash of what the rule says. Logged with each decision, so a rule file edited by hand
        without a version bump still shows up in the audit trail as different content."""
        body = json.dumps([self.when, self.then, self.why, self.exceptions, self.risk], ensure_ascii=False)
        return hashlib.sha256(body.encode("utf-8")).hexdigest()[:12]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Rule:
        return _from_dict(cls, d)


@dataclass
class Proposal:
    """A suggested change to the rulebook. Nothing reaches the rulebook without one.

    kind:
      create     - add `rule` as a new rule
      update     - replace fields of `target` with those in `rule`
      deprecate  - retire `target`
    origin: where the suggestion came from (mine | gap | wrap | drift | manual)
    """

    id: str
    kind: str
    rule: dict[str, Any] = field(default_factory=dict)
    target: str | None = None
    rationale: str = ""
    evidence: list[str] = field(default_factory=list)
    origin: str = "manual"
    status: str = "pending"      # pending | approved | rejected
    created: str = ""
    decided_by: str = ""
    decided_at: str = ""
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Proposal:
        return _from_dict(cls, d)


@dataclass
class Decision:
    """What the runtime told an agent, and why. Appended to the decision log."""

    id: str
    ts: str
    situation: str
    outcome: str                     # answer | escalate | needs_approval
    answer: str = ""
    cited: list[dict[str, Any]] = field(default_factory=list)   # [{id, version}]
    confidence: float = 0.0
    risk: str = "low"
    reasons: list[str] = field(default_factory=list)
    considered: list[str] = field(default_factory=list)
    gap_id: str | None = None
    provider: str = ""
    run_id: str | None = None        # groups the decisions of one agent run, so a halt can stop all of it

    @property
    def ok(self) -> bool:
        return self.outcome == ANSWER

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Decision:
        return _from_dict(cls, d)


@dataclass
class Gap:
    """A case the agents could not handle alone. Gaps are next week's KB update."""

    id: str
    ts: str
    situation: str
    reason: str
    decision_id: str | None = None
    considered: list[str] = field(default_factory=list)
    status: str = "open"             # open | answered (proposal pending) | resolved | dismissed
    resolution: str = ""
    resolved_by: str = ""
    resolved_at: str = ""
    proposal_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Gap:
        return _from_dict(cls, d)


@dataclass
class Halt:
    """The record of a halted action: which decision, which run, which rule versions, at what stage,
    why, and who created it. Appended to logs/events.jsonl and never edited."""

    id: str
    ts: str
    decision_id: str
    reason: str                      # one of HALT_REASONS
    stage: str = "during"            # during | after
    run_id: str | None = None
    detail: str = ""
    by: str = ""                     # precedent:check for automatic halts; the person or probe otherwise
    step: str = ""                   # what the agent was doing or reported doing (redacted)
    rules: list[dict[str, Any]] = field(default_factory=list)   # the decision's cited rules [{id, version, sha}]
    gap_id: str | None = None
    proposal_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Halt:
        return _from_dict(cls, d)
