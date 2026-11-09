"""Command line interface.

    precedent init my-rulebook --pack support
    precedent ingest chat.txt              # WhatsApp export, Slack export dir, or JSONL
    precedent review                       # approve / edit / reject drafts
    precedent ask "customer wants a refund, order 3 days late"
    precedent gaps                         # what agents could not handle
    precedent gaps answer G-... "we refund shipping only"
    precedent cycle                        # the weekly 30-minute digest
    precedent doctor                       # check model provider + key
    precedent health | eval | calibrate | replay | export | mcp
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import shlex
import subprocess
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Any

import yaml

from . import __version__, loop
from .export import render_rulebook, to_agents_md, to_json, to_parlant, to_skill
from .kb import Precedent
from .llm import LLMError
from .models import Decision, Proposal, new_id
from .packs import PACKS
from .store import StoreError

OUTCOME_MARK = {"answer": "ACT", "needs_approval": "NEEDS APPROVAL", "escalate": "ESCALATE"}


def _user(args: argparse.Namespace) -> str:
    return args.by or os.environ.get("PRECEDENT_USER") or getpass.getuser()


def _kb(args: argparse.Namespace) -> Precedent:
    return Precedent.open(args.root)


def _print_decision(d: Decision, as_json: bool) -> None:
    if as_json:
        print(json.dumps(d.to_dict(), indent=2, ensure_ascii=False))
        return
    # Confidence and risk describe the cited rules; an escalation that cites none has neither.
    scores = f"  confidence {d.confidence:.2f}  risk {d.risk}" if d.cited else ""
    print(f"[{OUTCOME_MARK[d.outcome]}] {d.id}{scores}")
    if d.answer:
        print(f"  answer : {d.answer}")
    if d.cited:
        cited = ", ".join(f"{c['id']} v{c['version']}" for c in d.cited)
        print(f"  rules  : {cited}")
    for r in d.reasons:
        if r:
            print(f"  why    : {r}")
    if d.gap_id:
        print(f"  gap    : {d.gap_id} (answer it with `precedent gaps answer {d.gap_id} \"...\"`)")


def _show_proposal(kb: Precedent, p: Proposal) -> None:
    print(f"\n{p.id}  [{p.kind}] from {p.origin}")
    print(f"  why proposed: {p.rationale}")
    if p.target:
        cur = kb.store.rule(p.target)
        print(f"  target: {cur.id} v{cur.version} - {cur.title}")
        for k, v in p.rule.items():
            if k == "sources":
                continue
            print(f"    {k}:\n      - {getattr(cur, k, '')}\n      + {v}")
    else:
        for k in ("title", "when", "then", "why", "exceptions", "risk", "tags"):
            if p.rule.get(k):
                print(f"  {k:10}: {p.rule[k]}")
    ev = [e for e in p.evidence if not e.startswith("G-")]
    if ev:
        print(f"  evidence  : {', '.join(ev[:6])}")
        _print_evidence(kb, ev)


def _print_evidence(kb: Precedent, ids: list[str]) -> None:
    wanted = set(ids)
    for line in kb.store._read_jsonl(kb.store.episodes_path):
        for m in [line["question"], *line["replies"]]:
            if m["id"] in wanted:
                print(f"      > {m['author']}: {m['text'][:160]}")
                wanted.discard(m["id"])
        if not wanted:
            return


def _edit(p: Proposal) -> dict[str, Any]:
    fields = {k: p.rule.get(k) for k in ("title", "when", "then", "why", "exceptions", "risk", "tags") if k in p.rule}
    editor = os.environ.get("EDITOR")
    if editor and sys.stdin.isatty():
        with tempfile.NamedTemporaryFile("w+", suffix=".yaml", delete=False, encoding="utf-8") as fh:
            yaml.safe_dump(fields, fh, sort_keys=False, allow_unicode=True)
            path = fh.name
        try:
            subprocess.call([*shlex.split(editor), path])
            edited = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise ValueError(f"edited YAML is invalid, nothing changed: {exc}") from None
        finally:
            os.unlink(path)
        if not isinstance(edited, dict):
            raise ValueError("edited YAML must be key: value lines, nothing changed")
        return {k: v for k, v in edited.items() if v != fields.get(k)}
    new_then = input("  new 'then' (enter to keep): ").strip()
    new_when = input("  new 'when' (enter to keep): ").strip()
    new_risk = input("  risk low/medium/high (enter to keep): ").strip()
    out = {}
    if new_then:
        out["then"] = new_then
    if new_when:
        out["when"] = new_when
    if new_risk:
        out["risk"] = new_risk
    return out


# ---------------------------------------------------------------------------- commands

def cmd_init(args: argparse.Namespace) -> int:
    kb = Precedent.init(args.path, name=args.name, pack=args.pack, by=_user(args))
    n = len(kb.store.rules("approved"))
    print(f"Created rulebook at {Path(args.path).resolve()} (pack: {args.pack}, {n} starter rule(s)).")
    print("Next: precedent ingest <whatsapp.txt | slack-export/ | messages.jsonl>")
    return 0


def cmd_ingest(args: argparse.Namespace) -> int:
    kb = _kb(args)

    def progress(n: int, total: int) -> None:
        if kb.reasoner.name != "offline" and sys.stderr.isatty():
            print(f"\r  mining episode {n}/{total} with {kb.reasoner.name}", end="", file=sys.stderr, flush=True)

    rep = kb.ingest(args.source, fmt=args.format, channel=args.channel, progress=progress)
    if kb.reasoner.name != "offline" and sys.stderr.isatty() and rep.episodes:
        print(file=sys.stderr)
    skipped = f" ({rep.already_mined} mined before, skipped)" if rep.already_mined else ""
    print(f"Read {rep.messages} messages -> {rep.episodes} new Q&A episode(s){skipped}")
    print(f"  {rep.proposed} new rule draft(s), {rep.updates} update draft(s), {rep.known} already in the rulebook, "
          f"{rep.duplicates} duplicate(s), {rep.ungrounded} dropped (no valid evidence)")
    usage = getattr(getattr(kb.reasoner, "client", None), "usage", None)
    if usage is not None and usage.calls:
        print(f"  {usage}")
    for e in rep.errors[:5]:
        print(f"  error: {e}")
    if len(rep.errors) > 5:
        print(f"  ... and {len(rep.errors) - 5} more error(s)")
    if rep.proposed or rep.updates:
        print("Next: precedent review")
    return 0


def cmd_review(args: argparse.Namespace) -> int:
    kb = _kb(args)
    by = _user(args)
    if args.action == "approve":
        r = kb.approve(args.id, by)
        print(f"approved -> {r.id} v{r.version}")
        return 0
    if args.action == "reject":
        kb.reject(args.id, by, args.note or "")
        print(f"rejected {args.id}")
        return 0
    if args.action == "show":
        _show_proposal(kb, kb.store.proposal(args.id))
        return 0
    pending = kb.store.proposals("pending")
    if not pending:
        print("Nothing to review.")
        return 0
    if args.action == "list" or not sys.stdin.isatty():
        for p in pending:
            print(f"{p.id}  {p.kind:9} {p.target or '':6} {loop.label(kb.store, p)[:70]}  ({p.origin})")
        return 0
    print(f"{len(pending)} draft(s). [a]pprove  [e]dit+approve  [r]eject  [s]kip  [q]uit")
    for p in pending:
        _show_proposal(kb, p)
        while True:
            choice = input("  > ").strip().lower()[:1]
            try:
                if choice == "a":
                    r = kb.approve(p.id, by)
                    print(f"  approved -> {r.id} v{r.version}")
                elif choice == "e":
                    r = kb.approve(p.id, by, edits=_edit(p))
                    print(f"  approved with edits -> {r.id} v{r.version}")
                elif choice == "r":
                    kb.reject(p.id, by, input("  reason (optional): ").strip())
                    print("  rejected")
                elif choice == "q":
                    return 0
                elif choice != "s":
                    continue
            except (ValueError, StoreError) as exc:
                print(f"  {exc}")
                continue
            break
    return 0


def cmd_ask(args: argparse.Namespace) -> int:
    kb = _kb(args)
    d = kb.resolve(" ".join(args.situation), args.context, dry_run=args.dry_run)
    _print_decision(d, args.json)
    return 0 if d.outcome == "answer" else 3


def cmd_replay(args: argparse.Namespace) -> int:
    kb = _kb(args)
    d = kb.resolve(" ".join(args.situation), as_of=args.as_of)
    print(f"Rulebook as of {args.as_of}:")
    _print_decision(d, args.json)
    return 0


def cmd_gaps(args: argparse.Namespace) -> int:
    kb = _kb(args)
    by = _user(args)
    if args.action == "answer":
        p = kb.answer_gap(args.id, args.text, by, target=args.rule, when=args.when, risk=args.risk,
                          include_similar=not args.only)
        print(f"Drafted {p.id} ({p.kind}{' ' + p.target if p.target else ''}) covering {len(p.evidence)} case(s). "
              f"Approve with `precedent review approve {p.id}`.")
        return 0
    if args.action == "dismiss":
        loop.dismiss_gap(kb.store, args.id, by, args.text or "")
        print(f"dismissed {args.id}")
        return 0
    gaps = kb.store.gaps(None if args.all else "open")
    if not gaps:
        print("No open gaps.")
    for g in gaps:
        print(f"{g.id}  {g.status:9} {g.reason:15} {g.situation.splitlines()[0][:90]}")
    return 0


def cmd_feedback(args: argparse.Namespace) -> int:
    kb = _kb(args)
    print(kb.feedback(args.decision, _user(args), confirm=args.confirm, correction=args.override or ""))
    return 0


def cmd_wrap(args: argparse.Namespace) -> int:
    kb = _kb(args)
    notes = Path(args.notes).read_text(encoding="utf-8") if args.notes != "-" else sys.stdin.read()
    props = kb.wrap(notes, _user(args))
    print(f"{len(props)} proposed change(s) from the session wrap.")
    for p in props:
        print(f"  {p.id} {p.kind} {p.target or ''} {p.rule.get('title', '')[:70]}")
    return 0


def cmd_cycle(args: argparse.Namespace) -> int:
    kb = _kb(args)
    text = kb.cycle()
    out = kb.store.root / "digests"
    out.mkdir(exist_ok=True)
    from datetime import date
    path = out / f"{date.today().isoformat()}.md"
    path.write_text(text, encoding="utf-8")
    print(text)
    print(f"(saved to {path})")
    return 0


def cmd_health(args: argparse.Namespace) -> int:
    kb = _kb(args)
    findings, stats = kb.health()
    if args.json:
        print(json.dumps({"stats": stats, "findings": [asdict(f) for f in findings]}, indent=2))
    else:
        for k, v in stats.items():
            print(f"{k:28} {v}")
        print()
        for f in findings:
            print(f"[{f.severity.upper():8}] {f.kind:10} {f.subject:10} {f.message}\n{'':32}-> {f.action}")
        if not findings:
            print("No drift found.")
    if args.propose:
        from .drift import propose_from_findings
        props = propose_from_findings(kb.store, findings)
        print(f"\n{len(props)} proposal(s) created for review.")
    critical = any(f.severity == "critical" for f in findings)
    return 2 if (args.strict and critical) else 0


def cmd_eval(args: argparse.Namespace) -> int:
    kb = _kb(args)
    res = kb.eval()
    print(f"{res.passed}/{res.total} passed ({res.rate:.0%}), {res.skipped} skipped")
    for f in res.failures:
        print(f"  FAIL {f['situation'][:90]!r}: expected {f['expected']} {f['expected_rules']}, "
              f"got {f['got']} {f['cited']}")
    if args.add:
        kb.store.add_golden({"id": new_id("E"), "situation": args.add, "expect": args.expect,
                             "rules": args.rules or [], "source": "manual"})
        print(f"added case: {args.add!r} -> {args.expect}")
    return 1 if res.rate < args.min_pass else 0


def cmd_calibrate(args: argparse.Namespace) -> int:
    kb = _kb(args)
    for t in kb.calibrate(target=args.target):
        if t.recommended is None:
            print(f"{t.risk:6}: {t.cases} case(s) - not enough evidence yet (need 5+). Current threshold {t.current}.")
        else:
            print(f"{t.risk:6}: current {t.current:.2f} -> recommended {t.recommended:.2f} "
                  f"(precision {t.precision_at_recommended:.0%}, agents handle {t.auto_rate_at_recommended:.0%} "
                  f"of {t.cases} cases)")
    print("Apply by editing policy.min_confidence in precedent.yaml.")
    return 0


def cmd_rules(args: argparse.Namespace) -> int:
    kb = _kb(args)
    if args.action == "show":
        print(yaml.safe_dump(kb.store.rule(args.id).to_dict(), sort_keys=False, allow_unicode=True))
        return 0
    if args.action == "history":
        for r in [*kb.store.rule_history(args.id), kb.store.rule(args.id)]:
            print(f"v{r.version} {r.updated} {r.status:10} then: {r.then[:100]}")
        return 0
    if args.action == "reconfirm":
        p = Proposal(id=new_id("P"), kind="reconfirm", target=args.id, rationale="reconfirmed by a person",
                     origin="manual")
        kb.store.save_proposal(p)
        r = kb.approve(p.id, _user(args))
        print(f"{r.id} reconfirmed; next review {r.review_by}")
        return 0
    for r in kb.store.rules(None if args.all else "approved"):
        stale = " STALE" if r.is_stale() else ""
        print(f"{r.id} v{r.version} {r.status:10} {r.risk:6} {r.title[:70]}{stale}")
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    kb = _kb(args)
    if args.target == "skill":
        path = to_skill(kb.store, Path(args.out or "."))
        print(f"Agent Skill written to {path}")
    elif args.target == "agents":
        text = to_agents_md(kb.store)
        if args.out:
            Path(args.out).write_text(text, encoding="utf-8")
            print(f"written to {args.out}")
        else:
            print(text)
    elif args.target == "parlant":
        out = Path(args.out or "load_guidelines.py")
        out.write_text(to_parlant(kb.store), encoding="utf-8")
        print(f"Parlant loader written to {out}")
    elif args.target == "json":
        print(to_json(kb.store))
    elif args.target == "markdown":
        out = Path(args.out or kb.store.root / "RULEBOOK.md")
        out.write_text(render_rulebook(kb.store), encoding="utf-8")
        print(f"written to {out}")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    """Check the model setup end to end: key, model list, a JSON round-trip, and one real decision."""
    import time

    from .llm import LLMError, make_client, parse_json, resolve_provider
    from .store import Store

    store = Store(args.root)
    store.ensure()
    p = resolve_provider(store.config.get("provider"))
    shown = {k: p.get(k) for k in ("kind", "model", "base_url", "api_key_env", "max_tokens", "temperature",
                                   "reasoning_effort")}
    print("provider: " + ", ".join(f"{k}={v}" for k, v in shown.items() if v is not None))
    if p["kind"] == "offline":
        print("ok: offline reasoner (no model). Set provider.kind in precedent.yaml to use one.")
        return 0
    env = p.get("api_key_env")
    if env:
        print(f"key: {env} is {'set' if os.environ.get(env) else 'NOT SET'}")
    if args.model:
        p["model"] = args.model
    try:
        client = make_client({**p, "model": p.get("model") or "probe"})
    except LLMError as exc:
        print(f"FAIL: {exc}")
        return 1
    if client is None:   # unreachable: offline returned above
        return 1
    try:
        lister = getattr(client, "list_models", None)
        if lister is None:
            raise LLMError("this client cannot list models")
        models: list[str] = lister()
        match = [m for m in models if args.filter.lower() in m.lower()] if args.filter else models
        print(f"models available to this key: {len(models)}" + (f" (showing '{args.filter}')" if args.filter else ""))
        for m in match[:40]:
            print(f"  {'*' if m == p.get('model') else ' '} {m}")
        if p.get("model") and models and p["model"] not in models:
            print(f"WARN: configured model {p['model']!r} is not in the list")
    except LLMError as exc:
        print(f"WARN: could not list models: {exc}")
    if not p.get("model"):
        print("FAIL: set provider.model in precedent.yaml (pick one from the list above)")
        return 1
    try:
        t0 = time.time()
        reply = client.complete("Reply with JSON only.", 'Return exactly {"ok": true}')
        ok = parse_json(reply).get("ok") is True
        print(f"{'ok' if ok else 'WARN'}: JSON round-trip in {time.time() - t0:.1f}s")
    except (LLMError, AttributeError) as exc:
        print(f"FAIL: test call: {exc}")
        return 1
    rules = store.rules("approved")
    if rules:
        kb = _kb(args)
        t0 = time.time()
        d = kb.resolve(rules[0].when, dry_run=True)
        cited = ", ".join(c["id"] for c in d.cited) or "-"
        print(f"{'ok' if rules[0].id in cited else 'WARN'}: decision on {rules[0].id}'s own situation -> "
              f"{d.outcome}, cited {cited}, confidence {d.confidence:.2f} ({time.time() - t0:.1f}s)")
    print("Model setup works.")
    return 0


def cmd_mcp(args: argparse.Namespace) -> int:
    from .mcp import Server
    Server(_kb(args)).serve()
    return 0


def cmd_packs(args: argparse.Namespace) -> int:
    for name, p in PACKS.items():
        print(f"{name:8} {p['description']} ({len(p['rules'])} starter rule(s))")
    return 0


# ---------------------------------------------------------------------------- parser

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="precedent",
                                 description="Operating rules for AI agents, mined from how your team works.")
    ap.add_argument("--version", action="version", version=f"precedent {__version__}")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", default=os.environ.get("PRECEDENT_ROOT", "."), help="rulebook directory")
    common.add_argument("--by", help="who is acting (default: $PRECEDENT_USER or OS user)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init", parents=[common], help="create a rulebook")
    p.add_argument("path")
    p.add_argument("--name")
    p.add_argument("--pack", default="general", choices=list(PACKS))
    p.set_defaults(fn=cmd_init)

    p = sub.add_parser("packs", parents=[common], help="list domain packs")
    p.set_defaults(fn=cmd_packs)

    p = sub.add_parser("ingest", parents=[common], help="mine team chat into rule drafts")
    p.add_argument("source")
    p.add_argument("--format", default="auto", choices=["auto", "whatsapp", "slack", "jsonl"])
    p.add_argument("--channel")
    p.set_defaults(fn=cmd_ingest)

    p = sub.add_parser("review", parents=[common], help="approve, edit or reject drafts")
    p.add_argument("action", nargs="?", default="interactive",
                   choices=["interactive", "list", "show", "approve", "reject"])
    p.add_argument("id", nargs="?")
    p.add_argument("--note")
    p.set_defaults(fn=cmd_review)

    p = sub.add_parser("ask", parents=[common], help="what should an agent do? (the runtime gate)")
    p.add_argument("situation", nargs="+")
    p.add_argument("--context")
    p.add_argument("--dry-run", action="store_true", help="do not log the decision or open gaps")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_ask)

    p = sub.add_parser("replay", parents=[common], help="decide against the rulebook as it was on a past date")
    p.add_argument("--as-of", required=True, help="YYYY-MM-DD")
    p.add_argument("situation", nargs="+")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_replay)

    p = sub.add_parser("gaps", parents=[common], help="list / answer / dismiss escalations")
    p.add_argument("action", nargs="?", default="list", choices=["list", "answer", "dismiss"])
    p.add_argument("id", nargs="?")
    p.add_argument("text", nargs="?", help="the answer (for answer) or a note (for dismiss)")
    p.add_argument("--rule", help="add the answer as an exception to this existing rule")
    p.add_argument("--when", help="phrase the situation more generally than the escalated case")
    p.add_argument("--risk", choices=["low", "medium", "high"])
    p.add_argument("--only", action="store_true", help="do not fold in similar open gaps")
    p.add_argument("--all", action="store_true")
    p.set_defaults(fn=cmd_gaps)

    p = sub.add_parser("feedback", parents=[common], help="confirm or override a past decision")
    p.add_argument("decision")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--confirm", action="store_true")
    g.add_argument("--override", metavar="WHAT_SHOULD_HAVE_HAPPENED")
    p.set_defaults(fn=cmd_feedback)

    p = sub.add_parser("wrap", parents=[common], help="propose rule changes from session notes (file or -)")
    p.add_argument("notes")
    p.set_defaults(fn=cmd_wrap)

    p = sub.add_parser("cycle", parents=[common], help="weekly digest: gaps, drafts, drift, evals")
    p.set_defaults(fn=cmd_cycle)

    p = sub.add_parser("health", parents=[common], help="drift report")
    p.add_argument("--json", action="store_true")
    p.add_argument("--propose", action="store_true", help="draft deprecate/reconfirm proposals")
    p.add_argument("--strict", action="store_true", help="exit 2 on critical findings (CI)")
    p.set_defaults(fn=cmd_health)

    p = sub.add_parser("eval", parents=[common], help="replay regression cases")
    p.add_argument("--min-pass", type=float, default=1.0)
    p.add_argument("--add", metavar="SITUATION", help="add a manual case")
    p.add_argument("--expect", default="covered", choices=["covered", "answer", "escalate", "needs_approval"])
    p.add_argument("--rules", nargs="*")
    p.set_defaults(fn=cmd_eval)

    p = sub.add_parser("calibrate", parents=[common], help="recommend confidence thresholds from evidence")
    p.add_argument("--target", type=float, default=0.95, help="required precision of auto-answers")
    p.set_defaults(fn=cmd_calibrate)

    p = sub.add_parser("rules", parents=[common], help="list / show / history / reconfirm")
    p.add_argument("action", nargs="?", default="list", choices=["list", "show", "history", "reconfirm"])
    p.add_argument("id", nargs="?")
    p.add_argument("--all", action="store_true")
    p.set_defaults(fn=cmd_rules)

    p = sub.add_parser("export", parents=[common], help="compile the rulebook for other tools")
    p.add_argument("target", choices=["skill", "agents", "parlant", "json", "markdown"])
    p.add_argument("--out")
    p.set_defaults(fn=cmd_export)

    p = sub.add_parser("doctor", parents=[common], help="check the model provider: key, models, a test decision")
    p.add_argument("--filter", help="only list models containing this text (e.g. flash, kimi)")
    p.add_argument("--model", help="try a model without editing precedent.yaml")
    p.set_defaults(fn=cmd_doctor)

    p = sub.add_parser("mcp", parents=[common], help="run the MCP server on stdio")
    p.set_defaults(fn=cmd_mcp)
    return ap


def _utf8_stdio() -> None:
    """Rules and chats are often not ASCII (Urdu, accents, emoji). Windows consoles default to a
    legacy code page, which would crash on print and corrupt MCP's JSON stream."""
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure and (getattr(stream, "encoding", "") or "").lower().replace("-", "") != "utf8":
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                pass


def main(argv: list[str] | None = None) -> int:
    _utf8_stdio()
    args = build_parser().parse_args(argv)
    needs_id = {("review", "show"), ("review", "approve"), ("review", "reject"), ("gaps", "answer"),
                ("gaps", "dismiss"), ("rules", "show"), ("rules", "history"), ("rules", "reconfirm")}
    if (args.cmd, getattr(args, "action", None)) in needs_id and not args.id:
        print(f"precedent {args.cmd} {args.action} needs an id", file=sys.stderr)
        return 64
    if args.cmd == "gaps" and args.action == "answer" and not args.text:
        print("precedent gaps answer <G-id> \"what we do\"", file=sys.stderr)
        return 64
    try:
        return args.fn(args)
    except (StoreError, LLMError, ValueError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:      # missing input file, unwritable directory...
        detail = f"{exc.strerror}: {exc.filename}" if exc.filename else str(exc)
        print(f"error: {detail}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
