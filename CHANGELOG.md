# Changelog

## 0.3.0 - 2026-11-12

Enforcement at the tool boundary. Until now, `consult_rulebook` and `check_action` were gates the
agent chose to call, so an agent that never asked was never stopped. Now the gate can sit inside the
tools themselves.

* `@kb.guarded()`: a decorator for Python tools (sync or async). The tool runs only when the execution
  gate says `continue` for its `decision_id`; otherwise it raises `Blocked` and never runs. A missing or
  unknown decision is still checked, so the attempt is a halt record. The step is built from the real tool
  name and arguments. `final=True` runs the after-Done check once the tool returns.
* `kb.guard_handlers()` for function-calling dispatch tables (a blocked call returns the halt as the tool
  result) and `precedent.boundary.require_decision()` to add `decision_id` to a tool schema.
* `precedent proxy -- <mcp server command>`: an MCP proxy that adds a required `decision_id` to every
  upstream tool, serves `consult_rulebook` / `check_action` / `report_outcome` itself, and never forwards a
  call that fails the gate. `--pass` for read-only tools, `--final` for tools that finish an action (a
  second call on the same decision is refused while the first is in flight). Also `boundary.pass_through`
  and `boundary.final` in precedent.yaml.
* Owner-gated approval: a change to an existing rule needs its owner, someone under
  `rules.delegates.<owner>`, or someone in `rules.admins`. On by default (`rules.owner_approval`). New
  rules and starter-pack rules need no owner. Each rule version records `approved_by`; `precedent review
  list` and `rules history` show who is needed and who approved.
* `precedent serve`: an HTTP API (`POST /consult`, `/check`, `/outcome`, `/tools/<name>`) for n8n, Make and
  Zapier. Standard library only, localhost by default, bearer token (`PRECEDENT_HTTP_TOKEN`) required
  beyond localhost. Halts reported over HTTP are recorded as `http:<name>`.
* Scope checks catch more phrasings: amounts in words ("five thousand"), shorthand ("5k", "2.5 lakh",
  "3 crore"), and synonyms of high-risk actions ("wire" = transfer, "reimburse" = refund, "IBAN" = bank).
  New setting `risk.synonyms` for your own. The grounding gate uses the same number matching.
* Outside verifiers: [docs/VERIFIERS.md](docs/VERIFIERS.md) and
  `examples/integrations/report_mismatches.py`, which feeds a shadow-mode verifier's mismatches into
  `report_outcome`.
* Config lists and maps (`risk.*`, `boundary.*`, `rules.delegates`, `rules.admins`) are type-checked on load.
* Tests: the stale-rule test used the local date, and failed for a few hours each day in time zones ahead
  of UTC.

## 0.2.3 - 2026-11-09

* First release archived on Zenodo, so Precedent has a citable DOI. No code changes.

## 0.2.2 - 2026-11-09

* Docs only: the package description on PyPI is refreshed from the current README. No code changes.

## 0.2.1 - 2026-11-09

* Fix: `precedent ask --dry-run` and `precedent replay` printed a gap id to answer, but dry runs never
  save gaps, so `precedent gaps answer` on that id failed. Dry runs no longer report a gap id.
* The Anthropic tool-use and LangGraph examples now register `check_action` next to `consult_rulebook`,
  so agents built from them re-check each step and halt.

## 0.2.0 - 2026-11-09

Execution gate: halt an action that is already running, and re-check after Done. In 0.1, act /
needs approval / escalate only gated the next action; none of them could halt one in progress.

* `check_action` (MCP), `kb.check()`, `precedent check`: called before each step and after Done; returns
  `continue` or `halt`. Deterministic checks, no model call: unknown decision, already halted (decision or
  run), already done, not authorised / no sign-off, expired, cited rule changed, out of scope (an amount or a
  high-risk word that is in neither the decision nor its rules).
* Halt records in `logs/events.jsonl`: decision, run, cited rule versions, stage (during / after), reason,
  and who created it (`precedent:check`, a person, or `mcp:<probe>`). `precedent halts` lists them.
* Halts are final: a halted decision or run stays halted. One decision covers one action.
* `run_id` on `resolve()`, `consult_rulebook` and `precedent ask --run`, so a halt stops the whole run.
* Sign-off for NEEDS APPROVAL decisions: `precedent signoff`, `kb.sign_off()`. Not available over MCP, so
  agents cannot approve themselves.
* `report_outcome` verdict `halt` and `precedent halt --fix`: an outside monitor's halt becomes a halt
  record, a gap, and a draft rule fix; approving the fix adds a regression case.
* New setting `runtime.decision_ttl_minutes` (default 60, range-checked).
* Drift: halts for out-of-scope or external reasons count towards a rule's "overridden" finding; the weekly
  digest and `health` report halts in the last 30 days.

## 0.1.0 - 2026-11-09

First public release.

* Ingest: WhatsApp (Android/iOS, all common locales), Slack exports, JSONL; PII redaction; Q&A episode detection.
* Mining with grounded evidence; de-duplication against pending drafts and approved rules.
* Proposal-only rulebook changes with version history and point-in-time replay.
* Runtime with five gates: coverage, judgement, grounding, risk-tiered confidence, risk. High-risk matches below `policy.approval_floor` (0.55) escalate and log a gap instead of going to sign-off.
* Fails closed: model errors escalate (`reasoner_error`); answers with numbers absent from the cited rules and the situation escalate (`ungrounded`); tool calls never raise to the agent. Decisions record each cited rule's content hash; tool results include the approved rule text.
* Security: redaction of secrets and personal data in situations as well as chats; prompt fences that untrusted text cannot close; id validation for file paths; cross-process lock on approvals; range-checked safety config; API keys only over HTTPS (or localhost); size limits on inputs and responses; sanitised exports; MCP feedback attributed as `mcp:`. See SECURITY.md.
* Supply chain: pip-audit in CI, Actions pinned to SHAs, Dependabot.
* Packaging: distributed as `precedent-ai` (the import and the `precedent` command are unchanged). Tagged releases build, attest provenance, create a GitHub release and publish to PyPI through Trusted Publishing.
* Docs: README, demo guide (docs/DEMO.md), security policy (SECURITY.md), CITATION.cff, llms.txt, code of conduct, issue and PR templates.
* Learning loop: gaps → clustered weekly digest → draft rules → regression cases; confirm/override feedback; session wrap.
* Drift detection: stale, overridden, unused, duplicate, coverage.
* Regression evals and evidence-based threshold calibration.
* Providers: presets for Gemini, Kimi (Moonshot), Anthropic, OpenAI, OpenRouter, Groq and Ollama; configurable max_tokens / temperature / reasoning_effort; rejected optional parameters are dropped automatically; `precedent doctor` checks a setup end to end.
* Interfaces: Python API, CLI, stdio MCP server, function-calling tool spec.
* Exports: Agent Skill, AGENTS.md, Parlant guidelines, JSON, Markdown.
* Domain packs: general, support, ops, sales, finance, hr, oncall.
