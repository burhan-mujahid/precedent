"""Feed an outside verifier's findings into Precedent (shadow mode).

The verifier never blocks anything here. It watches finished actions, and for each one it says
whether the outcome matched what should have happened. This script reports every mismatch as a
halt with a correction. Precedent then stops the rest of that run, opens a gap, and drafts a
change to the rule the decision cited. Nothing reaches the rulebook until that rule's owner
approves it, and approving it adds a regression case.

    python examples/integrations/report_mismatches.py ./my-rulebook findings.jsonl [--dry-run]

findings.jsonl, one finished action per line, as your verifier or execution monitor writes it:

    {"decision_id": "D-...", "did": "Refunded 1200 without asking for a photo",
     "matched": false, "expected": "Ask for a photo first, then remake or refund",
     "verifier": "monitor"}

Lines with "matched": true are confirmations: they are skipped here (or send them as
verdict "confirm" to turn them into regression cases).
"""
import argparse
import json
import sys
from pathlib import Path

from precedent import Precedent


def report(kb: Precedent, findings: list[dict], dry_run: bool = False) -> list[dict]:
    out = []
    for f in findings:
        if f.get("matched", True):
            continue
        reason = f"{f.get('verifier', 'verifier')}: outcome did not match ({f.get('did', 'no detail')})"
        if dry_run:
            out.append({"decision_id": f["decision_id"], "would_report": reason})
            continue
        c = kb.halt(f["decision_id"], reason, by=f"verifier:{f.get('verifier', 'unknown')}", stage="after",
                    step=f.get("did", ""), correction=f.get("expected", ""))
        out.append({"decision_id": f["decision_id"], "halt": c.halt_id, "gap": c.gap_id,
                    "draft_fix": c.proposal_id})
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("rulebook")
    ap.add_argument("findings", type=Path)
    ap.add_argument("--dry-run", action="store_true", help="show what would be reported")
    a = ap.parse_args()
    rows = [json.loads(line) for line in a.findings.read_text(encoding="utf-8").splitlines() if line.strip()]
    for r in report(Precedent.open(a.rulebook), rows, a.dry_run):
        print(json.dumps(r))
    sys.exit(0)
