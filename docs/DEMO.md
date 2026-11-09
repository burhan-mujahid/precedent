# Demo guide

How to show Precedent in about 10 minutes, and how to answer the questions people ask afterwards.
Every number here comes from `examples/demo.sh` or [LIVE-RESULTS.md](LIVE-RESULTS.md).

## Before the call

```bash
pip install -e .
bash examples/demo.sh                  # dry run: ~20 s offline, should end with "Done"
export GEMINI_API_KEY=...              # optional, for the live version (~45 s)
bash examples/demo.sh --live --pause   # Enter advances each step
```

* Have `examples/bakery/ops-group.txt` open in a second window. It's the "before" picture.
* Have the rulebook folder the demo prints at the end ready to open, to show it's just files.
* If the network is unreliable, run offline. The gates and the loop are identical; only the
  wording of answers changes.

## Talk track

**0. The problem (1 min).** *"Your agent is capable, but it doesn't know your house rules: when to
refund, who approves a discount, what never to say about allergies. Writing a knowledge base takes
weeks and goes stale. The real rules are in the chat where your team asks each other 'how do we
handle X?'."* Show the WhatsApp export.

**1–2. Mine (1 min).** `precedent ingest` reads 33 messages, finds 9 Q&A threads and drafts 9
rules. *"Every draft points to the messages it came from. Nothing is made up."*

**3. Approve (1 min).** *"Nothing enters the rulebook unless a person approves it. That's the
one door in."* Show `precedent rules`: risk levels, versions.

**4. The gate (2 min). This is the core of the demo.** Three situations, three outcomes:

| Situation | Outcome | Say |
|---|---|---|
| Delivery 1.5 h late | 🟢 **ACT** (medium risk, 0.89) | "Covered and low stakes: the agent just does it, and cites R-004 v1." |
| Cake arrived damaged | 🟡 **NEEDS APPROVAL** (high risk) | "Covered, but it moves money. The agent prepares the answer; a person clicks approve." |
| Supplier changed bank details | 🔴 **ESCALATE** (no coverage) | "Nobody ever answered this in the chat. A classic fraud vector. The agent doesn't improvise; it's logged as a gap." |

*"The model doesn't decide whether to act. Five checks outside the model do."*

**5–6. The gap becomes a rule (1.5 min).** The owner answers once. That becomes a draft rule
and a regression test. Ask the same question again: now covered (NEEDS APPROVAL, because it's high
risk). `precedent eval` shows 1/1 passing. *"Every escalation is next week's rule. The same
question never escalates twice."*

**7. Override (1 min).** A person corrects the allergy answer after the fact. That opens a gap
and drafts an update to R-009. *"Mistakes turn into fixes, through the same approval door."*

**8. Halt a running action (1 min).** The owner signs off the damaged-cake refund, and the agent
starts. Asking for a photo: continue. "Refund 5000": HALT, because no rule or decision mentioned
5000. The after-Done check stays halted, and `precedent halts` shows the record: which rule
version, which run, and who halted it. *"Approving the start isn't enough. Every step is
re-checked, and a halt is final."*

**9. Weekly digest (1 min).** `precedent cycle`: *"Thirty minutes a week for the owner: answer the
clusters, review drafts, check drift."*

**10. Export (30 s).** *"Use it from Claude or Cursor over MCP, from Python, from LangGraph, or
compile it into an Agent Skill or AGENTS.md."*

**Close.** Open the rulebook folder: YAML and JSONL. *"Plain files in git. Rule changes go through
pull requests, and CI blocks a change that breaks a regression case."*

## Hard questions

### "How is this different from Parlant / Mem0 / ACE?"

* **Parlant** is an engine for following guidelines that *developers write*. Precedent is about
  *where the guidelines come from* and *keeping them true*: mined from the team, approved by a
  person, tested, checked for drift. It can export to Parlant, so they combine.
* **Mem0, Zep, Letta** remember facts about *users*. They have no idea of a person approving what's
  true, and no gate on actions.
* **ACE, Hermes skills** let the *agent* evolve its own playbook from its own attempts. Precedent
  deliberately never lets a model change the rules; it learns from *people's* answers, and treats
  the agent's uncertainty as a request for one.

### "Approving the start isn't the same as stopping it. Where's the halt?"

