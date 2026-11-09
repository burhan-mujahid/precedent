# Changelog

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
