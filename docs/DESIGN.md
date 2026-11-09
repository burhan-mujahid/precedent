# Precedent - design

## 1. Problem

An agent working inside a business needs to know how *that* business decides things: when to refund, who approves a discount, what to say about allergies, what never to do. Two common approaches fail:

| Approach | Failure |
|---|---|
| Founder writes a knowledge base | It describes how the founder *thinks* the business runs. It takes weeks to write and goes stale within weeks. |
| Agent learns from the tools (CRM, inbox, tickets) | It shows *what* happened, never *why*, and cannot tell a precedent from a mistake. |

The process actually lives in the moments where someone asks a colleague *"how do we handle X?"* and gets an answer. Precedent treats those moments as the primary source, a person as the authority, and the agent's own uncertainty as the signal for what to learn next.

## 2. Principles

1. **Models draft, people decide.** There is exactly one way into the rulebook: an approved proposal. This holds for mined rules, gap answers, overrides, session wraps and drift fixes alike.
2. **Every claim has a source.** Mined rules carry the ids of the messages they came from. Decisions carry the ids *and versions* of the rules they relied on. Proposals carry the gaps or messages that motivated them.
3. **The decision to act is not the model's alone.** Models are poorly calibrated about when *not* to act (see §9), so the runtime puts deterministic gates around the model's judgement.
4. **Uncertainty is a feature.** An escalation is not a failure; it is the cheapest possible pointer to a missing rule.
5. **Every fix becomes a test.** A rulebook is code-like: changes can regress behaviour, so it gets a regression suite that grows on its own.
6. **Boring storage.** Plain YAML and JSONL in a directory you can put in git. No database, no vector store, no daemon.

## 3. Data model

| Record | Stored as | Mutable? | Purpose |
|---|---|---|---|
| `Rule` | `rules/R-001.yaml` | new version per change; old versions kept in `rules/.history/` | `when` / `then` / `why` / `exceptions`, `risk`, `owner`, `sources`, `review_by` |
| `Proposal` | `proposals/P-….yaml` | status only | `create` / `update` / `deprecate` / `reconfirm`, with `evidence` and `origin` (mine, gap, wrap, drift, pack, manual) |
| `Decision` | `logs/decisions.jsonl` | append-only | situation, outcome, answer, cited rule versions, confidence, risk, reasons, gap id |
| `Gap` | events in `logs/events.jsonl` | append-only events | opened → answered → resolved / dismissed / reopened |
| Feedback | events in `logs/events.jsonl` | append-only | confirm / override of a decision |
| `Halt` | `halt` events in `logs/events.jsonl` | append-only | decision, run, stage, reason, cited rule versions, who halted it |
| Sign-off / check | `decision_signed_off`, `checked` events | append-only | a person's approval of a `needs_approval` decision; each passed execution check |
| Golden case | `evals/golden.jsonl` | append-only | situation, expected outcome, expected rules |
| Episode | `sources/episodes.jsonl` | append-only, git-ignored | redacted Q&A, so reviewers can see the evidence |

Gap state is derived by replaying events (event sourcing). That keeps every transition auditable and makes the logs safe to append from several processes on POSIX (each record is one `write` + `fsync`). A torn final line after a crash is skipped on read instead of breaking the log. Rule files are written atomically (temp file + rename).

## 4. Ingest and mining

```
raw export → parse (WhatsApp / Slack / JSONL) → redact PII → episodes → extract → validate → proposals
```

