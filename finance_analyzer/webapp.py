"""Flask web interface for the Personal Finance Analyzer."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import List, Optional

from flask import Flask, jsonify, render_template, request

from .config import AppConfig
from .data_loader import load_csv_files
from .categorizer import categorize_transactions
from .reports import build_summary

PACKAGE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_ROOT.parent

WINDOW_OPTIONS = (1, 3, 6)
DEFAULT_WINDOW = 3


def _resolve_paths(paths: List[str]) -> List[Path]:
    resolved: List[Path] = []
    for raw in paths:
        p = Path(raw)
        if not p.is_absolute():
            p = PROJECT_ROOT / p
        resolved.append(p)
    return resolved


def _resolve_config_path(config_path: Optional[str]) -> Optional[Path]:
    if not config_path:
        return None
    cfg_path = Path(config_path)
    if not cfg_path.is_absolute():
        cfg_path = PROJECT_ROOT / cfg_path
    return cfg_path


def _month_floor(value: dt.date) -> dt.date:
    return dt.date(value.year, value.month, 1)


def _subtract_months(value: dt.date, months: int) -> dt.date:
    year = value.year
    month = value.month - months
    while month <= 0:
        month += 12
        year -= 1
    return dt.date(year, month, 1)


def _filter_recent_transactions(txns, months: Optional[int]):
    if not txns or not months or months <= 0:
        return list(txns)
    latest = max(t.date for t in txns)
    start_boundary = _subtract_months(_month_floor(latest), months - 1)
    return [t for t in txns if t.date >= start_boundary]


def _format_range_metadata(txns) -> dict:
    if not txns:
        return {"start": None, "end": None, "label": "No data available", "display": "with no available data"}
    start = min(t.date for t in txns)
    end = max(t.date for t in txns)
    start_label = start.strftime("%B %Y")
    end_label = end.strftime("%B %Y")
    if start_label == end_label:
        label = start_label
        display = f"for {start_label}"
    else:
        label = f"{start_label} to {end_label}"
        display = f"from {start_label} to {end_label}"
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "label": label,
        "display": display,
    }


def _normalize_window(value: Optional[str], default: int) -> int:
    try:
        months = int(value) if value is not None else default
        return months if months > 0 else default
    except (TypeError, ValueError):
        return default


def _window_label(months: int) -> str:
    if months == 1:
        return "Last Month"
    if months == 3:
        return "Last 3 Months"
    if months == 6:
        return "Last 6 Months"
    return f"Last {months} Months"


def _load_summary(inputs: List[str], config_path: Optional[str], window_months: int) -> dict:
    resolved_inputs = _resolve_paths(inputs)
    resolved_config = _resolve_config_path(config_path)
    cfg = AppConfig.load(resolved_config)
    txns = load_csv_files(resolved_inputs)
    categorize_transactions(txns, cfg.rules)
    filtered_txns = _filter_recent_transactions(txns, window_months)
    budget_limits = {b.category: b.monthly_limit for b in cfg.budgets}
    summary = build_summary(filtered_txns, budget_limits or None)
    summary["date_range"] = _format_range_metadata(filtered_txns)
    return summary


def create_app(default_inputs: Optional[List[str]] = None, config_path: Optional[str] = None) -> Flask:
    app = Flask(
        __name__,
        template_folder=str(PACKAGE_ROOT / "templates"),
        static_folder=str(PACKAGE_ROOT / "static"),
    )

    default_inputs = default_inputs or ["sample_data/sample_transactions.csv"]
    default_window = DEFAULT_WINDOW

    @app.route("/")
    def index() -> str:
        inputs = request.args.getlist("input") or default_inputs
        window_value = request.args.get("window")
        window_months = _normalize_window(window_value, default_window)
        summary = _load_summary(inputs, config_path, window_months)
        window_label = _window_label(window_months)
        chart_title = f"Spending by Category ({window_label})"
        return render_template(
            "index.html",
            summary=summary,
            inputs=inputs,
            active_window=window_months,
            window_options=WINDOW_OPTIONS,
            window_label=window_label,
            chart_title=chart_title,
        )

    @app.route("/api/summary")
    def api_summary():
        inputs = request.args.getlist("input") or default_inputs
        window_value = request.args.get("window")
        window_months = _normalize_window(window_value, default_window)
        summary = _load_summary(inputs, config_path, window_months)
        summary["window_months"] = window_months
        summary["window_label"] = _window_label(window_months)
        return jsonify(summary)

    return app


app = create_app()


if __name__ == "__main__":
    app.run(debug=True)
