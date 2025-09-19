"""Flask web interface for the Personal Finance Analyzer."""

import datetime as dt
import io
import uuid
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

from flask import Flask, jsonify, redirect, render_template, request, session, url_for

from .analytics import summarize_income_expense
from .config import AppConfig
from .data_loader import DEFAULT_ACCOUNT, Transaction, load_csv_files, load_csv_stream
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
SESSION_ACCOUNTS = "pfa_accounts"


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


def _filters_for_redirect(
    filters: Dict[str, str],
    inputs: Sequence[str],
    extra: Optional[Dict[str, Optional[str]]] = None,
) -> Dict[str, str]:
    params: Dict[str, str] = {}
    if inputs:
        params.setdefault("input", list(inputs))
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
        for key, value in extra.items():
            if value in (None, "", "all"):
                params.pop(key, None)
            else:
                params[key] = value
    return params


def _redirect_to_index(
    filters: Dict[str, str],
    inputs: Sequence[str],
    extra: Optional[Dict[str, Optional[str]]] = None,
):
    params = _filters_for_redirect(filters, inputs, extra)
    return redirect(url_for("index", **params))


def _safe_parse_date(value: str) -> Optional[dt.date]:
    if not value:
        return None
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        return None


def _normalize_account_name(value: Optional[str]) -> str:
    if value is None:
        return DEFAULT_ACCOUNT
    name = str(value).strip()
    return name or DEFAULT_ACCOUNT


def _sort_accounts(accounts: Sequence[str]) -> List[str]:
    ordered: List[str] = []
    for name in accounts:
        normalized = _normalize_account_name(name)
        if normalized not in ordered:
            ordered.append(normalized)
    if DEFAULT_ACCOUNT in ordered:
        others = sorted(x for x in ordered if x != DEFAULT_ACCOUNT)
        return [DEFAULT_ACCOUNT, *others]
    return sorted(ordered)


def _get_session_accounts() -> List[str]:
    raw = session.get(SESSION_ACCOUNTS, [])
    accounts: List[str] = []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, str):
                normalized = _normalize_account_name(item)
                if normalized not in accounts:
                    accounts.append(normalized)
    if DEFAULT_ACCOUNT not in accounts:
        accounts.insert(0, DEFAULT_ACCOUNT)
    session[SESSION_ACCOUNTS] = accounts
    return accounts


def _store_session_accounts(accounts: Sequence[str]) -> None:
    cleaned = _sort_accounts(accounts)
    if DEFAULT_ACCOUNT not in cleaned:
        cleaned.insert(0, DEFAULT_ACCOUNT)
    session[SESSION_ACCOUNTS] = cleaned


def _ensure_account(name: Optional[str]) -> str:
    account = _normalize_account_name(name)
    accounts = _get_session_accounts()
    if account not in accounts:
        accounts.append(account)
        _store_session_accounts(accounts)
    return account


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
        filtered = [t for t in filtered if _normalize_account_name(t.account) == account_filter]

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