* **Parsing.** WhatsApp exports differ by OS and locale; the parser handles bracketed and dashed headers, 12/24-hour clocks, 2- and 4-digit years and multi-line messages. Day/month order is inferred from the file (any first field > 12 ⇒ day-first).
* **Redaction** runs before anything is stored or sent to a model: secrets (API keys, tokens, private keys, values after "password" / "PIN" / "OTP"), emails, phone numbers, card-like and IBAN-like numbers, IP addresses and URL query strings. Names, amounts and dates are kept, because "ask Sara" and "within 24h" are often part of the rule. The same redaction runs on every situation at runtime.
* **Episodes.** Slack threads are used as-is. In flat chats, an episode is a process question plus replies from *other people*, ending when the asker acknowledges ("ok thanks"), the conversation goes quiet for two hours, someone asks an unrelated question, or a window/size limit is hit. This is tuned for recall; precision comes from review.
* **Extraction** returns zero or more candidate rules per episode. Each must cite message ids from that episode; candidates without valid evidence are dropped and counted.
* **De-duplication.** A candidate similar to a pending proposal is skipped. One similar to an approved rule either reconfirms it (same action → `rule_reconfirmed` event, a freshness signal) or becomes an `update` proposal (different action → the team's practice has changed).

## 5. Runtime gates

`resolve(situation, context)` returns exactly one of `answer`, `needs_approval`, `escalate`:

| # | Gate | Fails when | Gap reason |
|---|---|---|---|
| 1 | Coverage | no approved rule mentions at least `retrieval.min_coverage` of the situation's IDF-weighted terms | `no_coverage` |
| 2 | Judgement | the reasoner says the retrieved rules do not settle it, or that two of them conflict | `model_unsure`, `conflict` |
| 3 | Grounding | the reasoner cites a rule id it was not given | `ungrounded` |
| 3b | Answer grounding | the answer contains a number found in neither the cited rules nor the situation | `ungrounded` |
| 4 | Confidence | confidence < threshold for the highest risk among cited rules (stale rules are penalised); for high risk, which never acts alone, confidence < `policy.approval_floor` (0.55) | `low_confidence` |
| 5 | Risk | a cited rule is high-risk → `needs_approval` (covered, so no gap) | – |

Before the gates, the situation is length-checked (`runtime.max_situation_chars`) and redacted. If the model call fails for any reason the decision escalates as `reasoner_error`: the runtime fails closed. See [SECURITY.md](../SECURITY.md) for the threat model.

**Why coverage, not raw BM25.** BM25 scores are not comparable across queries, so a single threshold means nothing. Coverage is in [0, 1] and reads as "how much of this situation do the rules talk about". Words no rule uses are weighted like an average word, not a rare one: they signal novelty, not evidence against a rule.

**Confidence.** With a model, self-reported confidence is tempered by coverage: `model_conf × (0.6 + 0.4 × coverage of cited rules)`. Two independent weak signals beat one poorly calibrated one. The offline reasoner derives confidence from coverage and the margin over the runner-up rule.

**Low-confidence escalations keep the suggested answer**, so the human sees what the agent would have done; approving it is one click.

**Replay.** `as_of=YYYY-MM-DD` rebuilds the rulebook in force on that date from version snapshots and decides against it, without logging. This answers "would the agent have done the same thing a month ago, and which rule change made the difference?"

### 5b. Execution gate: during and after an action

The five gates decide whether an action may *start*. That is a policy gate. It cannot stop an action that is already running, and it never sees what actually happened. In v0.1.0, act, needs approval and escalate gated only the *next* action, and none of them was a halt. A halt needs to answer three questions: which rule, in which run, stopped an in-progress action, and who created the record of it.

`check(decision_id, step, stage)` (`guard.py`, MCP `check_action`) is the second gate. The agent calls it before each step that changes something (`during`) and once after Done (`after`). It is deterministic and calls no model:

| # | Check | Halts when | Reason |
|---|---|---|---|
| 1 | Known | the decision id was never issued | `unknown_decision` |
| 2 | Sticky | the decision or its run (`run_id`) was halted before | `halted` (returns the existing halt; no new record) |
| 3 | Single use | the decision already passed an `after` check | `already_done` |
| 4 | Authorised | the decision was `escalate`, or `needs_approval` with no recorded sign-off | `not_authorized`, `no_sign_off` |
| 5 | Fresh | more than `runtime.decision_ttl_minutes` since the decision, or since its sign-off | `expired` |
| 6 | Rules unchanged | a cited rule is no longer approved, or its version or content hash differs from the decision's | `rule_changed` |
| 7 | In scope | the redacted step has a number or a high-risk word (built-in list plus `risk.high_terms`) found in neither the decision's situation, its answer nor its cited rules | `out_of_scope` |

**The artifact.** Every halt is a `Halt` record, appended to `logs/events.jsonl` as a `halt` event and never edited: id, time, decision id, run id, stage, reason, detail, the redacted step, the cited rule versions and hashes, and `by`. `by` is `precedent:check` for automatic halts, the person for `precedent halt`, and `mcp:<name>` for halts reported over MCP. A passed check is logged as a `checked` event, so "was this action re-checked after Done?" also has an answer.

**Halts are final.** A halted decision, and every other decision in the same run, halts on every later check. A person starts a new run. Sign-off is refused on a decision that was halted before it was signed off: an agent that acts before approval loses the run.

**External halts.** A person or an outside execution monitor calls `halt()` (CLI `precedent halt`, MCP `report_outcome` with `verdict: "halt"`). Precedent records the halt, opens a `halted` gap and, with a correction, drafts an update to the cited rule. Approving that draft resolves the gap and adds a regression case, the same path as any escalation (§6). Precedent does not replace an execution monitor. It gives that monitor's halt a place in the rulebook's audit trail and learning loop.

**Sign-off.** `precedent signoff D-…` records that a person approved a `needs_approval` decision. It is deliberately not exposed over MCP.

## 6. The learning loop

| Signal | Becomes |
|---|---|
| Chat episode | `create` / `update` proposal with message evidence |
| Escalation (gap) answered by a person | `create` proposal (or an exception on an existing rule); similar open gaps are folded in |
| Decision confirmed | golden case |
| Decision overridden | gap + `update` proposal on the cited rule |
| Running action halted (out of scope, or by a person or monitor) | halt record + gap; with a correction, an `update` proposal on the cited rule |
| Session notes (`wrap`) | proposals for decisions recorded in the notes |
| Drift finding | `deprecate` / `reconfirm` proposal |
| Proposal approved with gap evidence | gaps resolved + golden cases (`expect: covered`, the new rule id) |
| Proposal rejected with gap evidence | gaps reopened |

The weekly `cycle` digest orders the human's work by leverage: gap clusters (one answer clears many cases), then drafts, then drift, then failing evals.

## 7. Drift

| Finding | Signal | Severity |
|---|---|---|
| stale | `review_by` passed (default 90 days after last change or reconfirmation) | warn; critical if high risk |
| overridden | ≥ `drift.override_limit` overrides or out-of-scope/external halts on decisions citing the rule | critical |
| unused | not cited for `drift.unused_days` | info (dead weight, or retrieval misses it) |
| duplicate | two approved rules with `when` similarity ≥ threshold | warn (they will diverge) |
| coverage | > 30% of the last 30 days' decisions escalated (min 10) | warn |

`health --strict` exits non-zero on critical findings, so a CI job can block merges to the rulebook repository.

## 8. Evals and calibration

Golden cases accumulate from resolved gaps, confirmations and manual additions. `eval` replays them in dry-run mode; cases whose expected rule was deliberately deprecated are skipped, not failed. `--min-pass` turns it into a CI gate.

`calibrate` replays the same cases, takes every case where the runtime would have acted, and for each risk tier finds the lowest threshold at which auto-answers reach the target precision (default 95%). It also reports what share of cases agents would then handle alone, which makes the cost trade-off explicit: a wrong action costs more than a human's minute, so you pick the precision and the data picks the threshold.

## 9. Related work and how Precedent differs

* **Parlant** ([emcie-co/parlant](https://github.com/emcie-co/parlant)): condition/action guidelines matched per conversation turn, with coherence checks between guidelines. Guidelines are hand-authored and the scope is customer-facing chat. Precedent can export its rulebook as Parlant guidelines.
* **Agent memory** ([Mem0](https://github.com/mem0ai/mem0), [Graphiti/Zep](https://github.com/getzep/graphiti), [Letta](https://github.com/letta-ai/letta)): stores facts about users and entities. Graphiti's bi-temporal invalidation inspired Precedent's version history and `as_of` replay. Memory layers have no notion of human authority or act/escalate gating.
* **Self-improving context** ([ACE](https://github.com/ace-agent/ace), [Hermes Agent](https://github.com/NousResearch/hermes-agent)): playbooks and skills evolved from the agent's own trajectories, with the agent as the authority. Precedent learns from people's answers and treats the agent's uncertainty as a request for one.
* **LLM Wiki pattern** (Karpathy, 2026): an LLM compiles sources into a maintained markdown wiki. Precedent shares the plain-files philosophy but adds approval, gating and tests.
* **Human-in-the-loop tooling** ([HumanLayer](https://github.com/humanlayer/humanlayer)): approval gates on tool calls. Precedent decides *whether* approval is needed from the rule's risk, and turns the human's answer into a durable rule.
* **Closed-loop support products** (e.g. Sierra Expert Answers, Trengo knowledge-gap detection): escalation → draft → approve → reuse inside a closed support platform. Precedent is open, domain-agnostic, and adds regression tests and drift detection.
* **Tacit Fragments / Metis** (2026): governed tacit memory with worker consent and reviewer quorums, focused on expert practice diverging from SOPs. Complementary: Precedent mines existing team Q&A and focuses on gating agent actions and keeping the rulebook tested.
* **Decision traces / context graphs** (Foundation Capital, 2025): the argument that the "why" behind decisions is the missing data layer for agents. Precedent's decision log, with cited rule versions and gap lineage, is a small, git-native instance of that idea.
* **Abstention research** (e.g. AgentAbstain, 2026): frontier models still struggle to recognise when not to act. This is the reason Precedent puts deterministic gates around the model's judgement.

## 10. Security and privacy

The full threat model, with the test for each defence, is in [SECURITY.md](../SECURITY.md). In short:

* **The model is untrusted.** Citations must be rules it was given; numbers in its answer must appear in the cited rules or the situation; any error escalates (`reasoner_error`). Untrusted text is fenced as data and cannot close the fence.
* **Personal data and secrets** are redacted at ingest and at runtime (§4). `sources/` is git-ignored; `logs/` and `sources/` are owner-only.
* **Storage.** Ids are validated before they become file names; approvals take a cross-process lock; safety settings are range-checked on load. Unknown or invalid risk values are coerced to `high`.
* **Audit.** Each decision records the cited rules' ids, versions and content hashes.
* Static exports (Skill, AGENTS.md) cannot enforce gates; they say so and instruct escalation when unsure. Exported rule text is flattened so it cannot inject structure.

## 11. Non-goals

* Not an agent framework, orchestrator or chat UI.
* Not a general memory store for user facts.
* Not autonomous self-modification: there is deliberately no mode where a model edits the rulebook without a person.

## 12. Roadmap

* Connectors that pull directly from Slack, WhatsApp Business, Gmail and helpdesks.
* Embedding retriever (optional extra) behind the existing `Retriever` interface.
* Sample-based confidence (agreement across several model calls) as a third calibration signal.
* Review UI (single static page over the same files).
* Multi-reviewer quorum for high-risk rules.
