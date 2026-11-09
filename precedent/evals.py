"""Regression evals for the rulebook.

Every resolved gap and every confirmed decision becomes a golden case. Before a rule
change ships (or in CI on every PR to the knowledge-base repo), `precedent eval` replays
them: a change that fixes one case but breaks three others is caught before an agent
acts on it.

Case format (evals/golden.jsonl):
    {"situation": "...", "expect": "covered|answer|needs_approval|escalate", "rules": ["R-004"]}
"covered" means the rulebook must settle it (answer or needs_approval, whichever the rule's
risk tier dictates). "rules" is optional; when present the decision must cite all of them.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .models import ANSWER, NEEDS_APPROVAL
from .runtime import Resolver
from .store import Store


@dataclass
class EvalResult:
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    failures: list[dict] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.passed + self.failed

    @property
    def rate(self) -> float:
        return self.passed / self.total if self.total else 1.0


def run(store: Store, resolver: Resolver) -> EvalResult:
    res = EvalResult()
    live = {r.id for r in store.rules("approved")}
    seen: set[str] = set()
    for case in store.golden():
        key = case["situation"].strip().lower()
        if key in seen:
            continue
        seen.add(key)
        expect_rules = case.get("rules") or []
        if any(r not in live for r in expect_rules):
            res.skipped += 1      # the rule it tested was deprecated on purpose
            continue
        d = resolver.resolve(case["situation"], dry_run=True)
        cited = {c["id"] for c in d.cited}
        expect = case.get("expect", "covered")
        outcome_ok = d.outcome in (ANSWER, NEEDS_APPROVAL) if expect == "covered" else d.outcome == expect
        ok = outcome_ok and set(expect_rules) <= cited
        if ok:
            res.passed += 1
        else:
            res.failed += 1
            res.failures.append({"case": case.get("id", ""), "situation": case["situation"],
                                 "expected": case.get("expect"), "expected_rules": expect_rules,
                                 "got": d.outcome, "cited": sorted(cited), "reasons": d.reasons})
    return res
