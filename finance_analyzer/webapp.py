"""Flask web interface for the Personal Finance Analyzer."""

import datetime as dt
import io
import uuid
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from flask import Flask, jsonify, redirect, render_template, request, session, url_for

from .config import AppConfig
from .data_loader import Transaction, load_csv_files, load_csv_stream
from .categorizer import categorize_transactions
from .reports import build_summary

PACKAGE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_ROOT.parent

RANGE_OPTIONS = (
    ("last_3", "Last 3 Months"),
    ("last_6", "Last 6 Months"),
    ("year", "This Year"),
    ("custom", "Custom Range"),
)
RANGE_LABELS = {key: label for key, label in RANGE_OPTIONS}
DEFAULT_RANGE = "last_3"

SESSION_TRANSACTIONS = "pfa_transactions"
SESSION_BUDGETS = "pfa_budgets"


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


def _filter_recent_transactions(txns: Sequence[Transaction], months: Optional[int]):
    if not txns or not months or months <= 0:
        return list(txns)
    latest = max(t.date for t in txns)
    start_boundary = _subtract_months(_month_floor(latest), months - 1)
    return [t for t in txns if t.date >= start_boundary]


def _format_range_metadata(txns) -> dict:
    if not txns:
        return {
            "start": None,
            "end": None,
            "label": "No data available",
            "display": "with no available data",
        }
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


def _parse_filters(data) -> Dict[str, str]:
    range_key = data.get("range") or DEFAULT_RANGE
    if range_key not in RANGE_LABELS:
        range_key = DEFAULT_RANGE
    category = data.get("category") or "all"
    account = data.get("account") or "all"
    start = data.get("start") or ""
    end = data.get("end") or ""
    if range_key != "custom":
        start = ""
        end = ""
    return {
        "range": range_key,
        "category": category,
        "account": account,
        "start": start,
        "end": end,
    }


def _filters_for_redirect(filters: Dict[str, str], inputs: Sequence[str], extra: Optional[Dict[str, str]] = None):
    params: Dict[str, object] = {}
    if inputs:
        params["input"] = list(inputs)
    if filters.get("category") and filters["category"] not in {"", "all"}:
        params["category"] = filters["category"]
    if filters.get("account") and filters["account"] not in {"", "all"}:
        params["account"] = filters["account"]
    if filters.get("range") and filters["range"] != DEFAULT_RANGE:
        params["range"] = filters["range"]
    if filters.get("range") == "custom":
        if filters.get("start"):
            params["start"] = filters["start"]
        if filters.get("end"):
            params["end"] = filters["end"]
    if extra:
        params.update(extra)
    return params


def _redirect_to_index(filters: Dict[str, str], inputs: Sequence[str], extra: Optional[Dict[str, str]] = None):
    params = _filters_for_redirect(filters, inputs, extra)
    return redirect(url_for("index", **params))


def _safe_parse_date(value: str) -> Optional[dt.date]:
    if not value:
        return None
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        return None


def _apply_filters(txns: List[Transaction], filters: Dict[str, str]) -> List[Transaction]:
    filtered = list(txns)
    range_key = filters.get("range", DEFAULT_RANGE)
    if range_key == "last_3":
        filtered = _filter_recent_transactions(filtered, 3)
    elif range_key == "last_6":
        filtered = _filter_recent_transactions(filtered, 6)
    elif range_key == "year":
        year = dt.date.today().year
        filtered = [t for t in filtered if t.date.year == year]
    elif range_key == "custom":
        start = _safe_parse_date(filters.get("start"))
        end = _safe_parse_date(filters.get("end"))
        if start or end:
            filtered = [
                t
                for t in filtered
                if (start is None or t.date >= start) and (end is None or t.date <= end)
            ]

    category_filter = filters.get("category")
    if category_filter and category_filter not in {"", "all"}:
        filtered = [t for t in filtered if (t.category or "Other") == category_filter]

    account_filter = filters.get("account")
    if account_filter and account_filter not in {"", "all"}:
        filtered = [t for t in filtered if (t.account or "Unspecified") == account_filter]

    return filtered


