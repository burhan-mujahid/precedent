"""Project configuration (precedent.yaml).

Defaults are deliberately conservative: an agent should escalate rather than guess,
and high-risk rules always need a human to sign off on the action.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml

DEFAULTS: dict[str, Any] = {
    "name": "my-business",
    "provider": {
        # offline | anthropic | openai | gemini | kimi | openrouter | groq | ollama
        # (all but offline/anthropic use the OpenAI-compatible client; presets fill base_url / api_key_env)
        "kind": "offline",
        "model": None,              # required for real providers; `precedent doctor` lists what your key can use
        "base_url": None,           # override the preset, or point at any OpenAI-compatible server
        "api_key_env": None,        # override the preset's env var name
        "max_tokens": None,         # preset default; reasoning models need more (they think before answering)
        "temperature": 0,           # dropped automatically if the model rejects it
        "reasoning_effort": None,   # e.g. low; dropped automatically if unsupported
        "timeout_s": 60,
        "max_retries": 3,
    },
    "policy": {
        # Minimum confidence to act without a human, per risk tier.
        # high > 1.0 means "never act alone": the answer is returned as needs_approval.
        "min_confidence": {"low": 0.55, "medium": 0.70, "high": 1.01},
        # Tiers that never act alone still need this much confidence to go to a human as
        # needs_approval; below it the match is too weak, so it escalates and logs a gap.
        "approval_floor": 0.55,
        # Confidence multiplier for rules past their review date.
        "stale_penalty": 0.8,
    },
    "runtime": {
        "redact": True,                 # strip personal data and secrets from situations before the model/log
        "max_situation_chars": 4000,    # longer inputs are refused (cost and prompt-injection surface)
    },
    "retrieval": {
        "top_k": 5,
        "min_coverage": 0.30,       # below this, the rulebook does not cover the situation
    },
    "rules": {
        "review_days": 90,          # every rule gets re-confirmed at least this often
    },
    "drift": {
        "unused_days": 60,
        "override_limit": 2,        # overrides on one rule before it is flagged
        "duplicate_similarity": 0.6,
    },
    "risk": {
        # Extra words that mark an action high/medium risk (domain packs fill these in).
        "high_terms": [],
        "medium_terms": [],
    },
    "ingest": {
        "redact": True,             # strip phone numbers / emails before anything is stored or sent
        "episode_window_hours": 12,
        "episode_max_replies": 8,
        "workers": 4,               # parallel model calls while mining (1 = sequential)
    },
}


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


class Config:
    def __init__(self, data: dict[str, Any] | None = None):
        self.data = _merge(DEFAULTS, data or {})

    @classmethod
    def load(cls, path: Path) -> Config:
        if not path.exists():
            return cls()
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            raise ValueError("expected a YAML mapping at the top level")
        cfg = cls(data)
        cfg.validate()
        return cfg

    def validate(self) -> None:
        """Safety settings must be numbers in range: a typo must not silently open a gate."""
        def number(key: str, lo: float, hi: float) -> None:
            value = self.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not lo <= value <= hi:
                raise ValueError(f"{key} must be a number between {lo} and {hi}, got {value!r}")
        for tier in ("low", "medium", "high"):
            number(f"policy.min_confidence.{tier}", 0, 1.01)
        number("policy.approval_floor", 0, 1)
        number("policy.stale_penalty", 0, 1)
        number("retrieval.min_coverage", 0, 1)
        number("runtime.max_situation_chars", 1, 1_000_000)

    def save(self, path: Path) -> None:
        path.write_text(yaml.safe_dump(self.data, sort_keys=False), encoding="utf-8")

    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self.data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def threshold(self, risk: str) -> float:
        return float(self.get(f"policy.min_confidence.{risk}", 1.01))
