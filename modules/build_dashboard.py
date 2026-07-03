"""
Orchestrator: load the synthetic data, run all three analytics modules,
and emit a single dashboard_data.json AND inject it into the web app
(app/index.html) so the app is fully self-contained (double-click to open,
or host on GitHub Pages).
"""

from __future__ import annotations

import csv
import json
import os

import pandas as pd

import variance
import anomaly
import recon

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
APP = os.path.join(ROOT, "app")
os.makedirs(APP, exist_ok=True)


def read_csv_dicts(name):
    with open(os.path.join(DATA, name), newline="") as f:
        return list(csv.DictReader(f))


def main():
    journal = pd.read_csv(os.path.join(DATA, "journal_entries.csv"), dtype={"account": str})
    budget = pd.read_csv(os.path.join(DATA, "budget.csv"), dtype={"account": str})
    coa = pd.read_csv(os.path.join(DATA, "chart_of_accounts.csv"), dtype={"account": str})
    erp = read_csv_dicts("erp_cash_ledger.csv")
    bank = read_csv_dicts("bank_statement.csv")

    payload = {
        "company": "Jinhee Financial Co.",
        "fiscal_year": 2025,
        "generated": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M"),
        "variance": variance.build(journal, budget, coa),
        "anomaly": anomaly.build(journal),
        "recon": recon.build(erp, bank),
    }

    out_json = os.path.join(APP, "dashboard_data.json")
    with open(out_json, "w") as f:
        json.dump(payload, f, indent=2, default=str)

    # Inject into index.html between the data markers, if the template exists
    tmpl = os.path.join(APP, "index.html")
    if os.path.exists(tmpl):
        with open(tmpl) as f:
            html = f.read()
        start = "/*DATA_START*/"
        end = "/*DATA_END*/"
        if start in html and end in html:
            pre = html.split(start)[0]
            post = html.split(end)[1]
            data_js = f"{start}\nconst DASHBOARD_DATA = {json.dumps(payload, default=str)};\n{end}"
            with open(tmpl, "w") as f:
                f.write(pre + data_js + post)
            print("Injected data into app/index.html")

    # Console summary
    v, a, r = payload["variance"], payload["anomaly"], payload["recon"]
    print("=== Dashboard data built ===")
    print(f"Net revenue (A/B): {v['summary']['net_revenue']['actual']:,} / "
          f"{v['summary']['net_revenue']['budget']:,}")
    print(f"Operating income (A): {v['summary']['operating_income']['actual']:,} "
          f"| Op margin {v['summary']['operating_margin_pct']}%")
    print(f"Anomalies flagged: {a['flagged_count']} / {a['total_entries']} "
          f"({a['flag_rate_pct']}%)  by_type={a['by_type']}")
    print(f"Recon: match {r['match_rate_pct']}%, auto-cleared {r['auto_cleared_pct']}%, "
          f"breaks {r['break_count']}  counts={r['counts']}")
    print(f"Wrote {out_json}")


if __name__ == "__main__":
    main()
