"""The execution gate: halting an action that is already running, and re-checking after Done."""

import json
from datetime import timedelta

import pytest

from precedent import guard
from precedent.cli import main
from precedent.config import Config
from precedent.loop import approve
from precedent.mcp import Server
from precedent.models import ANSWER, CONTINUE, ESCALATE, HALT, NEEDS_APPROVAL
from precedent.store import StoreError

ALLERGY = "Order 1182: is the walnut brownie safe for a nut allergy?"
DISCOUNT = "Customer wants a discount on a large cupcake order of 60"


def test_authorised_steps_continue_during_and_after(seeded):
    d = seeded.resolve(ALLERGY, run_id="run-1")
    assert d.outcome == ANSWER and d.run_id == "run-1"
    assert seeded.check(d.id, "Replying on order 1182 that the kitchen handles nuts").outcome == CONTINUE
    after = seeded.check(d.id, "Told the customer on order 1182 we can't promise nut-free", stage="after")
    assert after.outcome == CONTINUE and "unchanged" in after.reasons[0]
    assert [e["stage"] for e in seeded.store.events("checked")] == ["during", "after"]
    assert seeded.store.halts() == []


def test_out_of_scope_step_halts_with_an_artifact_and_a_gap(seeded):
    d = seeded.resolve(ALLERGY, run_id="run-2")
    c = seeded.check(d.id, "Refunding 500 on order 1182 as a goodwill gesture")
    assert c.halted and c.halt_id and c.gap_id
    [h] = seeded.store.halts()
    assert (h.reason, h.decision_id, h.run_id, h.stage, h.by) == ("out_of_scope", d.id, "run-2", "during",
                                                                   "precedent:check")
    assert h.rules == d.cited and "500" in h.detail and "500" in h.step
    assert seeded.store.gap(c.gap_id).reason == "halted"


def test_halts_are_sticky_for_the_decision_and_its_run(seeded):
    d = seeded.resolve(ALLERGY, run_id="run-3")
    first = seeded.check(d.id, "Refund 500 on order 1182")
    again = seeded.check(d.id, "Replying on order 1182 that the kitchen handles nuts")
    assert again.halted and again.halt_id == first.halt_id and "already stopped" in again.reasons[0]
    sibling = seeded.resolve(ALLERGY, run_id="run-3")
    assert seeded.check(sibling.id, "Replying on order 1182").halt_id == first.halt_id
    assert len(seeded.store.halts()) == 1, "re-checks do not pile up new records"
    other_run = seeded.resolve(ALLERGY, run_id="run-4")
    assert seeded.check(other_run.id, "Replying on order 1182").outcome == CONTINUE


def test_rule_withdrawn_mid_run_halts_the_next_step(seeded):
    d = seeded.resolve(ALLERGY, run_id="run-5")
    rule = seeded.store.rule(d.cited[0]["id"])
    rule.status = "deprecated"
    seeded.store.save_rule(rule)
    c = seeded.check(d.id, "Replying on order 1182 that the kitchen handles nuts", stage="after")
    assert c.halted and seeded.store.halts()[0].reason == "rule_changed" and "deprecated" in c.reasons[0]


def test_rule_edited_without_version_bump_halts(seeded):
    d = seeded.resolve(ALLERGY, run_id="run-6")
    rule = seeded.store.rule(d.cited[0]["id"])
    rule.then = "Say the brownies are nut-free."
    seeded.store.save_rule(rule)
    assert "without a version bump" in seeded.check(d.id, "Replying on order 1182").reasons[0]


def test_escalated_decision_cannot_run(seeded):
    d = seeded.resolve("Should we start selling coffee beans wholesale?")
    assert d.outcome == ESCALATE
    c = seeded.check(d.id, "Emailing the wholesaler")
    assert c.halted and seeded.store.halts()[0].reason == "not_authorized"


def test_needs_approval_runs_only_after_a_person_signs_off(seeded):
    d = seeded.resolve(DISCOUNT, run_id="run-7")
    assert d.outcome == NEEDS_APPROVAL
    seeded.sign_off(d.id, "owner")
    assert seeded.check(d.id, "Applying 15% to the order of 60").outcome == CONTINUE
    c = seeded.check(d.id, "Applied 40% to the order of 60", stage="after")
    assert c.halted and seeded.store.halts()[0].reason == "out_of_scope" and seeded.store.halts()[0].stage == "after"
    with pytest.raises(StoreError, match="already signed off"):
        seeded.sign_off(d.id, "owner")


