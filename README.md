# Personal Finance Analyzer

Personal Finance Analyzer is a lightweight full-stack tool for inspecting personal banking data. Import CSV exports from your financial institutions, categorize transactions, track budgets, and explore trends through an interactive web dashboard or CLI summaries.

## Key Capabilities

- Ingest transaction CSV files and normalize them for analysis.
- Auto-categorize income and expense activity with configurable rules.
- Review budgets, category spend, top merchants, and recurring payments.
- Launch a responsive dashboard to filter by date range, account, or category.
- Export the current dashboard summary to CSV or JSON for sharing or backups.
- Run the CLI to generate quick summaries directly in the terminal.

## Prerequisites

- Python 3.11+
- Recommended: a virtual environment to isolate dependencies

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## Command-Line Usage

Process one or more CSV files and print a summary report:

```bash
python -m finance_analyzer.cli -i sample_data/sample_transactions.csv
```

Add additional `-i path/to/other.csv` arguments as needed.

## Web Dashboard

Start the Flask app and explore your data in the browser:

```bash
flask --app finance_analyzer.webapp run
```

Then visit http://127.0.0.1:5000/.

From the dashboard you can:

- Upload additional CSV exports for the current session.
- Filter the view by account, category, or predefined/custom date ranges.
- Add manual transactions and track category budgets.
- Download the visible summary via the **Export CSV** and **Export JSON** buttons.

To preload transactions from another file at startup, append `?input=path/to/file.csv` to the URL. Add `?window=1`, `?window=3`, or `?window=6` to focus on the most recent months.

## Sample Data

A sample dataset is available at `sample_data/sample_transactions.csv` to help you explore the interface and verify that your setup works end-to-end.
