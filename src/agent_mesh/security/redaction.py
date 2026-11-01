"""Regex redaction for trace content, with checksums where the format has one.

This is the second line of defence. The first is not recording content at all
(`AGENT_MESH_CAPTURE_MESSAGE_CONTENT` is off by default).

Not Presidio: it needs a spaCy model per service image for four entity types that regexes with
checksums (Luhn for cards, mod 97 for IBANs) already catch with few false positives.

Placeholders use the `<ENTITY_TYPE>` form, the shape the trace-to-golden pipeline strips before
clustering, so redacted text does not leak marker noise into its topics.
"""

from __future__ import annotations

import re
from collections.abc import Callable

_EMAIL_PATTERN = re.compile(r"\b[\w.+-]+@[\w-]+(\.[\w-]+)+\b")
_IBAN_PATTERN = re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,4})?\b")
_CARD_PATTERN = re.compile(r"\b(?:\d[ -]?){12,18}\d\b")
_PHONE_PATTERN = re.compile(
    r"(?:\+33|0033)\s?[1-9](?:[\s.-]?\d{2}){4}"  # France, international
    r"|\b0[1-9](?:[\s.-]?\d{2}){4}\b"  # France, national
    r"|(?:\+971|00971)\s?\d{1,2}[\s.-]?\d{3}[\s.-]?\d{4}"  # United Arab Emirates
)


def _passes_luhn(candidate: str) -> bool:
    """Luhn checksum, so order numbers and other digit runs are not taken for card numbers."""
    digits = [int(c) for c in candidate if c.isdigit()]
    total = 0
    for index, digit in enumerate(reversed(digits)):
        doubled = digit * 2 if index % 2 else digit
        total += doubled - 9 if doubled > 9 else doubled
    return total % 10 == 0


def _passes_iban_mod97(candidate: str) -> bool:
    """ISO 13616 mod 97 check."""
    compact = candidate.replace(" ", "")
    rearranged = compact[4:] + compact[:4]
    return int("".join(str(int(c, 36)) for c in rearranged)) % 97 == 1


def _replace_checked_matches(
    pattern: re.Pattern[str], placeholder: str, checksum: Callable[[str], bool], text: str
) -> str:
    """Replace only the matches that pass `checksum`, so look-alike digit runs survive."""
    return pattern.sub(lambda m: placeholder if checksum(m.group(0)) else m.group(0), text)


def redact(text: str) -> str:
    """Replace emails, IBANs, card numbers and FR/AE phone numbers with placeholders.

    Order matters: IBANs and cards are long digit runs that the phone pattern could split.
    """
    text = _EMAIL_PATTERN.sub("<EMAIL_ADDRESS>", text)
    text = _replace_checked_matches(_IBAN_PATTERN, "<IBAN_CODE>", _passes_iban_mod97, text)
    text = _replace_checked_matches(_CARD_PATTERN, "<CREDIT_CARD>", _passes_luhn, text)
    return _PHONE_PATTERN.sub("<PHONE_NUMBER>", text)
