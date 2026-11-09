"""Give a Claude agent the rulebook as a tool (Anthropic Messages API, tool use).

pip install anthropic "git+https://github.com/burhan-mujahid/precedent" ; export ANTHROPIC_API_KEY=...
"""
import json

import anthropic

from precedent import Precedent

kb = Precedent.open("./my-rulebook")
spec = kb.tool_spec()
client = anthropic.Anthropic()
messages = [{"role": "user", "content": "A customer says their cake arrived damaged. Handle it."}]

while True:
    resp = client.messages.create(
        model="claude-sonnet-5-5", max_tokens=1024, tools=[spec], messages=messages,
        system="You are the ops assistant for a bakery. " + spec["description"],
    )
    messages.append({"role": "assistant", "content": resp.content})
    calls = [b for b in resp.content if b.type == "tool_use"]
    if not calls:
        print("".join(b.text for b in resp.content if b.type == "text"))
        break
    messages.append({"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": c.id, "content": json.dumps(kb.handle_tool_call(c.input))}
        for c in calls
    ]})
