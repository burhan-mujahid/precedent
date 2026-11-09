"""Live tests against real model providers. Skipped unless the provider's key is set.

    GEMINI_API_KEY=... MOONSHOT_API_KEY=... pytest -m live -v

Model names change often; override the defaults with PRECEDENT_GEMINI_MODEL / PRECEDENT_KIMI_MODEL
(`precedent doctor --filter flash` lists what your key can use). Each run costs a few cents.
"""

from __future__ import annotations

import os

import pytest

from precedent import Precedent
from precedent.ingest import build_episodes, load_messages
from precedent.llm import make_client, parse_json
from precedent.loop import mine
from precedent.reasoner import LLMReasoner

from .conftest import EXAMPLE

PROVIDERS = {
    "gemini": ("GEMINI_API_KEY", os.environ.get("PRECEDENT_GEMINI_MODEL", "gemini-3.8-flash")),
    "kimi": ("MOONSHOT_API_KEY", os.environ.get("PRECEDENT_KIMI_MODEL", "kimi-k3")),
}

pytestmark = pytest.mark.live


@pytest.fixture(params=list(PROVIDERS))
def reasoner(request) -> LLMReasoner:
    kind = request.param
    env, model = PROVIDERS[kind]
    if not os.environ.get(env):
        pytest.skip(f"{env} not set")
    client = make_client({"kind": kind, "model": model})
    assert client is not None
    return LLMReasoner(client)


def test_json_round_trip(reasoner):
    assert parse_json(reasoner.client.complete("Reply with JSON only.", 'Return exactly {"ok": true}')) == {"ok": True}


def test_extract_is_grounded_and_high_risk_for_money(reasoner, kb):
    damaged = next(e for e in build_episodes(load_messages(EXAMPLE)) if "damaged" in e.question.text)
    rep = mine(kb.store, reasoner, [damaged])
    assert rep.errors == [] and rep.proposed >= 1
    draft = kb.store.proposals("pending")[0]
    assert set(draft.evidence) <= {m.id for m in damaged.messages}
    assert draft.rule["risk"] == "high", "a full refund moves money"
    assert "photo" in draft.rule["then"].lower()


def test_decide_cites_the_right_rule(reasoner, seeded):
    kb = Precedent(seeded.store, reasoner)
    d = kb.resolve("A delivery is running about an hour and a half late", dry_run=True)
    assert d.outcome == "answer" and d.cited and d.cited[0]["id"] == "R-001", d.reasons
    assert "cookie" in d.answer.lower()


def test_decide_refuses_to_improvise(reasoner, seeded):
    # Mentions the allergy rule's words but asks for something the rule does not settle.
    kb = Precedent(seeded.store, reasoner)
    d = kb.resolve("Customer with a nut allergy wants a refund for a cake they already ate", dry_run=True)
    assert d.outcome != "answer", d
