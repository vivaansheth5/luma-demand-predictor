"""Currency formatting for briefs, tool output and guardrail messages."""

from __future__ import annotations

SYMBOLS: dict[str, str] = {
    "INR": "₹",
    "USD": "$",
    "EUR": "€",
    "GBP": "£",
    "JPY": "¥",
    "AUD": "A$",
    "CAD": "C$",
    "SGD": "S$",
    "AED": "AED ",
}


def symbol(currency: str) -> str:
    return SYMBOLS.get(currency.upper(), f"{currency.upper()} ")


def _indian_grouping(n: int) -> str:
    """12345678 -> 1,23,45,678 (the last three digits, then groups of two)."""
    s = str(abs(n))
    if len(s) <= 3:
        return s
    head, tail = s[:-3], s[-3:]
    parts = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    if head:
        parts.insert(0, head)
    return ",".join(parts) + "," + tail


def money(value: float, currency: str = "INR", compact: bool = True) -> str:
    """Format an amount: ₹12.3L, ₹1.18Cr, $12.3K, $1.2M; or in full with ``compact=False``."""
    sign = "-" if value < 0 else ""
    a = abs(float(value))
    sym = symbol(currency)
    indian = currency.upper() == "INR"
    if not compact:
        whole = round(a)
        return f"{sign}{sym}{_indian_grouping(whole) if indian else f'{whole:,}'}"
    if indian:
        if a >= 1e7:
            return f"{sign}{sym}{a / 1e7:.2f}Cr"
        if a >= 1e5:
            return f"{sign}{sym}{a / 1e5:.1f}L"
        return f"{sign}{sym}{_indian_grouping(round(a))}"
    if a >= 1e9:
        return f"{sign}{sym}{a / 1e9:.2f}B"
    if a >= 1e6:
        return f"{sign}{sym}{a / 1e6:.2f}M"
    if a >= 1e4:
        return f"{sign}{sym}{a / 1e3:.1f}K"
    return f"{sign}{sym}{round(a):,}"
