"""Parse displayed amounts into exact Decimal values, recording every change.

An amount keeps its original text. Nothing is converted silently: every change
(brackets, minus sign, separators, symbols) is listed, and anything that cannot be
read with confidence is marked ambiguous and gets no value.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

MINUS_SIGNS = ("-", "\u2212")  # hyphen-minus and the Unicode minus sign
# Em dash, en dash and hyphen: how statements show a nil value.
NIL_MARKS = ("\u2014", "\u2013", "-")
CURRENCY_SYMBOLS = ("$",)  # a symbol alone never decides the currency

PLAIN = re.compile(r"^(?:\d+(?:\.\d+)?|\.\d+)$")
US_GROUPED = re.compile(r"^\d{1,3}(?:,\d{3})+(?:\.\d+)?$")  # 1,234,567.89
INDIAN_GROUPED = re.compile(r"^\d{1,2}(?:,\d{2})*,\d{3}(?:\.\d+)?$")  # 12,34,567.89
NUMBER_CHARS = re.compile(r"^[\d,.]+$")
NUMBER_FORMATS = {"us": US_GROUPED, "indian": INDIAN_GROUPED}


@dataclass(frozen=True)
class ParsedAmount:
    """The result of reading one displayed amount."""

    raw: str  # exactly as displayed
    kind: str  # "number", "percent", "nil", "ambiguous" or "text"
    value: Decimal | None  # None unless kind is "number" or "percent"
    transformations: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    currency_symbol: str | None = None


def parse_amount(raw: str, number_format: str = "us") -> ParsedAmount:
    """Read a displayed amount. number_format is "us" or "indian" (digit grouping)."""
    if number_format not in NUMBER_FORMATS:
        raise ValueError(
            f"number_format must be one of {sorted(NUMBER_FORMATS)}, got {number_format!r}"
        )
    text = raw.strip()
    if text in NIL_MARKS:
        return ParsedAmount(raw, "nil", None)

    transformations: list[str] = []
    bracketed = False
    if text.startswith("(") and text.endswith(")") and len(text) > 2:
        bracketed = True
        text = text[1:-1].strip()
        transformations.append("brackets read as a negative sign")
    elif text.startswith("(") or text.endswith(")"):
        return ParsedAmount(raw, "ambiguous", None, (), ("unbalanced bracket",))

    symbol = None
    minus = False
    while text and (text[0] in MINUS_SIGNS or text[0] in CURRENCY_SYMBOLS):
        if text[0] in CURRENCY_SYMBOLS:
            if symbol is not None:
                break
            symbol = text[0]
        else:
            if minus:
                break
            minus = True
        text = text[1:].strip()

    percent = False
    if text.endswith("%"):
        percent = True
        text = text[:-1].strip()

    if text in NIL_MARKS and not minus:
        return ParsedAmount(raw, "nil", None, (), (), symbol)
    if bracketed and minus:
        return ParsedAmount(
            raw, "ambiguous", None, tuple(transformations),
            ("both brackets and a minus sign",), symbol,
        )
    if not text or not NUMBER_CHARS.match(text):
        return ParsedAmount(raw, "text", None, tuple(transformations), (), symbol)
    if not (PLAIN.match(text) or NUMBER_FORMATS[number_format].match(text)):
        return ParsedAmount(
            raw, "ambiguous", None, tuple(transformations),
            (
                f"digit grouping does not match the expected {number_format} format; "
                "it may use a decimal comma or another convention",
            ),
            symbol,
        )

    value = Decimal(text.replace(",", ""))
    if "," in text:
        transformations.append("thousands separators removed")
    if symbol is not None:
        transformations.append(
            f"currency symbol {symbol} removed (the currency itself is not decided here)"
        )
    if percent:
        transformations.append("percent sign removed; the value is a percentage, not a fraction")
    if minus:
        transformations.append("minus sign read as negative")
    if (bracketed or minus) and value != 0:
        value = -value
    return ParsedAmount(
        raw, "percent" if percent else "number", value, tuple(transformations), (), symbol
    )
