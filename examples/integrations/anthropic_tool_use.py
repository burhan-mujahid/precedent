"""Give a Claude agent the rulebook as tools (Anthropic Messages API, tool use).

pip install anthropic precedent-ai ; export ANTHROPIC_API_KEY=...

consult_rulebook decides whether an action may start; check_action re-checks each step and the
result after Done. The business tool (issue_refund) is guarded: it needs a decision_id and does
not run unless the execution gate says continue, so the agent can't skip the rulebook.
"""
import json

import anthropic

from precedent import Precedent
from precedent.boundary import require_decision

kb = Precedent.open("./my-rulebook")
consult, check = kb.tool_spec(), kb.check_tool_spec()
refund = require_decision({
    "name": "issue_refund", "description": "Refund a customer's order.",
    "input_schema": {"type": "object", "properties": {"order": {"type": "string"}, "amount": {"type": "number"}},
                     "required": ["order", "amount"]},
})


def issue_refund(args: dict) -> dict:
    return {"refunded": args["amount"], "order": args["order"]}   # your payment call goes here


handlers = kb.guard_handlers({
    consult["name"]: kb.handle_tool_call,       # Precedent's own tools always pass through
    check["name"]: kb.handle_check_call,
    "issue_refund": issue_refund,               # guarded: refused without a current decision
}, final=["issue_refund"])
client = anthropic.Anthropic()
messages = [{"role": "user", "content": "A customer says their cake arrived damaged. Handle it."}]

while True:
    resp = client.messages.create(
        model="claude-sonnet-5-5", max_tokens=1024, tools=[consult, check, refund], messages=messages,
        system="You are the ops assistant for a bakery. " + consult["description"],
    )
    messages.append({"role": "assistant", "content": resp.content})
    calls = [b for b in resp.content if b.type == "tool_use"]
    if not calls:
        print("".join(b.text for b in resp.content if b.type == "text"))
        break
    messages.append({"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": c.id, "content": json.dumps(handlers[c.name](c.input))}
        for c in calls
    ]})
