"""Flask web interface for the Personal Finance Analyzer."""

from __future__ import annotations

import datetime as dt
import io
import sqlite3
from functools import wraps
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from flask import (
    Flask,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

from .analytics import summarize_income_expense
from .config import AppConfig
from .categorizer import categorize_transactions
from .data_loader import DEFAULT_ACCOUNT, Transaction, load_csv_files, load_csv_stream
from .db import close_db, get_db, init_db
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

def _filter_recent_transactions(txns: Sequence[Transaction], months: Optional[int]):
    if not txns or not months or months <= 0:
        return list(txns)
    latest = max(t.date for t in txns)
    start_boundary = _subtract_months(_month_floor(latest), months - 1)
    return [t for t in txns if t.date >= start_boundary]


def _month_floor(value: dt.date) -> dt.date:
    return dt.date(value.year, value.month, 1)


def _subtract_months(value: dt.date, months: int) -> dt.date:
    year = value.year
    month = value.month - months
    while month <= 0:
        month += 12
        year -= 1
    return dt.date(year, month, 1)


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
    extra: Optional[Dict[str, Optional[str]]] = None,
) -> Dict[str, str]:
    params: Dict[str, str] = {}
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


def _redirect_to_index(filters: Dict[str, str], extra: Optional[Dict[str, Optional[str]]] = None):
    params = _filters_for_redirect(filters, extra)
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
        filtered = [t for t in filtered if (t.account or DEFAULT_ACCOUNT) == account_filter]
    return filtered


def _compose_data_source_label(counts: Dict[str, int]) -> str:
    parts: List[str] = []
    if counts.get("manual"):
        parts.append(f"{counts['manual']} manual entr{'y' if counts['manual'] == 1 else 'ies'}")
    if counts.get("upload"):
        parts.append(f"{counts['upload']} imported entr{'y' if counts['upload'] == 1 else 'ies'}")
    return " + ".join(parts) if parts else "No data yet"


def _compute_account_balances(
    txns: Sequence[Transaction],
    account_order: Sequence[str],
) -> Dict[str, Dict[str, float]]:
    balances: Dict[str, Dict[str, float]] = {
        name: {"income": 0.0, "expense": 0.0, "net": 0.0} for name in account_order
    }
    for txn in txns:
        account = txn.account or DEFAULT_ACCOUNT
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


def login_required(view):
    @wraps(view)
    def wrapped_view(**kwargs):
        if g.user is None:
            return redirect(url_for("login"))
        return view(**kwargs)

    return wrapped_view


def _load_logged_in_user() -> None:
    user_id = session.get("user_id")
    if user_id is None:
        g.user = None
        return
    db = get_db()
    g.user = db.execute(
        "SELECT id, username, email FROM users WHERE id = ?",
        (user_id,),
    ).fetchone()


def _ensure_account(user_id: int, name: str) -> sqlite3.Row:
    account_name = (name or DEFAULT_ACCOUNT).strip() or DEFAULT_ACCOUNT
    db = get_db()
    row = db.execute(
        "SELECT id, name FROM accounts WHERE user_id = ? AND name = ?",
        (user_id, account_name),
    ).fetchone()
    if row:
        return row
    db.execute(
        "INSERT INTO accounts (user_id, name) VALUES (?, ?)",
        (user_id, account_name),
    )
    db.commit()
    return db.execute(
        "SELECT id, name FROM accounts WHERE user_id = ? AND name = ?",
        (user_id, account_name),
    ).fetchone()


def _fetch_accounts(user_id: int) -> List[sqlite3.Row]:
    db = get_db()
    rows = db.execute(
        "SELECT id, name FROM accounts WHERE user_id = ? ORDER BY name",
        (user_id,),
    ).fetchall()
    if not rows:
        _ensure_account(user_id, DEFAULT_ACCOUNT)
        rows = db.execute(
            "SELECT id, name FROM accounts WHERE user_id = ? ORDER BY name",
            (user_id,),
        ).fetchall()
    return rows


def _fetch_transactions(user_id: int) -> List[sqlite3.Row]:
    db = get_db()
    return db.execute(
        """
        SELECT t.id, t.date, t.description, t.amount, t.source, t.label, a.name AS account, t.account_id
        FROM transactions t
        JOIN accounts a ON t.account_id = a.id
        WHERE t.user_id = ?
        ORDER BY t.date, t.id
        """,
        (user_id,),
    ).fetchall()


