"""Command-line interface for the Personal Finance Analyzer.

Usage:
  python -m finance_analyzer.cli --input sample_data/sample_transactions.csv

Options allow multiple CSVs, config with custom rules/budgets, and JSON export.
"""

from __future__ import annotations

import argparse
import datetime as dt
from pathlib import Path
from typing import List, Optional

from .config import AppConfig
from .data_loader import load_csv_files, Transaction
from .categorizer import categorize_transactions
from .reports import build_summary, format_text_report, save_json


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Personal Finance Analyzer")
    p.add_argument("--input", "-i", nargs="+", required=True, help="CSV file(s) to load")
    p.add_argument("--config", "-c", help="Path to JSON config with rules/budgets")
    p.add_argument("--from", dest="date_from", help="Start date (YYYY-MM-DD)")
    p.add_argument("--to", dest="date_to", help="End date (YYYY-MM-DD)")
    p.add_argument("--json", dest="json_out", help="Write summary JSON to path")
    return p.parse_args(argv)


def _parse_date(d: Optional[str]) -> Optional[dt.date]:
    if not d:
        return None
    return dt.date.fromisoformat(d)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)

    cfg = AppConfig.load(args.config)
    txns = load_csv_files(args.input)

    # Optional date filtering
    dfrom = _parse_date(args.date_from)
    dto = _parse_date(args.date_to)
    if dfrom or dto:
        txns = [t for t in txns if (not dfrom or t.date >= dfrom) and (not dto or t.date <= dto)]

    categorize_transactions(txns, cfg.rules)

    budget_limits = {b.category: b.monthly_limit for b in cfg.budgets}
    summary = build_summary(txns, budget_limits or None)
    print(format_text_report(summary))

    if args.json_out:
        save_json(summary, args.json_out)
        print(f"\nSaved JSON summary to: {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