def _get_session_transactions() -> List[Dict[str, object]]:
    raw = session.get(SESSION_TRANSACTIONS, [])
    entries: List[Dict[str, object]] = []
    changed = False
    items = raw if isinstance(raw, list) else []
    for item in items:
        if not isinstance(item, dict):
            changed = True
            continue
        entry: Dict[str, object] = {}
        entry_id = item.get("id") or uuid.uuid4().hex
        entry["id"] = entry_id
        entry["date"] = item.get("date")
        entry["description"] = (item.get("description") or "").strip()
        account_raw = (item.get("account") or "").strip()
        entry["account"] = account_raw
        entry["source"] = item.get("source") or "manual"
        try:
            amount = float(item.get("amount", 0))
        except (TypeError, ValueError):
            changed = True
            continue
        entry["amount"] = amount
        kind = item.get("kind") or ("income" if amount >= 0 else "expense")
        entry["kind"] = "income" if kind == "income" else "expense"
        entries.append(entry)
        if entry_id != item.get("id") or account_raw != (item.get("account") or ""):
            changed = True
    if changed:
        _store_session_transactions(entries)
    return entries
def _store_session_transactions(entries: List[Dict[str, object]]) -> None:
    session[SESSION_TRANSACTIONS] = entries


def _get_session_budgets() -> Dict[str, float]:
    raw = session.get(SESSION_BUDGETS, {})
    budgets: Dict[str, float] = {}
    if isinstance(raw, dict):
        for cat, value in raw.items():
            try:
                budgets[str(cat)] = float(value)
            except (TypeError, ValueError):
                continue
    session[SESSION_BUDGETS] = budgets
    return budgets


def _store_session_budgets(budgets: Dict[str, float]) -> None:
    session[SESSION_BUDGETS] = budgets


def _entries_to_transactions(entries: List[Dict[str, object]]) -> List[Transaction]:
    txns: List[Transaction] = []
    for item in entries:
        date_raw = item.get("date")
        if not date_raw:
            continue
        try:
            date = dt.date.fromisoformat(str(date_raw))
        except ValueError:
            continue
        try:
            amount = float(item.get("amount", 0))
        except (TypeError, ValueError):
            continue
        description = (item.get("description") or "").strip()
        account = (item.get("account") or None) if item.get("account") else None
        txns.append(
            Transaction(
                date=date,
                description=description,
                amount=amount,
                account=account,
                id=str(item.get("id")),
            )
        )
    txns.sort(key=lambda t: (t.date, t.description, t.amount))
    return txns


def _range_display(filters: Dict[str, str]) -> str:
    range_key = filters.get("range", DEFAULT_RANGE)
    if range_key == "custom":
        start = filters.get("start") or "-"
        end = filters.get("end") or "-"
        return f"Custom Range ({start} to {end})"
    return RANGE_LABELS.get(range_key, RANGE_LABELS[DEFAULT_RANGE])

def _classify_session_counts(entries: List[Dict[str, object]]) -> Dict[str, int]:
    counts = {"manual": 0, "upload": 0}
    for entry in entries:
        src = entry.get("source")
        if src not in counts:
            counts[src] = 0
        counts[src] += 1
    return counts


def _compose_data_source_label(counts: Dict[str, int]) -> str:
    parts: List[str] = []
    if counts.get("manual"):
        parts.append(f"{counts['manual']} manual entr{'y' if counts['manual'] == 1 else 'ies'}")
    if counts.get("upload"):
        parts.append(f"{counts['upload']} imported entr{'y' if counts['upload'] == 1 else 'ies'}")
    return " + ".join(parts) if parts else "No data yet"


