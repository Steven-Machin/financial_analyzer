"""Flask web interface for the Personal Finance Analyzer."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import List, Optional

from flask import Flask, jsonify, redirect, render_template, request, session, url_for

from .config import AppConfig
from .data_loader import Transaction, load_csv_files
from .categorizer import categorize_transactions
from .reports import build_summary

PACKAGE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_ROOT.parent

WINDOW_OPTIONS = (1, 3, 6)
DEFAULT_WINDOW = 3
SESSION_KEY = "manual_transactions"


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


def _get_manual_entries() -> List[dict]:
    data = session.get(SESSION_KEY, [])
    entries: List[dict] = []
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                entries.append(
                    {
                        "date": item.get("date"),
                        "description": item.get("description", ""),
                        "amount": item.get("amount"),
                        "account": item.get("account"),
                        "kind": item.get("kind"),
                    }
                )
    return entries


def _store_manual_entries(entries: List[dict]) -> None:
    session[SESSION_KEY] = entries


def _entries_to_transactions(entries: List[dict]) -> List[Transaction]:
    txns: List[Transaction] = []
    for item in entries:
        date_raw = item.get("date")
        amount_raw = item.get("amount")
        description = item.get("description", "")
        account = item.get("account") or None
        try:
            if not date_raw:
                continue
            date = dt.date.fromisoformat(str(date_raw))
        except (TypeError, ValueError):
            continue
        try:
            amount = float(amount_raw)
        except (TypeError, ValueError):
            continue
        txns.append(Transaction(date=date, description=description, amount=amount, account=account))
    txns.sort(key=lambda t: (t.date, t.description, t.amount))
    return txns


def _redirect_to_index(window_months: int, inputs: List[str], default_window: int):
    params: dict = {}
    if window_months != default_window:
        params["window"] = window_months
    if inputs:
        params["input"] = inputs
    return redirect(url_for("index", **params))


def _load_summary(
    inputs: List[str],
    config_path: Optional[str],
    window_months: int,
    manual_txns: Optional[List[Transaction]] = None,
) -> dict:
    resolved_inputs = _resolve_paths(inputs) if inputs else []
    resolved_config = _resolve_config_path(config_path)
    cfg = AppConfig.load(resolved_config)

    txns: List[Transaction] = []
    if resolved_inputs:
        txns.extend(load_csv_files(resolved_inputs))
    if manual_txns:
        txns.extend(manual_txns)
    if txns:
        txns.sort(key=lambda t: (t.date, t.description, t.amount))

    categorize_transactions(txns, cfg.rules)
    filtered_txns = _filter_recent_transactions(txns, window_months)
    budget_limits = {b.category: b.monthly_limit for b in cfg.budgets}
    summary = build_summary(filtered_txns, budget_limits or None)
    summary["date_range"] = _format_range_metadata(filtered_txns)
    summary["transaction_count"] = len(filtered_txns)
    return summary


def create_app(default_inputs: Optional[List[str]] = None, config_path: Optional[str] = None) -> Flask:
    app = Flask(
        __name__,
        template_folder=str(PACKAGE_ROOT / "templates"),
        static_folder=str(PACKAGE_ROOT / "static"),
    )
    app.config.setdefault("SECRET_KEY", "dev-secret-key")

    default_inputs = default_inputs or []
    default_window = DEFAULT_WINDOW

    @app.route("/", methods=["GET", "POST"])
    def index():
        inputs = (
            request.form.getlist("input")
            if request.method == "POST"
            else request.args.getlist("input")
        ) or default_inputs
        window_value = request.values.get("window")
        window_months = _normalize_window(window_value, default_window)

        errors: List[str] = []
        form_data = {
            "date": "",
            "description": "",
            "amount": "",
            "account": "",
            "kind": "expense",
        }

        manual_entries = _get_manual_entries()

        if request.method == "POST":
            action = request.form.get("action", "add")
            form_data = {
                "date": (request.form.get("date", "") or "").strip(),
                "description": (request.form.get("description", "") or "").strip(),
                "amount": (request.form.get("amount", "") or "").strip(),
                "account": (request.form.get("account", "") or "").strip(),
                "kind": request.form.get("kind", "expense"),
            }

            if action == "add":
                if not form_data["date"]:
                    errors.append("Date is required.")
                if not form_data["description"]:
                    errors.append("Description is required.")
                if not form_data["amount"]:
                    errors.append("Amount is required.")

                tx_type = form_data.get("kind") or "expense"
                if tx_type not in {"expense", "income"}:
                    tx_type = "expense"

                try:
                    date_value = dt.date.fromisoformat(form_data["date"])
                except ValueError:
                    errors.append("Date must be in YYYY-MM-DD format.")
                    date_value = None

                try:
                    amount_value = float(form_data["amount"])
                except ValueError:
                    errors.append("Amount must be a valid number.")
                    amount_value = None

                if amount_value == 0:
                    errors.append("Amount cannot be zero.")

                if not errors and amount_value is not None:
                    if tx_type == "expense":
                        amount_value = -abs(amount_value)
                    else:
                        amount_value = abs(amount_value)

                if not errors and date_value and amount_value is not None:
                    manual_entries.append(
                        {
                            "date": date_value.isoformat(),
                            "description": form_data["description"],
                            "amount": amount_value,
                            "account": form_data["account"] or None,
                            "kind": tx_type,
                        }
                    )
                    _store_manual_entries(manual_entries)
                    return _redirect_to_index(window_months, inputs, default_window)

            elif action == "delete":
                idx_raw = request.form.get("index")
                try:
                    idx = int(idx_raw)
                except (TypeError, ValueError):
                    idx = None
                if idx is None or idx < 0 or idx >= len(manual_entries):
                    errors.append("Unable to remove transaction.")
                else:
                    manual_entries.pop(idx)
                    _store_manual_entries(manual_entries)
                    return _redirect_to_index(window_months, inputs, default_window)

            elif action == "clear":
                manual_entries = []
                _store_manual_entries(manual_entries)
                return _redirect_to_index(window_months, inputs, default_window)

        manual_txns = _entries_to_transactions(manual_entries)
        summary = _load_summary(inputs, config_path, window_months, manual_txns)
        window_label = _window_label(window_months)
        chart_title = f"Spending by Category ({window_label})"

        manual_display = []
        for idx, entry in enumerate(manual_entries):
            amount_val = entry.get("amount")
            try:
                amount_val = float(amount_val)
            except (TypeError, ValueError):
                amount_val = None
            manual_display.append(
                {
                    "index": idx,
                    "date": entry.get("date"),
                    "description": entry.get("description", ""),
                    "amount": amount_val,
                    "account": entry.get("account") or "",
                    "kind": entry.get("kind") or ("income" if (amount_val or 0) >= 0 else "expense"),
                }
            )

        manual_count = len(manual_txns)
        if inputs:
            parts = [", ".join(inputs)]
            if manual_count:
                parts.append(f"{manual_count} manual entr{'y' if manual_count == 1 else 'ies'}")
            data_source_label = " + ".join(parts)
        else:
            data_source_label = (
                f"{manual_count} manual entr{'y' if manual_count == 1 else 'ies'}"
                if manual_count
                else "No data yet"
            )

        return render_template(
            "index.html",
            summary=summary,
            inputs=inputs,
            active_window=window_months,
            window_options=WINDOW_OPTIONS,
            window_label=window_label,
            chart_title=chart_title,
            manual_transactions=manual_display,
            manual_count=manual_count,
            data_source_label=data_source_label,
            errors=errors,
            form_data=form_data,
        )

    @app.route("/api/summary")
    def api_summary():
        inputs = request.args.getlist("input") or default_inputs
        window_value = request.args.get("window")
        window_months = _normalize_window(window_value, default_window)
        manual_txns = _entries_to_transactions(_get_manual_entries())
        summary = _load_summary(inputs, config_path, window_months, manual_txns)
        summary["window_months"] = window_months
        summary["window_label"] = _window_label(window_months)
        summary["manual_transaction_count"] = len(manual_txns)
        return jsonify(summary)

    return app


app = create_app()


if __name__ == "__main__":
    app.run(debug=True)
