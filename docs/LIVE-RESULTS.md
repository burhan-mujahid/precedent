# Live model results

Real output from one run on 2026-11-09T07:21Z, Precedent 0.1.0. Nothing below is edited: the tables are generated from
each rulebook's `logs/decisions.jsonl` and `precedent export json`, and the transcripts are the CLI's stdout.
One exception: the offline baseline's situation 3 was re-run at 11:40Z after adding `policy.approval_floor`
(below), because the original 0.52 → NEEDS APPROVAL no longer matches the code. The offline reasoner is
deterministic, so nothing else in its column changes. Every model decision on this page cleared the new floor.

**Setup.** For each reasoner: `precedent init --pack support`, then `precedent ingest examples/bakery/ops-group.txt`,
approve every draft, then `precedent ask` with the five situations from `examples/demo.sh`.

| | Model | Ingest (9 Q&A threads) | Tokens used by ingest |
|---|---|---|---|
| Gemini | `gemini-3.8-flash` | 9 drafts, 9.4s | 4235 in / 1517 out (9 calls) |
| Kimi | `kimi-k3` | 9 drafts, 57.0s | 4797 in / 5289 out (9 calls) |
| Offline baseline | `BM25 + coverage (no model)` | 9 drafts, 0.3s | none |

Ingest runs 4 model calls in parallel (`ingest.workers`). Each `ask` is one model call (none when the coverage
gate escalates first); times are in the transcripts below.

## Decisions side by side

### 1. Customer asks if the brownies are safe for someone with a nut allergy

| | Outcome | Cited rule | Confidence | Answer / reason |
|---|---|---|---|---|
| Gemini | **NEEDS APPROVAL** | R-009 v1 (Handling nut allergy inquiries) | 0.80 | Do not promise that the brownies are nut-free. State that everything is made in a kitchen that handles nuts, and if the customer requires a guarantee, politely explain that we cannot help this time. |
| Kimi | **NEEDS APPROVAL** | R-009 v1 (Never promise nut-free to customers) | 0.77 | Tell the customer that everything is made in a kitchen that handles nuts, so we cannot promise the brownies are nut-free. If they need a guarantee, politely say we can't help this time. |
| Offline baseline | **NEEDS APPROVAL** | R-009 v1 (Handling allergy questions about nuts) | 0.68 | We never promise nut-free. Everything is made in a kitchen that handles nuts and we say exactly that. If they need a guarantee we politely say we can't help this time. |

### 2. A delivery is running about an hour and a half late

| | Outcome | Cited rule | Confidence | Answer / reason |
|---|---|---|---|---|
| Gemini | **ACT** | R-004 v1 (Delivery running over an hour late) | 0.86 | Message the customer before they message us, provide the updated ETA, and add a free cookie box to the order. |
| Kimi | **NEEDS APPROVAL** | R-004 v1 (Proactively message customers for late deliveries) | 0.86 | Proactively message the customer before they contact us: inform them of the delay, give the new ETA, and add a free cookie box to the order. |
| Offline baseline | **ACT** | R-004 v1 (When a delivery is running more than an hour late) | 0.89 | Message the customer before they message us. Give the new ETA and add a free cookie box to the order. Because last time we stayed quiet we got two 1-star reviews. |

### 3. Customer wants to cancel their order for the day after tomorrow

| | Outcome | Cited rule | Confidence | Answer / reason |
|---|---|---|---|---|
| Gemini | **NEEDS APPROVAL** | R-011 v1 (Cancellation refund process) | 0.71 | Cancel the order and issue a full refund, as the cancellation is more than 24 hours before delivery. |
| Kimi | **NEEDS APPROVAL** | R-011 v1 (Full refund if cancelled 24h+ before delivery) | 0.67 | Give the customer a full refund, since the delivery is the day after tomorrow (more than 24 hours away). |
| Offline baseline | **ESCALATE** | R-011 v1 (When someone wants to cancel an order) | 0.52 | low_confidence: 0.52 < 0.55 approval floor for high risk (suggested: More than 24h before delivery: full refund. Less than 24h: we keep 50% unless it's a custom cake, then no refund since we've already bought the stuff.) |

### 4. Customer says the birthday cake arrived damaged

