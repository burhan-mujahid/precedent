"""Give a Claude agent the rulebook as tools (Anthropic Messages API, tool use).

pip install anthropic "git+https://github.com/burhan-mujahid/precedent@v0.2.1" ; export ANTHROPIC_API_KEY=...

Two tools: consult_rulebook decides whether an action may start; check_action re-checks each
step and the result after Done, and halts the run if a step goes beyond the decision.
"""
import json

import anthropic

from precedent import Precedent

kb = Precedent.open("./my-rulebook")
consult, check = kb.tool_spec(), kb.check_tool_spec()
handlers = {consult["name"]: kb.handle_tool_call, check["name"]: kb.handle_check_call}
client = anthropic.Anthropic()
messages = [{"role": "user", "content": "A customer says their cake arrived damaged. Handle it."}]

while True:
    resp = client.messages.create(
        model="claude-sonnet-5-5", max_tokens=1024, tools=[consult, check], messages=messages,
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
