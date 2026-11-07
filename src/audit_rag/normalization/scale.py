"""Find the unit scale a statement is presented in (thousands, millions, ...).

The scale is never assumed. If a page does not state one, the result is "unknown";
if it states two different ones, the result is "conflict". Neither gets a
multiplier, so a caller cannot silently apply the wrong one.

Two forms are recognised:
  - bracketed:  (in millions)   (Dollars in millions, except per share amount)
  - standalone: a whole line such as  $ in millions, except per share amounts
An unbracketed phrase inside a sentence is not a scale statement.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

MULTIPLIERS = {
    "thousand": Decimal(1000),
    "million": Decimal(1000000),
    "billion": Decimal(1000000000),
    "lakh": Decimal(100000),
    "crore": Decimal(10000000),
}
_WORD = r"(thousands?|millions?|billions?|lakhs?|crores?)"
# "(in millions)", "(Dollars in millions, except per share amount)", "($ in thousands)"
IN_SCALE = re.compile(
    r"\([^()]{0,100}?\b(?:in|of)\s+(?:u\.?s\.?\s+)?" + _WORD + r"\b[^()]{0,100}\)", re.I
)
# "(Millions of dollars)", "(Thousands)"
LEADING_SCALE = re.compile(r"\(\s*(?:\$\s*)?" + _WORD + r"\b[^()]{0,100}\)", re.I)
# A whole line: "$ in millions, except per share amounts", "In millions", "Dollars in thousands".
# Nothing may come before "in" except $, Dollars or Amounts.
LINE_SCALE = re.compile(
    r"^\s*(?:(?:u\.?s\.?\s+)?dollars?\s+|amounts?\s+|\$\s*)?(?:(?:and|&)\s+(?:shares?|units?)\s+)?in\s+(?:u\.?s\.?\s+)?"
    + _WORD
    + r"\b(?:\s*,?\s*except\b[^.]{0,100})?\s*$",
    re.I,
)
EXCEPT_RE = re.compile(r"\bexcept\b", re.I)


@dataclass(frozen=True)
class ScaleStatement:
    """One scale phrase found in the text."""

    word: str  # singular and lowercase, e.g. "million"
    multiplier: Decimal
    snippet: str  # the phrase as printed, e.g. "(in millions)"


@dataclass(frozen=True)
class ScaleResult:
    """What a page says about its scale."""

    status: str  # "found", "conflict" or "unknown"
    multiplier: Decimal | None  # set only when status is "found"
    statements: tuple[ScaleStatement, ...]
    # True if a statement says "except ..." (usually per-share data), meaning the
    # scale does not apply to every figure on the page.
    mentions_exceptions: bool


def _singular(word: str) -> str:
    word = word.lower()
    return word[:-1] if word.endswith("s") else word


def find_scale_statements(text: str) -> list[ScaleStatement]:
    """Every scale phrase in the text: bracketed phrases first, then standalone lines."""
    text = text.replace("\xa0", " ")  # a non-breaking space is not matched by \s in every case
    found: list[ScaleStatement] = []

    flat = " ".join(text.split())  # joins wrapped lines
    bracketed: list[tuple[int, ScaleStatement]] = []
    for pattern in (IN_SCALE, LEADING_SCALE):
        for match in pattern.finditer(flat):
            word = _singular(match.group(1))
            bracketed.append((match.start(), ScaleStatement(word, MULTIPLIERS[word], match.group(0))))
    found.extend(statement for _, statement in sorted(bracketed, key=lambda item: item[0]))

    for line in text.splitlines():
        match = LINE_SCALE.match(line)
        if match:
            word = _singular(match.group(1))
            found.append(ScaleStatement(word, MULTIPLIERS[word], " ".join(line.split())))
    return found


def detect_scale(text: str) -> ScaleResult:
    """Decide the scale of a page from its text."""
    statements = find_scale_statements(text)
    if not statements:
        return ScaleResult("unknown", None, (), False)
    exceptions = any(EXCEPT_RE.search(s.snippet) for s in statements)
    multipliers = {s.multiplier for s in statements}
    if len(multipliers) > 1:
        return ScaleResult("conflict", None, tuple(statements), exceptions)
    return ScaleResult("found", next(iter(multipliers)), tuple(statements), exceptions)
