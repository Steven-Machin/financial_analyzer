"""Transaction categorization logic.

Keyword-based matcher with simple heuristics. It assigns a category to each
transaction if not already set. Keywords are matched in the normalized
description (lowercase, punctuation stripped except spaces).
"""

from __future__ import annotations

import re
import string
from typing import Dict, List, Optional

from .data_loader import Transaction


_PUNCT_TABLE = str.maketrans({c: " " for c in string.punctuation})


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower().translate(_PUNCT_TABLE)).strip()


def categorize_transactions(
    txns: List[Transaction],
    rules: Dict[str, List[str]],
    default_category: str = "Other",
) -> None:
    """In-place categorization using keyword rules.

    - rules: {"Category": ["keyword", ...]}
    Order of categories matters only insofar as first match wins when a
    keyword appears in multiple categories.
    """

    # Precompute keyword -> category for speed, keep first occurrence
    kw_to_cat: Dict[str, str] = {}
    for cat, kws in rules.items():
        for kw in kws or []:
            kw = kw.strip().lower()
            if kw and kw not in kw_to_cat:
                kw_to_cat[kw] = cat

    # Also compile regexes for certain merchant-like tokens to improve matches
    merchant_tokens = [re.escape(k) for k in kw_to_cat.keys() if len(k) > 2]
    merchant_pattern = re.compile(r"(" + "|".join(merchant_tokens) + r")") if merchant_tokens else None

    for t in txns:
        if t.category:
            continue
        norm = _normalize(t.description)

        chosen: Optional[str] = None
        # Exact keyword containment
        for kw, cat in kw_to_cat.items():
            if kw in norm:
                chosen = cat
                break

        # Regex fallback to catch tokenized descriptions
        if not chosen and merchant_pattern and merchant_pattern.search(norm):
            token = merchant_pattern.search(norm).group(1)
            chosen = kw_to_cat.get(token, None)

        # Income heuristic: positive amounts without explicit match
        if not chosen and t.amount > 0:
            chosen = "Income"

        t.category = chosen or default_category
