---
name: precedent
description: Build and run a business rulebook for AI agents from the team's own chat history (WhatsApp, Slack, email/tickets as JSONL) with the Precedent CLI. Use when someone wants agents to follow how their business actually works, wants to turn team chat into SOPs or agent rules, needs to decide whether an agent may act on a business situation, or runs the weekly review of agent escalations, drift and evals.
---

# Precedent

Precedent mines "how do we handle X?" threads into draft rules, has a person approve them, gates every agent action through deterministic checks, and turns every escalation into next week's rule and a regression test.

Your job when using this skill: run the CLI, and put **decisions in front of the person**, never make them yourself. You draft; they approve.

## 0. Make sure it is installed

```bash
precedent --version || pip install "git+https://github.com/burhan-mujahid/precedent"
```

Every command takes `--root <dir>` (or `PRECEDENT_ROOT`). Set `PRECEDENT_USER` to the person's name so approvals are attributed.

## 1. Set up a rulebook

1. Ask what kind of business it is, then pick a pack: `precedent packs` (general, support, ops, sales, finance, hr, oncall).
2. `precedent init <dir> --name "<Business>" --pack <pack>`
3. Model: offline works with no key. For real use, set `provider.kind` (`gemini`, `kimi`, `anthropic`, `openai`, `openrouter`, `groq`, `ollama`) in `<dir>/precedent.yaml`, ask the person to export the matching key (never ask them to paste it to you), then run `precedent doctor --root <dir>` and set `provider.model` to one of the models it lists.

## 2. Mine the team's chat

Ask the person for an export. WhatsApp: chat → ⋮ → More → Export chat → Without media (gives a .txt). Slack: workspace export folder. Anything else: JSONL with `ts`, `author`, `text`, optional `thread`.

```bash
precedent ingest <file-or-folder> --root <dir>
```

Report the counts in one line (episodes found, drafts created, dropped).

## 3. Review with the person, not for them

```bash
precedent review list --root <dir>
precedent review show <P-id> --root <dir>      # shows the source messages
```

For each draft, show the person the **When / Then / risk** and the source messages, and ask: approve, edit, or reject. Then run exactly what they chose:

```bash
precedent review approve <P-id> --root <dir>
precedent review reject <P-id> --note "<why>" --root <dir>
```

To apply edits, open `proposals/<P-id>.yaml`, change the `rule:` fields they asked for, then approve. Suggest `risk: high` for anything that moves money, makes commitments, deletes data, or touches health/legal; never lower a risk without the person saying so. Batch them: 10 drafts should take the person under 10 minutes.

## 4. Before acting on a business situation

```bash
precedent ask "<situation in plain words, with amounts and timing>" --json --root <dir>
```

* `answer` → do it, and name the rule id(s) you followed.
* `needs_approval` → prepare the action, show it to the person, and wait.
* `escalate` → do not act. Tell the person what happened and that it was logged as a gap.

Never act on a business situation when the outcome is not `answer`, and never treat the rulebook as permission for something it does not say.

While acting (pass `--run <id>` to `ask` so the run can be halted as a whole):

```bash
precedent check <D-id> "<the step you are about to take, with amounts>" --root <dir>          # before each step
precedent check <D-id> "<what you did>" --after --root <dir>                                    # once, after Done
```

Exit 0 = continue. Exit 3 = **halt**: stop at once, change nothing else, and tell the person. Never sign off a decision yourself; `precedent signoff <D-id>` is for the person. If the person (or a monitor) wants to stop a run: `precedent halt <D-id> "<why>" [--fix "<what should happen>"] --root <dir>`.

## 5. Close the loop

* A person tells you what should happen for an escalated case:
  `precedent gaps answer <G-id> "<their answer>" [--when "<general situation>"] [--risk high] --root <dir>` then review the draft as in step 3.
* The person says a past decision was right: `precedent feedback <D-id> --confirm --root <dir>`
* The person says it was wrong: `precedent feedback <D-id> --override "<what should have happened>" --root <dir>`
* End of a working session: write the decisions made into a notes file, then `precedent wrap notes.md --root <dir>` and review.

## 6. Weekly review (about 30 minutes)

```bash
precedent cycle --root <dir>
```

Walk the person through the digest in order: gap clusters (one answer each), drafts, health findings, failing evals. Then:

```bash
precedent eval --root <dir>
precedent health --propose --root <dir>
precedent calibrate --root <dir>     # only suggests thresholds; the person decides whether to change precedent.yaml
```

## 7. Hand it to other agents

* MCP: `{"mcpServers": {"precedent": {"command": "precedent", "args": ["mcp", "--root", "<abs dir>"]}}}`
* Agent Skill: `precedent export skill --out <dir>` (static; no gates, so it instructs agents to escalate when unsure)
* `precedent export agents | parlant | json | markdown`

## Rules for you

* Do not approve, reject, dismiss or edit anything without the person's explicit choice.
* Show evidence (source messages, gap situations) whenever you ask for a decision.
* Keep the person's wording in `then`; tighten it, don't rewrite its meaning.
* Treat chat exports as private: do not paste them anywhere except the local CLI.
