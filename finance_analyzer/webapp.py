"""Flask web interface for the Personal Finance Analyzer."""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from flask import Flask, jsonify, render_template, request

from .config import AppConfig
from .data_loader import load_csv_files
from .categorizer import categorize_transactions
from .reports import build_summary

PACKAGE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_ROOT.parent


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


def _load_summary(inputs: List[str], config_path: Optional[str]) -> dict:
    resolved_inputs = _resolve_paths(inputs)
    resolved_config = _resolve_config_path(config_path)
    cfg = AppConfig.load(resolved_config)
    txns = load_csv_files(resolved_inputs)
    categorize_transactions(txns, cfg.rules)
    budget_limits = {b.category: b.monthly_limit for b in cfg.budgets}
    return build_summary(txns, budget_limits or None)


def create_app(default_inputs: Optional[List[str]] = None, config_path: Optional[str] = None) -> Flask:
    app = Flask(
        __name__,
        template_folder=str(PACKAGE_ROOT / "templates"),
        static_folder=str(PACKAGE_ROOT / "static"),
    )

    default_inputs = default_inputs or ["sample_data/sample_transactions.csv"]

    @app.route("/")
    def index() -> str:
        inputs = request.args.getlist("input") or default_inputs
        summary = _load_summary(inputs, config_path)
        return render_template("index.html", summary=summary, inputs=inputs)

    @app.route("/api/summary")
    def api_summary():
        inputs = request.args.getlist("input") or default_inputs
        summary = _load_summary(inputs, config_path)
        return jsonify(summary)

    return app


app = create_app()


if __name__ == "__main__":
    app.run(debug=True)
