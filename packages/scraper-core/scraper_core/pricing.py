"""Price parsing that never silently guesses the unit or decimal style.

Built after a real production bug (PLAGG): four sources, four different price
formats. Sellpy returns prices as an INTEGER in oere/cents (minor units) -
forgetting to divide by 100 makes every price 100x too high, silently, with no
error. Reshopper/DBA use free text with "." as thousands separator and "," as
decimal ("1.234,56 kr."). Vinted uses a decimal STRING with "." as the decimal
separator ("47.26") - the opposite convention from Reshopper's free text, in
the same currency, on the same regional marketplace type. A single wrong
assumption produces a wrong number with no error, not a crash - the kind of
bug that only turns up when someone happens to eyeball a specific price.

`parse_price()` forces the unit to be stated explicitly at every call site
instead of defaulting to one, so a source that hands back minor units can't
be silently treated as major units.
"""

from __future__ import annotations

import re


def parse_price(
    raw: str | int | float | None,
    *,
    unit: str,
    decimal_style: str = "auto",
) -> float | None:
    """Parse a price into a float in MAJOR currency units (kr/EUR/etc), never
    minor units (oere/cents) - callers should store/compare prices in major
    units consistently everywhere.

    unit (no default - must be stated explicitly at every call site):
        "major" - raw is already kr/EUR/etc, as a number or free text
                   (e.g. 349.0, or "349,00 kr.")
        "minor" - raw is an integer count of oere/cents; result is raw / 100

    decimal_style:
        "auto"  (default) - guess from the string: if a "," appears AFTER the
                 last "." (or there's no "."), treat "," as the decimal
                 separator and "." as a thousands separator (Danish/European
                 free text, e.g. "1.234,56"). Otherwise treat "." as the
                 decimal separator (e.g. Vinted's "47.26").
        "comma" - force comma-as-decimal, dot-as-thousands.
        "dot"   - force dot-as-decimal, strip any "," as a thousands separator.

    Returns None for empty/unparseable input - this function never raises on
    bad input, so callers decide how to handle a missing price. It DOES raise
    ValueError for a bad `unit`/`decimal_style` argument - those are
    programmer errors, not scraped-data errors.
    """
    if unit not in ("major", "minor"):
        raise ValueError(f"unit must be 'major' or 'minor', got {unit!r}")
    if decimal_style not in ("auto", "comma", "dot"):
        raise ValueError(f"decimal_style must be 'auto', 'comma' or 'dot', got {decimal_style!r}")

    if raw is None:
        return None

    if isinstance(raw, (int, float)):
        value = float(raw)
    else:
        # Extract the number as a contiguous digit-led/digit-trailed run, not
        # by deleting disallowed characters from the whole string - the latter
        # would keep a stray "." from an abbreviation like "kr." even though
        # it's separated from the digits by a space and isn't part of the
        # number at all (a real bug caught by this module's own test suite).
        match = re.search(r"\d[\d.,]*\d|\d", str(raw).strip())
        if match is None:
            return None
        text = match.group(0)

        style = decimal_style
        if style == "auto":
            style = "comma" if text.rfind(",") > text.rfind(".") else "dot"

        if style == "comma":
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")

        try:
            value = float(text)
        except ValueError:
            return None

    if unit == "minor":
        value = value / 100

    return value