def _compute_account_balances(txns: Sequence[Transaction], account_order: Sequence[str]) -> Dict[str, Dict[str, float]]:
    balances: Dict[str, Dict[str, float]] = {
        _normalize_account_name(name): {"income": 0.0, "expense": 0.0, "net": 0.0}
        for name in account_order
    }
    for txn in txns:
        account = _normalize_account_name(txn.account)
        stats = balances.setdefault(account, {"income": 0.0, "expense": 0.0, "net": 0.0})
        if txn.amount >= 0:
            stats["income"] += txn.amount
        else:
            stats["expense"] += -txn.amount
        stats["net"] = stats["income"] - stats["expense"]
    for stats in balances.values():
        stats["income"] = round(stats["income"], 2)
        stats["expense"] = round(stats["expense"], 2)
        stats["net"] = round(stats["net"], 2)
    return balances


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

        session_accounts = _get_session_accounts()
        transactions_entries = _get_session_transactions()
        session_budgets = _get_session_budgets()

        manual_form = {
            "date": "",
            "description": "",
            "amount": "",
            "account": session_accounts[0],
            "kind": "expense",
        }
        budget_form = {"category": "", "limit": ""}
        form_mode = "add"
        edit_id = request.args.get("edit") if request.method == "GET" else None
        upload_account_choice = request.form.get("upload_account") if request.method == "POST" else "detect"
        upload_account_choice = upload_account_choice or "detect"

        if request.method == "POST":
            action = request.form.get("action", "")

            if action in {"add_manual", "save_edit"}:
                manual_form = {
                    "date": (request.form.get("date") or "").strip(),
                    "description": (request.form.get("description") or "").strip(),
                    "amount": (request.form.get("amount") or "").strip(),
                    "account": (request.form.get("account") or session_accounts[0]).strip(),
                    "kind": request.form.get("kind", "expense"),
                }
                tx_type = manual_form.get("kind")
                if tx_type not in {"income", "expense"}:
                    tx_type = "expense"

                if not manual_form["date"]:
                    errors.append("Date is required.")
                if not manual_form["description"]:
                    errors.append("Description is required.")
                if not manual_form["amount"]:
                    errors.append("Amount is required.")

                try:
                    date_value = dt.date.fromisoformat(manual_form["date"])
                except ValueError:
                    date_value = None
                    errors.append("Date must be in YYYY-MM-DD format.")

                try:
                    amount_value = float(manual_form["amount"])
                except ValueError:
                    amount_value = None
                    errors.append("Amount must be a valid number.")

                if amount_value == 0:
                    errors.append("Amount cannot be zero.")

                if amount_value is not None:
                    amount_value = abs(amount_value) if tx_type == "income" else -abs(amount_value)

                account_value = _ensure_account(manual_form["account"])
                manual_form["account"] = account_value

                if not errors and date_value and amount_value is not None:
                    if action == "add_manual":
                        transactions_entries.append(
                            {
                                "id": uuid.uuid4().hex,
                                "date": date_value.isoformat(),
                                "description": manual_form["description"],
                                "amount": amount_value,
                                "account": account_value,
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
                                        "description": manual_form["description"],
                                        "amount": amount_value,
                                        "account": account_value,
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
                        form_mode = "edit"

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
                    choice = upload_account_choice or "detect"
                    chosen_account = None if choice == "detect" else _ensure_account(choice)
                    try:
                        text_stream = file.read().decode("utf-8-sig")
                    except UnicodeDecodeError:
                        errors.append("Unable to decode the uploaded file. Ensure it is UTF-8 encoded.")
                    else:
                        stream = io.StringIO(text_stream)
                        try:
                            uploaded_txns = load_csv_stream(
                                stream,
                                label=file.filename,
                                default_account=chosen_account or DEFAULT_ACCOUNT,
                            )
                        except ValueError as exc:
                            errors.append(str(exc))
                        else:
                            new_entries = []
                            for txn in uploaded_txns:
                                account_value = txn.account or chosen_account or DEFAULT_ACCOUNT
                                account_value = _ensure_account(account_value)
                                new_entries.append(
                                    {
                                        "id": uuid.uuid4().hex,
                                        "date": txn.date.isoformat(),
                                        "description": txn.description,
                                        "amount": txn.amount,
                                        "account": account_value,
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

            elif action == "add_account":
                account_name = (request.form.get("account_name") or "").strip()
                if not account_name:
                    errors.append("Account name is required.")
                else:
                    normalized = _normalize_account_name(account_name)
                    accounts = _get_session_accounts()
                    if normalized in accounts:
                        errors.append("That account already exists.")
                    else:
                        accounts.append(normalized)
                        _store_session_accounts(accounts)
                        manual_form["account"] = normalized
                        return _redirect_to_index(filters, inputs)

            elif action == "delete_account":
                target_account = (request.form.get("account_name") or "").strip()
                normalized = _normalize_account_name(target_account)
                accounts = _get_session_accounts()
                if normalized == DEFAULT_ACCOUNT:
                    errors.append("Cannot delete the default account.")
                elif normalized not in accounts:
                    errors.append("Unable to find the selected account.")
                else:
                    in_use_session = any(
                        _normalize_account_name(entry.get("account")) == normalized for entry in transactions_entries
                    )
                    in_use_files = False
                    if inputs and not in_use_session:
                        try:
                            preview_txns = load_csv_files(_resolve_paths(inputs), default_account=DEFAULT_ACCOUNT)
                        except ValueError:
                            preview_txns = []
                        in_use_files = any(_normalize_account_name(txn.account) == normalized for txn in preview_txns)
                    if in_use_session or in_use_files:
                        errors.append("Cannot delete an account that has transactions.")
                    elif len(accounts) <= 1:
                        errors.append("At least one account must remain.")
                    else:
                        remaining = [acc for acc in accounts if acc != normalized]
                        _store_session_accounts(remaining)
                        if filters.get("account") == normalized:
                            filters["account"] = "all"
                        if manual_form.get("account") == normalized:
                            manual_form["account"] = remaining[0]
                        return _redirect_to_index(filters, inputs)

        transactions_entries = _get_session_transactions()
        session_accounts = _get_session_accounts()
        session_budgets = _get_session_budgets()

        if request.method == "GET" and edit_id:
            for entry in transactions_entries:
                if entry.get("id") == edit_id:
                    manual_form = {
                        "date": entry.get("date", ""),
                        "description": entry.get("description", ""),
                        "amount": str(abs(float(entry.get("amount", 0)))),
                        "account": entry.get("account", session_accounts[0]),
                        "kind": entry.get("kind", "expense"),
                    }
                    form_mode = "edit"
                    break
            else:
                edit_id = None

        if manual_form.get("account") not in session_accounts:
            manual_form["account"] = session_accounts[0]

        session_txns = _entries_to_transactions(transactions_entries)
        resolved_inputs = _resolve_paths(inputs) if inputs else []
        file_txns: List[Transaction] = []
        if resolved_inputs:
            try:
                file_txns = load_csv_files(resolved_inputs, default_account=DEFAULT_ACCOUNT)
            except ValueError as exc:
                errors.append(str(exc))
        all_txns: List[Transaction] = []
        all_txns.extend(file_txns)
        all_txns.extend(session_txns)
        if all_txns:
            all_txns.sort(key=lambda t: (t.date, t.description, t.amount))

        for txn in all_txns:
            _ensure_account(txn.account)

        session_accounts = _get_session_accounts()

        available_categories = sorted({t.category or "Other" for t in all_txns}) if all_txns else []
        available_accounts = _sort_accounts(list(session_accounts) + [_normalize_account_name(t.account) for t in all_txns])
        if filters.get("account") not in {"all", ""} | set(available_accounts):
            filters["account"] = "all"

        resolved_config = _resolve_config_path(config_path)
        cfg = AppConfig.load(resolved_config)
        if all_txns:
            categorize_transactions(all_txns, cfg.rules)

        filtered_txns = _apply_filters(all_txns, filters)

        combined_budgets: Dict[str, float] = {b.category: b.monthly_limit for b in cfg.budgets}
        combined_budgets.update(session_budgets)

        summary = build_summary(filtered_txns, combined_budgets or None)
        summary["date_range"] = _format_range_metadata(filtered_txns)
        summary["transaction_count"] = len(filtered_txns)

        session_index = {str(entry.get("id")): entry for entry in transactions_entries}
        transaction_rows: List[Dict[str, object]] = []
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

        cancel_edit_url = url_for("index", **_filters_for_redirect(filters, inputs, {"edit": None}))
        range_label = RANGE_LABELS.get(filters.get("range", DEFAULT_RANGE), RANGE_LABELS[DEFAULT_RANGE])
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

        account_order = list(session_accounts)
        for txn in all_txns:
            account_name = _normalize_account_name(txn.account)
            if account_name not in account_order:
                account_order.append(account_name)
        account_order = _sort_accounts(account_order)
        _store_session_accounts(account_order)

        account_balances_all = _compute_account_balances(all_txns, account_order)
        account_balances_view = _compute_account_balances(filtered_txns, account_order)
        selected_account = filters.get("account", "all")
        account_rows = []
        for account in account_order:
            overall = account_balances_all.get(account, {"income": 0.0, "expense": 0.0, "net": 0.0})
            view_stats = account_balances_view.get(account)
            account_rows.append(
                {
                    "name": account,
                    "all_income": overall["income"],
                    "all_expense": overall["expense"],
                    "all_net": overall["net"],
                    "view_income": view_stats["income"] if view_stats else None,
                    "view_expense": view_stats["expense"] if view_stats else None,
                    "view_net": view_stats["net"] if view_stats else None,
                    "is_selected": selected_account == account,
                }
            )

        overall_totals_all = summarize_income_expense(all_txns) if all_txns else {"income": 0.0, "expense": 0.0, "net": 0.0}

        account_usage: Dict[str, int] = {acc: 0 for acc in account_order}
        for txn in all_txns:
            account_name = _normalize_account_name(txn.account)
            account_usage[account_name] = account_usage.get(account_name, 0) + 1
        for entry in transactions_entries:
            account_name = _normalize_account_name(entry.get("account"))
            account_usage[account_name] = account_usage.get(account_name, 0) + 1
        account_management = []
        for account in account_order:
            in_use = account_usage.get(account, 0) > 0
            account_management.append(
                {
                    "name": account,
                    "in_use": in_use,
                    "can_delete": account != DEFAULT_ACCOUNT and not in_use,
                }
            )

        return render_template(
            "index.html",
            summary=summary,
            overall_totals_all=overall_totals_all,
            inputs=inputs,
            filters=filters,
            filter_form_values=filter_form_values,
            range_options=RANGE_OPTIONS,
            range_label=range_label,
            chart_title=chart_title,
            transaction_rows=transaction_rows,
            available_categories=available_categories,
            available_accounts=available_accounts,
            accounts_list=session_accounts,
            account_rows=account_rows,
            account_management=account_management,
            manual_form=manual_form,
            form_mode=form_mode,
            edit_id=edit_id,
            errors=errors,
            data_source_label=data_source_label,
            budget_rows=budget_rows,
            session_budgets=session_budgets,
            budget_form=budget_form,
            cancel_edit_url=cancel_edit_url,
            upload_account_choice=upload_account_choice,
            selected_account=selected_account,
        )

    @app.route("/api/summary")
    def api_summary():
        inputs = request.args.getlist("input") or default_inputs
        filters = _parse_filters(request.args)

        resolved_inputs = _resolve_paths(inputs) if inputs else []
        file_txns = load_csv_files(resolved_inputs, default_account=DEFAULT_ACCOUNT) if resolved_inputs else []
        session_txns = _entries_to_transactions(_get_session_transactions())
        all_txns: List[Transaction] = []
        all_txns.extend(file_txns)
        all_txns.extend(session_txns)
        if all_txns:
            all_txns.sort(key=lambda t: (t.date, t.description, t.amount))

        for txn in all_txns:
            _ensure_account(txn.account)

        session_accounts = _get_session_accounts()
        available_accounts = _sort_accounts(list(session_accounts) + [_normalize_account_name(t.account) for t in all_txns])
        if filters.get("account") not in {"all", ""} | set(available_accounts):
            filters["account"] = "all"

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
        summary["range_label"] = RANGE_LABELS.get(filters.get("range", DEFAULT_RANGE), RANGE_LABELS[DEFAULT_RANGE])

        account_balances_all = _compute_account_balances(all_txns, available_accounts)
        account_balances_view = _compute_account_balances(filtered_txns, available_accounts)
        summary["accounts_all"] = account_balances_all
        summary["accounts_view"] = account_balances_view
        summary["available_accounts"] = available_accounts

        return jsonify(summary)

    return app


app = create_app()


if __name__ == "__main__":
    app.run(debug=True)
