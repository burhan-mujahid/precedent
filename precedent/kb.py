"""Precedent: the one object most code needs.

    from precedent import Precedent

    kb = Precedent.open("./my-rulebook")
    d = kb.resolve("Customer wants a refund, the order arrived 3 days late")
    if d.outcome == "answer":
        act(d.answer)                       # d.cited -> [{"id": "R-004", "version": 2}]
    elif d.outcome == "needs_approval":
        ask_human_to_approve(d.answer)
    else:
        hand_off(d)                         # a gap was logged; it will come back as a rule

Framework-agnostic: wrap `tool_spec()` / `handle_tool_call()` as a tool in LangGraph,
CrewAI, the OpenAI or Anthropic SDKs, or run `precedent mcp` for any MCP client.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

from . import drift, evals, loop
from .calibrate import TierResult, calibrate
from .drift import Finding
from .evals import EvalResult
from .ingest import build_episodes, load_messages
from .llm import make_client
from .models import Decision, Gap, Proposal, Rule
from .packs import get_pack
from .reasoner import Reasoner, make_reasoner
from .retrieve import jaccard
from .runtime import Resolver
from .store import Store

AGENT_INSTRUCTIONS = (
    "Call consult_rulebook before acting on any business situation (customers, suppliers, money, data, staff). "
    "If outcome is 'answer', do what it says and mention the rule ids. If 'needs_approval', prepare the action "
    "but get a human to approve it first. If 'escalate', do not act: tell the user a person will decide."
)


class Precedent:
    def __init__(self, store: Store, reasoner: Reasoner | None = None):
        self.store = store
        cfg = store.config
        self.reasoner = reasoner or make_reasoner(make_client(cfg.get("provider")),
                                                  cfg.get("risk.high_terms") or [], cfg.get("risk.medium_terms") or [])
        self.resolver = Resolver(store, self.reasoner)

    # ------------------------------------------------------------------ lifecycle
    @classmethod
    def open(cls, root: str | Path = ".", reasoner: Reasoner | None = None) -> Precedent:
        store = Store(root)
        store.ensure()
        return cls(store, reasoner)

    @classmethod
    def init(cls, root: str | Path, name: str | None = None, pack: str = "general",
             by: str = "owner") -> Precedent:
        store = Store.init(root, name)
        p = get_pack(pack)
        store.config.data["pack"] = pack
        store.config.data["risk"] = {"high_terms": p["risk"]["high"], "medium_terms": p["risk"]["medium"]}
        store.config.save(store.root / "precedent.yaml")
        kb = cls(store, make_reasoner(None))
        for raw in p["rules"]:
            prop = loop.Proposal(id=loop.new_id("P"), kind="create", rule=dict(raw),
                                 rationale=f"starter rule from the '{pack}' pack", origin="pack")
            store.save_proposal(prop)
            loop.approve(store, prop.id, by=f"pack:{pack}")
        return kb

    # ------------------------------------------------------------------ runtime
    def resolve(self, situation: str, context: dict[str, Any] | str | None = None, *,
                dry_run: bool = False, as_of: str | None = None) -> Decision:
        return self.resolver.resolve(situation, context, dry_run=dry_run, as_of=as_of)

    # ------------------------------------------------------------------ learning loop
    def ingest(self, path: str | Path, fmt: str = "auto", channel: str | None = None,
               progress: Callable[[int, int], None] | None = None) -> loop.MineReport:
        cfg = self.store.config
        msgs = load_messages(Path(path), fmt=fmt, channel=channel)
        eps = build_episodes(msgs, window_hours=float(cfg.get("ingest.episode_window_hours", 12)),
                             max_replies=int(cfg.get("ingest.episode_max_replies", 8)),
                             redact_pii=bool(cfg.get("ingest.redact", True)))
        rep = loop.mine(self.store, self.reasoner, eps, progress=progress)
        rep.messages = len(msgs)
        return rep

    def approve(self, pid: str, by: str, edits: dict[str, Any] | None = None) -> Rule:
        return loop.approve(self.store, pid, by, edits)

    def reject(self, pid: str, by: str, note: str = "") -> Proposal:
        return loop.reject(self.store, pid, by, note)

    def answer_gap(self, gid: str, answer: str, by: str, **kw: Any) -> Proposal:
        return loop.answer_gap(self.store, gid, answer, by, **kw)

    def feedback(self, decision_id: str, by: str, *, confirm: bool = False, correction: str = "") -> str:
        return loop.feedback(self.store, decision_id, by, confirm=confirm, correction=correction)

    def wrap(self, notes: str, by: str) -> list[Proposal]:
        return loop.wrap(self.store, self.reasoner, notes, by)

    # ------------------------------------------------------------------ quality
    def health(self) -> tuple[list[Finding], dict[str, Any]]:
        return drift.health(self.store)

    def eval(self) -> EvalResult:
        return evals.run(self.store, self.resolver)

    def calibrate(self, target: float = 0.95) -> list[TierResult]:
        return calibrate(self.store, self.resolver, target=target)

    def cycle(self) -> str:
        """The weekly digest: everything a founder needs to spend 30 minutes on, in priority order."""
        gaps = self.store.gaps("open")
        clusters: list[list[Gap]] = []
        for g in sorted(gaps, key=lambda g: g.ts):
            for c in clusters:
                if jaccard(g.situation, c[0].situation) >= 0.35:
                    c.append(g)
                    break
            else:
                clusters.append([g])
        clusters.sort(key=len, reverse=True)
        findings, stats = drift.health(self.store)
        pending = self.store.proposals("pending")
        res = evals.run(self.store, self.resolver)

        out = [f"# Weekly rulebook cycle - {self.store.config.get('name')} - {date.today().isoformat()}", "",
               f"- Decisions (30d): {stats['decisions_30d']} | answered {stats['answered_30d']} | "
               f"needed approval {stats['needs_approval_30d']} | escalation rate {stats['escalation_rate_30d']:.0%}",
               f"- Open gaps: {len(gaps)} in {len(clusters)} cluster(s) | pending proposals: {len(pending)}",
               f"- Regression evals: {res.passed}/{res.total} passing"
               + (f", {res.skipped} skipped" if res.skipped else ""),
               ""]
        if clusters:
            out += ["## 1. Answer these (each answer becomes a draft rule)", ""]
            for i, c in enumerate(clusters, 1):
                reasons = Counter(g.reason for g in c)
                out += [f"**{i}. {c[0].situation.splitlines()[0][:140]}**",
                        f"   {len(c)} case(s), {', '.join(f'{k} x{v}' for k, v in reasons.items())}"
                        + (f"; closest rules: {', '.join(c[0].considered[:3])}" if c[0].considered else ""),
                        f"   `precedent gaps answer {c[0].id} \"<what we do>\"`", ""]
        if pending:
            out += ["## 2. Review these drafts", ""]
            for p in pending[:20]:
                label = loop.label(self.store, p)
                out.append(f"- `{p.id}` {p.kind} {p.target or ''} - {label} ({p.origin})")
            out += ["", "`precedent review`", ""]
        if findings:
            out += ["## 3. Rulebook health", ""]
            for f in findings:
                out.append(f"- **{f.severity}** {f.kind} `{f.subject}`: {f.message} -> {f.action}")
            out.append("")
        if res.failures:
            out += ["## 4. Failing regression cases", ""]
            for fl in res.failures[:10]:
                out.append(f"- {fl['situation'][:120]} - expected {fl['expected']} {fl['expected_rules']}, "
                           f"got {fl['got']} {fl['cited']}")
            out.append("")
        if len(out) <= 6:
            out.append("Nothing needs you this week.")
        return "\n".join(out) + "\n"

    # ------------------------------------------------------------------ agent integration
    @staticmethod
    def tool_spec() -> dict[str, Any]:
        """Tool definition (JSON Schema) usable with any function-calling API."""
        return {
            "name": "consult_rulebook",
            "description": "Check the company's approved operating rules before acting on a business situation. "
                           + AGENT_INSTRUCTIONS,
            "input_schema": {
                "type": "object",
                "properties": {
                    "situation": {"type": "string", "description": "What is happening and what you are about to do, "
                                  "in plain words. Include amounts, timing and who is involved."},
                    "context": {"type": "string", "description": "Optional extra facts (customer tier, order age...)."},
                },
                "required": ["situation"],
            },
        }

    def handle_tool_call(self, args: dict[str, Any]) -> dict[str, Any]:
        """The agent-facing gate. Fails closed: any error comes back as escalate, never as an exception
        an agent framework might swallow and carry on."""
        try:
            if not isinstance(args, dict):
                raise ValueError("arguments must be an object with a 'situation' string")
            d = self.resolve(str(args.get("situation", "")), args.get("context") or None)
        except Exception as exc:
            return {"decision_id": None, "outcome": "escalate", "answer": "", "rules": [], "rule_text": [],
                    "risk": None, "confidence": 0.0, "reasons": [f"error: {type(exc).__name__}: {exc}"],
                    "instruction": "Do NOT act. The rulebook could not be consulted; hand this to a person."}
        texts = {r.id: r.then for r in self.store.rules("approved")} if d.cited else {}
        return {
            "decision_id": d.id,
            "outcome": d.outcome,
            "answer": d.answer,
            "rules": d.cited,
            # The approved wording, verbatim. `answer` is the model applying it to this case.
            "rule_text": [{"id": c["id"], "then": texts.get(c["id"], "")} for c in d.cited],
            "risk": d.risk,
            "confidence": d.confidence,
            "reasons": d.reasons,
            "instruction": {
                "answer": "Act on `answer`. Cite the rule ids.",
                "needs_approval": "Do NOT act yet. Show `answer` to a human and wait for approval.",
                "escalate": "Do NOT act. Tell the user this needs a person; it has been logged as "
                            + str(d.gap_id) + ".",
            }[d.outcome],
        }
