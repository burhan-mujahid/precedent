from datetime import date, timedelta

from precedent.models import ANSWER, ESCALATE, NEEDS_APPROVAL
from precedent.retrieve import BM25Retriever, stem, tokens
from precedent.runtime import Resolver

from .conftest import add_rule


class FakeReasoner:
    """Lets a test dictate exactly what the 'model' says."""

    name = "fake"

    def __init__(self, reply):
        self.reply = reply

    def extract(self, ep):
        return []

    def decide(self, situation, hits):
        return self.reply(hits) if callable(self.reply) else self.reply

    def from_notes(self, notes, rules):
        return []


def test_stemmer_keeps_customer_and_custom_apart():
    assert stem("customer") != stem("custom")
    assert tokens("refunds refunded") == ["refund", "refund"]


def test_retrieval_ranks_and_reports_coverage(seeded):
    hits = BM25Retriever(seeded.store.rules("approved")).search("delivery an hour late")
    assert hits[0].rule.title == "Late delivery"
    assert 0 < hits[0].coverage <= 1


def test_answer_for_covered_low_risk(seeded):
    d = seeded.resolve("Is the walnut brownie safe for a nut allergy?")
    assert d.outcome == ANSWER and d.cited[0]["version"] == 1
    assert seeded.store.decisions()[-1].id == d.id


def test_no_coverage_escalates_and_opens_gap(seeded):
    d = seeded.resolve("Should we start selling coffee beans wholesale?")
    assert d.outcome == ESCALATE and d.gap_id
    assert seeded.store.gaps()[0].reason == "no_coverage"


def test_high_risk_needs_approval_and_opens_no_gap(seeded):
    d = seeded.resolve("Customer wants a discount on a large cupcake order of 60")
    assert d.outcome == NEEDS_APPROVAL and d.answer
    assert seeded.store.gaps() == []


def test_dry_run_logs_nothing(seeded):
    d = seeded.resolve("Should we start selling coffee beans wholesale?", dry_run=True)
    assert seeded.store.decisions() == [] and seeded.store.gaps() == []
    assert d.outcome == "escalate" and d.gap_id is None, "never point at a gap that was not saved"


def test_ungrounded_citation_escalates(seeded):
    reply = {"status": "covered", "answer": "x", "rule_ids": ["R-999"], "confidence": 0.99}
    r = Resolver(seeded.store, FakeReasoner(reply))
    d = r.resolve("delivery running an hour late")
    assert d.outcome == ESCALATE and seeded.store.gaps()[0].reason == "ungrounded"


def test_model_unsure_and_conflict(seeded):
    r = Resolver(seeded.store, FakeReasoner({"status": "not_covered"}))
    assert r.resolve("delivery running an hour late").outcome == ESCALATE
    r = Resolver(seeded.store, FakeReasoner(lambda hits: {"status": "conflict", "rule_ids": [h.rule.id for h in hits]}))
    r.resolve("delivery running an hour late")
    assert {g.reason for g in seeded.store.gaps()} == {"model_unsure", "conflict"}


def test_low_confidence_escalates_with_suggestion(seeded):
    r = Resolver(seeded.store, FakeReasoner(lambda hits: {"status": "covered", "answer": "suggested",
                                                          "rule_ids": [hits[0].rule.id], "confidence": 0.2}))
    d = r.resolve("delivery running an hour late")
    assert d.outcome == ESCALATE and d.answer == "suggested"
    assert seeded.store.gaps()[0].reason == "low_confidence"


def test_high_risk_below_approval_floor_escalates(seeded):
    """A weak match on a high-risk rule escalates with a gap instead of going to sign-off."""
    def reply(conf):
        return lambda hits: {"status": "covered", "answer": "suggested", "rule_ids": [hits[0].rule.id],
                             "confidence": conf}
    situation = "Customer wants a discount on a large cupcake order of 60"
    d = Resolver(seeded.store, FakeReasoner(reply(0.5))).resolve(situation)
    assert d.outcome == ESCALATE and d.answer == "suggested" and d.risk == "high"
    assert seeded.store.gaps()[0].reason == "low_confidence"
    assert Resolver(seeded.store, FakeReasoner(reply(0.6))).resolve(situation).outcome == NEEDS_APPROVAL
    seeded.store.config.data["policy"]["approval_floor"] = 0.0
    assert Resolver(seeded.store, FakeReasoner(reply(0.5))).resolve(situation).outcome == NEEDS_APPROVAL


def test_stale_rule_is_penalised(seeded):
    rule = seeded.store.rules("approved")[0]
    rule.review_by = (date.today() - timedelta(days=1)).isoformat()
    seeded.store.save_rule(rule)
    def reply(hits):
        return {"status": "covered", "answer": "a", "rule_ids": [hits[0].rule.id], "confidence": 0.8}
    d = Resolver(seeded.store, FakeReasoner(reply)).resolve("delivery running an hour late")
    assert d.confidence == 0.64 and any("stale" in r for r in d.reasons)


def test_replay_as_of_uses_rules_in_force(kb):
    rid = add_rule(kb, title="Returns", when="A customer wants to return an item", then="Accept within 14 days.")
    rule = kb.store.rule(rid)
    rule.created = rule.updated = "2026-01-01"
    kb.store.save_rule(rule)
    assert kb.resolve("customer wants to return an item", as_of="2025-12-31").outcome == ESCALATE
    assert kb.resolve("customer wants to return an item", as_of="2026-01-02").cited[0]["id"] == rid
    assert kb.store.decisions() == [], "replay must never log"


def test_tool_call_contract(seeded):
    out = seeded.handle_tool_call({"situation": "Should we start selling coffee beans wholesale?"})
    assert out["outcome"] == "escalate" and "Do NOT act" in out["instruction"]
    assert seeded.tool_spec()["input_schema"]["required"] == ["situation"]