| | Outcome | Cited rule | Confidence | Answer / reason |
|---|---|---|---|---|
| Gemini | **NEEDS APPROVAL** | R-003 v1 (Damaged cake complaint resolution) | 0.88 | Ask the customer for a photo of the damaged cake first. If the damage is clear, offer them their choice of a free remake or a full refund, without arguing. |
| Kimi | **NEEDS APPROVAL** | R-003 v1 (Handling cakes that arrive damaged) | 0.86 | Ask the customer for a photo of the damage first. If the damage is clear, offer them their choice of a free remake or a full refund, and do not argue about it. |
| Offline baseline | **NEEDS APPROVAL** | R-003 v1 (Handling a customer who says their cake arrived damaged) | 0.88 | Ask for a photo first. If the damage is clear we remake it for free or refund in full, their choice. Don't argue about it, it's never worth the review. |

### 5. Supplier emailed saying their bank account has changed

| | Outcome | Cited rule | Confidence | Answer / reason |
|---|---|---|---|---|
| Gemini | **ESCALATE** | - | - | no_coverage: best match R-006 at 0.21 < 0.3 |
| Kimi | **ESCALATE** | - | - | no_coverage: best match R-006 at 0.21 < 0.3 |
| Offline baseline | **ESCALATE** | - | - | no_coverage: best match R-006 at 0.21 < 0.3 |

## Rules each reasoner drafted from the same chat

R-001 and R-002 come from the support pack. Risk decides whether an agent may act alone, so differences matter.

| Rule | Gemini | Kimi | Offline baseline |
|---|---|---|---|
| R-001 | Customer threatens legal action (**high**) | Customer threatens legal action (**high**) | Customer threatens legal action (**high**) |
| R-002 | Customer asks to delete their data (**high**) | Customer asks to delete their data (**high**) | Customer asks to delete their data (**high**) |
| R-003 | Damaged cake complaint resolution (**high**) | Handling cakes that arrive damaged (**high**) | Handling a customer who says their cake arrived damaged (**high**) |
| R-004 | Delivery running over an hour late (**medium**) | Proactively message customers for late deliveries (**high**) | When a delivery is running more than an hour late (**medium**) |
| R-005 | Cupcake bulk order discounts (**high**) | Bulk cupcake order discount tiers (**high**) | Whether we can give a discount to someone ordering 20+... (**high**) |
| R-006 | Handling supplier invoices that mismatch delivery (**high**) | Hold mismatched supplier invoices, send photos to Ayesha (**high**) | Deciding who handles supplier invoices that don't match the delivery (**high**) |
| R-007 | Custom cakes with under 48 hours notice (**medium**) | Short-notice custom cake requests (**medium**) | If a customer asks for a custom cake with less... (**medium**) |
| R-008 | Replying to bad Google reviews (**medium**) | Reply to bad Google reviews within 24 hours (**medium**) | Deciding whether to reply to bad Google reviews (**medium**) |
| R-009 | Handling nut allergy inquiries (**high**) | Never promise nut-free to customers (**high**) | Handling allergy questions about nuts (**high**) |
| R-010 | Donating unsold bread at day end (**low**) | Donate unsold bread after 7pm (**low**) | Whether we keep unsold bread at the end... (**low**) |
| R-011 | Cancellation refund process (**high**) | Full refund if cancelled 24h+ before delivery (**high**) | When someone wants to cancel an order (**high**) |

## What this shows

* **Same chat, same rules, mostly the same calls.** All three reasoners drafted 9 rules from the 9 threads, each
  citing real message ids, and settled situations 1 and 4 the same way. The bank-details email (5) escalated
  everywhere: no rule covers it, which is the gap the demo then closes.
* **Models apply the rule to the case; the baseline only quotes it.** On the cancellation (3), both models worked
  out that "the day after tomorrow" is more than 24 hours away and answered "full refund"; the offline reasoner
  only matched two words ("cancel", "order"), scored 0.52, and escalated it to a person. That is the difference a
  model buys.
* **Models disagree on risk, which is why a person approves every draft.** Kimi rated "message the customer and add
  a free cookie box" as high risk; Gemini and the baseline said medium. So with Kimi's draft the agent needs a
  human for situation 2, and with Gemini's it acts alone. The reviewer settles this once, at approval time.
