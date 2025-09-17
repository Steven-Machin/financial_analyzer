# financial_analyzer

Personal Finance Analyzer with CSV ingestion, categorization, reporting, and a web dashboard.

## Getting Started

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

### CLI Usage

```bash
python -m finance_analyzer.cli -i sample_data/sample_transactions.csv
```

### Web Dashboard

```bash
flask --app finance_analyzer.webapp run
```

Navigate to http://127.0.0.1:5000/ to view the dashboard. Use the `?input=path` query string to point at other CSV files. Add `?window=1`, `?window=3`, or `?window=6` to focus on the most recent months.
