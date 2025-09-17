"""Reporting utilities.

Formats analytics into human-readable text and JSON-serializable dicts.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

from .data_loader import Transaction
from . import analytics as an


def build_summary(txns: Iterable[Transaction]) -> Dict:
    txns = list(txns)
    summary = {
        "totals": an.summarize_income_expense(txns),
        "category_spend": an.spending_by_category(txns),
        "monthly": an.monthly_totals(txns),
        "top_merchants": an.top_merchants(txns, n=10),
        "recurring": an.detect_recurring(txns),
    }
    return summary


def format_text_report(summary: Dict) -> str:
    lines: List[str] = []
    t = summary["totals"]
    lines.append("=== Personal Finance Summary ===")
    lines.append(f"Income:  ${t['income']:.2f}")
    lines.append(f"Expense: ${t['expense']:.2f}")
    lines.append(f"Net:     ${t['net']:.2f}")
    lines.append("")

    lines.append("-- Spend by Category --")
    for cat, amt in summary["category_spend"].items():
        lines.append(f"{cat:15} ${amt:.2f}")
    lines.append("")

    lines.append("-- Monthly Totals --")
    for m, vals in summary["monthly"].items():
        lines.append(f"{m} | Inc ${vals['income']:.2f}  Exp ${vals['expense']:.2f}  Net ${vals['net']:.2f}")
    lines.append("")

    lines.append("-- Top Merchants (Spend) --")
    for desc, amt in summary["top_merchants"]:
        lines.append(f"{desc[:40]:40} ${amt:.2f}")
    lines.append("")

    lines.append("-- Recurring Payments (Detected) --")
    for desc, amt, months in summary["recurring"]:
        lines.append(f"{desc[:40]:40} ${amt:.2f}  ({months} months)")
    return "\n".join(lines)


def save_json(summary: Dict, path: str | Path) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
