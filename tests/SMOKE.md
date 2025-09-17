Quick smoke test
================

From the project root, run:

1) Text report only

   `python -m finance_analyzer.cli -i sample_data/sample_transactions.csv`

2) With JSON output

   `python -m finance_analyzer.cli -i sample_data/sample_transactions.csv --json summary.json`

3) With custom config (optional)

   Create `my_config.json`, e.g.:

   {
     "rules": {"Pets": ["petco", "chewy"]},
     "budgets": [{"category": "Groceries", "monthly_limit": 400}]
   }

   Then run:

   `python -m finance_analyzer.cli -i sample_data/sample_transactions.csv -c my_config.json`

4) Web dashboard

   Install requirements (`pip install -r requirements.txt`), then launch:

   `flask --app finance_analyzer.webapp run`

   Visit http://127.0.0.1:5000/ to view the dashboard. Append `?window=1` or `?window=6` to adjust the time window.
