"""Set the act-or-escalate thresholds from evidence instead of guessing.

For every golden case the runtime is replayed (dry run). Each case where the runtime
*would* have acted gives a (confidence, was-it-right) pair. For each risk tier we then
pick the lowest threshold at which auto-answers are at least `target` precise, and
report how much work that leaves for humans.

This is the cost trade-off from the escalation literature made concrete: a wrong action
costs more than a human's minute, so you choose the precision you need and the data
tells you the threshold, not the other way round.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .models import ESCALATE
from .runtime import Resolver
from .store import Store


@dataclass
class TierResult:
    risk: str
    cases: int
    current: float
    recommended: float | None
    precision_at_recommended: float | None
    auto_rate_at_recommended: float | None
    points: list[tuple[float, bool]] = field(default_factory=list)


def calibrate(store: Store, resolver: Resolver, target: float = 0.95, min_cases: int = 5) -> list[TierResult]:
    by_tier: dict[str, list[tuple[float, bool]]] = {"low": [], "medium": []}
    for case in store.golden():
        d = resolver.resolve(case["situation"], dry_run=True)
        if not d.cited or d.risk not in by_tier:
            continue   # never auto-answerable (no coverage, conflict) or always needs a human (high)
        expected_rules = set(case.get("rules") or [])
        right = case.get("expect", "covered") != ESCALATE and expected_rules <= {c["id"] for c in d.cited}
        by_tier[d.risk].append((d.confidence, right))

    out = []
    for risk, pts in by_tier.items():
        current = store.config.threshold(risk)
        res = TierResult(risk=risk, cases=len(pts), current=current, recommended=None,
                         precision_at_recommended=None, auto_rate_at_recommended=None, points=sorted(pts))
        if len(pts) >= min_cases:
            for t in sorted({c for c, _ in pts}):
                auto = [ok for c, ok in pts if c >= t]
                precision = sum(auto) / len(auto)
                if precision >= target:
                    res.recommended = round(t, 3)
                    res.precision_at_recommended = round(precision, 3)
                    res.auto_rate_at_recommended = round(len(auto) / len(pts), 3)
                    break
        out.append(res)
    return out
