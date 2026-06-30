"""
FP&A Variance Analyzer
======================
Builds a budget-vs-actual P&L from the GL and budget, computes variances
(favorable/unfavorable with correct sign by account type), KPIs, and a
simple next-period forecast.

Returns plain dicts/lists ready to be serialized for the web app.
"""

from __future__ import annotations

import pandas as pd

# Income-statement ordering and grouping
PL_ORDER = ["4000", "4100", "4900", "5000",
            "6000", "6100", "6200", "6300", "6400", "6500", "6600", "6900"]

REVENUE_ACCTS = {"4000", "4100"}
CONTRA_REV_ACCTS = {"4900"}
COGS_ACCTS = {"5000"}
OPEX_ACCTS = {"6000", "6100", "6200", "6300", "6400", "6500", "6600", "6900"}


def _signed_actual(df_pl: pd.DataFrame) -> pd.DataFrame:
    """Net activity per account-month, signed so revenue is +, expense is +
    in its own line (we present magnitudes and handle P&L math explicitly)."""
    g = df_pl.groupby(["account", "account_name", "month"], as_index=False).agg(
        debit=("debit", "sum"), credit=("credit", "sum"))
    # For revenue (normal credit) actual = credit - debit
    # For expense/contra (normal debit) actual = debit - credit
    def net(row):
        if row["account"] in REVENUE_ACCTS:
            return row["credit"] - row["debit"]
        return row["debit"] - row["credit"]
    g["actual"] = g.apply(net, axis=1)
    return g


def build(journal: pd.DataFrame, budget: pd.DataFrame, coa: pd.DataFrame) -> dict:
    journal = journal.copy()
    journal["account"] = journal["account"].astype(str)
    journal["month"] = journal["date"].str.slice(0, 7)
    budget = budget.copy()
    budget["account"] = budget["account"].astype(str)

    pl_accts = REVENUE_ACCTS | CONTRA_REV_ACCTS | COGS_ACCTS | OPEX_ACCTS
    df_pl = journal[journal["account"].isin(pl_accts)]

    actual = _signed_actual(df_pl)

    # Budget signed the same way (budget_amount already signed: returns negative)
    bud = budget.copy()
    def bsign(row):
        if row["account"] in REVENUE_ACCTS:
            return abs(row["budget_amount"])
        if row["account"] in CONTRA_REV_ACCTS:
            return abs(row["budget_amount"])  # present as positive magnitude of returns
        return abs(row["budget_amount"])
    bud["budget"] = bud.apply(bsign, axis=1)

    # ---- Annual budget vs actual by account ----
    act_year = actual.groupby(["account", "account_name"], as_index=False)["actual"].sum()
    bud_year = bud.groupby(["account"], as_index=False)["budget"].sum()
    merged = act_year.merge(bud_year, on="account", how="outer").fillna(0.0)

    def variance_row(r):
        acct = r["account"]
        actual_v = r["actual"]
        budget_v = r["budget"]
        var = actual_v - budget_v
        # Favorable logic: more revenue good; more expense/returns bad
        if acct in REVENUE_ACCTS:
            favorable = var >= 0
        else:
            favorable = var <= 0  # expense/contra under budget is favorable
        pct = (var / budget_v * 100) if budget_v else 0.0
        return pd.Series({"variance": round(var, 2),
                          "variance_pct": round(pct, 1),
                          "favorable": bool(favorable)})

    merged = pd.concat([merged, merged.apply(variance_row, axis=1)], axis=1)
    merged["account"] = pd.Categorical(merged["account"], categories=PL_ORDER, ordered=True)
    merged = merged.sort_values("account")
    merged["account"] = merged["account"].astype(str)

    pl_lines = []
    for _, r in merged.iterrows():
        grp = ("Revenue" if r["account"] in REVENUE_ACCTS else
               "Contra-Revenue" if r["account"] in CONTRA_REV_ACCTS else
               "COGS" if r["account"] in COGS_ACCTS else "Opex")
        pl_lines.append({
            "account": r["account"], "account_name": r["account_name"],
            "group": grp,
            "actual": round(r["actual"], 0), "budget": round(r["budget"], 0),
            "variance": round(r["variance"], 0),
            "variance_pct": r["variance_pct"], "favorable": r["favorable"],
        })

    # ---- Summary P&L math ----
    def s(accts):
        return float(merged[merged["account"].isin(accts)]["actual"].sum())
    def sb(accts):
        return float(merged[merged["account"].isin(accts)]["budget"].sum())

    gross_rev_a = s(REVENUE_ACCTS); gross_rev_b = sb(REVENUE_ACCTS)
    returns_a = s(CONTRA_REV_ACCTS); returns_b = sb(CONTRA_REV_ACCTS)
    net_rev_a = gross_rev_a - returns_a; net_rev_b = gross_rev_b - returns_b
    cogs_a = s(COGS_ACCTS); cogs_b = sb(COGS_ACCTS)
    gp_a = net_rev_a - cogs_a; gp_b = net_rev_b - cogs_b
    opex_a = s(OPEX_ACCTS); opex_b = sb(OPEX_ACCTS)
    oi_a = gp_a - opex_a; oi_b = gp_b - opex_b

    summary = {
        "net_revenue": {"actual": round(net_rev_a), "budget": round(net_rev_b)},
        "cogs": {"actual": round(cogs_a), "budget": round(cogs_b)},
        "gross_profit": {"actual": round(gp_a), "budget": round(gp_b)},
        "opex": {"actual": round(opex_a), "budget": round(opex_b)},
        "operating_income": {"actual": round(oi_a), "budget": round(oi_b)},
        "gross_margin_pct": round(gp_a / net_rev_a * 100, 1) if net_rev_a else 0,
        "operating_margin_pct": round(oi_a / net_rev_a * 100, 1) if net_rev_a else 0,
    }

    # ---- Monthly trend (net revenue, gross profit, operating income) ----
    months = sorted(actual["month"].unique())
    monthly = []
    for m in months:
        am = actual[actual["month"] == m]
        def sm(accts):
            return float(am[am["account"].isin(accts)]["actual"].sum())
        nrev = sm(REVENUE_ACCTS) - sm(CONTRA_REV_ACCTS)
        cogs = sm(COGS_ACCTS)
        gp = nrev - cogs
        opex = sm(OPEX_ACCTS)
        oi = gp - opex
        monthly.append({"month": m, "net_revenue": round(nrev),
                        "gross_profit": round(gp), "operating_income": round(oi),
                        "gross_margin_pct": round(gp / nrev * 100, 1) if nrev else 0})

    # ---- Simple forecast: 3-month moving average of net revenue, next 3 months ----
    nrev_series = [x["net_revenue"] for x in monthly]
    forecast = []
    hist = list(nrev_series)
    last_month = months[-1]
    y, mo = int(last_month[:4]), int(last_month[5:7])
    for i in range(3):
        ma = sum(hist[-3:]) / 3
        mo += 1
        if mo > 12:
            mo = 1; y += 1
        forecast.append({"month": f"{y}-{mo:02d}", "net_revenue_forecast": round(ma)})
        hist.append(ma)

    # ---- Top variances (by absolute $) ----
    top_var = sorted(pl_lines, key=lambda x: abs(x["variance"]), reverse=True)[:6]

    return {
        "summary": summary,
        "pl_lines": pl_lines,
        "monthly": monthly,
        "forecast": forecast,
        "top_variances": top_var,
    }
