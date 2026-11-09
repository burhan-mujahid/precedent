"""Lexical retrieval with no dependencies.

Two numbers per rule:
  * BM25 score  - used to rank rules.
  * coverage    - IDF-weighted share of the situation's informative terms that the rule
                  mentions, in [0, 1]. Used to decide whether the rulebook covers the
                  situation at all. Unlike raw BM25 it is comparable across queries, so a
                  single threshold (retrieval.min_coverage) means the same thing everywhere.

Why lexical and not embeddings: rulebooks are small (tens to low thousands of rules),
written in the team's own words, and need to work offline and be explainable
("matched on: refund, late, delivery"). The Retriever interface is one method, so an
embedding retriever can be swapped in without touching the runtime.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from .models import Rule

STOPWORDS = frozenset(
    """a an the and or but if then else of to in on at by for with from into onto about over under
    is are was were be been being am do does did doing done have has had having can could should would
    will shall may might must i me my we us our you your he she it its they them their this that these
    those there here what which who whom whose when where why how all any some no not only so than too
    very just also as up out off again once each other such own same both few more most s t don ok okay
    hi hey thanks thank pls please guys team anyone someone something get got gonna wanna u ur yes yeah
    handle handled handling process usually always normally ask asks asked asking give gives keep say says said
    tell tells want wants need needs don't doesn't can't won't let lets make bad good new""".split()
    # Urdu and Roman Urdu function words, so chats in Pakistan/India score on content words too.
    + """ہے ہیں تھا تھی تھے کے کی کا کو سے میں پر اور یا تو ہم آپ وہ یہ جو کہ بھی نہیں کیا کر
    hai hain tha thi the ke ki ka ko se mein par aur ya to hum aap wo woh ye yeh jo bhi kya kar""".split()
)

# Letters and digits in any script (Urdu, Arabic, accented Latin...), not just a-z.
_TOKEN = re.compile(r"[^\W_]+(?:'[^\W_]+)?")
_UNDOUBLE = frozenset("bdgmnprt")   # running -> run, shipped -> ship; not staff, call, miss, buzz


def stem(word: str) -> str:
    """Tiny suffix stripper. Good enough to match refund/refunds/refunded and damage/damaged."""
    # No "-er": it merges customer/custom and order/ord, which matters more than catching baker/bake.
    for suffix in ("ations", "ation", "ingly", "ings", "ing", "edly", "ies", "ied", "ed", "es", "ly", "s"):
        if len(word) > len(suffix) + 2 and word.endswith(suffix):
            base = word[: -len(suffix)]
            if suffix in ("ies", "ied"):
                return base + "y"
            if suffix in ("ing", "ings", "ed", "edly") and len(base) >= 4 and base[-1] == base[-2] \
                    and base[-1] in _UNDOUBLE:
                base = base[:-1]
            word = base
            break
    # Drop a final silent "e" so damage/damaged/damages, cake/cakes and change/changed meet.
    if len(word) >= 4 and word.endswith("e") and not word.endswith("ee"):
        word = word[:-1]
    return word


_STEMMED_STOP = frozenset(stem(w) for w in STOPWORDS)


def _raw_words(text: str) -> list[str]:
    text = text.lower().replace("’", "'")
    text = re.sub(r"'s\b", "", text)          # possessives: customer's -> customer
    return [t for t in _TOKEN.findall(text) if t not in STOPWORDS and len(t) > 1]


def tokens(text: str) -> list[str]:
    return [s for s in (stem(t) for t in _raw_words(text)) if s not in _STEMMED_STOP]


def keywords(text: str, n: int = 3) -> list[str]:
    """Readable (unstemmed) keywords, for tags."""
    seen: dict[str, str] = {}
    for w in _raw_words(text):
        if not w.isdigit() and stem(w) not in _STEMMED_STOP:
            seen.setdefault(stem(w), w)
    return list(seen.values())[:n]


def jaccard(a: str, b: str) -> float:
    sa, sb = set(tokens(a)), set(tokens(b))
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


@dataclass
class Hit:
    rule: Rule
    score: float
    coverage: float
    matched: list[str]


class Retriever(Protocol):
    def search(self, query: str, top_k: int = 5) -> list[Hit]: ...


class BM25Retriever:
    def __init__(self, rules: Sequence[Rule], k1: float = 1.4, b: float = 0.75):
        self.rules = list(rules)
        self.k1, self.b = k1, b
        self.docs = [Counter(tokens(r.search_text())) for r in self.rules]
        self.lengths = [sum(d.values()) for d in self.docs]
        self.avg_len = (sum(self.lengths) / len(self.lengths)) if self.lengths else 0.0
        df: Counter[str] = Counter()
        for d in self.docs:
            df.update(d.keys())
        n = len(self.docs)
        # BM25+ style idf, always positive.
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}
        # A word no rule uses tells us the situation has something new in it, but it is not
        # evidence against any particular rule. Weight it like an average word, not a rare one.
        self._unseen_idf = (sum(self.idf.values()) / len(self.idf)) if self.idf else 1.0

    def search(self, query: str, top_k: int = 5) -> list[Hit]:
        surface: dict[str, str] = {}
        for w in _raw_words(query):                    # stem -> the word the caller actually used, for "matched on"
            surface.setdefault(stem(w), w)
        q_terms = set(tokens(query))
        if not q_terms or not self.rules:
            return []
        total_weight = sum(self.idf.get(t, self._unseen_idf) for t in q_terms)
        hits = []
        for rule, doc, length in zip(self.rules, self.docs, self.lengths, strict=True):
            score = 0.0
            matched = []
            for t in q_terms:
                tf = doc.get(t, 0)
                if not tf:
                    continue
                matched.append(t)
                idf = self.idf[t]
                denom = tf + self.k1 * (1 - self.b + self.b * length / (self.avg_len or 1))
                score += idf * tf * (self.k1 + 1) / denom
            if score <= 0:
                continue
            coverage = sum(self.idf[t] for t in matched) / total_weight if total_weight else 0.0
            hits.append(Hit(rule=rule, score=score, coverage=round(coverage, 4),
                            matched=sorted(surface.get(t, t) for t in matched)))
        hits.sort(key=lambda h: (h.score, h.coverage), reverse=True)
        return hits[:top_k]
