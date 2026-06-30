"""
Accounting Anomaly Detector
===========================
Scans journal entries for control-relevant red flags and assigns each
flagged entry a risk score. Rule-based + statistical, mirroring how an
internal-audit / SOX analyst would triage a population of entries.

Detection rules
  1. Duplicate entries     - same account/amount/date posted more than once
  2. Round-dollar entries  - large, suspiciously round amounts
  3. Off-hours postings    - weekend or late-night/early-morning timestamps
  4. Statistical outliers  - amount far above the norm for that account (z-score)

Returns dicts ready for serialization.
"""

from __future__ import annotations

import pandas as pd

ROUND_THRESHOLD = 20_000      # only flag round numbers above this
OUTLIER_Z = 2.5               # z-score cutoff for statistical outliers
OFF_HOURS = set(range(0, 6)) | {22, 23}   # 10pm-6am


def _entry_amounts(journal: pd.DataFrame) -> pd.DataFrame:
    """Collapse line-level GL into one row per entry with its driving amount,
    primary (non-cash) account, timestamp, user, etc."""
    journal = journal.copy()
    journal["account"] = journal["account"].astype(str)
    journal["amount"] = journal[["debit", "credit"]].max(axis=1)
    rows = []
    for eid, g in journal.groupby("entry_id"):
        amt = g["amount"].max()
        # primary account = the non-cash/AR line if present, else first
        non_cash = g[~g["account"].isin(["1000", "1100"])]
        prim = non_cash.iloc[0] if len(non_cash) else g.iloc[0]
        rows.append({
            "entry_id": eid,
            "date": g["date"].iloc[0],
            "posted_ts": g["posted_ts"].iloc[0],
            "account": prim["account"],
            "account_name": prim["account_name"],
            "department": prim["department"],
            "description": g["description"].iloc[0],
            "amount": round(float(amt), 2),
            "user": g["user"].iloc[0],
        })
    return pd.DataFrame(rows)


def build(journal: pd.DataFrame) -> dict:
    ent = _entry_amounts(journal)
    ent["ts"] = pd.to_datetime(ent["posted_ts"])
    ent["weekday"] = ent["ts"].dt.weekday
    ent["hour"] = ent["ts"].dt.hour

    flags = {eid: [] for eid in ent["entry_id"]}
    scores = {eid: 0 for eid in ent["entry_id"]}

    # 1. Duplicates: identical account + amount + date appearing >1
    dup_keys = ent.groupby(["account", "amount", "date"])["entry_id"].apply(list)
    for key, eids in dup_keys.items():
        if len(eids) > 1:
            for eid in eids:
                flags[eid].append("Duplicate")
                scores[eid] += 40

    # 2. Round-dollar large entries
    for _, r in ent.iterrows():
        if r["amount"] >= ROUND_THRESHOLD and r["amount"] % 5000 == 0:
            flags[r["entry_id"]].append("Round-dollar")
            scores[r["entry_id"]] += 25

    # 3. Off-hours / weekend
    for _, r in ent.iterrows():
        if r["weekday"] >= 5:
            flags[r["entry_id"]].append("Weekend")
            scores[r["entry_id"]] += 30
        if r["hour"] in OFF_HOURS:
            flags[r["entry_id"]].append("Off-hours")
            scores[r["entry_id"]] += 25

    # 4. Statistical outliers by account (z-score on amount)
    for acct, g in ent.groupby("account"):
        if len(g) < 8:
            continue
        mu, sd = g["amount"].mean(), g["amount"].std()
        if not sd or pd.isna(sd):
            continue
        for _, r in g.iterrows():
            z = (r["amount"] - mu) / sd
            if z >= OUTLIER_Z:
                flags[r["entry_id"]].append(f"Outlier (z={z:.1f})")
                scores[r["entry_id"]] += 35

    # Assemble flagged set
    flagged = []
    for _, r in ent.iterrows():
        eid = r["entry_id"]
        if flags[eid]:
            flagged.append({
                "entry_id": eid, "date": r["date"], "posted_ts": r["posted_ts"],
                "account": r["account"], "account_name": r["account_name"],
                "department": r["department"], "description": r["description"],
                "amount": r["amount"], "user": r["user"],
                "flags": sorted(set(flags[eid])),
                "risk_score": min(scores[eid], 100),
            })
    flagged.sort(key=lambda x: x["risk_score"], reverse=True)

    # Summary
    def count_flag(name):
        return sum(1 for f in flagged if any(name in x for x in f["flags"]))

    by_type = {
        "Duplicate": count_flag("Duplicate"),
        "Round-dollar": count_flag("Round-dollar"),
        "Weekend": count_flag("Weekend"),
        "Off-hours": count_flag("Off-hours"),
        "Outlier": count_flag("Outlier"),
    }
    risk_band = {
        "High (>=70)": sum(1 for f in flagged if f["risk_score"] >= 70),
        "Medium (40-69)": sum(1 for f in flagged if 40 <= f["risk_score"] < 70),
        "Low (<40)": sum(1 for f in flagged if f["risk_score"] < 40),
    }

    return {
        "total_entries": int(len(ent)),
        "flagged_count": len(flagged),
        "flag_rate_pct": round(len(flagged) / len(ent) * 100, 1),
        "by_type": by_type,
        "risk_band": risk_band,
        "flagged": flagged,
    }
