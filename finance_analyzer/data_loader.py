"""Data loading helpers.

Supports reading one or more CSV files and normalizing them into a common
transaction schema with fields:
    date (datetime.date), description (str), amount (float), account (str|None)

CSV columns are auto-detected case-insensitively among common variants.
"""

from __future__ import annotations

import csv
import datetime as dt
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional


@dataclass
class Transaction:
    date: dt.date
    description: str
    amount: float  # negative = expense, positive = income
    account: Optional[str] = None
    category: Optional[str] = None  # filled later by categorizer


def _parse_date(value: str) -> dt.date:
    value = value.strip()
    # Try multiple common date formats
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            return dt.datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    # Fallback to fromisoformat if possible
    try:
        return dt.date.fromisoformat(value)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"Unrecognized date format: {value}") from exc


def _to_float(value: str) -> float:
    v = value.replace(",", "").strip()
    # Some exports wrap negatives in parentheses, e.g., (12.34)
    if v.startswith("(") and v.endswith(")"):
        v = "-" + v[1:-1]
    try:
        return float(v)
    except ValueError as exc:  # noqa: BLE001
        raise ValueError(f"Invalid amount: {value}") from exc


def _find_column(row_keys: Iterable[str], candidates: Iterable[str]) -> Optional[str]:
    low = {k.lower(): k for k in row_keys}
    for cand in candidates:
        if cand.lower() in low:
            return low[cand.lower()]
    return None


_DATE_COLS = ("date", "posted date", "posting date", "transaction date")
_DESC_COLS = ("description", "details", "memo", "name")
_AMT_COLS = ("amount", "amt", "value")
_DEBIT_COLS = ("debit", "withdrawal")
_CREDIT_COLS = ("credit", "deposit")
_ACCOUNT_COLS = ("account", "account name", "account number")


def load_csv_file(path: str | Path) -> List[Transaction]:
    p = Path(path)
    with p.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        date_col = _find_column(fieldnames, _DATE_COLS)
        desc_col = _find_column(fieldnames, _DESC_COLS)
        amt_col = _find_column(fieldnames, _AMT_COLS)
        debit_col = _find_column(fieldnames, _DEBIT_COLS)
        credit_col = _find_column(fieldnames, _CREDIT_COLS)
        account_col = _find_column(fieldnames, _ACCOUNT_COLS)

        if not date_col or not desc_col or (not amt_col and not (debit_col or credit_col)):
            raise ValueError(
                f"{p.name}: Missing required columns. Need date+description and amount OR debit/credit."
            )

        txns: List[Transaction] = []
        for row in reader:
            date = _parse_date(row[date_col])
            description = (row[desc_col] or "").strip()
            amount: float
            if amt_col:
                amount = _to_float(row[amt_col])
            else:
                debit = row.get(debit_col) if debit_col else None
                credit = row.get(credit_col) if credit_col else None
                d = _to_float(debit) if debit not in (None, "") else 0.0
                c = _to_float(credit) if credit not in (None, "") else 0.0
                amount = c - d  # credit positive, debit negative

            account = (row.get(account_col) or None) if account_col else None
            txns.append(Transaction(date=date, description=description, amount=amount, account=account))
    return txns


def load_csv_files(paths: Iterable[str | Path]) -> List[Transaction]:
    all_txns: List[Transaction] = []
    for p in paths:
        all_txns.extend(load_csv_file(p))
    # Sort by date ascending
    all_txns.sort(key=lambda t: (t.date, t.description, t.amount))
    return all_txns
