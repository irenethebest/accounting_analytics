"""
Reconciliation Automation
=========================
Matches the internal ERP cash ledger against the bank statement and
classifies every line as one of:

  - matched          exact amount + same date
  - timing           same amount, date within a tolerance window (clears later)
  - amount           same date window, amount differs within a tolerance
  - missing_in_bank  ERP line with no bank counterpart (in transit)
  - bank_only        bank line with no ERP counterpart (unbooked fee, etc.)

Greedy matching: exact matches first, then timing, then amount. Each line is
consumed once. Mirrors a treasury/controllership bank-rec workflow.
"""

from __future__ import annotations

from datetime import datetime

DATE_TOL_DAYS = 5        # timing window
AMOUNT_TOL = 30.00       # absolute $ tolerance for "amount" breaks


def _d(s):
    return datetime.strptime(s, "%Y-%m-%d")


def build(erp, bank) -> dict:
    """erp, bank: lists of dicts from the CSVs."""
    erp = [dict(r, amount=float(r["amount"])) for r in erp]
    bank = [dict(r, amount=float(r["amount"])) for r in bank]
    for r in erp:
        r["_matched"] = False
    for r in bank:
        r["_matched"] = False

    results = []  # match records

    # Pass 1: exact amount + exact date
    bank_index = {}
    for b in bank:
        bank_index.setdefault((round(b["amount"], 2), b["date"]), []).append(b)
    for e in erp:
        key = (round(e["amount"], 2), e["date"])
        cands = [b for b in bank_index.get(key, []) if not b["_matched"]]
        if cands:
            b = cands[0]
            b["_matched"] = True
            e["_matched"] = True
            results.append({"type": "matched", "erp_ref": e["erp_ref"],
                            "bank_ref": b["bank_ref"], "amount": e["amount"],
                            "erp_date": e["date"], "bank_date": b["date"],
                            "diff": 0.0, "memo": e["memo"]})

    # Pass 2: timing - same amount, date within tolerance
    for e in erp:
        if e["_matched"]:
            continue
        best = None
        for b in bank:
            if b["_matched"]:
                continue
            if round(b["amount"], 2) == round(e["amount"], 2):
                gap = abs((_d(b["date"]) - _d(e["date"])).days)
                if gap <= DATE_TOL_DAYS:
                    if best is None or gap < best[1]:
                        best = (b, gap)
        if best:
            b, gap = best
            b["_matched"] = True
            e["_matched"] = True
            results.append({"type": "timing", "erp_ref": e["erp_ref"],
                            "bank_ref": b["bank_ref"], "amount": e["amount"],
                            "erp_date": e["date"], "bank_date": b["date"],
                            "diff": 0.0, "days": gap, "memo": e["memo"]})

    # Pass 3: amount difference - within date window, amount close but not equal
    for e in erp:
        if e["_matched"]:
            continue
        best = None
        for b in bank:
            if b["_matched"]:
                continue
            gap = abs((_d(b["date"]) - _d(e["date"])).days)
            adiff = abs(b["amount"] - e["amount"])
            if gap <= DATE_TOL_DAYS and 0 < adiff <= AMOUNT_TOL:
                score = gap + adiff
                if best is None or score < best[1]:
                    best = (b, score)
        if best:
            b, _ = best
            b["_matched"] = True
            e["_matched"] = True
            results.append({"type": "amount", "erp_ref": e["erp_ref"],
                            "bank_ref": b["bank_ref"], "amount": e["amount"],
                            "bank_amount": b["amount"],
                            "erp_date": e["date"], "bank_date": b["date"],
                            "diff": round(b["amount"] - e["amount"], 2),
                            "memo": e["memo"]})

    # Unmatched
    for e in erp:
        if not e["_matched"]:
            results.append({"type": "missing_in_bank", "erp_ref": e["erp_ref"],
                            "bank_ref": None, "amount": e["amount"],
                            "erp_date": e["date"], "bank_date": None,
                            "diff": None, "memo": e["memo"]})
    for b in bank:
        if not b["_matched"]:
            results.append({"type": "bank_only", "erp_ref": None,
                            "bank_ref": b["bank_ref"], "amount": b["amount"],
                            "erp_date": None, "bank_date": b["date"],
                            "diff": None, "memo": b.get("description", "")})

    counts = {}
    for r in results:
        counts[r["type"]] = counts.get(r["type"], 0) + 1

    total = len(results)
    matched_clean = counts.get("matched", 0)
    breaks = [r for r in results if r["type"] in
              ("timing", "amount", "missing_in_bank", "bank_only")]

    # Dollar exposure of breaks
    erp_total = round(sum(e["amount"] for e in erp), 2)
    bank_total = round(sum(b["amount"] for b in bank), 2)
    amount_diff_total = round(sum(r["diff"] for r in results
                                  if r["type"] == "amount" and r["diff"]), 2)
    in_transit_total = round(sum(r["amount"] for r in results
                                 if r["type"] == "missing_in_bank"), 2)
    bank_only_total = round(sum(r["amount"] for r in results
                                if r["type"] == "bank_only"), 2)

    return {
        "erp_lines": len(erp),
        "bank_lines": len(bank),
        "counts": counts,
        "match_rate_pct": round(matched_clean / len(erp) * 100, 1) if erp else 0,
        "auto_cleared_pct": round((matched_clean + counts.get("timing", 0))
                                  / len(erp) * 100, 1) if erp else 0,
        "break_count": len(breaks),
        "exposure": {
            "erp_total": erp_total, "bank_total": bank_total,
            "book_to_bank_gap": round(erp_total - bank_total, 2),
            "amount_diff_total": amount_diff_total,
            "in_transit_total": in_transit_total,
            "bank_only_total": bank_only_total,
        },
        "breaks": sorted(breaks, key=lambda r: -abs(r["amount"]))[:50],
    }
