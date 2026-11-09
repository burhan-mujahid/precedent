from __future__ import annotations

from pathlib import Path

import pytest

from precedent import Precedent
from precedent.loop import approve
from precedent.models import Proposal, new_id

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "bakery" / "ops-group.txt"


def add_rule(kb: Precedent, by: str = "tester", **fields) -> str:
    """Create and approve a rule through the normal proposal path; return its id."""
    p = Proposal(id=new_id("P"), kind="create", rule=fields, origin="manual")
    kb.store.save_proposal(p)
    return approve(kb.store, p.id, by).id


@pytest.fixture
def kb(tmp_path) -> Precedent:
    return Precedent.init(tmp_path / "kb", name="Test Co")


@pytest.fixture
def seeded(kb) -> Precedent:
    add_rule(kb, title="Late delivery", when="A delivery is running more than an hour late",
             then="Message the customer first with a new ETA and add a free cookie box.", risk="medium",
             tags=["delivery"])
    add_rule(kb, title="Nut allergy questions", when="A customer asks whether a product is safe for a nut allergy",
             then="Never promise nut-free; say the kitchen handles nuts.", risk="low", tags=["allergy"])
    add_rule(kb, title="Bulk discount", when="A customer asks for a discount on a large cupcake order",
             then="10% for 20+, 15% for 50+, bigger orders go to the owner.", risk="high", tags=["sales"])
    return kb
