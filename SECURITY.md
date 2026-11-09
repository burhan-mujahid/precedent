# Security

## Reporting a vulnerability

Please report privately through GitHub: **Security → Report a vulnerability** on this repository,
or by email to burhanmujahid552@gmail.com. Do not open a public issue. You will get a reply within 7 days. Fixes are released as a patch
version and credited in the CHANGELOG unless you ask otherwise.

Supported versions: the latest release only (Precedent is pre-1.0).

## What Precedent protects

Precedent decides whether an AI agent may act on its own. The main risk is an agent acting
when it should not, so every safeguard below fails closed: when in doubt, a person decides.

| Threat | Defence | Test |
|---|---|---|
| Prompt injection in a situation makes the model cite a harmless rule but answer "refund 5000" | An answer containing a number found in neither the cited rules nor the situation escalates as `ungrounded`. Callers also get the approved rule text verbatim (`rule_text`) and the rule's content hash (`sha`) | `test_injected_amount_in_answer_escalates` |
| The model cites a rule it was not shown | Escalates as `ungrounded` | `test_ungrounded_citation_escalates` |
| Untrusted text closes the prompt's `<situation>` / `<episode>` fence and adds its own "rules" | Fence tags inside untrusted text are neutralised; prompts tell the model the content is data | `test_untrusted_text_cannot_close_prompt_tags` |
| Model outage, quota error or unparseable reply | Escalates as `reasoner_error`; the tool call never raises to the agent | `test_reasoner_failure_fails_closed`, `test_tool_call_fails_closed` |
| A weak match on a high-risk rule is sent for sign-off as if the rule clearly applied | Below `policy.approval_floor` it escalates as `low_confidence` and logs a gap | `test_high_risk_below_approval_floor_escalates` |
| A config typo (`"0,7"`, `2`, `true`) silently opens a gate | Safety settings are range-checked on load | `test_config_typo_cannot_open_a_gate` |
| A rule enters the rulebook without a person | The only way in is an approved proposal; mined drafts must cite real message ids | `test_loop.py` |
| A compromised agent marks its own decisions as correct | Feedback over MCP is recorded as `mcp:<name>`; overrides only create drafts a person must approve | `test_mcp_marks_agent_feedback_and_validates_input` |
| Rule text in an export adds fields to SKILL.md frontmatter, closes the AGENTS.md markers, or fakes a rule heading | Exported text is flattened to one inert line; the description is a quoted YAML string | `test_exports_cannot_be_hijacked_by_rule_text` |
| An id like `../../x` reads or writes outside the rulebook | Ids are checked before they become file names | `test_ids_cannot_escape_the_rulebook` |
| Two reviewers approve at once and get the same rule number | Approvals take a cross-process lock file | `test_concurrent_approvals_get_distinct_rule_ids` |
| An agent keeps going after the rulebook changed underneath it | `check_action` halts the next step if a cited rule was retired, re-versioned or edited | `test_rule_withdrawn_mid_run_halts_the_next_step`, `test_rule_edited_without_version_bump_halts` |
| An agent stretches a decision beyond what was approved (a bigger amount, or "change the bank details" on an allergy answer) | A step with an amount or a high-risk word found in neither the decision nor its rules halts as `out_of_scope` | `test_out_of_scope_step_halts_with_an_artifact_and_a_gap`, `test_a_decision_cannot_authorise_a_riskier_step` |
| An agent reuses one approval for many actions, or days later | A decision closes after its after-Done check (`already_done`) and expires after `runtime.decision_ttl_minutes` (`expired`) | `test_one_decision_covers_one_action`, `test_decisions_expire` |
| An agent retries past a halt | Halts are final for the decision and its whole run | `test_halts_are_sticky_for_the_decision_and_its_run` |
| An agent signs off its own high-risk action | There is no sign-off over MCP; a NEEDS APPROVAL decision halts until a person runs `precedent signoff` | `test_agents_cannot_sign_off_over_mcp` |
| The execution gate errors or gets bad input | `handle_check_call` returns `halt`; it never raises to the agent | `test_bad_inputs` |
| Oversized inputs (cost, memory) | Situations over `runtime.max_situation_chars` (4000) are refused; MCP lines over 1 MB and model responses over 10 MB are rejected; MCP `limit` is capped at 100 | `test_tool_call_fails_closed` |

## Personal data and secrets

* **Redaction** runs on chat exports at ingest *and* on every situation before it reaches the
  model or the decision log (`ingest.redact`, `runtime.redact`, both on by default). It removes
  private keys, API tokens (OpenAI, Anthropic, AWS, Google, GitHub, Slack, JWTs), values after
  "password / PIN / OTP / token / api key", emails, phone numbers, card and IBAN numbers, IP
  addresses and URL query strings. Names, amounts and dates are kept because rules need them.
  This is a floor, not a guarantee: free-text addresses or ID numbers in unusual formats can slip
  through. Do not ingest chats you are not allowed to process.
* **Where data goes.** With `provider.kind: offline` nothing leaves the machine. With a model
  provider, redacted episodes and situations are sent to that provider under its data policy.
  Use a local model (Ollama, vLLM) if that is not acceptable.
* **API keys** are read from environment variables only, never from `precedent.yaml`, and never
  logged. A key is only sent over HTTPS, or plain HTTP to `localhost`.
* **On disk.** `logs/` and `sources/` are created owner-only (0700 on macOS/Linux). `sources/`
  is git-ignored. Decision logs are append-only and hold redacted situations.

## What Precedent does not do

* **No authentication.** Anyone who can write the rulebook directory can approve rules or edit
  them by hand. Put the rulebook in a git repository with protected branches and review rule
  changes in pull requests; each decision records the rule versions and content hashes it used,
  so an edit without a version bump is still visible in the audit trail.
* **The MCP server trusts its client.** It runs over stdio as a subprocess of the agent host;
  it does not listen on a network port.
* **It does not stop an agent that never asks.** Precedent is a gate the agent calls. Enforce
  the call in your agent framework (for example, a tool that can only run after
  `consult_rulebook` returned `answer` and `check_action` returned `continue`). A halt takes
  effect at the agent's next check, not mid-way through a single tool call.
* **Halts can be used to deny service.** Anyone who can call `report_outcome` with `halt`, or
  write to the rulebook folder, can halt a run. That fails closed, and the halt record says who
  did it (`mcp:<name>` for anything over MCP).
* **Scope checks are lexical.** The execution gate compares numbers and a list of high-risk
  words. A step written in other words ("five thousand", "wire it") can pass. Keep
  `risk.high_terms` up to date for your domain.
* **Number grounding is not semantic grounding.** An injected answer with no numbers ("cancel
  the order") that cites a real rule can still pass. Keep money, legal, health and irreversible
  actions on `high` risk, so a person signs off.

## Supply chain

One runtime dependency (PyYAML), plus the optional `certifi`. CI runs `pip-audit` on both,
GitHub Actions are pinned to commit SHAs and checkout does not keep credentials, and
Dependabot opens weekly update PRs for pip and Actions.
