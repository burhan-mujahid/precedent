#!/usr/bin/env bash
# The whole Precedent loop on a sample bakery WhatsApp group. Run from the repo root:
#
#   bash examples/demo.sh            # offline, no API key, ~15 s
#   bash examples/demo.sh --live     # uses the first key found: GEMINI_API_KEY, MOONSHOT_API_KEY,
#                                    # ANTHROPIC_API_KEY, OPENAI_API_KEY (set PRECEDENT_MODEL to pick a model)
#   bash examples/demo.sh --pause    # wait for Enter between steps (for presenting)
set -euo pipefail

LIVE=0 PAUSE=0
for arg in "$@"; do
  case "$arg" in
    --live) LIVE=1 ;;
    --pause) PAUSE=1 ;;
    -h|--help) sed -n '2,7p' "$0"; exit 0 ;;
    *) echo "unknown option: $arg (use --live, --pause)" >&2; exit 64 ;;
  esac
done

REPO=$(cd "$(dirname "$0")/.." && pwd)
DIR=$(mktemp -d)/crumb
export PRECEDENT_USER=Ayesha
START=$SECONDS

if [ -t 1 ]; then BOLD=$'\033[1m' DIM=$'\033[2m' RST=$'\033[0m'; else BOLD= DIM= RST=; fi
step() {
  if [ "$PAUSE" = 1 ] && [ -t 0 ]; then printf '\n%s(press Enter)%s' "$DIM" "$RST"; read -r _; fi
  printf '\n%s== %s%s\n%s%s%s\n' "$BOLD" "$1" "$RST" "$DIM" "$2" "$RST"
}
ask() { printf '\n%s> %s%s\n' "$BOLD" "$*" "$RST"; precedent ask "$@" || true; }   # non-zero exit = not "answer"

step "1. Create a rulebook" "One folder of plain YAML and JSONL files. The 'support' pack adds two starter rules."
precedent init "$DIR" --name "Crumb & Co" --pack support
cd "$DIR"

if [ "$LIVE" = 1 ]; then
  # Default models are what was current when this script was written; PRECEDENT_MODEL overrides them.
  if   [ -n "${GEMINI_API_KEY:-}" ];    then KIND=gemini    MODEL=${PRECEDENT_MODEL:-gemini-3.8-flash}
  elif [ -n "${MOONSHOT_API_KEY:-}" ];  then KIND=kimi      MODEL=${PRECEDENT_MODEL:-kimi-k3}
  elif [ -n "${ANTHROPIC_API_KEY:-}" ]; then KIND=anthropic MODEL=${PRECEDENT_MODEL:-}
  elif [ -n "${OPENAI_API_KEY:-}" ];    then KIND=openai    MODEL=${PRECEDENT_MODEL:-}
  else echo "--live needs GEMINI_API_KEY, MOONSHOT_API_KEY, ANTHROPIC_API_KEY or OPENAI_API_KEY" >&2; exit 64; fi
  if [ -z "$MODEL" ]; then echo "set PRECEDENT_MODEL for $KIND (precedent doctor lists models)" >&2; exit 64; fi
  sed -i.bak -e "s/^  kind: offline/  kind: $KIND/" -e "s/^  model: null/  model: $MODEL/" precedent.yaml && rm -f precedent.yaml.bak
  echo "Model: $KIND / $MODEL"
else
  echo "Model: none (offline keyword baseline). Use --live for a real model."
fi

step "2. Mine the team's WhatsApp group" \
     "Finds 'how do we handle X?' threads and drafts one rule per answer, each citing the messages it came from."
precedent ingest "$REPO/examples/bakery/ops-group.txt"

step "3. A person approves the drafts" \
     "Nothing enters the rulebook any other way. Normally 'precedent review' (approve / edit / reject); here: approve all."
for p in $(precedent review list | cut -d' ' -f1); do precedent review approve "$p" >/dev/null; done
precedent rules

step "4. Agents ask before acting" \
     "ACT = do it. NEEDS APPROVAL = covered, but high risk: a person signs off. ESCALATE = not covered: logged as a gap."
ask "A delivery is running about an hour and a half late"
ask "Customer says the birthday cake arrived damaged"
ask "Supplier emailed saying their bank account has changed"

step "5. The gap becomes a rule" \
     "The owner answers the escalation once. The answer becomes a draft rule, and the case becomes a regression test."
precedent gaps
G=$(precedent gaps | grep -i "bank" | head -1 | cut -d' ' -f1)
precedent gaps answer "$G" "Never change supplier bank details from an email. Call the supplier on the number we already have, then ask Ayesha to approve." \
  --when "A supplier asks to change the bank account we pay them to" --risk high
P=$(precedent review list | head -1 | cut -d' ' -f1)
precedent review approve "$P"

step "6. Same situation, now covered" "And 'precedent eval' replays every resolved gap, in CI if you like."
ask "Supplier emailed saying their bank account has changed"
precedent eval || true

step "7. A person overrides a decision" "The correction opens a gap and drafts a fix to the rule that was cited."
DEC=$(precedent ask "Customer asks if the brownies are safe for someone with a nut allergy" --json \
    | grep -m1 '"id"' | sed -E 's/.*"(D-[^"]+)".*/\1/' || true)
precedent feedback "$DEC" --override "Also offer the nut-free certified partner bakery as an alternative."
precedent review list

step "8. Halt an action that is already running" \
     "After sign-off the agent re-checks before each step and after Done. A step the decision did not authorise halts the whole run, with a record of which rule, which run, and who halted it."
D2=$(precedent ask "Customer says the birthday cake arrived damaged" --run run-cake --json \
    | grep -m1 '"id"' | sed -E 's/.*"(D-[^"]+)".*/\1/' || true)
precedent signoff "$D2" || true
check() { printf '\n%s> check: %s%s\n' "$BOLD" "$2" "$RST"; precedent check "$@" || true; }   # exit 3 = halt
check "$D2" "Asking the customer for a photo of the damaged cake"
check "$D2" "Refunding 5000 to the customer's card"
check "$D2" "Refunded the cake in full" --after
precedent halts

step "9. The weekly digest" "One page for the owner: gaps to answer, drafts to review, drift, failing evals."
precedent cycle | sed '/^(saved to/d'
precedent health | tail -1

step "10. Export for other agent tools" "Agent Skill, AGENTS.md, Parlant, JSON, Markdown. Or run 'precedent mcp' for the live gates."
precedent export skill --out "$DIR/export"

printf '\n%sDone in %ss.%s Rulebook left at %s\n' "$BOLD" "$((SECONDS - START))" "$RST" "$DIR"
