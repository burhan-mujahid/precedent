"""The tool boundary: guarded tools do not run without a current decision."""

import asyncio
import inspect
import typing

import pytest

from precedent.boundary import Blocked, require_decision, step_text
from precedent.models import ANSWER, NEEDS_APPROVAL

ALLERGY = "Order 1182: is the walnut brownie safe for a nut allergy?"
DISCOUNT = "Customer wants a discount on a large cupcake order of 60"


@pytest.fixture
def tools(seeded):
    calls = []

    @seeded.guarded()
    def reply_to_customer(order: str, message: str) -> str:
        calls.append(("reply", order, message))
        return "sent"

    @seeded.guarded()
    def issue_refund(order: str, amount: int) -> str:
        calls.append(("refund", order, amount))
        return "refunded"

    return seeded, reply_to_customer, issue_refund, calls


def test_runs_with_a_current_decision(tools):
    kb, reply, _, calls = tools
    d = kb.resolve(ALLERGY, run_id="b-1")
    assert d.outcome == ANSWER
    assert reply("1182", "The kitchen handles nuts, so we can't promise nut-free.", decision_id=d.id) == "sent"
    assert calls == [("reply", "1182", "The kitchen handles nuts, so we can't promise nut-free.")]
    assert kb.store.events("checked")[-1]["decision_id"] == d.id


def test_no_decision_means_no_call_and_a_halt_record(tools):
    kb, reply, _, calls = tools
    with pytest.raises(Blocked) as exc:
        reply("1182", "hello")
    assert calls == [] and "Do not retry" in str(exc.value)
    [h] = kb.store.halts()
    assert h.reason == "unknown_decision" and h.step.startswith("reply to customer:")


def test_step_is_built_from_the_real_arguments(tools):
    kb, reply, refund, calls = tools
    d = kb.resolve(ALLERGY, run_id="b-2")
    with pytest.raises(Blocked) as exc:
        refund("1182", 500, decision_id=d.id)          # the decision never covered a refund
    assert calls == [] and exc.value.check.halted
    [h] = kb.store.halts()
    assert h.reason == "out_of_scope" and "500" in h.detail and "refund" in h.detail
    with pytest.raises(Blocked):                       # sticky: the run is over, even for an in-scope tool
        reply("1182", "The kitchen handles nuts.", decision_id=d.id)
    assert calls == []


def test_amounts_in_words_are_still_amounts(seeded):
    d = seeded.resolve(ALLERGY, run_id="b-3")
    c = seeded.check(d.id, "Sending the customer five hundred as a goodwill gesture")
    assert c.halted and "500" in c.reasons[0]


def test_synonyms_of_high_risk_actions_are_caught(seeded):
    d = seeded.resolve(ALLERGY, run_id="b-4")
    c = seeded.check(d.id, "Wire it to the customer on order 1182")
    assert c.halted and "transfer" in c.reasons[0]


def test_final_tool_closes_the_decision(tools):
    kb, _, _, _ = tools

    @kb.guarded(final=True)
    def send_reply(order: str) -> str:
        return "ok"

    d = kb.resolve(ALLERGY, run_id="b-5")
    assert send_reply("1182", decision_id=d.id) == "ok"
    assert [e["stage"] for e in kb.store.events("checked")] == ["during", "after"]
    with pytest.raises(Blocked) as exc:
        send_reply("1182", decision_id=d.id)
    assert kb.store.halts()[0].reason == "already_done" and exc.value.result is None


def test_needs_approval_runs_only_after_sign_off(seeded):
    ran = []

    @seeded.guarded()
    def apply_discount(order_size: int, percent: int) -> None:
        ran.append(percent)

    d = seeded.resolve(DISCOUNT, run_id="b-6")
    assert d.outcome == NEEDS_APPROVAL
    with pytest.raises(Blocked):
        apply_discount(60, 15, decision_id=d.id)
    assert ran == [] and seeded.store.halts()[0].reason == "no_sign_off"

    d2 = seeded.resolve(DISCOUNT, run_id="b-7")
    seeded.sign_off(d2.id, "owner")
    apply_discount(60, 15, decision_id=d2.id)
    assert ran == [15]


def test_signature_shows_decision_id_and_passes_it_when_declared(seeded):
    @seeded.guarded()
    def plain(order: str) -> str:
        return order

    @seeded.guarded()
    def wants_it(order: str, decision_id: str = "") -> str:
        return decision_id

    assert "decision_id" in inspect.signature(plain).parameters
    # pydantic (LangChain's @tool) builds the schema from type hints, so they must match the signature
    assert typing.get_type_hints(plain) == {"order": str, "decision_id": str, "return": str}
    d = seeded.resolve(ALLERGY, run_id="b-8")
    assert wants_it("1182", decision_id=d.id) == d.id


def test_async_tools_are_guarded(seeded):
    @seeded.guarded()
    async def reply(order: str) -> str:
        return "sent"

    d = seeded.resolve(ALLERGY, run_id="b-9")
    assert asyncio.run(reply("1182", decision_id=d.id)) == "sent"
    with pytest.raises(Blocked):
        asyncio.run(reply("1182"))


def test_guard_handlers_return_the_halt_instead_of_raising(seeded):
    ran = []
    handlers = seeded.guard_handlers({
        "issue_refund": lambda a: ran.append(a) or "refunded",
        "lookup_order": lambda a: {"order": a["order"], "status": "delivered"},
    }, pass_through=["lookup_order"])
    assert handlers["lookup_order"]({"order": "1182"})["status"] == "delivered"   # read-only: no decision
    d = seeded.resolve(ALLERGY, run_id="b-10")
    out = handlers["issue_refund"]({"order": "1182", "amount": 500, "decision_id": d.id})
    assert out["blocked"] and out["outcome"] == "halt" and "Do not retry" in out["instruction"] and ran == []


def test_step_text_and_require_decision():
    assert step_text("issue_refund", {"order_id": "1182", "amount": 1200, "decision_id": "D-x"}) == \
        "issue refund: order id=1182, amount=1200"
    for spec, key in [({"name": "t", "input_schema": {"type": "object", "properties": {}}}, "input_schema"),
                      ({"name": "t", "inputSchema": {"type": "object"}}, "inputSchema"),
                      ({"type": "function", "function": {"name": "t", "parameters": {"type": "object"}}},
                       "parameters")]:
        out = require_decision(spec)
        schema = out.get("function", out)[key]
        assert "decision_id" in schema["properties"] and "decision_id" in schema["required"]
        assert "decision_id" not in str(spec), "the original is not modified"
