# Outside verifiers and execution monitors

Precedent decides whether an action may start (`consult_rulebook`) and whether each step stays
within that decision (`check_action`). It never sees what actually happened in the world. A
separate verifier can: it watches outcomes, compares them with what should have happened, and
reports mismatches back. This page is the contract for that.

## What a verifier gets from Precedent

Every decision is a record in `logs/decisions.jsonl` with:

| Field | Meaning |
|---|---|
| `id` | the decision id (`D-...`) the agent was given |
| `run_id` | the agent run it belongs to, if the agent passed one |
| `situation` | what the agent asked about (redacted) |
| `outcome` | `answer`, `needs_approval` or `escalate` |
| `answer` | what the agent was told to do |
| `cited` | the exact rules relied on: `[{"id", "version", "sha"}]`, where `sha` hashes the rule's content |

Sign-offs (`decision_signed_off`), checks (`checked`) and halts (`halt`) are events in
`logs/events.jsonl`, each with the `decision_id` and `run_id`. So from a decision id a verifier can
find the approved rule text that permitted the action, who signed it off, and every step that was
checked.

## What a verifier sends back

One call, `report_outcome` with verdict `halt`. It works the same over every transport:

| Field | Required | Meaning |
|---|:-:|---|
| `decision_id` | yes | the decision the action ran under |
| `reason` | yes | why it is a mismatch, in a sentence |
| `stage` | | `during` (caught while running) or `after` (caught after Done) |
| `step` | | what the agent did |
| `correction` | | what should have happened. With it, Precedent drafts a rule change |
| `by` | | the verifier's name. It is recorded with a prefix (`mcp:`, `http:`), so a caller can't pose as a person |

```python
kb.halt("D-...", "monitor: refunded without asking for a photo", by="verifier:monitor", stage="after",
        step="Refunded 1200", correction="Ask for a photo first, then remake or refund")
```

```bash
curl -s localhost:8787/outcome -d '{"decision_id": "D-...", "verdict": "halt", "stage": "after",
  "reason": "refunded without asking for a photo", "correction": "Ask for a photo first", "by": "monitor"}'
```

Over MCP, call `report_outcome` with the same arguments. A confirmation goes the other way:
verdict `confirm` turns the decision into a regression case.

## What happens next

1. **A halt record** names the decision, the run, the cited rule versions, the stage, the reason and
   who reported it.
2. **The rest of the run stops.** Halts are final: every later check in that run halts, including
   calls through guarded tools and `precedent proxy`.
3. **A gap opens.** It shows up in `precedent gaps` and the weekly `precedent cycle`.
4. **With a correction, a rule change is drafted** against the rule the decision cited.
5. **Only that rule's owner can approve it** (or a delegate or admin, see `rules.owner_approval`).
   The verifier can't approve its own fix. The new version records who approved it.
6. **Approving it adds a regression case**, so the same mismatch fails `precedent eval` if it ever
   comes back.

## Trying it in shadow mode

A verifier that only observes is a safe place to start. Collect its findings as JSONL and replay
them with the adapter:

```bash
python examples/integrations/report_mismatches.py ./my-rulebook findings.jsonl --dry-run
python examples/integrations/report_mismatches.py ./my-rulebook findings.jsonl
precedent review list        # the drafted rule changes, each showing whose approval it needs
```

Then check whether the drafted changes are ones the owner would approve. If most are, the verifier
and the rulebook agree on what "right" means. If not, the disagreement shows you where one of them
is wrong.

## Limits

* Reporting a halt stops the rest of the run. It cannot undo a step that already happened.
* A halt takes effect at the next check. Steps that don't go through `check_action`, a guarded tool
  or the proxy are not stopped. See [SECURITY.md](../SECURITY.md).
* Anyone who can call `report_outcome` can halt a run. That fails closed, and the halt record says
  who did it.
