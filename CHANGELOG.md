# Changelog

## 0.2.0 - 2026-11-09

Execution gate: halt an action that is already running, and re-check after Done. Thanks to
[Gary Ko](https://www.linkedin.com/in/gary-ko-976863210/), who pointed out that act / needs approval / escalate only gate the next action and none
of them is a HALT.

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
