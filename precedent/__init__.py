"""Precedent: turn how your team already works into rules your agents follow, and keep them true.

Mine rules from team chat -> a person approves -> agents consult the rulebook through
deterministic gates -> every escalation becomes next week's rule and a regression test.
"""

__version__ = "0.1.0"

from .kb import Precedent  # noqa: E402
from .models import ANSWER, ESCALATE, NEEDS_APPROVAL, Decision, Gap, Proposal, Rule  # noqa: E402

__all__ = ["Precedent", "Rule", "Proposal", "Decision", "Gap", "ANSWER", "ESCALATE", "NEEDS_APPROVAL", "__version__"]
