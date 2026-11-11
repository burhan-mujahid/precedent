"""What a piece of text commits to: the amounts it uses and the high-risk actions it names.

The gates compare these between what was authorised (the situation, the answer, the cited rules)
and what an agent is doing. Matching is deterministic and runs on both sides, so it only has to
be consistent, not perfect:

    numbers("Refund 5k")                 -> {"5000"}
    numbers("refund five thousand")      -> {"5000"}
    numbers("2.5 lakh")                  -> {"250000"}
    risk_terms("wire it to the supplier") -> {"transfer"}

Number words below ten on their own ("one moment", "two options") are ignored, because they
are almost always prose, not amounts.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation

_SCALES = {"k": 1_000, "thousand": 1_000, "m": 1_000_000, "mn": 1_000_000, "million": 1_000_000,
           "bn": 1_000_000_000, "billion": 1_000_000_000, "lakh": 100_000, "lakhs": 100_000, "lac": 100_000,
           "crore": 10_000_000, "crores": 10_000_000, "cr": 10_000_000}
# The scale must end a word ("5m", "5 k"), but the number need not ("1200PKR", "5th").
_DIGITS = re.compile(r"(\d+(?:[.,]\d+)*)(?:\s?(" + "|".join(sorted(_SCALES, key=len, reverse=True)) + r")\b)?",
                     re.I)

_UNITS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen "
    "seventeen eighteen nineteen".split())}
_TENS = {w: 10 * i for i, w in enumerate("twenty thirty forty fifty sixty seventy eighty ninety".split(), 2)}
_WORD_SCALES = {"hundred": 100, **{k: v for k, v in _SCALES.items() if len(k) > 2}}
_WORD = re.compile(r"[a-z]+", re.I)


def _fmt(value: Decimal) -> str:
    if value == value.to_integral_value():
        return str(int(value))
    return format(value.normalize(), "f")


def _digit_numbers(text: str) -> set[str]:
    out = set()
    for m in _DIGITS.finditer(text):
        raw, scale = m.group(1).replace(",", ""), (m.group(2) or "").lower()
        try:
            value = Decimal(raw) * _SCALES.get(scale, 1)
        except InvalidOperation:          # "1.2.3": a version or a date, keep it as written
            out.add(raw)
            continue
        out.add(_fmt(value))
    return out


def _word_numbers(text: str) -> set[str]:
    """'twenty-five' -> 25, 'two thousand five hundred' -> 2500, 'a hundred' -> 100."""
    out: set[str] = set()
    words = [w.lower() for w in _WORD.findall(text)]
    total = current = 0
    phrase: list[str] = []

    def flush() -> None:
        nonlocal total, current
        # A lone small number word is prose ("one moment"), not an amount.
        if phrase and not (len(phrase) == 1 and _UNITS.get(phrase[0], 10) < 10):
            out.add(str(total + current))
        total = current = 0
        phrase.clear()

    for i, w in enumerate(words):
        nxt = words[i + 1] if i + 1 < len(words) else ""
        if w in _UNITS or w in _TENS:
            current += _UNITS.get(w, 0) + _TENS.get(w, 0)
            phrase.append(w)
        elif w in _WORD_SCALES:
            current = current or 1           # "hundred", "a thousand"
            if w == "hundred":
                current *= 100
            else:
                total, current = total + current * _WORD_SCALES[w], 0
            phrase.append(w)
        elif w == "a" and nxt in _WORD_SCALES and not phrase:
            continue
        elif w == "and" and phrase and (nxt in _UNITS or nxt in _TENS):
            continue
        else:
            flush()
    flush()
    return out


def numbers(*texts: str) -> set[str]:
    """Every amount in the texts, normalised: '1,000', '1000.00', '1k' and 'one thousand' are all '1000'."""
    out: set[str] = set()
    for t in texts:
        out |= _digit_numbers(t)
        out |= _word_numbers(_DIGITS.sub(" ", t))   # "5 thousand" is already counted above
    return out


# High-risk actions: money, legal, health, deletion. Each match is reduced to one stem, and the
# synonyms below map other ways of saying it onto the same stem.
_HIGH = re.compile(r"\b(refund|chargeback|payment|pay|invoice|discount|price|contract|legal|lawyer|delete|"
                   r"terminate|fire|salary|bank|transfer|credit|tax|compensat|waive|write.?off|allerg|poison|"
                   r"injur|medical|safety)\w*", re.I)
_SYNONYMS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(p, re.I), stem) for p, stem in [
        (r"\b(?:wir(?:e|ed|es|ing)|remit\w*|send(?:ing)? (?:the )?money|move (?:the )?funds)\b", "transfer"),
        (r"\b(?:reimburs\w*|money back|repay\w*|payback)\b", "refund"),
        (r"\b(?:payout\w*|pay out|disburs\w*)\b", "pay"),
        (r"\b(?:rebate\w*|markdown\w*|percent off|% off|price cut)", "discount"),
        (r"\b(?:iban|swift|account number|routing number|sort code)\b", "bank"),
        (r"\b(?:payroll|wages?)\b", "salary"),
        (r"\b(?:sue|sued|suing|lawsuit\w*|litigat\w*|attorney\w*|solicitor\w*)\b", "legal"),
        (r"\b(?:eras(?:e|ed|es|ing)|wip(?:e|ed|es|ing)|purg(?:e|ed|es|ing)|destroy\w*|drop table|truncat\w*)\b",
         "delete"),
        (r"\b(?:comp(?:ed|ing|s)?|freebie\w*)\b", "compensat"),
        (r"\b(?:lay ?off\w*|laid off|let (?:him|her|them) go)\b", "terminate"),
    ]
]
_CANONICAL = {"payment": "pay", "write off": "write-off", "writeoff": "write-off"}


def _stem(word: str) -> str:
    w = word.lower()
    return _CANONICAL.get(w, w)


def risk_terms(text: str, high_terms: Sequence[str] = (), synonyms: Mapping[str, str] | None = None) -> set[str]:
    """The high-risk actions a text names, as stems: built-in words, their synonyms, the rulebook's
    own `risk.high_terms`, and its `risk.synonyms` (phrase -> the term it means)."""
    low = text.lower()
    found = {_stem(m) for m in _HIGH.findall(text)}
    found |= {stem for pattern, stem in _SYNONYMS if pattern.search(text)}
    found |= {t.lower() for t in high_terms if t and t.lower() in low}
    found |= {str(v).lower() for k, v in (synonyms or {}).items() if k and str(k).lower() in low}
    return found


def is_high_risk(text: str, high_terms: Sequence[str] = (), synonyms: Mapping[str, str] | None = None) -> bool:
    return bool(risk_terms(text, high_terms, synonyms))
