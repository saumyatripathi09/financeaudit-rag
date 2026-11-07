"""Guess which financial statement a page shows. Never guesses when evidence is thin."""
from __future__ import annotations

import re
from dataclasses import dataclass

TITLE_PATTERNS = [
    ("cash_flow", re.compile(r"statements?\s+of\s+(?:consolidated\s+)?cash\s*flows?", re.I)),
    ("balance_sheet", re.compile(
        r"balance\s+sheets?|statements?\s+of\s+financial\s+(?:position|condition)", re.I)),
    ("equity", re.compile(
        r"statements?\s+of\s+(?:changes\s+in\s+)?(?:stockholders|shareholders|shareowners|owners)"
        r"['\u2019]?\s*(?:equity|deficit)", re.I)),
    ("comprehensive_income", re.compile(r"statements?\s+of\s+comprehensive\s+(?:income|loss)", re.I)),
    ("income_statement", re.compile(
        r"statements?\s+of\s+(?:consolidated\s+)?(?:income|operations|earnings)"
        r"|income\s+statements?", re.I)),
]

# Words that, in row labels, point to a statement. Matched on normalised labels.
LABEL_CUES = {
    "balance_sheet": ["total assets", "total current assets", "total liabilities",
                      "total current liabilities", "retained earnings", "accounts receivable"],
    "income_statement": ["net income", "gross profit", "operating income", "income before",
                         "cost of sales", "cost of revenue", "net sales", "revenue"],
    "cash_flow": ["operating activities", "investing activities", "financing activities",
                  "depreciation and amortization", "net increase", "net decrease"],
}


@dataclass(frozen=True)
class StatementGuess:
    statement: str  # a type name, or "unknown"
    basis: str  # "title", "labels" or "none"
    evidence: tuple[str, ...] = ()


MAX_TITLE_WORDS = 12
HEAD_LINES = 14  # a statement title is near the top of its page


def _title_candidates(title_text: str) -> list[str]:
    """Short lines that could be a heading, plus pairs of neighbours (a title can wrap)."""
    lines = [" ".join(line.split()) for line in title_text.splitlines()]
    lines = [line for line in lines if line][:HEAD_LINES]
    out = []
    for i, line in enumerate(lines):
        out.append(line)
        if i + 1 < len(lines):
            out.append(line + " " + lines[i + 1])
    return out


def _is_heading(candidate: str) -> bool:
    """A heading is short and is not a sentence that merely mentions a statement."""
    if len(candidate.split()) > MAX_TITLE_WORDS:
        return False
    if candidate.endswith((":", ".", ",", ";")):
        return False
    return not candidate[0].islower()


TITLE_TAIL_WORDS = {"as", "at", "and", "for", "of", "unaudited", "audited", "condensed"}


def _ends_like_a_title(tail: str) -> bool:
    """After the statement name comes nothing, punctuation or a date phrase, not another
    capitalised word ("Balance Sheet Location" is a table column, not a title)."""
    tail = tail.strip()
    if not tail or not tail[0].isalpha():
        return True
    return tail.split()[0].lower() in TITLE_TAIL_WORDS


def detect_statement(title_text: str, labels: list[str]) -> StatementGuess:
    """`title_text` is the top of the page; `labels` are the row labels on it.

    Only a heading counts as a title. A sentence such as "the location of derivative
    instruments in the consolidated balance sheet:" mentions a statement without being one.
    """
    found: list[tuple[str, int]] = []
    for position, candidate in enumerate(_title_candidates(title_text)):
        if not _is_heading(candidate):
            continue
        for name, pat in TITLE_PATTERNS:
            match = pat.search(candidate)
            if match and _ends_like_a_title(candidate[match.end():]) and name not in [n for n, _ in found]:
                found.append((name, position))
    if found:
        found.sort(key=lambda item: item[1])  # the earliest title wins
        names = [n for n, _ in found]
        return StatementGuess(names[0], "title", tuple(names))
    text = [" ".join(re.sub(r"[^a-z0-9 ]", " ", x.lower()).split()) for x in labels]
    scores = {
        name: sorted({cue for cue in cues if any(cue in t for t in text)})
        for name, cues in LABEL_CUES.items()
    }
    ranked = sorted(scores.items(), key=lambda kv: len(kv[1]), reverse=True)
    best, second = ranked[0], ranked[1]
    if len(best[1]) >= 3 and len(best[1]) >= len(second[1]) + 2:
        return StatementGuess(best[0], "labels", tuple(best[1]))
    return StatementGuess("unknown", "none", ())