Right, and that's a separate gate. `consult_rulebook` decides whether an action may start.
`check_action` runs before each step and after Done. It halts if the step uses an amount or a
high-risk word the decision never covered, if a cited rule changed mid-run, if the decision
expired or was already used, or if a high-risk action was never signed off. The halt record names
the decision, the run, the rule versions, the stage and who halted it (`precedent halts`). A halted
run stays halted. If your own monitor halts a run, it reports it with `report_outcome` →
`halt`, and that halt becomes a draft rule fix and a regression test.

### "What if the model is wrong?"

Layered, and each layer fails closed:

1. It only sees rules that passed the coverage check, and must cite one of them. An invented
   citation escalates.
2. A number in its answer that is in neither the rule nor the situation escalates. That catches
   a prompt-injected "refund 5000".
3. Below the confidence threshold for the rule's risk tier, it escalates. High-risk rules
   always go to a person, and a weak high-risk match escalates rather than going to sign-off.
4. A model error or outage escalates. It never silently lets the agent act.
5. After the fact, `feedback --override` turns a wrong call into a drafted fix, and drift checks
   flag rules that keep getting overridden.

The remaining risk is a confident, wrong answer on a low- or medium-risk rule with no numbers in
it. That's why money, legal, health and data actions are high risk by default (a keyword floor
stops a draft being rated lower), and why the thresholds are tunable and `precedent calibrate`
sets them from evidence.

### "Does it scale?"

* **Rulebook size:** retrieval is BM25 over rule text, comfortable from tens to low thousands of
  rules. Beyond that, or for multilingual matching, an embedding retriever drops in behind a
  one-method interface.
* **Chat size:** episode building is linear (a 120k-line export is a test case). Mining runs 4
  model calls in parallel: 9 threads take about 9 s on Gemini Flash.
* **Decisions:** one model call each, 3–4 s on Gemini Flash, none when the coverage gate
  escalates first. Logs are append-only files; for very high volume, put a service in front
  of the Python API.
* **People:** the human cost is the weekly digest. Gaps are clustered, so one answer clears many.

### "What about privacy?"

* Secrets and personal data (API keys, passwords, emails, phones, cards, IBANs, IPs) are
  redacted before anything is stored or sent to a model, both for chat exports and for every
  situation an agent sends.
* With `provider.kind: ollama` (or offline), nothing leaves the machine.
* The raw chat isn't kept: only redacted Q&A episodes, in a git-ignored, owner-only folder.
* Full threat model, including what is *not* defended: [SECURITY.md](../SECURITY.md).

### "How do we plug it into our automations (n8n, Make, Zapier, our own code)?"

| You have | Use |
|---|---|
| Python, LangGraph, CrewAI, OpenAI / Anthropic SDKs | `Precedent.open(...).resolve(...)` or the tool spec ([examples](../examples/integrations)) |
| Claude, Cursor, any MCP client | `precedent mcp --root <rulebook>` |
| n8n / Make / cron / shell | `precedent ask "…" --json`: exit code 0 only when the agent may act alone |
| A static agent config | `precedent export skill` or `agents` |

There's no hosted HTTP API yet. For workflow tools without a command step, a thin HTTP wrapper
around `handle_tool_call` is a few lines; it's on the roadmap with direct Slack and WhatsApp
Business connectors.

### "Who maintains the rulebook?"

The business owner or an ops lead, about 30 minutes a week with `precedent cycle`. Drafts are
pre-written; their job is approve, edit or reject. Rules expire for review after 90 days by
default, so nothing goes stale silently.

### "Why not just put everything in the system prompt?"

You can, with `precedent export agents`. But a prompt can't refuse to act, can't tell you what
it didn't know, and can't show which rule version justified an action last Tuesday. The gate,
the gap log and the decision log are the product; the rules are the input.

## If something goes wrong live

| Symptom | Fix |
|---|---|
| `TLS certificate check failed` | macOS python.org build: `pip install certifi`, or run offline |
| `model hit max_tokens before answering` | raise `provider.max_tokens` or set `reasoning_effort: low` |
| A live answer differs from the recording | Expected: runs vary even at temperature 0. The gates are what's being shown; see "Runs vary" in [LIVE-RESULTS.md](LIVE-RESULTS.md) |
| Anything else | `bash examples/demo.sh` offline. It's deterministic. |
