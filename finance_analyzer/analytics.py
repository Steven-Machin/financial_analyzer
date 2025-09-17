"""Analytics and trend calculations.

Functions that compute summaries and trends from transactions.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict, Counter
from dataclasses import dataclass
from statistics import median
from typing import Dict, Iterable, List, Tuple

from .data_loader import Transaction


def month_key(d: dt.date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def summarize_income_expense(txns: Iterable[Transaction]) -> Dict[str, float]:
    income = sum(t.amount for t in txns if t.amount > 0)
    expense = -sum(t.amount for t in txns if t.amount < 0)
    net = income - expense
    return {"income": round(income, 2), "expense": round(expense, 2), "net": round(net, 2)}


def spending_by_category(txns: Iterable[Transaction]) -> Dict[str, float]:
    totals: Dict[str, float] = defaultdict(float)
    for t in txns:
        if t.amount < 0:
            cat = t.category or "Other"
            totals[cat] += -t.amount
    return {k: round(v, 2) for k, v in sorted(totals.items(), key=lambda kv: kv[1], reverse=True)}


def monthly_totals(txns: Iterable[Transaction]) -> Dict[str, Dict[str, float]]:
    months: Dict[str, Dict[str, float]] = defaultdict(lambda: {"income": 0.0, "expense": 0.0, "net": 0.0})
    for t in txns:
        m = month_key(t.date)
        if t.amount > 0:
            months[m]["income"] += t.amount
        else:
            months[m]["expense"] += -t.amount
        months[m]["net"] = months[m]["income"] - months[m]["expense"]
    # Round
    return {m: {k: round(v, 2) for k, v in vals.items()} for m, vals in sorted(months.items())}


def top_merchants(txns: Iterable[Transaction], n: int = 10) -> List[Tuple[str, float]]:
    spend: Dict[str, float] = defaultdict(float)
    for t in txns:
        if t.amount < 0:
            spend[t.description] += -t.amount
    ranked = sorted(spend.items(), key=lambda kv: kv[1], reverse=True)
    return [(d, round(v, 2)) for d, v in ranked[:n]]


def detect_recurring(txns: Iterable[Transaction], min_months: int = 3, tolerance: float = 0.15) -> List[Tuple[str, float, int]]:
    """Detect recurring payments by normalized description.

    Groups by description; if a merchant appears in >= min_months distinct months
    with amounts that are within `tolerance` (relative) of the median amount,
    it is considered recurring.
    Returns list of (description, typical_amount, month_count).
    """

    # Map description -> month -> list[amounts]
    by_desc_month: Dict[str, Dict[str, List[float]]] = defaultdict(lambda: defaultdict(list))
    for t in txns:
        if t.amount < 0:
            by_desc_month[t.description][month_key(t.date)].append(-t.amount)

    recurring: List[Tuple[str, float, int]] = []
    for desc, months in by_desc_month.items():
        month_amounts = [median(amts) for amts in months.values() if amts]
        if len(month_amounts) >= min_months:
            med = median(month_amounts)
            # Check relative variation
            ok = [abs(x - med) / med <= tolerance for x in month_amounts if med > 0]
            if ok and sum(ok) >= min_months:
                recurring.append((desc, round(med, 2), len(month_amounts)))
    # Sort by months desc then amount
    recurring.sort(key=lambda x: (-x[2], -x[1]))
    return recurring


def budget_comparison(
    txns: Iterable[Transaction],
    monthly_budgets: Dict[str, float],
) -> Dict[str, Dict[str, float]]:
    """Compare actual spend vs budgets per category (current month only)."""
    today = next(iter(txns)).date if txns else dt.date.today()
    current_month = month_key(today)
    # Sum spend per category in current month
    per_cat: Dict[str, float] = defaultdict(float)
    for t in txns:
        if t.amount < 0 and month_key(t.date) == current_month:
            per_cat[t.category or "Other"] += -t.amount
    result: Dict[str, Dict[str, float]] = {}
    for cat, limit in monthly_budgets.items():
        actual = round(per_cat.get(cat, 0.0), 2)
        remaining = round(limit - actual, 2)
        result[cat] = {"limit": round(limit, 2), "actual": actual, "remaining": remaining}
    return result
