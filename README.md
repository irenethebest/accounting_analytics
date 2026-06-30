# Finance Analytics Platform

A finance & accounting data product built from scratch on a synthetic dataset.
It generates a coherent company's books, then runs three analytics engines over
them and presents the results in a single, self-contained, browser-based app.

**Built to demonstrate:** FP&A, accounting controls / internal audit, and
data-engineering skills end to end — from raw double-entry journal entries to an
interactive dashboard.

> All data is synthetic. No real financial information is used.

---

## What it does

The platform models a fictional mid-size consumer-products company,
**Meridian Outdoor Co.** (FY2025), and ships three modules:

### 1. FP&A Variance Analyzer
Builds a budget-vs-actual P&L from the general ledger and the operating budget.
- Full income statement with $ and % variances, signed correctly by account type
  (more revenue = favorable; over-budget expense = unfavorable)
- KPIs: net revenue, gross margin, operating income, operating margin
- Monthly trend of net revenue / gross profit / operating income
- Simple 3-month forward forecast (moving average)

### 2. Accounting Anomaly Detector
Scans every journal entry for control-relevant red flags and assigns a composite
risk score (0–100), mirroring an internal-audit / SOX triage workflow.
- **Duplicate** entries (same account, amount, and date)
- **Round-dollar** large entries (potential override / fabricated amounts)
- **Off-hours / weekend** postings (unusual timing)
- **Statistical outliers** (z-score vs. the account's own history)
- Output is a ranked triage queue — highest risk first

### 3. Reconciliation Automation
Matches the internal ERP cash ledger against the bank statement using a greedy,
multi-pass matcher (exact → timing window → amount tolerance), then classifies
every remaining line as a break:
- `matched`, `timing`, `amount diff`, `missing in bank` (in-transit), `bank only` (unbooked fees)
- Reports auto-clear rate and the dollar exposure sitting in each break bucket

---

## Architecture

```
FinanceData_Portfolio/
├── data_engine/
│   └── generate_data.py      # Synthetic double-entry GL, budget, ERP & bank ledgers
├── data/                     # Generated CSVs (the "source systems")
│   ├── chart_of_accounts.csv
│   ├── journal_entries.csv   # Line-level GL (debits == credits)
│   ├── budget.csv            # Monthly operating plan
│   ├── erp_cash_ledger.csv   # Internal cash record
│   └── bank_statement.csv    # External cash record
├── modules/
│   ├── variance.py           # FP&A engine
│   ├── anomaly.py            # Anomaly-detection engine
│   ├── recon.py             # Reconciliation engine
│   └── build_dashboard.py    # Runs all three, emits JSON, injects into the app
├── app/                      # Local, self-contained web dashboard
│   ├── index.html            # Open in any browser (data embedded)
│   └── dashboard_data.json   # Computed results
└── databricks/               # Databricks Lakehouse deployment
    ├── 01_ingest_bronze.py   # Generate → bronze Delta tables (Unity Catalog)
    ├── 02_build_gold.py      # Run modules → gold result tables
    ├── app/                  # Streamlit Databricks App (SQL backend + local fallback)
    └── README.md             # Databricks setup & deploy guide
```

## Two ways to run

1. **Local** — a single self-contained `app/index.html` (no backend). See *Run it* below.
2. **Databricks Lakehouse** — generate → bronze Delta tables (Unity Catalog) → gold
   result tables → a **Streamlit Databricks App**. The notebooks reuse the same
   `data_engine` and `modules` code. See [`databricks/README.md`](databricks/README.md).

A Python data + analytics layer (pandas) feeds a self-contained HTML/JS front end
(Chart.js). The computed results are embedded directly into `index.html`, so the
app opens with a double-click and can also be hosted as a static page
(e.g. GitHub Pages) with no backend.

---

## Run it

```bash
# 1. Generate the synthetic dataset
python3 data_engine/generate_data.py

# 2. Run the three modules and build the dashboard
cd modules && python3 build_dashboard.py

# 3. Open the app
open app/index.html        # or just double-click it
```

Everything is seeded (`SEED = 42`), so results are fully reproducible.

---

## Design notes

- **Realistic by construction.** Revenue follows outdoor-gear seasonality
  (spring/summer peak); expenses split into fixed vs. sales-driven; ~60% of sales
  are cash, the rest on account. Every journal entry balances.
- **Imperfections on purpose.** The data engine injects known anomalies and
  reconciliation breaks so the modules have something real to find. Ground-truth
  files (`data/_*_truth.json`) are written purely for validation.
- **Validated.** The GL ties out to the penny, the reconciliation engine recovers
  the injected break counts exactly, and the anomaly detector catches 20/20
  injected anomalies.

## Skills demonstrated

| Area | What's shown |
|------|--------------|
| Accounting | Double-entry GL, chart of accounts, contra-revenue, COGS/inventory relief, accruals |
| FP&A | Budget-vs-actual, variance favorability logic, margin analysis, forecasting |
| Controls / Audit | Duplicate / round-dollar / off-hours / outlier detection, risk scoring, SOX-style triage |
| Treasury / Controllership | Bank reconciliation, timing vs. amount breaks, in-transit exposure |
| Data engineering | Synthetic data generation, pandas pipelines, reproducible builds |
| Front end | Self-contained interactive dashboard, charts, no-backend deployment |