def _fetch_transaction(user_id: int, txn_id: int) -> Optional[sqlite3.Row]:
    db = get_db()
    return db.execute(
        """
        SELECT t.id, t.date, t.description, t.amount, t.source, t.label, a.name AS account, t.account_id
        FROM transactions t
        JOIN accounts a ON t.account_id = a.id
        WHERE t.user_id = ? AND t.id = ?
        """,
        (user_id, txn_id),
    ).fetchone()


def _fetch_budgets(user_id: int) -> List[sqlite3.Row]:
    db = get_db()
    return db.execute(
        "SELECT category, monthly_limit FROM budgets WHERE user_id = ? ORDER BY category",
        (user_id,),
    ).fetchall()


def _count_sources(rows: Sequence[sqlite3.Row]) -> Dict[str, int]:
    counts = {"manual": 0, "upload": 0}
    for row in rows:
        source = (row["source"] or "manual").lower()
        if source not in counts:
            counts[source] = 0
        counts[source] += 1
    return counts

def create_app(default_inputs: Optional[List[str]] = None, config_path: Optional[str] = None) -> Flask:
    app = Flask(
        __name__,
        template_folder=str(PACKAGE_ROOT / "templates"),
        static_folder=str(PACKAGE_ROOT / "static"),
    )
    app.config["SECRET_KEY"] = "fl99032"
    app.config.setdefault("DATABASE", str(PROJECT_ROOT / "finance_analyzer.db"))

    default_inputs = default_inputs or []

    app.teardown_appcontext(close_db)
    app.before_request(_load_logged_in_user)
    with app.app_context():
        init_db()

    @app.route("/signup", methods=["GET", "POST"])
    def signup():
        if g.user is not None:
            return redirect(url_for("index"))
        errors: List[str] = []
        form = {"username": "", "email": ""}
        if request.method == "POST":
            username = (request.form.get("username") or "").strip()
            email = (request.form.get("email") or "").strip()
            password = request.form.get("password") or ""
            form["username"] = username
            form["email"] = email
            if not username:
                errors.append("Username is required.")
            if not email:
                errors.append("Email is required.")
            if not password:
                errors.append("Password is required.")
            if not errors:
                db = get_db()
                try:
                    cursor = db.execute(
                        "INSERT INTO users (username, email, password_hash) VALUES (?, ?, ?)",
                        (username, email, generate_password_hash(password)),
                    )
                    user_id = cursor.lastrowid
                    _ensure_account(user_id, DEFAULT_ACCOUNT)
                    db.commit()
                except sqlite3.IntegrityError:
                    errors.append("Username or email already exists.")
                else:
                    session.clear()
                    session["user_id"] = user_id
                    return redirect(url_for("index"))
        return render_template("auth.html", mode="signup", errors=errors, form=form)

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if g.user is not None:
            return redirect(url_for("index"))
        errors: List[str] = []
        form = {"username": ""}
        if request.method == "POST":
            username = (request.form.get("username") or "").strip()
            password = request.form.get("password") or ""
            form["username"] = username
            db = get_db()
            user = db.execute(
                "SELECT * FROM users WHERE username = ? OR email = ?",
                (username, username),
            ).fetchone()
            if user is None or not check_password_hash(user["password_hash"], password):
                errors.append("Invalid credentials.")
            else:
                session.clear()
                session["user_id"] = user["id"]
                return redirect(url_for("index"))
        return render_template("auth.html", mode="login", errors=errors, form=form)

    @app.route("/logout", methods=["POST"])
    @login_required
    def logout():
        session.clear()
        return redirect(url_for("login"))
    @app.route("/", methods=["GET", "POST"])
    @login_required
    def index():
        user_id = g.user["id"]
        errors: List[str] = []
        filters = _parse_filters(request.form if request.method == "POST" else request.args)
        accounts = _fetch_accounts(user_id)
        account_names = [row["name"] for row in accounts]
        if DEFAULT_ACCOUNT not in account_names:
            accounts = _fetch_accounts(user_id)
            account_names = [row["name"] for row in accounts]
        manual_form = {
            "date": "",
            "description": "",
            "amount": "",
            "account": account_names[0],
            "kind": "expense",
        }
        budget_form = {"category": "", "limit": ""}
        form_mode = "add"
        edit_id = request.args.get("edit") if request.method == "GET" else None
        upload_account_choice = request.form.get("upload_account") if request.method == "POST" else "detect"
        upload_account_choice = upload_account_choice or "detect"

        if request.method == "POST":
            action = request.form.get("action", "")
            db = get_db()
            if action in {"add_manual", "save_edit"}:
                manual_form = {
                    "date": (request.form.get("date") or "").strip(),
                    "description": (request.form.get("description") or "").strip(),
                    "amount": (request.form.get("amount") or "").strip(),
                    "account": (request.form.get("account") or account_names[0]).strip(),
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
                account_row = _ensure_account(user_id, manual_form["account"])
                manual_form["account"] = account_row["name"]
                if not errors and date_value and amount_value is not None:
                    if action == "add_manual":
                        db.execute(
                            """
                            INSERT INTO transactions (user_id, account_id, date, description, amount, source)
                            VALUES (?, ?, ?, ?, ?, ?)
                            """,
                            (
                                user_id,
                                account_row["id"],
                                date_value.isoformat(),
                                manual_form["description"],
                                amount_value,
                                "manual",
                            ),
                        )
                        db.commit()
                        return _redirect_to_index(filters)
                    if action == "save_edit":
                        target_id = request.form.get("transaction_id") or ""
                        if not target_id.isdigit():
                            errors.append("Unable to update the selected transaction.")
                        else:
                            existing = _fetch_transaction(user_id, int(target_id))
                            if not existing:
                                errors.append("Unable to update the selected transaction.")
                            else:
                                db.execute(
                                    """
                                    UPDATE transactions
                                    SET account_id = ?, date = ?, description = ?, amount = ?, source = 'manual'
                                    WHERE id = ? AND user_id = ?
                                    """,
                                    (
                                        account_row["id"],
                                        date_value.isoformat(),
                                        manual_form["description"],
                                        amount_value,
                                        int(target_id),
                                        user_id,
                                    ),
                                )
                                db.commit()
                                return _redirect_to_index(filters)
                else:
                    form_mode = "edit" if action == "save_edit" else "add"
                    edit_id = request.form.get("transaction_id")
            elif action == "delete_transaction":
                target_id = request.form.get("transaction_id") or ""
                if target_id.isdigit():
                    db.execute(
                        "DELETE FROM transactions WHERE id = ? AND user_id = ?",
                        (int(target_id), user_id),
                    )
                    db.commit()
                else:
                    errors.append("Unable to remove the selected transaction.")
                return _redirect_to_index(filters)
            elif action == "clear_transactions":
                db.execute("DELETE FROM transactions WHERE user_id = ?", (user_id,))
                db.commit()
                return _redirect_to_index(filters)
            elif action == "upload_csv":
                file = request.files.get("csv_file")
                if not file or not file.filename:
                    errors.append("Please choose a CSV file to upload.")
                else:
                    choice = upload_account_choice or "detect"
                    chosen_account = None if choice == "detect" else _ensure_account(user_id, choice)["name"]
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
                            for txn in uploaded_txns:
                                account_name = txn.account or chosen_account or DEFAULT_ACCOUNT
                                account_row = _ensure_account(user_id, account_name)
                                db.execute(
                                    """
                                    INSERT INTO transactions (user_id, account_id, date, description, amount, source, label)
                                    VALUES (?, ?, ?, ?, ?, ?, ?)
                                    """,
                                    (
                                        user_id,
                                        account_row["id"],
                                        txn.date.isoformat(),
                                        txn.description,
                                        txn.amount,
                                        "upload",
                                        file.filename,
                                    ),
                                )
                            db.commit()
                            return _redirect_to_index(filters)
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
                    db.execute(
                        """
                        INSERT INTO budgets (user_id, category, monthly_limit)
                        VALUES (?, ?, ?)
                        ON CONFLICT(user_id, category) DO UPDATE
                        SET monthly_limit = excluded.monthly_limit
                        """,
                        (user_id, budget_form["category"], limit_value),
                    )
                    db.commit()
                    return _redirect_to_index(filters)
            elif action == "remove_budget":
                category = (request.form.get("budget_category") or "").strip()
                if category:
                    db.execute(
                        "DELETE FROM budgets WHERE user_id = ? AND category = ?",
                        (user_id, category),
                    )
                    db.commit()
                    return _redirect_to_index(filters)
            elif action == "add_account":
                account_name = (request.form.get("account_name") or "").strip()
                if not account_name:
                    errors.append("Account name is required.")
                else:
                    try:
                        _ensure_account(user_id, account_name)
                        manual_form["account"] = account_name
                        db.commit()
                        return _redirect_to_index(filters)
                    except sqlite3.IntegrityError:
                        errors.append("That account already exists.")
            elif action == "delete_account":
                account_name = (request.form.get("account_name") or "").strip()
                account_row = _ensure_account(user_id, account_name)
                account_id = account_row["id"]
                if account_row["name"] == DEFAULT_ACCOUNT:
                    errors.append("Cannot delete the default account.")
                else:
                    usage = db.execute(
                        "SELECT COUNT(*) AS count FROM transactions WHERE user_id = ? AND account_id = ?",
                        (user_id, account_id),
                    ).fetchone()["count"]
                    if usage:
                        errors.append("Cannot delete an account that has transactions.")
                    else:
                        db.execute(
                            "DELETE FROM accounts WHERE id = ? AND user_id = ?",
                            (account_id, user_id),
                        )
                        db.commit()
                        return _redirect_to_index(filters, {"account": "all"})
        transactions_rows = _fetch_transactions(user_id)
        transactions = [
            Transaction(
                date=dt.date.fromisoformat(row["date"]),
                description=row["description"],
                amount=row["amount"],
                account=row["account"],
                id=str(row["id"]),
            )
            for row in transactions_rows
        ]

        cfg = AppConfig.load(_resolve_config_path(config_path))
        if transactions:
            categorize_transactions(transactions, cfg.rules)

        available_categories = sorted({t.category or "Other" for t in transactions}) if transactions else []
        available_accounts = sorted(account_names)
        if filters.get("account") not in {"all", ""} | set(available_accounts):
            filters["account"] = "all"

        filtered_txns = _apply_filters(transactions, filters)

        budgets_rows = _fetch_budgets(user_id)
        user_budgets = {row["category"]: row["monthly_limit"] for row in budgets_rows}
        combined_budgets = {b.category: b.monthly_limit for b in cfg.budgets}
        combined_budgets.update(user_budgets)

        summary = build_summary(filtered_txns, combined_budgets or None)
        summary["date_range"] = _format_range_metadata(filtered_txns)
        summary["transaction_count"] = len(filtered_txns)

        transaction_meta = {row["id"]: row for row in transactions_rows}
        transaction_rows = []
        for txn in filtered_txns:
            row = transaction_meta.get(int(txn.id or 0))
            edit_params = _filters_for_redirect(filters, {"edit": txn.id})
            transaction_rows.append(
                {
                    "id": txn.id,
                    "date": txn.date.isoformat(),
                    "description": txn.description,
                    "category": txn.category or "Other",
                    "account": txn.account or DEFAULT_ACCOUNT,
                    "amount": abs(txn.amount),
                    "signed_amount": txn.amount,
                    "kind": "income" if txn.amount >= 0 else "expense",
                    "source": (row["source"] if row else "manual") or "manual",
                    "label": row["label"] if row else None,
                    "edit_url": url_for("index", **edit_params),
                }
            )

        counts = _count_sources(transactions_rows)
        data_source_label = _compose_data_source_label(counts)

        filter_form_values = {
            "category": filters.get("category", "all"),
            "account": filters.get("account", "all"),
            "range": filters.get("range", DEFAULT_RANGE),
            "start": filters.get("start", ""),
            "end": filters.get("end", ""),
        }

        cancel_edit_url = url_for("index", **_filters_for_redirect(filters, {"edit": None}))
        range_label = RANGE_LABELS.get(filters.get("range", DEFAULT_RANGE), RANGE_LABELS[DEFAULT_RANGE])
        chart_title = f"Spending by Category ({range_label})"

        budget_status = summary.get("budget_status") or {}
        budget_rows = []
        combined_categories = sorted({*budget_status.keys(), *combined_budgets.keys()})
        for cat in combined_categories:
            stats = budget_status.get(cat, {})
            limit = combined_budgets.get(cat)
            actual = stats.get("actual", 0.0)
            remaining = stats.get("remaining")
            if limit is not None and remaining is None and actual is not None:
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
                    "editable": cat in user_budgets,
                }
            )

        account_order = available_accounts or [DEFAULT_ACCOUNT]
        account_balances_all = _compute_account_balances(transactions, account_order)
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

        overall_totals_all = summarize_income_expense(transactions) if transactions else {"income": 0.0, "expense": 0.0, "net": 0.0}

        db = get_db()
        account_usage = {
            row["name"]: db.execute(
                "SELECT COUNT(*) AS count FROM transactions WHERE user_id = ? AND account_id = ?",
                (user_id, row["id"]),
            ).fetchone()["count"]
            for row in accounts
        }
        account_management = [
            {
                "name": row["name"],
                "in_use": bool(account_usage.get(row["name"], 0)),
                "can_delete": row["name"] != DEFAULT_ACCOUNT and not account_usage.get(row["name"], 0),
            }
            for row in accounts
        ]

        if request.method == "GET" and request.args.get("edit"):
            edit_arg = request.args["edit"]
            if edit_arg.isdigit():
                edit_row = _fetch_transaction(user_id, int(edit_arg))
            else:
                edit_row = None
            if edit_row:
                manual_form = {
                    "date": edit_row["date"],
                    "description": edit_row["description"],
                    "amount": str(abs(float(edit_row["amount"]))),
                    "account": edit_row["account"],
                    "kind": "income" if edit_row["amount"] >= 0 else "expense",
                }
                form_mode = "edit"
                edit_id = str(edit_row["id"])

        if manual_form.get("account") not in account_names:
            manual_form["account"] = account_names[0]

        return render_template(
            "index.html",
            user=dict(g.user),
            summary=summary,
            overall_totals_all=overall_totals_all,
            inputs=[],
            filters=filters,
            filter_form_values=filter_form_values,
            range_options=RANGE_OPTIONS,
            range_label=range_label,
            chart_title=chart_title,
            transaction_rows=transaction_rows,
            available_categories=available_categories,
            available_accounts=available_accounts,
            accounts_list=account_names,
            account_rows=account_rows,
            account_management=account_management,
            manual_form=manual_form,
            form_mode=form_mode,
            edit_id=edit_id,
            errors=errors,
            data_source_label=data_source_label,
            budget_rows=budget_rows,
            budget_form=budget_form,
            cancel_edit_url=cancel_edit_url,
            upload_account_choice=upload_account_choice,
            selected_account=selected_account,
        )
    @app.route("/api/summary")
    @login_required
    def api_summary():
        user_id = g.user["id"]
        filters = _parse_filters(request.args)
        transactions_rows = _fetch_transactions(user_id)
        transactions = [
            Transaction(
                date=dt.date.fromisoformat(row["date"]),
                description=row["description"],
                amount=row["amount"],
                account=row["account"],
                id=str(row["id"]),
            )
            for row in transactions_rows
        ]
        cfg = AppConfig.load(_resolve_config_path(config_path))
        if transactions:
            categorize_transactions(transactions, cfg.rules)
        filtered_txns = _apply_filters(transactions, filters)
        budgets_rows = _fetch_budgets(user_id)
        user_budgets = {row["category"]: row["monthly_limit"] for row in budgets_rows}
        combined_budgets = {b.category: b.monthly_limit for b in cfg.budgets}
        combined_budgets.update(user_budgets)
        summary = build_summary(filtered_txns, combined_budgets or None)
        summary["date_range"] = _format_range_metadata(filtered_txns)
        summary["transaction_count"] = len(filtered_txns)
        summary["filters"] = filters
        summary["range_label"] = RANGE_LABELS.get(filters.get("range", DEFAULT_RANGE), RANGE_LABELS[DEFAULT_RANGE])
        account_names = [row["account"] for row in transactions_rows]
        account_order = sorted(set(account_names)) or [DEFAULT_ACCOUNT]
        summary["accounts_all"] = _compute_account_balances(transactions, account_order)
        summary["accounts_view"] = _compute_account_balances(filtered_txns, account_order)
        summary["available_accounts"] = account_order
        return jsonify(summary)

    return app


def _resolve_config_path(config_path: Optional[str]) -> Optional[Path]:
    if not config_path:
        return None
    path = Path(config_path)
    if path.is_absolute():
        return path
    return PROJECT_ROOT / path


app = create_app()


if __name__ == "__main__":
    app.run(debug=True)
