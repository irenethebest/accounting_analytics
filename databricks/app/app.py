"""
Meridian Finance Analytics Platform — Streamlit (Databricks App)
================================================================
Dual-mode dashboard:

  * In Databricks  -> reads the GOLD Delta tables via the Databricks SQL
                      connector (uses the app's OAuth service principal).
  * Locally        -> falls back to the project's CSVs and computes the same
                      results with modules/{variance,anomaly,recon}.py, so you
                      can run `streamlit run app.py` on your laptop for dev /
                      screenshots.

Config via env vars (set as Databricks App resources / env):
  DATABRICKS_WAREHOUSE_ID   SQL warehouse to query (required in Databricks)
  APP_CATALOG               Unity Catalog        (default: finance_portfolio)
  APP_GOLD_SCHEMA           Gold schema          (default: gold)
"""

from __future__ import annotations

import os
import sys

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

CATALOG = os.getenv("APP_CATALOG", "finance_portfolio")
GOLD = os.getenv("APP_GOLD_SCHEMA", "gold")
WAREHOUSE_ID = os.getenv("DATABRICKS_WAREHOUSE_ID")

st.set_page_config(page_title="Meridian Finance Analytics",
                   page_icon="📊", layout="wide")

NAVY, ACCENT, TEAL, GOOD, BAD, WARN, GOLD_C, PURPLE = (
    "#0f2742", "#2f80ed", "#1aa098", "#1a9c6b", "#d2483f", "#e0883a", "#c8b53a", "#5b3aa0")


# ---------------------------------------------------------------------------
# Data loading — two backends, one shape
# ---------------------------------------------------------------------------
def _in_databricks() -> bool:
    return bool(WAREHOUSE_ID and os.getenv("DATABRICKS_HOST"))


@st.cache_data(ttl=600, show_spinner=False)
def load_from_databricks() -> dict:
    from databricks import sql
    from databricks.sdk.core import Config

    cfg = Config()  # picks up the app's OAuth credentials automatically
    conn = sql.connect(
        server_hostname=cfg.host,
        http_path=f"/sql/1.0/warehouses/{WAREHOUSE_ID}",
        credentials_provider=lambda: cfg.authenticate,
    )

    def q(table):
        with conn.cursor() as cur:
            cur.execute(f"SELECT * FROM {CATALOG}.{GOLD}.{table}")
            return cur.fetchall_arrow().to_pandas()

    return {
        "pl_variance": q("gold_pl_variance"),
        "pl_summary": q("gold_pl_summary"),
        "monthly_pl": q("gold_monthly_pl"),
        "forecast": q("gold_forecast"),
        "anomaly_flags": q("gold_anomaly_flags"),
        "anomaly_summary": q("gold_anomaly_summary"),
        "recon_breaks": q("gold_recon_breaks"),
        "recon_summary": q("gold_recon_summary"),
    }