* **Runs vary even at temperature 0.** An earlier Gemini run with the same prompts and gate logic escalated situation 3
  ("the order details are missing" - it could be a custom cake) and, on situation 5, passed the coverage gate
  but had the model decline to stretch the invoice rule to a bank-details change. Both are safe outcomes; the
  transcripts are at the end. The gates are built for this: a model that says it is unsure escalates, and
  even a confident answer on a high-risk rule goes to a person first. A confident wrong answer on a low- or
  medium-risk rule can still act; overrides (`precedent feedback --override`) and drift checks catch those.
* **Speed and tokens.** Gemini Flash: ingest in about 9 s, 3-4 s per decision. Kimi K3 reasons before it answers:
  about 3.5x the output tokens, ingest in about a minute, 7-14 s per decision. No call hit the configured
  `max_tokens` (Gemini 8000, Kimi 4000). This page does not estimate prices; check the providers' current rates.
* **Scope.** One sample chat and five situations. This is a smoke test of the integration, not a benchmark.

## What live testing changed in the code

Each item has a regression test with a fake client in `tests/test_hardening.py`.

| Found live | Fix |
|---|---|
| python.org macOS Python has no CA certificates, so every HTTPS call failed with a TLS error after 4 retries | Use `certifi` when the system store is empty (`pip install "precedent-ai[certs]"`); otherwise fail once with instructions |
| Kimi K3 only accepts `temperature: 1`. With parallel mining, threads raced on the shared "drop rejected parameter" state and 3 of 9 threads failed | Per-call copy of optional parameters, shared state behind a lock |
| Gemini rated the nut-allergy rule **medium** (the agent could act alone) because the extract prompt's "high risk" list left out health and safety | Prompt now lists health/safety and personal data; a draft's risk can no longer be lower than the keyword floor |
| A first prompt fix ("when unsure, choose the higher level") made Gemini rate *every* customer-facing rule high, so no decision could act alone | Removed; the keyword floor covers the dangerous categories without blanket over-escalation |
| Titles up to 80 characters; conditions copied into `exceptions` | Prompt limits titles to 8 words and defines an exception |
| Mining was sequential (9 threads: 32 s on Gemini) | `ingest.workers` (default 4); 9 threads now take about 9 s |
| No way to see what a run cost | Clients count calls and tokens; `precedent ingest` prints them |
| High-risk decisions skipped the confidence gate, so the baseline's weak 0.52 match on the cancellation rule went to sign-off as if the rule clearly applied, and no gap was logged | `policy.approval_floor` (default 0.55, the low-risk bar): below it a high-risk match escalates as `low_confidence` and keeps the suggested answer (test in `tests/test_runtime.py`) |

To reproduce: `GEMINI_API_KEY=... MOONSHOT_API_KEY=... pytest -m live -v` (8 tests, about a minute), or follow
the setup above with `precedent doctor` to pick current model names.

## Raw transcripts

<details><summary>Gemini (<code>gemini-3.8-flash</code>)</summary>

