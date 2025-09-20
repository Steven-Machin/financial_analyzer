"""Analytics and trend calculations.

Functions that compute summaries and trends from transactions.
"""

from __future__ import annotations

import datetime as dt
import re
from collections import defaultdict, Counter
from dataclasses import dataclass
from statistics import median
from typing import Dict, Iterable, List, Tuple

from .data_loader import Transaction


def month_key(d: dt.date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def _normalize_description(desc: str) -> str:
    desc = desc.lower()
    desc = re.sub(r"\d+", " ", desc)
    desc = re.sub(r"[^a-z\s]", " ", desc)
    desc = re.sub(r"\s+", " ", desc).strip()
    return desc


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

    A merchant is considered recurring when the charge appears in at least
    ``min_months`` unique months and the amounts remain within a tolerance.
    ``tolerance`` is a relative percentage with a $1 absolute floor to avoid
    rejecting small fluctuations on low recurring charges.
    """

    by_desc_month: Dict[str, Dict[str, List[float]]] = defaultdict(lambda: defaultdict(list))
    display_names: Dict[str, Counter[str]] = defaultdict(Counter)
    for t in txns:
        if t.amount < 0:
            norm = _normalize_description(t.description) or t.description.lower().strip()
            by_desc_month[norm][month_key(t.date)].append(-t.amount)
            display_names[norm][t.description] += 1

    recurring: List[Tuple[str, float, int]] = []
    for norm_desc, months in by_desc_month.items():
        month_amounts = {month: median(amts) for month, amts in months.items() if amts}
        if len(month_amounts) < min_months:
            continue
        med = median(month_amounts.values())
        tolerance_amount = max(abs(med) * tolerance, 1.0) if med else 1.0
        accepted = [amount for amount in month_amounts.values() if abs(amount - med) <= tolerance_amount]
        if len(accepted) < min_months:
            continue
        typical_amount = round(sum(accepted) / len(accepted), 2)
        display = display_names[norm_desc].most_common(1)[0][0]
        recurring.append((display, typical_amount, len(accepted)))

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