@st.cache_data(ttl=600, show_spinner=False)
def load_from_local() -> dict:
    """Compute the same gold-shaped frames from the local CSVs."""
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    sys.path.insert(0, root)
    sys.path.insert(0, os.path.join(root, "modules"))
    from modules import variance, anomaly, recon

    data_dir = os.path.join(root, "data")
    journal = pd.read_csv(os.path.join(data_dir, "journal_entries.csv"), dtype={"account": str})
    journal["date"] = journal["date"].astype(str)
    journal["posted_ts"] = journal["posted_ts"].astype(str)
    budget = pd.read_csv(os.path.join(data_dir, "budget.csv"), dtype={"account": str})
    coa = pd.read_csv(os.path.join(data_dir, "chart_of_accounts.csv"), dtype={"account": str})
    erp = pd.read_csv(os.path.join(data_dir, "erp_cash_ledger.csv"))
    bank = pd.read_csv(os.path.join(data_dir, "bank_statement.csv"))

    v = variance.build(journal, budget, coa)
    a = anomaly.build(journal)
    r = recon.build(erp.to_dict("records"), bank.to_dict("records"))

    s = v["summary"]
    pl_summary = pd.DataFrame([{
        "net_revenue_actual": s["net_revenue"]["actual"], "net_revenue_budget": s["net_revenue"]["budget"],
        "gross_profit_actual": s["gross_profit"]["actual"], "gross_profit_budget": s["gross_profit"]["budget"],
        "opex_actual": s["opex"]["actual"], "opex_budget": s["opex"]["budget"],
        "operating_income_actual": s["operating_income"]["actual"],
        "operating_income_budget": s["operating_income"]["budget"],
        "gross_margin_pct": s["gross_margin_pct"], "operating_margin_pct": s["operating_margin_pct"],
    }])
    flags = pd.DataFrame(a["flagged"]).copy()
    flags["flags"] = flags["flags"].apply(lambda xs: ", ".join(xs))
    an_summary = pd.DataFrame([{
        "total_entries": a["total_entries"], "flagged_count": a["flagged_count"],
        "flag_rate_pct": a["flag_rate_pct"],
        "risk_high": a["risk_band"]["High (>=70)"],
        "risk_medium": a["risk_band"]["Medium (40-69)"],
        "risk_low": a["risk_band"]["Low (<40)"],
        **{f"type_{k.lower().replace('-', '_').replace(' ', '_')}": vv for k, vv in a["by_type"].items()},
    }])
    ex = r["exposure"]
    rc_summary = pd.DataFrame([{
        "erp_lines": r["erp_lines"], "bank_lines": r["bank_lines"],
        "match_rate_pct": r["match_rate_pct"], "auto_cleared_pct": r["auto_cleared_pct"],
        "break_count": r["break_count"],
        **{f"count_{k}": vv for k, vv in r["counts"].items()},
        **{f"exposure_{k}": vv for k, vv in ex.items()},
    }])
    return {
        "pl_variance": pd.DataFrame(v["pl_lines"]),
        "pl_summary": pl_summary,
        "monthly_pl": pd.DataFrame(v["monthly"]),
        "forecast": pd.DataFrame(v["forecast"]),
        "anomaly_flags": flags,
        "anomaly_summary": an_summary,
        "recon_breaks": pd.DataFrame(r["breaks"]),
        "recon_summary": rc_summary,
    }


def money(n):
    n = float(n)
    a = abs(n); sgn = "-" if n < 0 else ""
    if a >= 1e6:
        return f"{sgn}${a/1e6:.1f}M"
    if a >= 1e3:
        return f"{sgn}${a/1e3:.0f}K"
    return f"{sgn}${a:,.0f}"


# ---------------------------------------------------------------------------
# Load
# ---------------------------------------------------------------------------
try:
    if _in_databricks():
        D = load_from_databricks()
        source = f"Databricks · {CATALOG}.{GOLD}"
    else:
        D = load_from_local()
        source = "Local CSVs (dev mode)"
except Exception as e:  # pragma: no cover
    st.error(f"Could not load data: {e}")
    st.stop()

st.markdown(
    f"<h2 style='margin-bottom:0;color:{NAVY}'>Meridian Outdoor Co. — Finance Analytics Platform</h2>"
    f"<p style='color:#6b7a8d;margin-top:4px'>FP&A Variance · Anomaly Detection · Reconciliation "
    f"&nbsp;|&nbsp; Source: {source}</p>", unsafe_allow_html=True)

tab_fpa, tab_anom, tab_recon = st.tabs(
    ["📈 FP&A Variance", "🚩 Anomaly Detector", "🔗 Reconciliation"])

