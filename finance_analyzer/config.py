"""Configuration utilities for the Personal Finance Analyzer.

Provides default categorization rules and helpers to load user-defined
configuration (e.g., custom keyword rules, budgets) from JSON files.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional


# Default keyword-based categorization rules.
# Keys: Category names. Values: list of lowercase keywords to search in description.
DEFAULT_RULES: Dict[str, List[str]] = {
    "Income": ["payroll", "direct deposit", "salary", "stripe payout", "refund"],
    "Rent": ["apartment", "rent", "landlord"],
    "Groceries": ["whole foods", "trader joe", "kroger", "walmart grocery", "aldi", "heb"],
    "Dining": ["starbucks", "mcdonald", "ubereats", "doordash", "grubhub", "restaurant", "bar"],
    "Transport": ["uber", "lyft", "shell", "exxon", "chevron", "gas", "metro", "transit"],
    "Utilities": ["comcast", "xfinity", "att", "verizon", "electric", "water", "gas co"],
    "Subscriptions": ["netflix", "spotify", "icloud", "google storage", "prime", "hulu"],
    "Shopping": ["amazon", "target", "walmart", "best buy", "ebay"],
    "Health": ["pharmacy", "cvS", "walgreens", "doctor", "dentist", "copay"],
    "Entertainment": ["movie", "theater", "concert", "ticketmaster"],
    "Travel": ["airbnb", "hotel", "delta", "united", "aa", "southwest", "booking"],
    "Savings": ["transfer to savings", "ally", "capital one 360"],
    "Fees": ["fee", "interest charge", "atm fee"],
    "Other": [],
}


@dataclass
class Budget:
    category: str
    monthly_limit: float


@dataclass
class AppConfig:
    rules: Dict[str, List[str]]
    budgets: List[Budget]

    @staticmethod
    def load(config_path: Optional[str | Path] = None) -> "AppConfig":
        """Load config from JSON if provided, else use defaults.

        JSON format:
        {
          "rules": {"Category": ["keyword1", "keyword2"]},
          "budgets": [{"category": "Groceries", "monthly_limit": 400}]
        }
        """

        rules = DEFAULT_RULES
        budgets: List[Budget] = []

        if config_path:
            p = Path(config_path)
            if p.exists():
                with p.open("r", encoding="utf-8") as f:
                    raw = json.load(f)
                if isinstance(raw, dict):
                    if isinstance(raw.get("rules"), dict):
                        # Normalize all keywords to lowercase
                        rules = {
                            str(cat): [str(k).lower() for k in (kw or [])]
                            for cat, kw in raw.get("rules", {}).items()
                        }
                    if isinstance(raw.get("budgets"), list):
                        budgets = [
                            Budget(category=str(b["category"]), monthly_limit=float(b["monthly_limit"]))
                            for b in raw["budgets"]
                            if "category" in b and "monthly_limit" in b
                        ]
        return AppConfig(rules=rules, budgets=budgets)
