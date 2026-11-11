"""The learning loop. One door into the rulebook: a reviewed proposal.

    chat threads ──mine──▶ proposals ◀──resolve── gaps ◀── runtime escalations
    session notes ─wrap──▶ proposals                  ◀── human overrides
    drift checks ─────────▶ proposals
                               │
                         human review (approve / edit / reject)
                               ▼
                           rulebook ──▶ golden evals (every resolved gap becomes a test)

Models draft, people decide. Nothing is ever written to the rulebook directly.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any

from .ingest.episodes import episode_to_dict
from .llm import LLMError
from .models import RISK_LEVELS, Episode, Gap, Proposal, Rule, iso, new_id, utcnow
from .reasoner import Reasoner, _title, classify_risk
from .retrieve import jaccard
from .store import Store, StoreError

EDITABLE = ("title", "when", "then", "why", "exceptions", "risk", "tags", "owner")


def _risk(store: Store, text: str) -> str:
    return classify_risk(text, store.config.get("risk.high_terms") or [], store.config.get("risk.medium_terms") or [])


def _clean_rule_fields(raw: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k in EDITABLE:
        if k in raw and raw[k] not in (None, ""):
            out[k] = raw[k]
    if "risk" in out and out["risk"] not in RISK_LEVELS:
        out["risk"] = "high"   # unknown risk is treated as the worst case
    for k in ("exceptions", "tags"):
        if k in out and isinstance(out[k], str):
            out[k] = [out[k]]
    return out


# ----------------------------------------------------------------------------- mining

@dataclass
class MineReport:
    messages: int = 0
    episodes: int = 0
    proposed: int = 0
    updates: int = 0
    known: int = 0
    duplicates: int = 0
    ungrounded: int = 0
    already_mined: int = 0
    errors: list[str] = field(default_factory=list)


def _fingerprint(question: dict[str, Any]) -> tuple[str, str, str]:
    return (str(question.get("ts", "")), str(question.get("author", "")), str(question.get("text", ""))[:500])


def mine(store: Store, reasoner: Reasoner, episodes: Iterable[Episode],
         progress: Callable[[int, int], None] | None = None) -> MineReport:
    rep = MineReport()
    approved = store.rules("approved")
    pending = store.proposals("pending")
    dup_threshold = float(store.config.get("drift.duplicate_similarity", 0.6))
    # WhatsApp exports are cumulative: next month's export repeats this month's threads. Skip
    # episodes already mined so re-ingesting costs nothing and does not fake "reconfirmations".
    episodes = list(episodes)
    seen = {_fingerprint(e["question"]) for e in store._read_jsonl(store.episodes_path) if "question" in e}
    fresh = [e for e in episodes if _fingerprint(e.question.to_dict()) not in seen]
    rep.already_mined = len(episodes) - len(fresh)
    store.save_episodes(episode_to_dict(e) for e in fresh)

    # Model calls are slow and independent, so extract in parallel; results are then handled in
    # chat order, which keeps de-duplication and proposal order deterministic.
    workers = max(1, int(store.config.get("ingest.workers", 4)))
    outcomes: list[list[Any] | Exception] = [[] for _ in fresh]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(reasoner.extract, ep): i for i, ep in enumerate(fresh)}
        for done, fut in enumerate(as_completed(futures), 1):
            i = futures[fut]
            try:
                result = fut.result()
                outcomes[i] = result if isinstance(result, list) else []
            except LLMError as exc:
                if exc.status in (401, 403, 404):   # bad key or model: every other episode would fail too
                    for f in futures:
                        f.cancel()
                    raise
                outcomes[i] = exc
            except Exception as exc:   # one bad episode must not stop a batch
                outcomes[i] = exc
            if progress:
                progress(done, len(fresh))

    for ep, outcome in zip(fresh, outcomes, strict=True):
        rep.episodes += 1
        valid_ids = {m.id for m in ep.messages}
        if isinstance(outcome, Exception):
            rep.errors.append(f"{ep.id}: {outcome}")
            continue
        candidates = outcome
        for c in candidates if isinstance(candidates, list) else []:
            if not isinstance(c, dict):
                rep.ungrounded += 1
                continue
            raw_ev = c.get("evidence") or []
            raw_ev = [raw_ev] if isinstance(raw_ev, str) else raw_ev
            evidence = [e for e in raw_ev if isinstance(e, str) and e in valid_ids] if isinstance(raw_ev, list) else []
            if not evidence:
                rep.ungrounded += 1          # the model invented its sources: drop it
                continue
            fields_ = _clean_rule_fields(c)
            if "when" not in fields_ or "then" not in fields_:
                rep.ungrounded += 1
                continue
            # Risk floor: the model's rating, but never below the keyword check (money, health, legal...).
            floor = _risk(store, fields_["when"] + " " + fields_["then"])
            fields_["risk"] = max(fields_.get("risk", floor), floor, key=RISK_LEVELS.index)
            fields_.setdefault("title", _title(fields_["when"]))
            fields_["sources"] = evidence

            if any(jaccard(fields_["when"], p.rule.get("when", "")) >= dup_threshold for p in pending):
                rep.duplicates += 1
                continue
            match = max(approved, key=lambda r: jaccard(fields_["when"], r.when), default=None)
            if match and jaccard(fields_["when"], match.when) >= dup_threshold:
                if jaccard(fields_["then"], match.then) >= 0.6:
                    rep.known += 1           # the team still works this way: good, nothing to do
                    store.log_event("rule_reconfirmed", rule_id=match.id, evidence=evidence)
                    continue
                p = Proposal(id=new_id("P"), kind="update", target=match.id,
                             rule={k: fields_[k] for k in ("then", "why", "exceptions", "sources") if k in fields_},
                             rationale=f"Chat shows a different practice than {match.id}",
                             evidence=evidence, origin="mine")
                rep.updates += 1
            else:
                p = Proposal(id=new_id("P"), kind="create", rule=fields_,
                             rationale=f"Team answered this in chat (confidence {c.get('confidence', '?')})",
                             evidence=evidence, origin="mine")
                rep.proposed += 1
            store.save_proposal(p)
            pending.append(p)
    return rep


# ----------------------------------------------------------------------------- review

def label(store: Store, p: Proposal) -> str:
    """One-line human description of a proposal."""
    if p.rule.get("title"):
        return p.rule["title"]
    if p.target:
        try:
            return store.rule(p.target).title
        except StoreError:
            return p.target
    return ""


def approvers(store: Store, p: Proposal) -> set[str] | None:
    """Who may approve this proposal, or None when anyone may.

    A change to an existing rule needs that rule's owner, someone listed for them under
    `rules.delegates`, or someone in `rules.admins`. New rules, and rules with no owner (or a
    starter-pack owner), can be approved by any reviewer. `rules.owner_approval: false` turns
    this off."""
    if not store.config.get("rules.owner_approval", True) or p.kind == "create" or not p.target:
        return None
    try:
        owner = store.rule(p.target).owner.strip()
    except StoreError:
        return None
    if not owner or owner.startswith("pack:"):
        return None
    delegates = (store.config.get("rules.delegates") or {}).get(owner) or []
    return {owner, *delegates, *(store.config.get("rules.admins") or [])}


def _same_person(a: str, b: str) -> bool:
    return a.strip().casefold() == b.strip().casefold()


def approve(store: Store, pid: str, by: str, edits: dict[str, Any] | None = None) -> Rule:
    with store.lock():
        return _approve(store, pid, by, edits)


def _approve(store: Store, pid: str, by: str, edits: dict[str, Any] | None = None) -> Rule:
    p = store.proposal(pid)
    if p.status != "pending":
        raise StoreError(f"{pid} is already {p.status}")
    allowed = approvers(store, p)
    if allowed is not None and not any(_same_person(by, a) for a in allowed):
        owner = store.rule(p.target or "").owner
        raise StoreError(f"{pid} changes {p.target}, which {owner} owns: only they can approve it "
                         f"(or someone under rules.delegates.{owner} or rules.admins in precedent.yaml). "
                         f"You are {by}.")
    review_days = int(store.config.get("rules.review_days", 90))
    fields_ = {**p.rule, **(edits or {})}
    clean = _clean_rule_fields(fields_)

    if p.kind != "create" and not p.target:
        raise StoreError(f"{pid} is a {p.kind} proposal with no target rule")
    target = p.target or ""
    if p.kind == "create":
        rule = Rule(id=store.next_rule_id(), status="approved", owner=clean.pop("owner", by),
                    sources=list(fields_.get("sources", [])) + [pid], **clean)
        rule.touch(review_days)
    elif p.kind == "update":
        rule = store.rule(target)
        for k, v in clean.items():
            setattr(rule, k, v)
        rule.sources = list(dict.fromkeys([*rule.sources, *fields_.get("sources", []), pid]))
        rule.status = "approved"
        rule.version += 1
        rule.touch(review_days)
        rule = Rule.from_dict(rule.to_dict())   # re-validate (and normalise) after edits
    elif p.kind == "deprecate":
        rule = store.rule(target)
        rule.status = "deprecated"
        rule.version += 1
        rule.touch(review_days)
    elif p.kind == "reconfirm":
        rule = store.rule(target)
        rule.touch(review_days)          # same content, fresh review date
    else:
        raise StoreError(f"unknown proposal kind {p.kind}")

    rule.approved_by = by       # who permitted this version
    store.save_rule(rule)
    p.status, p.decided_by, p.decided_at = "approved", by, iso(utcnow())
    if edits:
        p.note = f"edited on approval: {', '.join(sorted(edits))}"
    store.save_proposal(p)
    store.log_event("proposal_approved", proposal_id=pid, rule_id=rule.id, version=rule.version, by=by)

    # Close the loop: gaps this answered are resolved and become regression tests.
    for gid in [e for e in p.evidence if e.startswith("G-")]:
        try:
            gap = store.gap(gid)
        except StoreError:
            continue
        store.log_event("gap_resolved", gap_id=gid, proposal_id=pid, by=by, resolution=rule.then)
        if rule.status == "approved":
            store.add_golden({"id": f"E-{gid[2:]}", "situation": gap.situation, "expect": "covered",
                              "rules": [rule.id], "source": gid, "added": iso(utcnow())})
    return rule


def reject(store: Store, pid: str, by: str, note: str = "") -> Proposal:
    p = store.proposal(pid)
    if p.status != "pending":
        raise StoreError(f"{pid} is already {p.status}")
    p.status, p.decided_by, p.decided_at, p.note = "rejected", by, iso(utcnow()), note
    store.save_proposal(p)
    store.log_event("proposal_rejected", proposal_id=pid, by=by, note=note)
    for gid in [e for e in p.evidence if e.startswith("G-")]:
        store.log_event("gap_reopened", gap_id=gid)
    return p


# ----------------------------------------------------------------------------- gaps

def similar_gaps(store: Store, gid: str, threshold: float = 0.35) -> list[str]:
    base = store.gap(gid)
    return [g.id for g in store.gaps("open") if g.id != gid and jaccard(g.situation, base.situation) >= threshold]


def answer_gap(store: Store, gid: str, answer: str, by: str, *, target: str | None = None,
               when: str | None = None, risk: str | None = None, include_similar: bool = True) -> Proposal:
    """A person answers what the agent could not. That answer becomes a proposal."""
    gap = store.gap(gid)
    if gap.status not in ("open",):
        raise StoreError(f"{gid} is {gap.status}")
    group = [gid, *(similar_gaps(store, gid) if include_similar else [])]
    if target:
        rule = store.rule(target)
        p = Proposal(id=new_id("P"), kind="update", target=target,
                     rule={"exceptions": [*rule.exceptions, f"{when or gap.situation}: {answer}"]},
                     rationale=f"Agents escalated {len(group)} case(s) that {target} did not settle",
                     evidence=group, origin="gap")
    else:
        p = Proposal(id=new_id("P"), kind="create",
                     rule={"title": _title((when or gap.situation).splitlines()[0]), "when": when or gap.situation,
                           "then": answer, "risk": risk or _risk(store, gap.situation + " " + answer)},
                     rationale=f"Agents escalated {len(group)} case(s) with no rule ({gap.reason})",
                     evidence=group, origin="gap")
    store.save_proposal(p)
    for g in group:
        store.log_event("gap_answered", gap_id=g, proposal_id=p.id, by=by, resolution=answer)
    return p


def dismiss_gap(store: Store, gid: str, by: str, note: str = "") -> None:
    store.gap(gid)
    store.log_event("gap_dismissed", gap_id=gid, by=by, resolution=note or "one-off, not a rule")


# ----------------------------------------------------------------------------- feedback

def feedback(store: Store, decision_id: str, by: str, *, confirm: bool = False, correction: str = "") -> str:
    """After the fact: was the agent right? Confirmations become tests; overrides become fixes."""
    d = store.decision(decision_id)
    if confirm:
        store.log_event("feedback", decision_id=d.id, verdict="confirm", by=by,
                        rules=[c["id"] for c in d.cited])
        store.add_golden({"id": f"E-{d.id[2:]}", "situation": d.situation, "expect": d.outcome,
                          "rules": [c["id"] for c in d.cited], "source": d.id, "added": iso(utcnow())})
        return "confirmed: added as a regression case"
    if not correction:
        raise ValueError("give --confirm or a correction")
    store.log_event("feedback", decision_id=d.id, verdict="override", by=by, correction=correction,
                    rules=[c["id"] for c in d.cited])
    gap = Gap(id=new_id("G"), ts=iso(utcnow()), situation=d.situation, reason="human_override",
              decision_id=d.id, considered=d.considered)
    store.log_event("gap_opened", gap=gap.to_dict())
    target = d.cited[0]["id"] if d.cited else None
    p = answer_gap(store, gap.id, correction, by, target=target, include_similar=False)
    return f"override recorded: {gap.id} -> proposal {p.id}"


# ----------------------------------------------------------------------------- session wrap

def wrap(store: Store, reasoner: Reasoner, notes: str, by: str) -> list[Proposal]:
    """End-of-session wrap: turn what happened today into proposed rulebook changes."""
    rules = store.rules("approved")
    ids = {r.id for r in rules}
    out = []
    for c in reasoner.from_notes(notes, rules):
        kind = c.get("kind", "create")
        target = c.get("target")
        if kind in ("update", "deprecate") and target not in ids:
            continue
        fields_ = _clean_rule_fields(c.get("rule") or {})
        if kind == "create" and not ("when" in fields_ and "then" in fields_):
            continue
        p = Proposal(id=new_id("P"), kind=kind, target=target if kind != "create" else None, rule=fields_,
                     rationale=c.get("rationale", "from session wrap"), evidence=[f"wrap:{by}:{iso(utcnow())}"],
                     origin="wrap")
        store.save_proposal(p)
        out.append(p)
    return out
