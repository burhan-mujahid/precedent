"""The runtime: what an agent calls before it acts.

    decision = kb.resolve("Customer wants a refund, order arrived 3 days late")

The runtime never lets an agent improvise around the rulebook. Every call ends in
exactly one of three outcomes:

    answer          act on decision.answer; decision.cited says which rule versions allowed it
    needs_approval  the rulebook covers it but the action is high-risk: a human signs off
    escalate        the rulebook does not settle it; a human decides, and a gap is logged

Gates, in order (first failure wins):
    1. coverage    no approved rule mentions enough of the situation       -> no_coverage
    2. judgement   the reasoner says the rules don't settle it / conflict  -> model_unsure / conflict
    3. grounding   the reasoner cited a rule it was not given              -> ungrounded
    4. confidence  below the threshold for the cited rules' risk tier      -> low_confidence
                   (high risk: below policy.approval_floor, so a weak match is not
                   sent for sign-off as if the rule clearly applied)
    5. risk        cited rule is high-risk                                 -> needs_approval

This gate decides whether an action may start. guard.check() is the second gate, called while the
action runs and after it is done; `run_id` ties a run's decisions together so a halt stops the run.

The model is treated as untrusted: if it fails, the call escalates; if its answer contains a
number that is in neither the cited rules nor the situation (a prompt-injected "refund 5000"),
the call escalates as ungrounded. Callers also get the approved rule text verbatim.
"""

from __future__ import annotations

from typing import Any

from .ingest.redact import redact
from .models import ANSWER, ESCALATE, NEEDS_APPROVAL, RISK_LEVELS, Decision, Gap, iso, new_id, utcnow, valid_id
from .reasoner import Reasoner, normalize_decision
from .retrieve import BM25Retriever, Hit
from .scope import numbers
from .store import Store


