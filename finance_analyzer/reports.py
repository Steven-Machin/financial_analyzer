"""Reporting utilities.

Formats analytics into human-readable text and JSON-serializable dicts.
"""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path
from typing import Dict, IO, Iterable, List, Optional

from .data_loader import Transaction
from . import analytics as an


def build_summary(txns: Iterable[Transaction], budgets: Optional[Dict[str, float]] = None) -> Dict:
    txns = list(txns)
    summary = {
        "totals": an.summarize_income_expense(txns),
        "category_spend": an.spending_by_category(txns),
        "monthly": an.monthly_totals(txns),
        "top_merchants": an.top_merchants(txns, n=10),
        "recurring": an.detect_recurring(txns),
    }
    if budgets:
        summary["budget_status"] = an.budget_comparison(txns, budgets)
    summary["budget_usage"] = calculate_budget_usage(txns, budgets)
    return summary


def calculate_budget_usage(
    txns: Iterable[Transaction],
    budgets: Optional[Dict[str, float]] = None,
) -> List[Dict[str, float]]:
    """Compute budget usage metrics per category."""
    if not budgets:
        return []
    spend_per_category = defaultdict(float)
    for txn in txns:
        if txn.amount < 0:
            spend_per_category[txn.category or "Other"] += -txn.amount
    usage: List[Dict[str, float]] = []
    for category, limit in budgets.items():
        if limit is None:
            continue
        limit_value = float(limit)
        spent = round(spend_per_category.get(category, 0.0), 2)
        if limit_value > 0:
            percent_used = round((spent / limit_value) * 100, 2)
        else:
            percent_used = 100.0 if spent > 0 else 0.0
        usage.append({
            "category": category,
            "spent": spent,
            "limit": round(limit_value, 2),
            "percent_used": percent_used,
        })
    return usage



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

    budget = summary.get("budget_status")
    if budget:
        lines.append("-- Budget Status (Current Month) --")
        for cat, vals in budget.items():
            lines.append(f"{cat:15} Limit ${vals['limit']:.2f}  Actual ${vals['actual']:.2f}  Remaining ${vals['remaining']:.2f}")
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


def _ensure_text_writer(target: str | Path | IO[str]):
    if hasattr(target, "write"):
        return target, None
    path = Path(target)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("w", newline="", encoding="utf-8")
    return handle, handle


def export_summary_csv(summary: Dict, path: str | Path | IO[str]) -> None:
    def fmt_amount(value) -> str:
        if value is None:
            return ""
        return f"{float(value):.2f}"

    rows: List[List[str]] = [["Section", "Item", "Metric", "Value"]]

    totals = summary.get("totals") or {}
    for key, label in (("income", "Income"), ("expense", "Expense"), ("net", "Net")):
        if key in totals:
            rows.append(["Totals", "", label, fmt_amount(totals.get(key))])

    for cat, amt in (summary.get("category_spend") or {}).items():
        rows.append(["Category Spend", cat or "Uncategorized", "Amount", fmt_amount(amt)])

    for month, vals in (summary.get("monthly") or {}).items():
        if not isinstance(vals, dict):
            continue
        for key, label in (("income", "Income"), ("expense", "Expense"), ("net", "Net")):
            if key in vals:
                rows.append(["Monthly Totals", month, label, fmt_amount(vals.get(key))])

    for desc, amt in (summary.get("top_merchants") or []):
        rows.append(["Top Merchants", desc, "Spend", fmt_amount(amt)])

    for desc, amt, months in (summary.get("recurring") or []):
        rows.append(["Recurring Payments", desc, "Typical Amount", fmt_amount(amt)])
        rows.append(["Recurring Payments", desc, "Months Seen", str(months)])

    budget = summary.get("budget_status") or {}
    if isinstance(budget, dict):
        for cat, vals in budget.items():
            if not isinstance(vals, dict):
                continue
            for key, label in (("limit", "Limit"), ("actual", "Actual"), ("remaining", "Remaining")):
                if key in vals:
                    rows.append(["Budget Status", cat, label, fmt_amount(vals.get(key))])

    date_range = summary.get("date_range") or {}
    if isinstance(date_range, dict) and any(date_range.values()):
        rows.append(["Metadata", "Range Label", "", date_range.get("label", "")])
        rows.append(["Metadata", "Range Start", "", date_range.get("start", "")])
        rows.append(["Metadata", "Range End", "", date_range.get("end", "")])

    txn_count = summary.get("transaction_count")
    if txn_count is not None:
        rows.append(["Metadata", "Transaction Count", "", str(txn_count)])

    writer_target, to_close = _ensure_text_writer(path)
    try:
        writer = csv.writer(writer_target, lineterminator="\n")
        writer.writerows(rows)
    finally:
        if to_close is not None:
            to_close.close()


def export_summary_json(summary: Dict, path: str | Path | IO[str]) -> None:
    if hasattr(path, "write"):
        json.dump(summary, path, indent=2)
        path.write("\n")
        return
    save_json(summary, path)
