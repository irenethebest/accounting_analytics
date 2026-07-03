"""
Synthetic finance-data engine for the FinanceData Portfolio platform.

Generates a coherent, realistic financial dataset for a fictional mid-size
consumer-products company ("Jinhee Financial Co.") for fiscal year 2025.

Public API
----------
    generate() -> dict[str, pandas.DataFrame]
        Returns every dataset as a DataFrame plus two ground-truth frames.
        Used by both the local CSV build and the Databricks ingestion notebook.

Run as a script (`python generate_data.py`) to write the datasets to ../data
as CSVs (local-app behaviour, unchanged).

Datasets
    chart_of_accounts   Chart of accounts (account, type, statement)
    journal_entries     Line-level, double-entry GL (debits == credits)
    budget              Monthly annual operating budget by account
    erp_cash_ledger     Cash-affecting transactions per the ERP/GL
    bank_statement      The same cash flows as seen by the bank
    _anomaly_truth      Injected-anomaly labels (validation only)
    _recon_truth        Injected reconciliation-break labels (validation only)

Realistic imperfections are injected on purpose so the downstream modules have
something to find (duplicates, round-dollar/off-hours/outlier entries; timing,
amount, missing, and bank-only reconciliation breaks).

Deterministic: generate() reseeds RNG so output is reproducible.
"""

from __future__ import annotations

import os
import random
from datetime import datetime, timedelta

import pandas as pd

SEED = 42
YEAR = 2025
COMPANY = "Jinhee Financial Co."

# ---------------------------------------------------------------------------
# Static reference data
# ---------------------------------------------------------------------------
# (number, name, type, statement, normal_balance)
COA = [
    ("1000", "Cash - Operating", "Asset", "BS", "D"),
    ("1100", "Accounts Receivable", "Asset", "BS", "D"),
    ("1200", "Inventory", "Asset", "BS", "D"),
    ("2000", "Accounts Payable", "Liability", "BS", "C"),
    ("2100", "Accrued Payroll", "Liability", "BS", "C"),
    ("3000", "Retained Earnings", "Equity", "BS", "C"),
    ("4000", "Product Revenue", "Revenue", "PL", "C"),
    ("4100", "Service Revenue", "Revenue", "PL", "C"),
    ("4900", "Sales Returns & Allowances", "Contra-Revenue", "PL", "D"),
    ("5000", "Cost of Goods Sold", "COGS", "PL", "D"),
    ("6000", "Salaries & Wages", "Opex", "PL", "D"),
    ("6100", "Marketing & Advertising", "Opex", "PL", "D"),
    ("6200", "Rent & Facilities", "Opex", "PL", "D"),
    ("6300", "Software & IT", "Opex", "PL", "D"),
    ("6400", "Travel & Entertainment", "Opex", "PL", "D"),
    ("6500", "Professional Fees", "Opex", "PL", "D"),
    ("6600", "Depreciation", "Opex", "PL", "D"),
    ("6900", "Other Operating Expense", "Opex", "PL", "D"),
]
COA_BY_NUM = {c[0]: c for c in COA}

DEPARTMENTS = ["Sales", "Marketing", "R&D", "Operations", "G&A"]
USERS = ["jchen", "spatel", "mlopez", "kkim", "dwright", "afarrell", "SYSTEM"]

SEASONALITY = {
    1: 0.70, 2: 0.75, 3: 0.95, 4: 1.10, 5: 1.25, 6: 1.30,
    7: 1.20, 8: 1.05, 9: 1.00, 10: 0.90, 11: 1.05, 12: 1.20,
}

BASELINE = {
    "4000": 1_850_000, "4100": 240_000, "4900": -65_000, "5000": 920_000,
    "6000": 430_000, "6100": 180_000, "6200": 55_000, "6300": 38_000,
    "6400": 22_000, "6500": 30_000, "6600": 26_000, "6900": 14_000,
}

SEASONAL_ACCOUNTS = {"4000", "4100", "4900", "5000", "6100", "6400"}
FIXED_ACCOUNTS = {"6000", "6200", "6300", "6500", "6600", "6900"}