# ---------------------------------------------------------------------------
# FP&A
# ---------------------------------------------------------------------------
with tab_fpa:
    s = D["pl_summary"].iloc[0]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Net Revenue", money(s["net_revenue_actual"]),
              f"{(s['net_revenue_actual']/s['net_revenue_budget']-1)*100:+.1f}% vs budget")
    c2.metric("Gross Margin", f"{s['gross_margin_pct']:.1f}%", f"GP {money(s['gross_profit_actual'])}")
    oi_var = s["operating_income_actual"] - s["operating_income_budget"]
    c3.metric("Operating Income", money(s["operating_income_actual"]), f"{money(oi_var)} vs budget")
    c4.metric("Operating Margin", f"{s['operating_margin_pct']:.1f}%", f"Opex {money(s['opex_actual'])}")

    col1, col2 = st.columns(2)
    with col1:
        st.subheader("Net Revenue, Gross Profit & Operating Income")
        m = D["monthly_pl"].sort_values("month")
        fc = D["forecast"].sort_values("month")
        fig = go.Figure()
        fig.add_scatter(x=m["month"], y=m["net_revenue"], name="Net Revenue",
                        line=dict(color=ACCENT, width=3))
        fig.add_scatter(x=m["month"], y=m["gross_profit"], name="Gross Profit",
                        line=dict(color=TEAL, width=2))
        fig.add_scatter(x=m["month"], y=m["operating_income"], name="Operating Income",
                        line=dict(color=GOLD_C, width=2))
        bridge_x = [m["month"].iloc[-1]] + list(fc["month"])
        bridge_y = [m["net_revenue"].iloc[-1]] + list(fc["net_revenue_forecast"])
        fig.add_scatter(x=bridge_x, y=bridge_y, name="Forecast",
                        line=dict(color=ACCENT, dash="dash"))
        fig.update_layout(height=360, margin=dict(l=10, r=10, t=10, b=10),
                          legend=dict(orientation="h", y=-0.2))
        st.plotly_chart(fig, use_container_width=True)
    with col2:
        st.subheader("Largest Budget Variances")
        pv = D["pl_variance"].copy()
        pv["abs"] = pv["variance"].abs()
        top = pv.sort_values("abs", ascending=False).head(6)
        fig2 = go.Figure()
        fig2.add_bar(y=top["account_name"], x=top["actual"], name="Actual",
                     orientation="h", marker_color=NAVY)
        fig2.add_bar(y=top["account_name"], x=top["budget"], name="Budget",
                     orientation="h", marker_color="#b9c6d6")
        fig2.update_layout(height=360, barmode="group", margin=dict(l=10, r=10, t=10, b=10),
                           legend=dict(orientation="h", y=-0.2))
        st.plotly_chart(fig2, use_container_width=True)

    st.subheader("Budget vs Actual P&L")
    pl = D["pl_variance"].copy()
    pl["Status"] = pl["favorable"].map({True: "✅ Favorable", False: "🔻 Unfavorable"})
    show = pl[["account_name", "group", "actual", "budget", "variance", "variance_pct", "Status"]]
    show.columns = ["Account", "Group", "Actual", "Budget", "Variance $", "Var %", "Status"]
    st.dataframe(show, use_container_width=True, hide_index=True,
                 column_config={
                     "Actual": st.column_config.NumberColumn(format="$%d"),
                     "Budget": st.column_config.NumberColumn(format="$%d"),
                     "Variance $": st.column_config.NumberColumn(format="$%d"),
                     "Var %": st.column_config.NumberColumn(format="%.1f%%")})

