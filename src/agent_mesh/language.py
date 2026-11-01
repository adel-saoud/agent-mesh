"""Cheap English/French detection used by the offline models and as a fallback hint.

With a real LLM the router decides the language; this heuristic only has to be right on the
short shopping questions the demo and the golden set use.
"""

from __future__ import annotations

import re
import unicodedata

from agent_mesh.contracts import Language

_FRENCH_MARKERS = re.compile(
    r"\b(je|j'|mon|ma|mes|est|quel|quelle|quels|combien|commande|retour|retourner|bouilloire|"
    r"prix|livr\w*|puis|pour|avec|une|des|le|la|les|bonjour|merci|garantie|puissance)\b"
    r"|[éèêàçù]"
)


def detect_language(text: str) -> Language:
    """French when at least two French markers appear; one is too easily a loanword."""
    return Language.FR if len(_FRENCH_MARKERS.findall(text.lower())) >= 2 else Language.EN


def fold_accents(text: str) -> str:
    """Lowercase and strip accents, so one pattern or index term matches "règles" and "regles"."""
    return unicodedata.normalize("NFKD", text.lower()).encode("ascii", "ignore").decode()


def localized(language: Language, english: str, french: str) -> str:
    """Pick the user-facing string for `language`, keeping both wordings side by side at the call."""
    return french if language is Language.FR else english