DEPT_FOR_ACCT = {
    "4000": "Sales", "4100": "Sales", "4900": "Sales", "5000": "Operations",
    "6000": "G&A", "6100": "Marketing", "6200": "G&A", "6300": "G&A",
    "6400": "Sales", "6500": "G&A", "6600": "Operations", "6900": "G&A",
}


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------
def _jitter(amount: float, pct: float = 0.06) -> float:
    return amount * (1 + random.uniform(-pct, pct))


def _month_dates(year: int, month: int):
    start = datetime(year, month, 1)
    end = datetime(year + 1, 1, 1) if month == 12 else datetime(year, month + 1, 1)
    return start, end - timedelta(days=1)


def _random_business_datetime(year: int, month: int) -> datetime:
    """A normal posting: a weekday during business hours."""
    start, end = _month_dates(year, month)
    for _ in range(20):
        day = random.randint(start.day, end.day)
        d = datetime(year, month, day)
        if d.weekday() < 5:
            hour = random.randint(8, 17)
            minute = random.choice([0, 5, 10, 15, 20, 30, 40, 45, 50])
            return d.replace(hour=hour, minute=minute)
    return start.replace(hour=10)


# ---------------------------------------------------------------------------
# Main generation routine
# ---------------------------------------------------------------------------
def generate() -> dict:
    """Generate the full synthetic dataset. Returns a dict of DataFrames."""
    random.seed(SEED)

    journal_rows = []
    entry_counter = [0]
    cash_events = []

    def new_entry_id() -> str:
        entry_counter[0] += 1
        return f"JE{YEAR}{entry_counter[0]:05d}"

    def post_entry(dt, lines, description, user=None, source="ERP"):
        eid = new_entry_id()
        if user is None:
            user = random.choice(USERS)
        total_d = round(sum(l[1] for l in lines), 2)
        total_c = round(sum(l[2] for l in lines), 2)
        assert abs(total_d - total_c) < 0.01, f"Unbalanced {eid}"
        for acct, deb, cred in lines:
            journal_rows.append({
                "entry_id": eid, "line_no": 0,
                "date": dt.strftime("%Y-%m-%d"),
                "posted_ts": dt.strftime("%Y-%m-%d %H:%M:%S"),
                "account": acct, "account_name": COA_BY_NUM[acct][1],
                "department": DEPT_FOR_ACCT.get(acct, "G&A"),
                "description": description,
                "debit": round(deb, 2), "credit": round(cred, 2),
                "source_system": source, "user": user,
            })
        return eid

    def record_cash(dt, amount_signed, memo, ref):
        cash_events.append({"date": dt, "amount": round(amount_signed, 2),
                            "memo": memo, "ref": ref})

    # ----- Business activity per month -----
    for month in range(1, 13):
        season = SEASONALITY[month]

        # Product revenue across several invoices
        prod_rev_target = BASELINE["4000"] * season
        n_invoices = random.randint(18, 28)
        for _ in range(n_invoices):
            amt = _jitter(prod_rev_target / n_invoices, 0.35)
            dt = _random_business_datetime(YEAR, month)
            if random.random() < 0.6:
                post_entry(dt, [("1000", amt, 0.0), ("4000", 0.0, amt)],
                           "Product sale - cash")
                record_cash(dt, +amt, "Customer payment - product", f"INV{random.randint(10000,99999)}")
            else:
                post_entry(dt, [("1100", amt, 0.0), ("4000", 0.0, amt)],
                           "Product sale - on account")

        # Service revenue
        svc_target = BASELINE["4100"] * season
        for _ in range(random.randint(4, 7)):
            amt = _jitter(svc_target / 5, 0.4)
            dt = _random_business_datetime(YEAR, month)
            post_entry(dt, [("1000", amt, 0.0), ("4100", 0.0, amt)], "Service revenue")
            record_cash(dt, +amt, "Customer payment - service", f"SVC{random.randint(10000,99999)}")

        # Returns & allowances
        ret_target = abs(BASELINE["4900"]) * season
        for _ in range(random.randint(3, 6)):
            amt = _jitter(ret_target / 4, 0.5)
            dt = _random_business_datetime(YEAR, month)
            post_entry(dt, [("4900", amt, 0.0), ("1000", 0.0, amt)],
                       "Customer refund / allowance")
            record_cash(dt, -amt, "Customer refund", f"RET{random.randint(10000,99999)}")

        # COGS / inventory relief
        cogs_target = BASELINE["5000"] * season
        for _ in range(random.randint(10, 16)):
            amt = _jitter(cogs_target / 13, 0.3)
            dt = _random_business_datetime(YEAR, month)
            post_entry(dt, [("5000", amt, 0.0), ("1200", 0.0, amt)],
                       "COGS - inventory relief")

        # Inventory purchases
        for _ in range(random.randint(6, 10)):
            amt = _jitter(cogs_target / 9, 0.3)
            dt = _random_business_datetime(YEAR, month)
            if random.random() < 0.5:
                post_entry(dt, [("1200", amt, 0.0), ("1000", 0.0, amt)],
                           "Inventory purchase - paid")
                record_cash(dt, -amt, "Supplier payment - inventory", f"PO{random.randint(10000,99999)}")
            else:
                post_entry(dt, [("1200", amt, 0.0), ("2000", 0.0, amt)],
                           "Inventory purchase - on account")

        # Operating expenses
        for acct in ["6000", "6100", "6200", "6300", "6400", "6500", "6600", "6900"]:
            base = BASELINE[acct] * (season if acct in SEASONAL_ACCOUNTS else 1)
            n = random.randint(2, 5) if acct in ("6000", "6100") else random.randint(1, 3)
            for _ in range(n):
                amt = _jitter(base / n, 0.12 if acct in FIXED_ACCOUNTS else 0.25)
                dt = _random_business_datetime(YEAR, month)
                if acct == "6000":
                    post_entry(dt, [("6000", amt, 0.0), ("1000", 0.0, amt)],
                               "Payroll run", user="SYSTEM")
                    record_cash(dt, -amt, "Payroll", f"PR{random.randint(10000,99999)}")
                elif acct == "6600":
                    post_entry(dt, [("6600", amt, 0.0), ("1200", 0.0, amt)],
                               "Monthly depreciation", user="SYSTEM")
                else:
                    post_entry(dt, [(acct, amt, 0.0), ("1000", 0.0, amt)],
                               f"{COA_BY_NUM[acct][1]} expense")
                    record_cash(dt, -amt, COA_BY_NUM[acct][1], f"EXP{random.randint(10000,99999)}")

    # ----- Inject anomalies -----
    anomaly_log = []

    existing_eids = sorted({r["entry_id"] for r in journal_rows})  # sorted -> hash-seed independent
    for src in random.sample(existing_eids, 6):
        src_lines = [r for r in journal_rows if r["entry_id"] == src]
        new_eid = new_entry_id()
        for r in src_lines:
            dup = dict(r); dup["entry_id"] = new_eid
            dup["description"] = r["description"] + " (DUP)"
            journal_rows.append(dup)
        anomaly_log.append({"type": "duplicate", "entry_id": new_eid, "note": f"Duplicate of {src}"})

    for _ in range(5):
        month = random.randint(1, 12)
        dt = _random_business_datetime(YEAR, month)
        amt = float(random.choice([50_000, 75_000, 100_000, 25_000]))
        acct = random.choice(["6500", "6900", "6400"])
        eid = post_entry(dt, [(acct, amt, 0.0), ("1000", 0.0, amt)],
                         "Consulting services", user=random.choice(USERS))
        record_cash(dt, -amt, "Consulting services", f"EXP{random.randint(10000,99999)}")
        anomaly_log.append({"type": "round_number", "entry_id": eid, "note": f"Round ${amt:,.0f} to {acct}"})

    for _ in range(5):
        month = random.randint(1, 12)
        start, end = _month_dates(YEAR, month)
        d = start
        for _try in range(30):
            day = random.randint(start.day, end.day)
            d = datetime(YEAR, month, day)
            if d.weekday() >= 5:
                break
        dt = d.replace(hour=random.choice([2, 3, 23]), minute=random.randint(0, 59))
        amt = _jitter(40_000, 0.4)
        acct = random.choice(["6900", "6500", "6400"])
        eid = post_entry(dt, [(acct, amt, 0.0), ("1000", 0.0, amt)],
                         "Manual adjustment", user=random.choice([u for u in USERS if u != "SYSTEM"]))
        record_cash(dt, -amt, "Manual adjustment", f"ADJ{random.randint(10000,99999)}")
        anomaly_log.append({"type": "off_hours", "entry_id": eid, "note": f"Posted {dt.strftime('%a %H:%M')}"})

    for _ in range(4):
        month = random.randint(1, 12)
        dt = _random_business_datetime(YEAR, month)
        amt = _jitter(280_000, 0.2)
        acct = random.choice(["6100", "6900", "6500"])
        eid = post_entry(dt, [(acct, amt, 0.0), ("1000", 0.0, amt)],
                         "Large vendor invoice", user=random.choice(USERS))
        record_cash(dt, -amt, "Large vendor invoice", f"EXP{random.randint(10000,99999)}")
        anomaly_log.append({"type": "outlier", "entry_id": eid, "note": f"${amt:,.0f} to {acct}"})

    # Assign line numbers within each entry
    by_entry = {}
    for r in journal_rows:
        by_entry.setdefault(r["entry_id"], []).append(r)
    for eid, lines in by_entry.items():
        for i, r in enumerate(lines, 1):
            r["line_no"] = i

    # ----- Reconciliation systems from cash events -----
    erp_ledger, bank_stmt, recon_truth = [], [], []
    n_break = {"timing": 0, "amount": 0, "missing_in_bank": 0, "bank_only": 0, "matched": 0}

    for i, ev in enumerate(cash_events, 1):
        erp_ledger.append({"erp_ref": f"ERP{i:05d}", "date": ev["date"].strftime("%Y-%m-%d"),
                           "amount": ev["amount"], "memo": ev["memo"]})

    bank_counter = 1
    for i, ev in enumerate(cash_events, 1):
        erp_ref = f"ERP{i:05d}"
        roll = random.random()
        if roll < 0.08:
            n_break["missing_in_bank"] += 1
            recon_truth.append({"erp_ref": erp_ref, "bank_ref": None, "type": "missing_in_bank"})
            continue
        bank_date, bank_amt, btype = ev["date"], ev["amount"], "matched"
        if roll < 0.20:
            bank_date = ev["date"] + timedelta(days=random.randint(1, 4)); btype = "timing"; n_break["timing"] += 1
        elif roll < 0.28:
            bank_amt = round(ev["amount"] - random.choice([1.50, 4.00, 12.25, 25.00, -3.00]), 2)
            btype = "amount"; n_break["amount"] += 1
        else:
            n_break["matched"] += 1
        bank_stmt.append({"bank_ref": f"BNK{bank_counter:05d}", "date": bank_date.strftime("%Y-%m-%d"),
                          "amount": bank_amt, "description": ev["memo"]})
        recon_truth.append({"erp_ref": erp_ref, "bank_ref": f"BNK{bank_counter:05d}", "type": btype})
        bank_counter += 1

    for _ in range(7):
        month = random.randint(1, 12)
        start, end = _month_dates(YEAR, month)
        d = datetime(YEAR, month, random.randint(start.day, end.day))
        amt = -round(random.uniform(25, 350), 2)
        bank_stmt.append({"bank_ref": f"BNK{bank_counter:05d}", "date": d.strftime("%Y-%m-%d"),
                          "amount": amt, "description": random.choice(
                              ["Bank service charge", "Wire fee", "FX adjustment", "Card processing fee"])})
        recon_truth.append({"erp_ref": None, "bank_ref": f"BNK{bank_counter:05d}", "type": "bank_only"})
        bank_counter += 1
        n_break["bank_only"] += 1

    random.shuffle(bank_stmt)
    bank_stmt.sort(key=lambda r: r["date"])

    # ----- Budget -----
    budget_rows = []
    for acct in BASELINE:
        for month in range(1, 13):
            base = BASELINE[acct] * (SEASONALITY[month] if acct in SEASONAL_ACCOUNTS else 1)
            budget_rows.append({
                "account": acct, "account_name": COA_BY_NUM[acct][1],
                "month": f"{YEAR}-{month:02d}",
                "budget_amount": round(abs(base), 2) * (1 if BASELINE[acct] >= 0 else -1),
            })

    # ----- Assemble DataFrames -----
    coa_df = pd.DataFrame(
        [{"account": c[0], "account_name": c[1], "type": c[2],
          "statement": c[3], "normal_balance": c[4]} for c in COA])

    journal_rows.sort(key=lambda r: (r["date"], r["entry_id"], r["line_no"]))
    je_cols = ["entry_id", "line_no", "date", "posted_ts", "account", "account_name",
               "department", "description", "debit", "credit", "source_system", "user"]
    je_df = pd.DataFrame(journal_rows)[je_cols]
    budget_df = pd.DataFrame(budget_rows)[["account", "account_name", "month", "budget_amount"]]
    erp_df = pd.DataFrame(erp_ledger)[["erp_ref", "date", "amount", "memo"]]
    bank_df = pd.DataFrame(bank_stmt)[["bank_ref", "date", "amount", "description"]]
    anomaly_truth_df = pd.DataFrame(anomaly_log)
    recon_truth_df = pd.DataFrame(recon_truth)

    return {
        "chart_of_accounts": coa_df,
        "journal_entries": je_df,
        "budget": budget_df,
        "erp_cash_ledger": erp_df,
        "bank_statement": bank_df,
        "_anomaly_truth": anomaly_truth_df,
        "_recon_truth": recon_truth_df,
    }


