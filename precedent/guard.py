"""The execution gate: what an agent calls while an action runs, and once more after it is done.

`resolve()` decides whether an action may start. That is a policy gate: it cannot stop an
action that is already running, and it never looks at what actually happened. `check()` is
the second gate. The agent calls it with the decision id before each step that changes
something, and once more after Done:

    d = kb.resolve("Refund order 1182, the cake arrived damaged", run_id="run-42")
    # needs_approval: a person signs off (`precedent signoff D-...`), then the agent starts
    kb.check(d.id, "About to refund 1200 to the customer")              # before each step
    kb.check(d.id, "Refunded 1200 to the customer", stage="after")      # after Done

Every call ends in `continue` or `halt`. The checks run in order, the first failure wins,
and none of them calls a model:

    1. known       the decision exists                                        -> unknown_decision
    2. sticky      this decision or its run was halted before                 -> halted
    3. single use  the decision was not already checked after Done            -> already_done
    4. authorised  the decision was answer, or needs_approval with a sign-off -> not_authorized / no_sign_off
    5. fresh       the decision (or its sign-off) is younger than
                   runtime.decision_ttl_minutes (default 60)                  -> expired
    6. rules       every cited rule is still approved, same version and text  -> rule_changed
    7. scope       every amount and every high-risk action (bank, refund,
                   delete, the pack's own terms...) in the step appears in
                   the decision, its situation or its cited rules. Amounts
                   match however they are written (1,000 / 1k / one
                   thousand), and so do actions ("wire" is a transfer)    -> out_of_scope

Scope is what stops a decision being reused for something else: an answer about a nut allergy
cannot authorise "change the supplier's bank details", even though that step has no amount.

Each halt is a Halt record in logs/events.jsonl. It names the decision, the run, the cited
rule versions, the stage, the reason, and who created it: `precedent:check` for these checks,
or the person or probe that called `halt()`. Halts are sticky. Once a run is halted, every
later check in that run halts too, so an agent cannot retry its way past one.

Out-of-scope and external halts also open a gap. A person answers it once, the answer becomes
a draft rule fix, and approving the fix adds a regression case. That is the same loop as any
escalation.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from .ingest.redact import redact
from .loop import answer_gap
from .models import CONTINUE, ESCALATE, HALT, NEEDS_APPROVAL, STAGES, Decision, Gap, Halt, iso, new_id, utcnow, valid_id
from .runtime import _rule_text
from .scope import numbers, risk_terms
from .store import Store, StoreError

AUTO = "precedent:check"


@dataclass
class Check:
    """What the execution gate told an agent."""

    outcome: str                     # continue | halt
    decision_id: str
    stage: str
    run_id: str | None = None
    reasons: list[str] = field(default_factory=list)
    halt_id: str | None = None
    gap_id: str | None = None
    proposal_id: str | None = None   # a drafted rule fix, when a halt came with a correction

    @property
    def halted(self) -> bool:
        return self.outcome == HALT

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _clean(store: Store, text: str, *, required: bool, what: str = "step") -> str:
    """Length-check and redact free text before it is stored, like every situation."""
    text = (text or "").strip()
    if required and not text:
        raise ValueError(f"{what} is empty: say what you are about to do, or what you did")
    limit = int(store.config.get("runtime.max_situation_chars", 4000))
    if len(text) > limit:
        raise ValueError(f"{what} is {len(text)} characters; the limit is {limit} (runtime.max_situation_chars)")
    return redact(text) if store.config.get("runtime.redact", True) else text


def _ts(value: Any) -> datetime:
    dt = datetime.fromisoformat(str(value))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def check(store: Store, decision_id: str, step: str, *, stage: str = "during") -> Check:
    """Re-check a decision while its action runs (stage 'during') or after it is done ('after')."""
    if stage not in STAGES:
        raise ValueError(f"stage must be one of {STAGES}, got {stage!r}")
    step = _clean(store, step, required=True)
    decision_id = str(decision_id or "").strip()
    if not valid_id(decision_id):
        decision_id = "(invalid id)"   # agent-supplied: never store arbitrary text as an id

    try:
        d = store.decision(decision_id)
    except StoreError:
        c = Check(HALT, decision_id, stage)
        return _halt(store, c, None, "unknown_decision",
                     f"no decision {decision_id} was issued: consult the rulebook first", step, by=AUTO)

    c = Check(CONTINUE, d.id, stage, d.run_id)
    earlier = store.halts(decision_id=d.id, run_id=d.run_id)
    if earlier:
        h = earlier[0]
        scope = "decision" if h.decision_id == d.id else f"run {d.run_id}"
        c.outcome, c.halt_id, c.gap_id, c.proposal_id = HALT, h.id, h.gap_id, h.proposal_id
        c.reasons.append(f"halted: {h.id} ({h.reason}) already stopped this {scope}; a person starts a new run")
        return c

    done = [e for e in store.events("checked") if e.get("decision_id") == d.id and e.get("stage") == "after"]
    if done:
        return _halt(store, c, d, "already_done",
                     f"{d.id} was checked after Done at {done[0].get('ts')}: one decision covers one action, "
                     "so consult the rulebook again", step, by=AUTO)

    signed = store.sign_off_of(d.id)
    if d.outcome == ESCALATE:
        return _halt(store, c, d, "not_authorized",
                     f"{d.id} was escalate: the action should not have started", step, by=AUTO)
    if d.outcome == NEEDS_APPROVAL and not signed:
        return _halt(store, c, d, "no_sign_off",
                     f"{d.id} needs a person's sign-off and none is recorded (precedent signoff {d.id})", step, by=AUTO)

    ttl = timedelta(minutes=float(store.config.get("runtime.decision_ttl_minutes", 60)))
    since = _ts(signed["ts"]) if signed else _ts(d.ts)
    if utcnow() - since > ttl:
        return _halt(store, c, d, "expired",
                     f"{d.id} was {'signed off' if signed else 'issued'} at {since.isoformat()}, more than "
                     f"{ttl.total_seconds() / 60:g} minutes ago (runtime.decision_ttl_minutes)", step, by=AUTO)

    changed, texts = [], []
    for cite in d.cited:
        try:
            rule = store.rule(str(cite.get("id")))
        except StoreError:
            changed.append(f"{cite.get('id')} was removed")
            continue
        if rule.status != "approved":
            changed.append(f"{rule.id} is now {rule.status}")
        elif rule.version != cite.get("version"):
            changed.append(f"{rule.id} is now v{rule.version}, the decision used v{cite.get('version')}")
        elif cite.get("sha") and rule.fingerprint() != cite["sha"]:
            changed.append(f"{rule.id} v{rule.version} was edited without a version bump")
        texts.append(_rule_text(rule))
    if changed:
        return _halt(store, c, d, "rule_changed", "; ".join(changed), step, by=AUTO)

    authorised = " ".join([d.situation, d.answer, *texts])
    extra = sorted(numbers(step) - numbers(authorised))
    terms, synonyms = store.config.get("risk.high_terms") or [], store.config.get("risk.synonyms") or {}
    extra += sorted(risk_terms(step, terms, synonyms) - risk_terms(authorised, terms, synonyms))
    if extra:
        return _halt(store, c, d, "out_of_scope",
                     f"the step uses {', '.join(extra)}, which the decision did not authorise",
                     step, by=AUTO, open_gap=True)

    cited = ", ".join(f"{x.get('id')} v{x.get('version')}" for x in d.cited)
    c.reasons.append((f"rules {cited} unchanged; " if cited else "") + "within what the decision authorised")
    store.log_event("checked", decision_id=d.id, run_id=d.run_id, stage=stage, outcome=CONTINUE)
    return c


def halt(store: Store, decision_id: str, reason: str, by: str, *, stage: str = "during", step: str = "",
         correction: str = "") -> Check:
    """A person or an outside probe halts a running action.

    Records the halt, opens a gap, and stops the decision's whole run. With `correction` (what
    should happen instead) it also drafts a rule fix, so approving it adds a regression case."""
    if stage not in STAGES:
        raise ValueError(f"stage must be one of {STAGES}, got {stage!r}")
    reason = _clean(store, reason, required=True, what="reason")
    d = store.decision(decision_id)
    c = Check(HALT, d.id, stage, d.run_id)
    return _halt(store, c, d, "external", reason, _clean(store, step, required=False), by=by,
                 open_gap=True, correction=_clean(store, correction, required=False, what="correction"))


def sign_off(store: Store, decision_id: str, by: str) -> dict[str, Any]:
    """A person approves a needs_approval decision, so the execution gate lets it run."""
    d = store.decision(decision_id)
    if d.outcome != NEEDS_APPROVAL:
        raise StoreError(f"{d.id} is {d.outcome}; only needs_approval decisions take a sign-off")
    earlier = store.sign_off_of(d.id)
    if earlier:
        raise StoreError(f"{d.id} was already signed off by {earlier.get('by')} at {earlier.get('ts')}")
    halted = store.halts(decision_id=d.id, run_id=d.run_id)
    if halted:
        raise StoreError(f"{d.id} was halted before sign-off ({halted[0].id}, {halted[0].reason}); halts are final, "
                         "so ask the rulebook again in a new run")
    store.log_event("decision_signed_off", decision_id=d.id, run_id=d.run_id, by=by)
    return store.sign_off_of(d.id) or {}


def _halt(store: Store, c: Check, d: Decision | None, reason: str, detail: str, step: str, *, by: str,
          open_gap: bool = False, correction: str = "") -> Check:
    h = Halt(id=new_id("H"), ts=iso(utcnow()), decision_id=c.decision_id, reason=reason, stage=c.stage,
             run_id=c.run_id, detail=detail, by=by, step=step, rules=list(d.cited) if d else [])
    if d and open_gap:
        situation = f"{d.situation}\nHalted step: {step}" if step else d.situation
        gap = Gap(id=new_id("G"), ts=h.ts, situation=situation, reason="halted", decision_id=d.id,
                  considered=d.considered)
        store.log_event("gap_opened", gap=gap.to_dict())
        h.gap_id = gap.id
        if correction:
            live = {r.id for r in store.rules("approved")}
            target = next((x["id"] for x in d.cited if x.get("id") in live), None)
            h.proposal_id = answer_gap(store, gap.id, correction, by, target=target, include_similar=False).id
    store.log_event("halt", halt=h.to_dict())
    c.outcome, c.halt_id, c.gap_id, c.proposal_id = HALT, h.id, h.gap_id, h.proposal_id
    c.reasons.append(f"{reason}: {detail}")
    return c
