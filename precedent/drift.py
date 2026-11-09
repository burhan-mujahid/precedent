"""Drift detection: how a correct rulebook quietly becomes a wrong one.

A rulebook drifts in five ways, and each has a signal in data Precedent already keeps:

    stale       nobody has re-confirmed the rule within its review window
    overridden  people keep overriding decisions that cite it           (it is wrong)
    unused      no agent has cited it for weeks                          (it is dead weight, or retrieval misses it)
    duplicate   two approved rules describe the same situation           (they will diverge)
    coverage    a rising share of situations escalate                    (the business moved on)
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from itertools import combinations
from typing import Any

from .models import ESCALATE, Proposal, new_id, utcnow
from .retrieve import jaccard
from .store import Store

SEVERITY_ORDER = {"critical": 0, "warn": 1, "info": 2}


@dataclass
class Finding:
    severity: str
    kind: str
    subject: str
    message: str
    action: str


def _parse(ts: str) -> datetime:
    dt = datetime.fromisoformat(ts)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def health(store: Store, now: datetime | None = None) -> tuple[list[Finding], dict[str, Any]]:
    cfg = store.config
    now = now or utcnow()
    rules = store.rules("approved")
    decisions = store.decisions()
    feedback = store.events("feedback")
    findings: list[Finding] = []

    last_cited: dict[str, datetime] = {}
    cite_count: Counter[str] = Counter()
    for d in decisions:
        for c in d.cited:
            cite_count[c["id"]] += 1
            last_cited[c["id"]] = max(last_cited.get(c["id"], _parse(d.ts)), _parse(d.ts))

    overrides: Counter[str] = Counter()
    for f in feedback:
        if f.get("verdict") == "override":
            for rid in f.get("rules", []):
                overrides[rid] += 1

    unused_days = int(cfg.get("drift.unused_days", 60))
    override_limit = int(cfg.get("drift.override_limit", 2))
    for r in rules:
        if r.is_stale(now.date()):
            sev = "critical" if r.risk == "high" else "warn"
            findings.append(Finding(sev, "stale", r.id, f"'{r.title}' was due for review on {r.review_by}",
                                    f"precedent reconfirm {r.id}   (or edit rules/{r.id}.yaml)"))
        if overrides[r.id] >= override_limit:
            findings.append(Finding("critical", "overridden", r.id,
                                    f"'{r.title}' was overridden {overrides[r.id]} times",
                                    "review the open proposals that target it"))
        since = last_cited.get(r.id) or _parse(r.updated or r.created or now.isoformat())
        if now - since > timedelta(days=unused_days):
            findings.append(Finding("info", "unused", r.id,
                                    f"'{r.title}' has not been cited in {(now - since).days} days",
                                    "deprecate it, or check that situations are phrased the way the rule is"))

    dup = float(cfg.get("drift.duplicate_similarity", 0.6))
    for a, b in combinations(rules, 2):
        if jaccard(a.when, b.when) >= dup:
            findings.append(Finding("warn", "duplicate", f"{a.id}+{b.id}",
                                    f"'{a.title}' and '{b.title}' cover the same situation",
                                    "merge them, then deprecate one"))

    recent = [d for d in decisions if now - _parse(d.ts) <= timedelta(days=30)]
    escalated = [d for d in recent if d.outcome == ESCALATE]
    rate = len(escalated) / len(recent) if recent else 0.0
    if len(recent) >= 10 and rate > 0.3:
        findings.append(Finding("warn", "coverage", "rulebook",
                                f"{rate:.0%} of the last {len(recent)} decisions escalated",
                                "run `precedent cycle` and answer the top gap clusters"))

    findings.sort(key=lambda f: (SEVERITY_ORDER[f.severity], f.kind, f.subject))
    reasons = Counter(g.reason for g in store.gaps("open"))
    stats = {
        "rules_approved": len(rules),
        "rules_draft_or_deprecated": len(store.rules()) - len(rules),
        "proposals_pending": len(store.proposals("pending")),
        "gaps_open": sum(reasons.values()),
        "gaps_by_reason": dict(reasons),
        "decisions_30d": len(recent),
        "answered_30d": sum(1 for d in recent if d.outcome == "answer"),
        "needs_approval_30d": sum(1 for d in recent if d.outcome == "needs_approval"),
        "escalation_rate_30d": round(rate, 3),
        "most_cited": cite_count.most_common(5),
    }
    return findings, stats


def propose_from_findings(store: Store, findings: list[Finding]) -> list[Proposal]:
    """Turn unused/stale findings into reviewable proposals (deprecate / reconfirm)."""
    pending_targets = {p.target for p in store.proposals("pending")}
    out = []
    for f in findings:
        if f.subject in pending_targets:
            continue
        if f.kind == "unused":
            p = Proposal(id=new_id("P"), kind="deprecate", target=f.subject, rationale=f.message,
                         evidence=[f"drift:{f.kind}"], origin="drift")
        elif f.kind == "stale":
            p = Proposal(id=new_id("P"), kind="reconfirm", target=f.subject, rationale=f.message,
                         evidence=[f"drift:{f.kind}"], origin="drift")
        else:
            continue
        store.save_proposal(p)
        out.append(p)
    return out