# ---------------------------------------------------------------------------
# Local CSV build (unchanged behaviour)
# ---------------------------------------------------------------------------
def _write_local(out_dir: str):
    import json
    os.makedirs(out_dir, exist_ok=True)
    data = generate()

    for name in ["chart_of_accounts", "journal_entries", "budget",
                 "erp_cash_ledger", "bank_statement"]:
        data[name].to_csv(os.path.join(out_dir, f"{name}.csv"), index=False)

    with open(os.path.join(out_dir, "_anomaly_truth.json"), "w") as f:
        json.dump(data["_anomaly_truth"].to_dict("records"), f, indent=2)
    counts = data["_recon_truth"]["type"].value_counts().to_dict()
    with open(os.path.join(out_dir, "_recon_truth.json"), "w") as f:
        json.dump({"counts": counts, "labels": data["_recon_truth"].to_dict("records")}, f, indent=2)

    je = data["journal_entries"]
    td, tc = je["debit"].sum(), je["credit"].sum()
    print(f"Company: {COMPANY}  FY{YEAR}")
    print(f"Journal lines : {len(je):,}  across {je['entry_id'].nunique():,} entries")
    print(f"Total debits  : {td:,.2f}")
    print(f"Total credits : {tc:,.2f}")
    print(f"Balanced      : {abs(td - tc) < 0.01}")
    print(f"Budget rows   : {len(data['budget']):,}")
    print(f"ERP cash lines: {len(data['erp_cash_ledger']):,}")
    print(f"Bank lines    : {len(data['bank_statement']):,}")
    print(f"Injected anomalies: {len(data['_anomaly_truth'])}  -> "
          f"{data['_anomaly_truth']['type'].value_counts().to_dict()}")
    print(f"Recon breaks (truth): {counts}")
    print(f"Output dir    : {out_dir}")


if __name__ == "__main__":
    OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
    _write_local(OUT_DIR)
