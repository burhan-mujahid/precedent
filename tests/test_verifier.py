"""An outside verifier's mismatches become owner-approved rule changes and regression cases."""

import importlib.util
from pathlib import Path

import pytest

from precedent.loop import approve
from precedent.store import StoreError

ALLERGY = "Order 1182: is the walnut brownie safe for a nut allergy?"
SCRIPT = Path(__file__).resolve().parents[1] / "examples" / "integrations" / "report_mismatches.py"


def _example():
    spec = importlib.util.spec_from_file_location("report_mismatches", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_shadow_mode_mismatch_becomes_an_owner_approved_fix(seeded):
    ok = seeded.resolve(ALLERGY, run_id="shadow-1")
    bad = seeded.resolve(ALLERGY, run_id="shadow-2")
    findings = [
        {"decision_id": ok.id, "did": "Said the kitchen handles nuts", "matched": True, "verifier": "monitor"},
        {"decision_id": bad.id, "did": "Told the customer the brownie is nut-free", "matched": False,
         "expected": "Never call anything nut-free, even recipes without nuts", "verifier": "monitor"},
    ]
    assert _example().report(seeded, findings, dry_run=True) == [
        {"decision_id": bad.id, "would_report": "monitor: outcome did not match "
                                                "(Told the customer the brownie is nut-free)"}]
    assert seeded.store.halts() == []

    [out] = _example().report(seeded, findings)
    h = seeded.store.halts()[0]
    assert (h.decision_id, h.run_id, h.stage, h.by) == (bad.id, "shadow-2", "after", "verifier:monitor")
    assert h.rules == bad.cited, "the halt names the exact rule versions the decision relied on"
    assert seeded.check(bad.id, "Replying on order 1182").halted, "the rest of the run is stopped"
    assert not seeded.check(ok.id, "Replying on order 1182").halted, "other runs are not"

    fix = seeded.store.proposal(out["draft_fix"])
    assert fix.kind == "update" and fix.target == bad.cited[0]["id"]
    with pytest.raises(StoreError, match="owns"):
        approve(seeded.store, fix.id, "monitor")          # the verifier cannot approve its own fix
    rule = approve(seeded.store, fix.id, "tester")          # the rule's owner can
    assert rule.approved_by == "tester" and rule.version == 2
    assert any(c["source"] == out["gap"] for c in seeded.store.golden()), "the mismatch is now a regression case"
