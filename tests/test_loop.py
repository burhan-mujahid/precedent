from datetime import date, timedelta

import pytest

from precedent import Precedent
from precedent.drift import health, propose_from_findings
from precedent.loop import answer_gap, approve, reject
from precedent.models import Episode, Message, Proposal, new_id
from precedent.store import StoreError

from .conftest import EXAMPLE, add_rule


def test_init_with_pack_installs_starter_rules(tmp_path):
    kb = Precedent.init(tmp_path / "s", pack="support")
    assert len(kb.store.rules("approved")) == 2
    assert "refund" in kb.store.config.get("risk.high_terms")


def test_ingest_mines_grounded_drafts(kb):
    rep = kb.ingest(EXAMPLE)
    assert rep.episodes == 9 and rep.proposed == 9
    p = kb.store.proposals()[0]
    assert p.evidence and all(e.startswith("ops-group:") for e in p.evidence)
    # Re-ingesting the same chat must not duplicate drafts.
    assert kb.ingest(EXAMPLE).proposed == 0


def test_hallucinated_evidence_is_dropped(kb):
    class Liar:
        name = "liar"
        def extract(self, ep):
            return [{"when": "x happens", "then": "do y", "evidence": ["not-a-real-id"]}]
    from precedent.loop import mine
    ep = Episode(id="e", channel="c", question=Message(id="m1", ts="2026-01-01T00:00:00", author="A", text="q?"),
                 replies=[Message(id="m2", ts="2026-01-01T00:01:00", author="B", text="a")])
    rep = mine(kb.store, Liar(), [ep])
    assert rep.ungrounded == 1 and kb.store.proposals() == []


def test_approve_update_versions_and_history(seeded):
    rid = seeded.store.rules("approved")[0].id
    p = Proposal(id=new_id("P"), kind="update", target=rid, rule={"then": "New action."})
    seeded.store.save_proposal(p)
    r = approve(seeded.store, p.id, "owner")
    assert r.version == 2 and r.then == "New action."
    assert [h.version for h in seeded.store.rule_history(rid)] == [1]
    with pytest.raises(StoreError):
        approve(seeded.store, p.id, "owner")


def test_edit_on_approval_and_reject(kb):
    p = Proposal(id=new_id("P"), kind="create", rule={"title": "t", "when": "w happens", "then": "t"})
    kb.store.save_proposal(p)
    r = approve(kb.store, p.id, "owner", edits={"then": "better", "risk": "medium"})
    assert r.then == "better" and r.risk == "medium"
    p2 = Proposal(id=new_id("P"), kind="create", rule={"title": "t", "when": "w", "then": "t"})
    kb.store.save_proposal(p2)
    assert reject(kb.store, p2.id, "owner", "not a rule").status == "rejected"


def test_invalid_risk_is_treated_as_high(kb):
    rid = add_rule(kb, title="t", when="w happens", then="t", risk="catastrophic")
    assert kb.store.rule(rid).risk == "high"


def test_gap_to_rule_to_regression_test(seeded):
    d1 = seeded.resolve("Supplier says their bank account changed")
    d2 = seeded.resolve("A supplier emailed new bank account details")
    assert d1.outcome == d2.outcome == "escalate"
    p = answer_gap(seeded.store, d1.gap_id, "Call them on the number on file; owner approves.", "owner",
                   when="A supplier asks to change their bank account", risk="high")
    assert set(p.evidence) == {d1.gap_id, d2.gap_id}, "similar open gaps are folded in"
    assert {g.status for g in seeded.store.gaps(None)} == {"answered"}
    rule = approve(seeded.store, p.id, "owner")
    assert {g.status for g in seeded.store.gaps(None)} == {"resolved"}
    assert seeded.resolve("Supplier says their bank account changed").cited[0]["id"] == rule.id
    res = seeded.eval()
    assert res.total == 2 and res.rate == 1.0


def test_rejecting_gap_proposal_reopens_gap(seeded):
    d = seeded.resolve("Should we start selling coffee beans wholesale?")
    p = answer_gap(seeded.store, d.gap_id, "No.", "owner")
    reject(seeded.store, p.id, "owner")
    assert seeded.store.gap(d.gap_id).status == "open"


def test_feedback_confirm_and_override(seeded):
    d = seeded.resolve("Is the walnut brownie safe for a nut allergy?")
    assert "regression" in seeded.feedback(d.id, "owner", confirm=True)
    msg = seeded.feedback(d.id, "owner", correction="Also suggest the nut-free partner bakery.")
    prop = seeded.store.proposals()[0]
    assert prop.kind == "update" and prop.target == d.cited[0]["id"] and "override recorded" in msg
    gap = seeded.store.gaps(None)[0]
    assert gap.reason == "human_override" and gap.status == "answered" and gap.proposal_id == prop.id


def test_wrap_proposes_changes(seeded):
    props = seeded.wrap("- Coffee chat about branding\n- From now on, orders above 100 cupcakes need a 50% deposit\n",
                        "owner")
    assert len(props) == 1 and props[0].kind in ("create", "update")


def test_drift_findings(seeded):
    rules = seeded.store.rules("approved")
    stale = rules[2]                      # the high-risk one
    stale.review_by = (date.today() - timedelta(days=3)).isoformat()
    seeded.store.save_rule(stale)
    old = rules[0]
    old.updated = old.created = (date.today() - timedelta(days=200)).isoformat()
    seeded.store.save_rule(old)
    for _ in range(2):
        d = seeded.resolve("Is the walnut brownie safe for a nut allergy?")
        seeded.store.log_event("feedback", decision_id=d.id, verdict="override", rules=[c["id"] for c in d.cited])
    findings, stats = health(seeded.store)
    kinds = {(f.kind, f.severity) for f in findings}
    assert ("stale", "critical") in kinds and ("overridden", "critical") in kinds and ("unused", "info") in kinds
    props = propose_from_findings(seeded.store, findings)
    assert {p.kind for p in props} == {"reconfirm", "deprecate"}
    assert stats["rules_approved"] == 3


def test_duplicate_detection(kb):
    add_rule(kb, title="a", when="customer asks for a refund on a late order", then="x")
    add_rule(kb, title="b", when="customer asks for a refund on a late order today", then="y")
    assert any(f.kind == "duplicate" for f in health(kb.store)[0])


def test_cycle_digest(seeded):
    seeded.resolve("Should we start selling coffee beans wholesale?")
    text = seeded.cycle()
    assert "Answer these" in text and "coffee beans" in text


def test_calibrate_needs_evidence_then_recommends(seeded):
    assert all(t.recommended is None for t in seeded.calibrate())
    for i in range(6):
        seeded.store.add_golden({"situation": f"delivery running an hour late case {i}", "expect": "covered",
                                 "rules": [seeded.store.rules("approved")[0].id]})
    med = next(t for t in seeded.calibrate() if t.risk == "medium")
    assert med.recommended is not None and med.precision_at_recommended >= 0.95
