"""Prompt-injection screening: detection and telemetry, not prevention.

OWASP LLM01:2025 states there is no fool-proof prevention for prompt injection. The controls
that actually bound the damage here are elsewhere: identity comes from verified tokens, return
eligibility is deterministic, writes need a signed human confirmation, and specialist replies are
parsed into the outcome contract. This screen catches the common phrasings in English and French,
records which rule fired, and lets the mesh answer DECLINE early.

It runs on user input (coordinator) and on retrieved third-party text such as product reviews
(catalog MCP server), the indirect-injection path.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from agent_mesh.language import fold_accents

_INJECTION_RULES: dict[str, re.Pattern[str]] = {
    "override_instructions": re.compile(
        r"\b(ignore|disregard|forget|override)\b.{0,40}\b(instructions?|rules|prompt|guidelines)\b"
        r"|\b(ignore[sz]?|oublie[sz]?)\b.{0,40}\b(instructions?|consignes|regles)\b"
    ),
    "role_hijack": re.compile(
        r"\b(you are now|act as|pretend to be|developer mode|jailbreak|tu es maintenant)\b"
    ),
    "fake_system_block": re.compile(
        r"(<\|?\s*(system|im_start)\s*\|?>|#{2,}\s*system|\bsystem (note|prompt|message)\b)"
    ),
    "prompt_exfiltration": re.compile(
        r"\b(reveal|print|show|repeat|revele[sz]?|affiche[sz]?)\b.{0,30}"
        r"\b(system prompt|instructions|prompt systeme|consignes)\b"
    ),
    "bulk_data_request": re.compile(
        r"\b(all|every|other|tous les|toutes les|autres?)\b.{0,30}"
        r"\b(customers?|clients?|orders in the database|addresses|adresses)\b"
    ),
}


WITHHELD_REVIEW = "[review withheld: flagged by the injection screen]"
"""Replaces a flagged review, so the model sees that a review existed but not its text."""


@dataclass(frozen=True)
class ScreeningResult:
    """Whether the text was flagged, and by which rules (recorded as a span event)."""

    flagged: bool
    rules: tuple[str, ...]
    """Names of the rules that fired, safe to record: they never contain the flagged text."""


def screen_for_injection(text: str) -> ScreeningResult:
    """Run every rule on accent-folded `text` and report all that fire, not just the first."""
    folded = fold_accents(text)
    hits = tuple(name for name, rule in _INJECTION_RULES.items() if rule.search(folded))
    return ScreeningResult(flagged=bool(hits), rules=hits)
