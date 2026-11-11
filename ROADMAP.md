# Roadmap

What's planned after 0.3.0, in priority order. Each item names the gap it closes.

## Done in 0.3.0 (see [CHANGELOG.md](CHANGELOG.md))

* **Enforce the gate at the tool boundary.** `consult_rulebook` and `check_action` were gates the
  agent chose to call, so an agent that never asked was never stopped. Now:
  * `@kb.guarded()` for Python tools (sync and async) and `kb.guard_handlers()` for function-calling
    dispatch tables. The step is built from the real tool name and arguments. `final=True` runs the
    after-Done check.
  * `precedent proxy -- <upstream MCP server>`: every upstream tool gets a required `decision_id`,
    and a call that fails the gate never reaches the upstream server. `--pass` for read-only tools,
    `--final` for tools that finish an action. Everything is guarded unless listed.
  * The Anthropic and LangGraph examples use guarded tools.
* **Owner-gated approval.** A change to an existing rule needs its owner, a delegate
  (`rules.delegates`) or an admin (`rules.admins`). Each version records `approved_by`.
* **Outside verifier hook.** [docs/VERIFIERS.md](docs/VERIFIERS.md) and
  `examples/integrations/report_mismatches.py`, tested by feeding shadow-mode mismatches into
  `report_outcome`. The verifier can't approve its own fix; the rule's owner has to.
* **HTTP API.** `precedent serve`: `POST /consult`, `/check`, `/outcome`, for n8n, Make and Zapier.
* **Scope checks beyond exact words.** Amounts in words and shorthand ("five thousand", "5k",
  "2 lakh"), synonyms of high-risk actions ("wire" = transfer), and `risk.synonyms` for your own.

## Next

* **Credential-mediated enforcement.** The boundary only covers paths routed through it. The next
  step is for the proxy (or a small broker) to hold the credentials for side-effecting tools, so an
  agent can't reach the real API without going through the gate.
* **Thread-safe final calls in the decorator.** The proxy refuses a second final call on a decision
  while the first is still running. `@kb.guarded(final=True)` does not yet do this when called from
  several threads at once.

## Later

* Connectors that pull directly from Slack, WhatsApp Business, Gmail and helpdesks.
* Optional embedding retriever behind the existing `Retriever` interface.
* Sample-based confidence (agreement across several model calls) as a third calibration signal.
* Review UI: a single static page over the same files.
* Multi-reviewer quorum for high-risk rules.
* Reviewer identity for approvals and sign-offs. Owner-gated approval compares names, and anyone can
  type someone else's name, so for now access is controlled through git and protected branches.

## Not planned

These stay out of scope (see [docs/DESIGN.md](docs/DESIGN.md)): an agent framework, a general
memory store, and any mode where a model edits the rulebook without a person approving it.