```
$ precedent ingest examples/bakery/ops-group.txt
Read 33 messages -> 9 new Q&A episode(s)
  9 new rule draft(s), 0 update draft(s), 0 already in the rulebook, 0 duplicate(s), 0 dropped (no valid evidence)
  9 model call(s), 4235 input / 1517 output tokens
Next: precedent review

$ precedent ask "Customer asks if the brownies are safe for someone with a nut allergy"
[NEEDS APPROVAL] D-20261109T0722-13ca  confidence 0.80  risk high
  answer : Do not promise that the brownies are nut-free. State that everything is made in a kitchen that handles nuts, and if the customer requires a guarantee, politely explain that we cannot help this time.
  rules  : R-009 v1
  why    : reasoner: The customer asked if an item is safe for someone with a nut allergy, which directly matches Rule R-009 ('asked about nut allergies or if food is nut-free'). The rule provides specific instructions on what to state.
  why    : high-risk action: a human must approve
(3.0s)

$ precedent ask "A delivery is running about an hour and a half late"
[ACT] D-20261109T0722-b4d9  confidence 0.86  risk medium
  answer : Message the customer before they message us, provide the updated ETA, and add a free cookie box to the order.
  rules  : R-004 v1
  why    : reasoner: The delivery is running an hour and a half late, which is more than an hour late, directly triggering rule R-004.
(3.4s)

$ precedent ask "Customer wants to cancel their order for the day after tomorrow"
[NEEDS APPROVAL] D-20261109T0722-192f  confidence 0.71  risk high
  answer : Cancel the order and issue a full refund, as the cancellation is more than 24 hours before delivery.
  rules  : R-011 v1
  why    : reasoner: The order is scheduled for the day after tomorrow, which is more than 24 hours before delivery. Rule R-011 specifies issuing a full refund under these conditions.
  why    : high-risk action: a human must approve
(4.0s)

$ precedent ask "Customer says the birthday cake arrived damaged"
[NEEDS APPROVAL] D-20261109T0722-f747  confidence 0.88  risk high
  answer : Ask the customer for a photo of the damaged cake first. If the damage is clear, offer them their choice of a free remake or a full refund, without arguing.
  rules  : R-003 v1
  why    : reasoner: The situation matches rule R-003 directly, which specifies asking for a photo first and offering a full refund or remake if damage is verified.
  why    : high-risk action: a human must approve
(3.6s)

$ precedent ask "Supplier emailed saying their bank account has changed"
[ESCALATE] D-20261109T0722-b834
  why    : no_coverage: best match R-006 at 0.21 < 0.3
  gap    : G-20261109T0722-37be (answer it with `precedent gaps answer G-20261109T0722-37be "..."`)
(0.3s)
```

</details>

<details><summary>Kimi (<code>kimi-k3</code>)</summary>

```
$ precedent ingest examples/bakery/ops-group.txt
Read 33 messages -> 9 new Q&A episode(s)
  9 new rule draft(s), 0 update draft(s), 0 already in the rulebook, 1 duplicate(s), 0 dropped (no valid evidence)
  9 model call(s), 4797 input / 5289 output tokens
Next: precedent review

$ precedent ask "Customer asks if the brownies are safe for someone with a nut allergy"
[NEEDS APPROVAL] D-20261109T0722-e03b  confidence 0.77  risk high
  answer : Tell the customer that everything is made in a kitchen that handles nuts, so we cannot promise the brownies are nut-free. If they need a guarantee, politely say we can't help this time.
  rules  : R-009 v1
  why    : reasoner: R-009 matches this situation directly: it covers customer allergy questions about nuts and prescribes the exact response — state the kitchen handles nuts, never promise nut-free, and decline if a guarantee is required. No other rules or exceptions apply.
  why    : high-risk action: a human must approve
(13.6s)

$ precedent ask "A delivery is running about an hour and a half late"
[NEEDS APPROVAL] D-20261109T0723-9a3b  confidence 0.86  risk high
  answer : Proactively message the customer before they contact us: inform them of the delay, give the new ETA, and add a free cookie box to the order.
  rules  : R-004 v1
  why    : reasoner: The delivery is about 1.5 hours late, which is clearly more than one hour late, matching R-004's trigger condition almost exactly. The rule prescribes a specific action (proactive customer message with new ETA plus free cookie box) and lists no exceptions.
  why    : high-risk action: a human must approve
(7.0s)

$ precedent ask "Customer wants to cancel their order for the day after tomorrow"
[NEEDS APPROVAL] D-20261109T0723-b38d  confidence 0.67  risk high
  answer : Give the customer a full refund, since the delivery is the day after tomorrow (more than 24 hours away).
  rules  : R-011 v1
  why    : reasoner: R-011 states that cancellations made more than 24 hours before delivery receive a full refund. An order for the day after tomorrow is roughly 48 hours away, which satisfies the 'more than 24h' condition. R-010 is unrelated (it concerns unsold bread). No exceptions apply and there is no conflict.
  why    : high-risk action: a human must approve
(14.0s)

$ precedent ask "Customer says the birthday cake arrived damaged"
[NEEDS APPROVAL] D-20261109T0723-9629  confidence 0.86  risk high
  answer : Ask the customer for a photo of the damage first. If the damage is clear, offer them their choice of a free remake or a full refund, and do not argue about it.
  rules  : R-003 v1
  why    : reasoner: R-003 matches the situation almost word for word: a customer reporting a cake arrived damaged. The rule prescribes a clear sequence—request a photo, then if damage is evident, let the customer choose between a free remake or full refund. No exceptions apply.
  why    : high-risk action: a human must approve
(13.1s)

$ precedent ask "Supplier emailed saying their bank account has changed"
[ESCALATE] D-20261109T0723-8cc3
  why    : no_coverage: best match R-006 at 0.21 < 0.3
  gap    : G-20261109T0723-2c9b (answer it with `precedent gaps answer G-20261109T0723-2c9b "..."`)
(0.3s)
```

