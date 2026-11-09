"""Domain packs: the same engine, tuned for a kind of work.

A pack adds two things to a new rulebook:
  * risk vocabulary  - words that mark an action as high/medium risk in that domain
  * starter rules    - a few safe defaults every business in that domain needs on day one
                       (approved by `precedent init --pack`, so the runtime has something to cite)

Packs are deliberately small. The real rulebook comes from your team's own threads.
"""

from __future__ import annotations

from typing import Any

PACKS: dict[str, dict[str, Any]] = {
    "general": {
        "description": "Any small business. Money, legal and data deletion are high risk.",
        "risk": {"high": [], "medium": []},
        "rules": [],
    },
    "support": {
        "description": "Customer support and success.",
        "risk": {"high": ["refund", "compensation", "credit note", "cancel subscription", "account deletion",
                          "gdpr", "chargeback"],
                 "medium": ["complaint", "escalate", "angry", "review", "ticket", "warranty"]},
        "rules": [
            {"title": "Customer threatens legal action", "risk": "high", "tags": ["support", "legal"],
             "when": "A customer mentions a lawyer, legal action, a lawsuit or a regulator",
             "then": "Stop replying on substance. Acknowledge receipt politely and hand the thread to the owner "
                     "the same day.",
             "why": "Anything said after a legal threat can be used later."},
            {"title": "Customer asks to delete their data", "risk": "high", "tags": ["support", "privacy"],
             "when": "A customer asks for their personal data or account to be deleted",
             "then": "Confirm receipt, do not delete anything yourself, and create a deletion request for the owner.",
             "why": "Deletion is irreversible and has legal deadlines."},
        ],
    },
    "ops": {
        "description": "Operations: orders, suppliers, inventory, logistics.",
        "risk": {"high": ["purchase order", "supplier payment", "write off", "stock adjustment"],
                 "medium": ["supplier", "delivery", "courier", "inventory", "stock", "dispatch"]},
        "rules": [
            {"title": "Supplier changes bank details", "risk": "high", "tags": ["ops", "fraud"],
             "when": "A supplier asks to change the bank account they are paid to",
             "then": "Do not update anything. Verify by phone using the number already on file, then ask the owner "
                     "to approve.",
             "why": "Changed bank details is the most common invoice fraud."},
        ],
    },
    "sales": {
        "description": "Sales and account management.",
        "risk": {"high": ["discount", "contract", "custom terms", "price change", "free trial extension"],
                 "medium": ["quote", "proposal", "lead", "demo", "renewal"]},
        "rules": [
            {"title": "Prospect asks for non-standard terms", "risk": "high", "tags": ["sales", "contract"],
             "when": "A prospect asks for a discount, custom payment terms or contract changes",
             "then": "Say you will confirm shortly and send the request to the owner with the deal size and the ask.",
             "why": "Terms set precedent for every later deal."},
        ],
    },
    "finance": {
        "description": "Bookkeeping, invoicing, payments.",
        "risk": {"high": ["payment", "transfer", "payroll", "tax", "invoice", "write off", "reimburse"],
                 "medium": ["receipt", "expense", "reconcile", "budget"]},
        "rules": [
            {"title": "Payment request without an invoice", "risk": "high", "tags": ["finance"],
             "when": "Someone asks for a payment to be made without a matching invoice or approval",
             "then": "Do not pay. Ask for the invoice and the approver's name, and flag it to the owner.",
             "why": "No invoice, no payment, no exceptions."},
        ],
    },
    "hr": {
        "description": "People operations.",
        "risk": {"high": ["salary", "termination", "fire", "disciplinary", "contract", "visa", "medical"],
                 "medium": ["leave", "holiday", "onboarding", "hiring", "interview"]},
        "rules": [
            {"title": "Anything about pay or termination", "risk": "high", "tags": ["hr"],
             "when": "A message concerns someone's pay, contract, discipline or termination",
             "then": "Do not answer on substance. Route it privately to the owner.",
             "why": "Confidential and legally sensitive."},
        ],
    },
    "oncall": {
        "description": "Engineering on-call and incident runbooks.",
        "risk": {"high": ["production", "rollback", "delete", "drop table", "migration", "failover", "customer data"],
                 "medium": ["restart", "deploy", "alert", "incident", "scale"]},
        "rules": [
            {"title": "Destructive action in production", "risk": "high", "tags": ["oncall"],
             "when": "A fix requires deleting data, running a migration or failing over in production",
             "then": "Write the exact command and expected effect, page the on-call lead, and wait for approval.",
             "why": "Irreversible actions need two people."},
        ],
    },
}


def get_pack(name: str) -> dict[str, Any]:
    if name not in PACKS:
        raise ValueError(f"unknown pack {name!r}; choose from {', '.join(PACKS)}")
    return PACKS[name]