# ---------------------------------------------------------------------------
# Anomaly
# ---------------------------------------------------------------------------
with tab_anom:
    a = D["anomaly_summary"].iloc[0]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Entries Scanned", f"{int(a['total_entries']):,}")
    c2.metric("Flagged", int(a["flagged_count"]), f"{a['flag_rate_pct']:.1f}% of population")
    c3.metric("High Risk", int(a["risk_high"]), "score ≥ 70")
    c4.metric("Detection Rules", "4", "duplicate · round · off-hours · outlier")

    col1, col2 = st.columns(2)
    type_cols = [c for c in D["anomaly_summary"].columns if c.startswith("type_")]
    with col1:
        st.subheader("Flags by Type")
        tdf = pd.DataFrame({"Type": [c.replace("type_", "").replace("_", "-").title() for c in type_cols],
                            "Count": [int(a[c]) for c in type_cols]})
        fig = px.bar(tdf, x="Type", y="Count",
                     color="Type", color_discrete_sequence=[BAD, WARN, GOLD_C, PURPLE, ACCENT])
        fig.update_layout(height=300, showlegend=False, margin=dict(l=10, r=10, t=10, b=10))
        st.plotly_chart(fig, use_container_width=True)
    with col2:
        st.subheader("Risk Banding")
        rdf = pd.DataFrame({"Band": ["High (≥70)", "Medium (40-69)", "Low (<40)"],
                            "Count": [int(a["risk_high"]), int(a["risk_medium"]), int(a["risk_low"])]})
        fig = px.pie(rdf, names="Band", values="Count", hole=0.55,
                     color_discrete_sequence=[BAD, WARN, GOLD_C])
        fig.update_layout(height=300, margin=dict(l=10, r=10, t=10, b=10))
        st.plotly_chart(fig, use_container_width=True)

    st.subheader("Flagged Entries — Triage Queue")
    fl = D["anomaly_flags"].sort_values("risk_score", ascending=False).copy()
    cols = ["risk_score", "entry_id", "date", "account_name", "amount", "user", "flags"]
    cols = [c for c in cols if c in fl.columns]
    fl = fl[cols]
    fl.columns = [c.replace("_", " ").title() for c in fl.columns]
    st.dataframe(fl, use_container_width=True, hide_index=True,
                 column_config={"Amount": st.column_config.NumberColumn(format="$%d"),
                                "Risk Score": st.column_config.ProgressColumn(
                                    min_value=0, max_value=100, format="%d")})

# ---------------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------------
with tab_recon:
    r = D["recon_summary"].iloc[0]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Auto-Cleared", f"{r['auto_cleared_pct']:.1f}%", "exact + timing")
    c2.metric("Open Breaks", int(r["break_count"]), "require review")
    c3.metric("In-Transit", money(r.get("exposure_in_transit_total", 0)), "ERP not yet at bank")
    c4.metric("Book-to-Bank Gap", money(r.get("exposure_book_to_bank_gap", 0)), "net cash diff")

    col1, col2 = st.columns(2)
    label_map = {"matched": "Matched", "timing": "Timing", "amount": "Amount diff",
                 "missing_in_bank": "Missing in bank", "bank_only": "Bank only"}
    with col1:
        st.subheader("Match Outcomes")
        cnt = {k: int(r.get(f"count_{k}", 0)) for k in label_map}
        cdf = pd.DataFrame({"Outcome": [label_map[k] for k in label_map],
                            "Count": [cnt[k] for k in label_map]})
        fig = px.pie(cdf, names="Outcome", values="Count", hole=0.55,
                     color_discrete_sequence=[GOOD, WARN, BAD, ACCENT, PURPLE])
        fig.update_layout(height=300, margin=dict(l=10, r=10, t=10, b=10))
        st.plotly_chart(fig, use_container_width=True)
    with col2:
        st.subheader("Break Exposure ($)")
        edf = pd.DataFrame({
            "Bucket": ["Amount diffs", "In-transit", "Bank-only"],
            "Amount": [r.get("exposure_amount_diff_total", 0),
                       r.get("exposure_in_transit_total", 0),
                       r.get("exposure_bank_only_total", 0)]})
        fig = px.bar(edf, x="Bucket", y="Amount", color="Bucket",
                     color_discrete_sequence=[BAD, ACCENT, PURPLE])
        fig.update_layout(height=300, showlegend=False, margin=dict(l=10, r=10, t=10, b=10))
        st.plotly_chart(fig, use_container_width=True)

    st.subheader("Reconciliation Breaks — Work Queue")
    rb = D["recon_breaks"].copy()
    if "type" in rb.columns:
        rb["type"] = rb["type"].map(label_map).fillna(rb["type"])
    st.dataframe(rb, use_container_width=True, hide_index=True,
                 column_config={"amount": st.column_config.NumberColumn(format="$%d"),
                                "diff": st.column_config.NumberColumn(format="$%.2f")})

st.caption("Synthetic data — built as a portfolio demonstration of FP&A, accounting "
           "controls, and data-engineering on the Databricks Lakehouse. No real financial data.")