</details>

<details><summary>Offline baseline (<code>BM25 + coverage (no model)</code>)</summary>

```
$ precedent ingest examples/bakery/ops-group.txt
Read 33 messages -> 9 new Q&A episode(s)
  9 new rule draft(s), 0 update draft(s), 0 already in the rulebook, 0 duplicate(s), 0 dropped (no valid evidence)
Next: precedent review

$ precedent ask "Customer asks if the brownies are safe for someone with a nut allergy"
[NEEDS APPROVAL] D-20261109T0721-dfa2  confidence 0.68  risk high
  answer : We never promise nut-free. Everything is made in a kitchen that handles nuts and we say exactly that. If they need a guarantee we politely say we can't help this time.
  rules  : R-009 v1
  why    : reasoner: matched R-009 on: allergy, nut
  why    : high-risk action: a human must approve
(0.3s)

$ precedent ask "A delivery is running about an hour and a half late"
[ACT] D-20261109T0721-40cd  confidence 0.89  risk medium
  answer : Message the customer before they message us. Give the new ETA and add a free cookie box to the order. Because last time we stayed quiet we got two 1-star reviews.
  rules  : R-004 v1
  why    : reasoner: matched R-004 on: delivery, hour, late, running
(0.3s)

$ precedent ask "Customer wants to cancel their order for the day after tomorrow"
[ESCALATE] D-20261109T1140-bac9  confidence 0.52  risk high
  answer : More than 24h before delivery: full refund. Less than 24h: we keep 50% unless it's a custom cake, then no refund since we've already bought the stuff.
  rules  : R-011 v1
  why    : reasoner: matched R-011 on: cancel, order
  why    : low_confidence: 0.52 < 0.55 approval floor for high risk
  gap    : G-20261109T1140-2de6 (answer it with `precedent gaps answer G-20261109T1140-2de6 "..."`)
(0.3s)

$ precedent ask "Customer says the birthday cake arrived damaged"
[NEEDS APPROVAL] D-20261109T0721-779d  confidence 0.88  risk high
  answer : Ask for a photo first. If the damage is clear we remake it for free or refund in full, their choice. Don't argue about it, it's never worth the review.
  rules  : R-003 v1
  why    : reasoner: matched R-003 on: arrived, cake, customer, damaged
  why    : high-risk action: a human must approve
(0.3s)

$ precedent ask "Supplier emailed saying their bank account has changed"
[ESCALATE] D-20261109T0721-65a3
  why    : no_coverage: best match R-006 at 0.21 < 0.3
  gap    : G-20261109T0721-0931 (answer it with `precedent gaps answer G-20261109T0721-0931 "..."`)
(0.3s)
```

</details>

<details><summary>Earlier Gemini run, same prompts and gate logic (variance, see above). Printed before escalations stopped showing a confidence/risk line.</summary>

```
$ precedent ask "Customer wants to cancel their order for the day after tomorrow"
[ESCALATE] D-20261109T0718-c05f  confidence 0.00  risk low
  why    : reasoner: The cancellation request is for the day after tomorrow (more than 24 hours in advance), but rule R-011 notes special conditions for custom cakes, and the order details are missing.
  gap    : G-20261109T0718-26fc (answer it with `precedent gaps answer G-20261109T0718-26fc "..."`)

$ precedent ask "Supplier emailed saying their bank account has changed"
[ESCALATE] D-20261109T0718-33b0  confidence 0.00  risk low
  why    : reasoner: Rule R-006 only applies when an invoice does not match the delivery, which does not cover bank account change notifications.
  gap    : G-20261109T0718-1bbc (answer it with `precedent gaps answer G-20261109T0718-1bbc "..."`)

```

</details>
