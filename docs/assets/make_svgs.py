# ruff: noqa: E501  (SVG markup)
"""Render the README banner and a terminal capture of real `precedent ask` output as SVG.

    bash examples/demo.sh > /tmp/demo.txt
    python docs/assets/make_svgs.py docs/assets /tmp/demo.txt
"""
import sys
from html import escape
from pathlib import Path

out = Path(sys.argv[1])
demo = Path(sys.argv[2]).read_text().splitlines()

NBSP = "\u00a0"   # renderers collapse normal spaces; terminal output needs them
MONO = "ui-monospace, SFMono-Regular, Menlo, Consolas, 'Liberation Mono', monospace"
SANS = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif"
GREEN, AMBER, RED, BLUE = "#3fb950", "#d29922", "#f85149", "#58a6ff"

# ---------------------------------------------------------------- banner
banner = f'''<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="400" viewBox="0 0 1280 400" role="img" aria-label="Precedent: turn how your team already works into rules your AI agents follow">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#0d1117"/><stop offset="1" stop-color="#161b22"/>
    </linearGradient>
    <linearGradient id="mark" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#7ee787"/><stop offset="1" stop-color="#58a6ff"/>
    </linearGradient>
    <pattern id="grid" width="32" height="32" patternUnits="userSpaceOnUse">
      <path d="M32 0H0V32" fill="none" stroke="#21262d" stroke-width="1"/>
    </pattern>
  </defs>
  <rect width="1280" height="400" rx="24" fill="url(#bg)"/>
  <rect width="1280" height="400" rx="24" fill="url(#grid)" opacity="0.6"/>

  <!-- mark: a stack of rule cards, the top one checked -->
  <g transform="translate(96 112)">
    <rect x="24" y="0" width="128" height="160" rx="14" fill="#21262d" stroke="#30363d" stroke-width="2"/>
    <rect x="12" y="12" width="128" height="160" rx="14" fill="#161b22" stroke="#30363d" stroke-width="2"/>
    <rect x="0" y="24" width="128" height="160" rx="14" fill="#0d1117" stroke="url(#mark)" stroke-width="3"/>
    <rect x="20" y="52" width="60" height="8" rx="4" fill="#30363d"/>
    <rect x="20" y="72" width="88" height="8" rx="4" fill="#30363d"/>
    <rect x="20" y="92" width="74" height="8" rx="4" fill="#30363d"/>
    <circle cx="94" cy="148" r="20" fill="url(#mark)"/>
    <path d="M84 148l7 7 13-14" fill="none" stroke="#0d1117" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/>
  </g>

  <text x="300" y="168" font-family="{SANS}" font-size="72" font-weight="700" fill="#f0f6fc" letter-spacing="-1.5">Precedent</text>
  <text x="302" y="216" font-family="{SANS}" font-size="26" fill="#c9d1d9">Turn how your team already works into rules your AI agents follow,</text>
  <text x="302" y="250" font-family="{SANS}" font-size="26" fill="#c9d1d9">and keep those rules true.</text>

  <g font-family="{MONO}" font-size="17" font-weight="600">
    <rect x="302" y="292" width="92" height="40" rx="20" fill="{GREEN}" fill-opacity="0.15" stroke="{GREEN}"/>
    <text x="348" y="318" text-anchor="middle" fill="{GREEN}">ACT</text>
    <rect x="410" y="292" width="196" height="40" rx="20" fill="{AMBER}" fill-opacity="0.15" stroke="{AMBER}"/>
    <text x="508" y="318" text-anchor="middle" fill="{AMBER}">NEEDS APPROVAL</text>
    <rect x="622" y="292" width="132" height="40" rx="20" fill="{RED}" fill-opacity="0.15" stroke="{RED}"/>
    <text x="688" y="318" text-anchor="middle" fill="{RED}">ESCALATE</text>
  </g>
  <text x="776" y="318" font-family="{SANS}" font-size="18" fill="#8b949e">every agent action, decided by gates outside the model</text>
</svg>
'''
(out / "banner.svg").write_text(banner)

# ---------------------------------------------------------------- terminal capture
start = demo.index("== 4. Agents ask before acting")
lines = []
for line in demo[start + 2:]:
    if line.startswith("== "):
        break
    lines.append(line)
while lines and not lines[-1].strip():
    lines.pop()

WIDTH = 96
def clip(s):
    return s if len(s) <= WIDTH else s[: WIDTH - 1] + "…"

COLORS = {"[ACT]": GREEN, "[NEEDS APPROVAL]": AMBER, "[ESCALATE]": RED}
rows = []
for raw in lines:
    s = clip(raw)
    if s.startswith("> "):
        rows.append([("$ ", "#8b949e"), ("precedent ask ", BLUE), ('"' + s[2:] + '"', "#f0f6fc")])
    elif s.startswith("["):
        tag = s.split("]")[0] + "]"
        rest = s[len(tag):]
        rows.append([(tag, COLORS.get(tag, "#c9d1d9")), (rest, "#8b949e")])
    elif s.strip().startswith(("answer", "rules", "why", "gap")):
        key, _, val = s.partition(":")
        rows.append([(key + ":", "#8b949e"), (val, "#c9d1d9")])
    else:
        rows.append([(s, "#c9d1d9")])

LH, PAD_TOP, CW = 24, 64, 9.6
H = PAD_TOP + LH * len(rows) + 28
W = int(32 + CW * (WIDTH + 2))
parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" role="img" '
         f'aria-label="precedent ask: one situation each for ACT, NEEDS APPROVAL and ESCALATE">',
         f'<rect width="{W}" height="{H}" rx="14" fill="#0d1117" stroke="#30363d"/>',
         '<circle cx="24" cy="22" r="6" fill="#ff5f57"/><circle cx="44" cy="22" r="6" fill="#febc2e"/>'
         '<circle cx="64" cy="22" r="6" fill="#28c840"/>',
         f'<text x="{W/2}" y="27" text-anchor="middle" font-family="{SANS}" font-size="13" fill="#8b949e">'
         'precedent ask - offline baseline, sample bakery rulebook</text>',
         f'<line x1="0" y1="44" x2="{W}" y2="44" stroke="#30363d"/>',
         f'<g font-family="{MONO}" font-size="16" xml:space="preserve" style="white-space:pre">']
for i, segs in enumerate(rows):
    y = PAD_TOP + LH * i + 4
    indent = len(segs[0][0]) - len(segs[0][0].lstrip(" "))
    segs[0] = (segs[0][0].lstrip(" "), segs[0][1])
    spans = "".join(f'<tspan fill="{c}">{escape(t).replace(" ", NBSP)}</tspan>' for t, c in segs)
    parts.append(f'<text x="{20 + indent * CW:.1f}" y="{y}">{spans}</text>')
parts.append("</g></svg>")
(out / "demo.svg").write_text("\n".join(parts) + "\n")
print("ok", W, H, len(rows))