def create_app(default_inputs: Optional[List[str]] = None, config_path: Optional[str] = None) -> Flask:
    app = Flask(
        __name__,
        template_folder=str(PACKAGE_ROOT / "templates"),
        static_folder=str(PACKAGE_ROOT / "static"),
    )
    app.config.setdefault("SECRET_KEY", "dev-secret-key")

    default_inputs = default_inputs or []

    @app.route("/", methods=["GET", "POST"])
    def index():
        raw_inputs = request.form.getlist("input") if request.method == "POST" else request.args.getlist("input")
        inputs = raw_inputs or default_inputs

        filters = _parse_filters(request.form if request.method == "POST" else request.args)
        errors: List[str] = []

        transactions_entries = _get_session_transactions()
        session_budgets = _get_session_budgets()

        form_data = {
            "date": "",
            "description": "",
            "amount": "",
            "account": "",
            "kind": "expense",
        }
        form_mode = "add"
        edit_id = request.args.get("edit") if request.method == "GET" else None
        budget_form = {"category": "", "limit": ""}

        if request.method == "POST":
            action = request.form.get("action", "")
            if action in {"add_manual", "save_edit"}:
                form_data = {
                    "date": (request.form.get("date") or "").strip(),
                    "description": (request.form.get("description") or "").strip(),
                    "amount": (request.form.get("amount") or "").strip(),
                    "account": (request.form.get("account") or "").strip(),
                    "kind": request.form.get("kind", "expense"),
                }
                tx_type = form_data.get("kind")
                if tx_type not in {"income", "expense"}:
                    tx_type = "expense"

                if not form_data["date"]:
                    errors.append("Date is required.")
                if not form_data["description"]:
                    errors.append("Description is required.")
                if not form_data["amount"]:
                    errors.append("Amount is required.")

                try:
                    date_value = dt.date.fromisoformat(form_data["date"])
                except ValueError:
                    date_value = None
                    errors.append("Date must be in YYYY-MM-DD format.")

                try:
                    amount_value = float(form_data["amount"])
                except ValueError:
                    amount_value = None
                    errors.append("Amount must be a valid number.")

                if amount_value == 0:
                    errors.append("Amount cannot be zero.")

                if amount_value is not None:
                    amount_value = abs(amount_value) if tx_type == "income" else -abs(amount_value)

                if not errors and date_value and amount_value is not None:
                    if action == "add_manual":
                        transactions_entries.append(
                            {
                                "id": uuid.uuid4().hex,
                                "date": date_value.isoformat(),
                                "description": form_data["description"],
                                "amount": amount_value,
                                "account": form_data["account"] or "",
                                "kind": "income" if amount_value >= 0 else "expense",
                                "source": "manual",
                            }
                        )
                        _store_session_transactions(transactions_entries)
                        return _redirect_to_index(filters, inputs)

                    if action == "save_edit":
                        target_id = request.form.get("transaction_id") or ""
                        updated = False
                        for entry in transactions_entries:
                            if entry.get("id") == target_id:
                                entry.update(
                                    {
                                        "date": date_value.isoformat(),
                                        "description": form_data["description"],
                                        "amount": amount_value,
                                        "account": form_data["account"] or "",
                                        "kind": "income" if amount_value >= 0 else "expense",
                                    }
                                )
                                updated = True
                                break
                        if not updated:
                            errors.append("Unable to update the selected transaction.")
                        else:
                            _store_session_transactions(transactions_entries)
                            return _redirect_to_index(filters, inputs)
                else:
                    if action == "save_edit":
                        edit_id = request.form.get("transaction_id") or None

            elif action == "delete_transaction":
                target_id = request.form.get("transaction_id") or ""
                next_entries = [entry for entry in transactions_entries if entry.get("id") != target_id]
                if len(next_entries) == len(transactions_entries):
                    errors.append("Unable to remove the selected transaction.")
                else:
                    _store_session_transactions(next_entries)
                    return _redirect_to_index(filters, inputs)

            elif action == "clear_transactions":
                session.pop(SESSION_TRANSACTIONS, None)
                return _redirect_to_index(filters, inputs)

            elif action == "upload_csv":
                file = request.files.get("csv_file")
                if not file or not file.filename:
                    errors.append("Please choose a CSV file to upload.")
                else:
                    try:
                        text = file.read().decode("utf-8-sig")
                    except UnicodeDecodeError:
                        errors.append("Unable to decode the uploaded file. Ensure it is UTF-8 encoded.")
                    else:
                        stream = io.StringIO(text)
                        try:
                            uploaded_txns = load_csv_stream(stream, label=file.filename)
                        except ValueError as exc:
                            errors.append(str(exc))
                        else:
                            new_entries = []
                            for txn in uploaded_txns:
                                new_entries.append(
                                    {
                                        "id": uuid.uuid4().hex,
                                        "date": txn.date.isoformat(),
                                        "description": txn.description,
                                        "amount": txn.amount,
                                        "account": txn.account or "",
                                        "kind": "income" if txn.amount >= 0 else "expense",
                                        "source": "upload",
                                        "label": file.filename,
                                    }
                                )
                            transactions_entries.extend(new_entries)
                            _store_session_transactions(transactions_entries)
                            return _redirect_to_index(filters, inputs)

            elif action == "add_budget":
                budget_form = {
                    "category": (request.form.get("budget_category") or "").strip(),
                    "limit": (request.form.get("budget_limit") or "").strip(),
                }
                if not budget_form["category"]:
                    errors.append("Budget category is required.")
                try:
                    limit_value = float(budget_form["limit"])
                except ValueError:
                    limit_value = None
                    errors.append("Budget limit must be a valid number.")
                else:
                    if limit_value <= 0:
                        errors.append("Budget limit must be greater than zero.")
                if not errors and limit_value is not None:
                    session_budgets[budget_form["category"]] = limit_value
                    _store_session_budgets(session_budgets)
                    return _redirect_to_index(filters, inputs)

            elif action == "remove_budget":
                category = request.form.get("budget_category") or ""
                if category in session_budgets:
                    session_budgets.pop(category, None)
                    _store_session_budgets(session_budgets)
                    return _redirect_to_index(filters, inputs)
                else:
                    errors.append("Unable to remove the selected budget.")

        transactions_entries = _get_session_transactions()
        session_budgets = _get_session_budgets()

        if request.method == "GET" and edit_id:
            for entry in transactions_entries:
                if entry.get("id") == edit_id:
                    form_data = {
                        "date": entry.get("date", ""),
                        "description": entry.get("description", ""),
                        "amount": str(abs(float(entry.get("amount", 0)))),
                        "account": entry.get("account", ""),
                        "kind": entry.get("kind", "expense"),
                    }
                    form_mode = "edit"
                    break
            else:
                edit_id = None

        if request.method == "POST" and request.form.get("action") == "save_edit" and errors:
            edit_id = request.form.get("transaction_id") or None
            form_mode = "edit"

        session_txns = _entries_to_transactions(transactions_entries)
        resolved_inputs = _resolve_paths(inputs) if inputs else []
        file_txns: List[Transaction] = []
        if resolved_inputs:
            try:
                file_txns = load_csv_files(resolved_inputs)
            except ValueError as exc:
                errors.append(str(exc))
        all_txns: List[Transaction] = []
        all_txns.extend(file_txns)
        all_txns.extend(session_txns)
        if all_txns:
            all_txns.sort(key=lambda t: (t.date, t.description, t.amount))

        resolved_config = _resolve_config_path(config_path)
        cfg = AppConfig.load(resolved_config)
        if all_txns:
            categorize_transactions(all_txns, cfg.rules)

        available_categories = sorted({t.category or "Other" for t in all_txns}) if all_txns else []
        account_values = {t.account or "Unspecified" for t in all_txns}
        if "Unspecified" in account_values:
            account_values = sorted(account_values - {"Unspecified"})
            available_accounts = ["Unspecified"] + account_values
        else:
            available_accounts = sorted(account_values)

        filtered_txns = _apply_filters(all_txns, filters)

        combined_budgets: Dict[str, float] = {b.category: b.monthly_limit for b in cfg.budgets}
        combined_budgets.update(session_budgets)

        summary = build_summary(filtered_txns, combined_budgets or None)
        summary["date_range"] = _format_range_metadata(filtered_txns)
        summary["transaction_count"] = len(filtered_txns)

        session_index = {str(entry.get("id")): entry for entry in transactions_entries}
        transaction_rows = []
        transaction_rows = []
        for txn in filtered_txns:
            entry_id = str(getattr(txn, "id", None))
            entry = session_index.get(entry_id)
            if entry:
                edit_params = _filters_for_redirect(filters, inputs, {"edit": entry_id})
                transaction_rows.append(
                    {
                        "id": entry.get("id"),
                        "date": txn.date.isoformat(),
                        "description": txn.description,
                        "category": txn.category or "Other",
                        "account": entry.get("account") or "",
                        "amount": abs(txn.amount),
                        "signed_amount": txn.amount,
                        "kind": "income" if txn.amount >= 0 else "expense",
                        "source": entry.get("source", "manual"),
                        "label": entry.get("label"),
                        "edit_url": url_for("index", **edit_params),
                    }
                )
        counts = _classify_session_counts(transactions_entries)
        data_source_label = _compose_data_source_label(counts)

        filter_form_values = {
            "category": filters.get("category", "all"),
            "account": filters.get("account", "all"),
            "range": filters.get("range", DEFAULT_RANGE),
            "start": filters.get("start", ""),
            "end": filters.get("end", ""),
        }

        cancel_edit_url = url_for("index", **_filters_for_redirect(filters, inputs))
        range_label = _range_display(filters)
        chart_title = f"Spending by Category ({range_label})"

        budget_rows = []
        budget_status = summary.get("budget_status") or {}
        config_budget_map = {b.category: b.monthly_limit for b in cfg.budgets}
        combined_categories = sorted({*budget_status.keys(), *session_budgets.keys(), *config_budget_map.keys()})
        for cat in combined_categories:
            stats = budget_status.get(cat, {})
            limit = session_budgets.get(cat, config_budget_map.get(cat))
            actual = stats.get("actual", 0.0)
            remaining = stats.get("remaining")
            if remaining is None and limit is not None and actual is not None:
                remaining = round(limit - actual, 2)
            progress = 0
            if limit and limit > 0 and actual is not None:
                progress = min(100, round((actual / limit) * 100, 1))
            over_budget = bool(limit and actual is not None and actual > limit)
            budget_rows.append(
                {
                    "category": cat,
                    "limit": limit,
                    "actual": actual,
                    "remaining": remaining,
                    "progress": progress,
                    "over": over_budget,
                    "editable": cat in session_budgets,
                }
            )

        return render_template(
            "index.html",
            summary=summary,
            inputs=inputs,
            filters=filters,
            filter_form_values=filter_form_values,
            range_options=RANGE_OPTIONS,
            range_label=range_label,
            chart_title=chart_title,
            transaction_rows=transaction_rows,
            available_categories=available_categories,
            available_accounts=available_accounts,
            manual_form=form_data,
            form_mode=form_mode,
            edit_id=edit_id,
            errors=errors,
            data_source_label=data_source_label,
            budget_rows=budget_rows,
            session_budgets=session_budgets,
            budget_form=budget_form,
            cancel_edit_url=cancel_edit_url,
        )

    @app.route("/api/summary")
    def api_summary():
        inputs = request.args.getlist("input") or default_inputs
        filters = _parse_filters(request.args)

        resolved_inputs = _resolve_paths(inputs) if inputs else []
        file_txns: List[Transaction] = []
        if resolved_inputs:
            file_txns = load_csv_files(resolved_inputs)

        session_txns = _entries_to_transactions(_get_session_transactions())
        all_txns: List[Transaction] = []
        all_txns.extend(file_txns)
        all_txns.extend(session_txns)
        if all_txns:
            all_txns.sort(key=lambda t: (t.date, t.description, t.amount))

        resolved_config = _resolve_config_path(config_path)
        cfg = AppConfig.load(resolved_config)
        if all_txns:
            categorize_transactions(all_txns, cfg.rules)

        filtered_txns = _apply_filters(all_txns, filters)

        combined_budgets: Dict[str, float] = {b.category: b.monthly_limit for b in cfg.budgets}
        combined_budgets.update(_get_session_budgets())

        summary = build_summary(filtered_txns, combined_budgets or None)
        summary["date_range"] = _format_range_metadata(filtered_txns)
        summary["transaction_count"] = len(filtered_txns)
        summary["filters"] = filters
        summary["range_label"] = _range_display(filters)
        return jsonify(summary)

    return app


app = create_app()


if __name__ == "__main__":
    app.run(debug=True)