class Resolver:
    def __init__(self, store: Store, reasoner: Reasoner):
        self.store = store
        self.reasoner = reasoner
        self._index: tuple[tuple, BM25Retriever] | None = None

    def _retriever(self, as_of: str | None = None) -> BM25Retriever:
        rules = self.store.rules_as_of(as_of) if as_of else self.store.rules("approved")
        key = tuple((r.id, r.version) for r in rules)
        if not self._index or self._index[0] != key:
            self._index = (key, BM25Retriever(rules))
        return self._index[1]

    def resolve(self, situation: str, context: dict[str, Any] | str | None = None, *,
                dry_run: bool = False, as_of: str | None = None, run_id: str | None = None) -> Decision:
        """Decide whether an agent may act. `as_of` (YYYY-MM-DD) replays against a past rulebook
        and implies dry_run. `run_id` groups the decisions of one agent run."""
        dry_run = dry_run or bool(as_of)
        if run_id is not None and not valid_id(run_id):
            raise ValueError("run_id must be 1-64 letters, digits, '-', '_' or '.'")
        cfg = self.store.config
        situation = situation.strip()
        if not situation:
            raise ValueError("situation is empty")
        if isinstance(context, dict):
            ctx = " ".join(f"{k}: {v}" for k, v in context.items())
        else:
            ctx = str(context) if context else ""
        full = f"{situation}\n{ctx}".strip()
        limit = int(cfg.get("runtime.max_situation_chars", 4000))
        if len(full) > limit:
            raise ValueError(f"situation + context is {len(full)} characters; the limit is {limit} "
                             "(runtime.max_situation_chars)")
        if cfg.get("runtime.redact", True):
            full = redact(full)   # personal data never reaches the model or the decision log

        hits = self._retriever(as_of).search(full, top_k=int(cfg.get("retrieval.top_k", 5)))
        min_cov = float(cfg.get("retrieval.min_coverage", 0.3))
        relevant = [h for h in hits if h.coverage >= min_cov]
        d = Decision(id=new_id("D"), ts=iso(utcnow()), situation=full, outcome=ESCALATE,
                     considered=[h.rule.id for h in hits], provider=self.reasoner.name, run_id=run_id)

        if not relevant:
            best = f"{hits[0].rule.id} at {hits[0].coverage:.2f}" if hits else "nothing"
            d.reasons.append(f"no_coverage: best match {best} < {min_cov}")
            return self._finish(d, "no_coverage", dry_run)

        try:
            result = normalize_decision(self.reasoner.decide(full, relevant))
        except Exception as exc:   # fail closed: an outage or a bad reply never lets an agent act
            d.reasons.append(f"reasoner_error: {type(exc).__name__}: {str(exc)[:200]}")
            return self._finish(d, "reasoner_error", dry_run)
        status = result["status"]
        given = {h.rule.id: h for h in relevant}
        cited_ids = list(dict.fromkeys(result["rule_ids"]))
        d.reasons.append(f"reasoner: {result.get('reasoning', '')}".strip())

        if status == "conflict":
            d.reasons.append(f"conflict between {', '.join(cited_ids) or 'rules'}")
            return self._finish(d, "conflict", dry_run)
        if status != "covered":
            return self._finish(d, "model_unsure", dry_run)
        if not cited_ids or any(i not in given for i in cited_ids):
            d.reasons.append(f"ungrounded: cited {cited_ids}, given {list(given)}")
            return self._finish(d, "ungrounded", dry_run)

        cited: list[Hit] = [given[i] for i in cited_ids]
        d.cited = [{"id": h.rule.id, "version": h.rule.version, "sha": h.rule.fingerprint()} for h in cited]
        d.answer = str(result.get("answer") or cited[0].rule.then)
        invented = numbers(d.answer) - numbers(full, *(_rule_text(h.rule) for h in cited))
        if invented:
            d.reasons.append(f"ungrounded: answer uses {', '.join(sorted(invented))}, "
                             "which is in neither the cited rules nor the situation")
            return self._finish(d, "ungrounded", dry_run)
        d.risk = max((h.rule.risk for h in cited), key=RISK_LEVELS.index)
        try:
            conf = max(0.0, min(1.0, float(result.get("confidence", 0))))
        except (TypeError, ValueError):
            conf = 0.0
        stale = [h.rule.id for h in cited if h.rule.is_stale()]
        if stale:
            conf *= float(cfg.get("policy.stale_penalty", 0.8))
            d.reasons.append(f"stale rules (past review date): {', '.join(stale)}")
        d.confidence = round(conf, 3)

        threshold = cfg.threshold(d.risk)
        if threshold > 1.0:
            floor = float(cfg.get("policy.approval_floor", 0.55))
            if conf < floor:
                d.reasons.append(f"low_confidence: {conf:.2f} < {floor:.2f} approval floor for {d.risk} risk")
                return self._finish(d, "low_confidence", dry_run)
            d.outcome = NEEDS_APPROVAL
            d.reasons.append(f"{d.risk}-risk action: a human must approve")
            return self._finish(d, None, dry_run)
        if conf < threshold:
            d.reasons.append(f"low_confidence: {conf:.2f} < {threshold:.2f} for {d.risk} risk")
            return self._finish(d, "low_confidence", dry_run)
        d.outcome = ANSWER
        return self._finish(d, None, dry_run)

    def _finish(self, d: Decision, gap_reason: str | None, dry_run: bool) -> Decision:
        if gap_reason:
            d.outcome = ESCALATE
            if not dry_run:   # a dry run or replay must not point people at a gap that was never saved
                gap = Gap(id=new_id("G"), ts=d.ts, situation=d.situation, reason=gap_reason,
                          decision_id=d.id, considered=d.considered)
                d.gap_id = gap.id
                self.store.log_event("gap_opened", gap=gap.to_dict())
        if not dry_run:
            self.store.log_decision(d)
        return d


def _rule_text(rule: Any) -> str:
    return " ".join([rule.title, rule.when, rule.then, rule.why, *rule.exceptions])