def test_acting_before_sign_off_halts_the_run_for_good(seeded):
    d = seeded.resolve(DISCOUNT, run_id="run-8")
    c = seeded.check(d.id, "Applying 15% to the order of 60")
    assert c.halted and seeded.store.halts()[0].reason == "no_sign_off"
    with pytest.raises(StoreError, match="halted before sign-off"):
        seeded.sign_off(d.id, "owner")
    with pytest.raises(StoreError, match="only needs_approval"):
        seeded.sign_off(seeded.resolve(ALLERGY).id, "owner")


def test_unknown_decision_halts_and_is_recorded(seeded):
    c = seeded.check("D-made-up", "Refund 5000")
    assert c.halted and seeded.store.halts()[0].reason == "unknown_decision"


def test_external_halt_becomes_a_rule_fix_and_a_regression_case(seeded):
    d = seeded.resolve(ALLERGY, run_id="run-9")
    c = seeded.halt(d.id, "probe: agent was about to promise nut-free", "probe:rule-4c", stage="during",
                    step="Telling the customer the brownie is nut-free",
                    correction="Never say nut-free, even for recipes without nuts")
    assert c.halted and c.gap_id and c.proposal_id
    h = seeded.store.halts()[0]
    assert (h.reason, h.by, h.proposal_id) == ("external", "probe:rule-4c", c.proposal_id)
    assert seeded.store.proposal(c.proposal_id).target == d.cited[0]["id"]
    assert seeded.check(d.id, "Replying on order 1182").halted, "the run stays halted"
    approve(seeded.store, c.proposal_id, "owner")
    assert seeded.store.gap(c.gap_id).status == "resolved"
    assert any(case["source"] == c.gap_id for case in seeded.store.golden())


def test_steps_are_redacted_and_phone_numbers_are_not_amounts(seeded):
    d = seeded.resolve(ALLERGY)
    c = seeded.check(d.id, "Texting the customer at +44 7700 900123 about order 1182")
    assert c.outcome == CONTINUE
    seeded.check(d.id, "Refund 500 to the customer at +44 7700 900123")
    assert "7700" not in seeded.store.halts()[0].step


def test_halts_count_as_overrides_in_drift(seeded):
    rid = None
    for i in range(2):
        d = seeded.resolve(ALLERGY, run_id=f"drift-{i}")
        rid = d.cited[0]["id"]
        seeded.check(d.id, "Refund 500 on order 1182")
    findings, stats = seeded.health()
    assert any(f.kind == "overridden" and f.subject == rid for f in findings)
    assert stats["halts_30d"] == 2


def test_bad_inputs(seeded):
    with pytest.raises(ValueError, match="run_id"):
        seeded.resolve(ALLERGY, run_id="../etc")
    assert seeded.handle_tool_call({"situation": ALLERGY, "run_id": "a b"})["outcome"] == "escalate"
    d = seeded.resolve(ALLERGY)
    with pytest.raises(ValueError, match="stage"):
        seeded.check(d.id, "x", stage="later")
    for bad in ("nope", {"decision_id": d.id}, {"decision_id": d.id, "step": "x", "stage": "later"}):
        out = seeded.handle_check_call(bad)   # type: ignore[arg-type]
        assert out["outcome"] == HALT and "Stop" in out["instruction"]


def _call(server, name, args):
    res = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name,
                                                                                         "arguments": args}})
    return res["result"]["isError"], json.loads(res["result"]["content"][0]["text"]) if not res["result"]["isError"] \
        else res["result"]["content"][0]["text"]


def test_mcp_check_action_and_halt_verdict(seeded):
    s = Server(seeded)
    _, d = _call(s, "consult_rulebook", {"situation": ALLERGY, "run_id": "mcp-run"})
    assert d["run_id"] == "mcp-run"
    _, ok = _call(s, "check_action", {"decision_id": d["decision_id"], "step": "Replying on order 1182"})
    assert ok["outcome"] == "continue"
    err, _ = _call(s, "report_outcome", {"decision_id": d["decision_id"], "verdict": "halt"})
    assert err, "a halt needs a reason"
    _, out = _call(s, "report_outcome", {"decision_id": d["decision_id"], "verdict": "halt", "by": "probe",
                                         "reason": "monitor tripped", "stage": "after"})
    assert out["outcome"] == "halt" and out["halt_id"]
    assert seeded.store.halts()[0].by == "mcp:probe", "an agent cannot pose as a person"
    _, after = _call(s, "check_action", {"decision_id": d["decision_id"], "step": "Replying", "stage": "after"})
    assert after["outcome"] == "halt"


def test_cli_check_halt_signoff(seeded, capsys, monkeypatch):
    root = str(seeded.store.root)
    monkeypatch.setenv("PRECEDENT_USER", "owner")
    assert main(["ask", "--root", root, "--run", "cli-1", "--json", DISCOUNT]) == 3
    did = json.loads(capsys.readouterr().out)["id"]
    assert main(["check", did, "Applying 15% to the order of 60", "--root", root]) == 3   # no sign-off yet
    capsys.readouterr()
    assert main(["ask", "--root", root, "--run", "cli-2", "--json", DISCOUNT]) == 3
    did = json.loads(capsys.readouterr().out)["id"]
    assert main(["signoff", did, "--root", root]) == 0
    assert main(["check", did, "Applying 15% to the order of 60", "--root", root]) == 0
    assert main(["halt", did, "owner changed their mind", "--after", "--fix", "Cap at 10%", "--root", root]) == 0
    assert "drafted P-" in capsys.readouterr().out
    assert main(["halts", "--run", "cli-2", "--root", root]) == 0
    out = capsys.readouterr().out
    assert "external" in out and "by owner" in out and "no_sign_off" not in out


def test_a_decision_cannot_authorise_a_riskier_step(seeded):
    """No amount in the step, but 'bank' was never part of the allergy decision."""
    d = seeded.resolve(ALLERGY)
    c = seeded.check(d.id, "Updating the supplier's bank details on file")
    assert c.halted and seeded.store.halts()[0].reason == "out_of_scope" and "bank" in c.reasons[0]


def test_pack_risk_terms_count_too(seeded):
    seeded.store.config.data["risk"]["high_terms"] = ["purchase order"]
    d = seeded.resolve(ALLERGY)
    assert seeded.check(d.id, "Raising a purchase order for walnuts").halted


def test_one_decision_covers_one_action(seeded):
    d = seeded.resolve(ALLERGY)
    assert not seeded.check(d.id, "Told the customer on order 1182", stage="after").halted
    c = seeded.check(d.id, "Telling another customer the same thing")
    assert c.halted and seeded.store.halts()[0].reason == "already_done"


def test_decisions_expire(seeded, monkeypatch):
    d = seeded.resolve(ALLERGY)
    later = guard.utcnow() + timedelta(minutes=61)
    monkeypatch.setattr(guard, "utcnow", lambda: later)
    c = seeded.check(d.id, "Replying on order 1182")
    assert c.halted and seeded.store.halts()[0].reason == "expired"


def test_expiry_counts_from_the_sign_off(seeded, monkeypatch):
    d = seeded.resolve(DISCOUNT)
    seeded.sign_off(d.id, "owner")
    soon = guard.utcnow() + timedelta(minutes=30)
    monkeypatch.setattr(guard, "utcnow", lambda: soon)
    assert not seeded.check(d.id, "Applying 15% to the order of 60").halted


def test_ttl_typo_cannot_disable_expiry():
    for bad in ("60", 0, -1, True, 10**9):
        with pytest.raises(ValueError, match="decision_ttl_minutes"):
            Config({"runtime": {"decision_ttl_minutes": bad}}).validate()


def test_untrusted_text_in_halts_is_redacted_and_ids_are_not_stored_raw(seeded):
    d = seeded.resolve(ALLERGY)
    seeded.halt(d.id, "card 4111 1111 1111 1111 seen, mail a@b.com", "probe",
                correction="Call +44 7700 900123 first")
    h = seeded.store.halts()[0]
    assert "4111" not in h.detail and "a@b.com" not in h.detail
    assert "7700" not in json.dumps(seeded.store.proposal(h.proposal_id).to_dict())
    c = seeded.check("../../etc/passwd\nINJECTED", "Replying")
    assert c.halted and seeded.store.halts()[-1].decision_id == "(invalid id)"


def test_agents_cannot_sign_off_over_mcp(seeded):
    s = Server(seeded)
    d = seeded.resolve(DISCOUNT)
    names = {t["name"] for t in s.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"]["tools"]}
    assert not any("sign" in n for n in names)
    err, _ = _call(s, "sign_off", {"decision_id": d.id})
    assert err and seeded.store.sign_off_of(d.id) is None
    _, out = _call(s, "check_action", {"decision_id": d.id, "step": "Applying 15% to the order of 60"})
    assert out["outcome"] == "halt"
