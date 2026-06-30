# Databricks notebook source
# MAGIC %md
# MAGIC # 02 · Build Gold — FP&A, Anomaly & Reconciliation Result Tables
# MAGIC
# MAGIC Reads the **bronze** tables, runs the same analytics modules the local app
# MAGIC uses (`modules/variance.py`, `anomaly.py`, `recon.py`), and writes flat
# MAGIC **gold** tables that the Streamlit Databricks App reads directly.
# MAGIC
# MAGIC Gold tables:
# MAGIC `gold_pl_variance`, `gold_pl_summary`, `gold_monthly_pl`, `gold_forecast`,
# MAGIC `gold_anomaly_flags`, `gold_anomaly_summary`,
# MAGIC `gold_recon_breaks`, `gold_recon_summary`.

# COMMAND ----------

dbutils.widgets.text("catalog", "finance_portfolio", "Unity Catalog")
dbutils.widgets.text("bronze_schema", "bronze", "Bronze schema")
dbutils.widgets.text("gold_schema", "gold", "Gold schema")

CATALOG = dbutils.widgets.get("catalog")
BRONZE = dbutils.widgets.get("bronze_schema")
GOLD = dbutils.widgets.get("gold_schema")
print(f"{CATALOG}.{BRONZE}  ->  {CATALOG}.{GOLD}")

# COMMAND ----------

import os
import sys

REPO_ROOT = os.path.abspath(os.path.join(os.getcwd(), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, "modules"))

import pandas as pd  # noqa: E402
from modules import variance, anomaly, recon  # noqa: E402

spark.sql(f"CREATE SCHEMA IF NOT EXISTS {CATALOG}.{GOLD}")

# COMMAND ----------

# MAGIC %md ### Load bronze → pandas
# MAGIC The modules expect string dates (they parse them internally), so we cast
# MAGIC date/timestamp columns back to string on read.

# COMMAND ----------

def bronze(name):
    return spark.table(f"{CATALOG}.{BRONZE}.{name}").toPandas()

journal = bronze("journal_entries")
journal["account"] = journal["account"].astype(str)
journal["date"] = journal["date"].astype(str)
journal["posted_ts"] = journal["posted_ts"].astype(str)

budget = bronze("budget"); budget["account"] = budget["account"].astype(str)
coa = bronze("chart_of_accounts"); coa["account"] = coa["account"].astype(str)

erp = bronze("erp_cash_ledger"); erp["date"] = erp["date"].astype(str)
bank = bronze("bank_statement"); bank["date"] = bank["date"].astype(str)
erp_recs = erp.to_dict("records")
bank_recs = bank.to_dict("records")

# COMMAND ----------

# MAGIC %md ### Run the analytics modules

# COMMAND ----------

v = variance.build(journal, budget, coa)
a = anomaly.build(journal)
r = recon.build(erp_recs, bank_recs)

print("FP&A   :", v["summary"]["net_revenue"], "| OM%", v["summary"]["operating_margin_pct"])
print("Anomaly:", a["flagged_count"], "flagged of", a["total_entries"])
print("Recon  :", r["counts"])

# COMMAND ----------

# MAGIC %md ### Flatten results and write gold Delta tables

# COMMAND ----------

def save_gold(pdf: pd.DataFrame, name: str):
    table = f"{CATALOG}.{GOLD}.{name}"
    (spark.createDataFrame(pdf).write.format("delta").mode("overwrite")
        .option("overwriteSchema", "true").saveAsTable(table))
    print(f"  wrote {table:<50} {len(pdf):>5,} rows")

# ---- FP&A ----
save_gold(pd.DataFrame(v["pl_lines"]), "gold_pl_variance")
save_gold(pd.DataFrame(v["monthly"]), "gold_monthly_pl")
save_gold(pd.DataFrame(v["forecast"]), "gold_forecast")

s = v["summary"]
pl_summary = pd.DataFrame([{
    "net_revenue_actual": s["net_revenue"]["actual"], "net_revenue_budget": s["net_revenue"]["budget"],
    "cogs_actual": s["cogs"]["actual"], "cogs_budget": s["cogs"]["budget"],
    "gross_profit_actual": s["gross_profit"]["actual"], "gross_profit_budget": s["gross_profit"]["budget"],
    "opex_actual": s["opex"]["actual"], "opex_budget": s["opex"]["budget"],
    "operating_income_actual": s["operating_income"]["actual"],
    "operating_income_budget": s["operating_income"]["budget"],
    "gross_margin_pct": s["gross_margin_pct"], "operating_margin_pct": s["operating_margin_pct"],
}])
save_gold(pl_summary, "gold_pl_summary")

# ---- Anomaly ----
flags_df = pd.DataFrame(a["flagged"]).copy()
flags_df["flags"] = flags_df["flags"].apply(lambda xs: ", ".join(xs))
save_gold(flags_df, "gold_anomaly_flags")

an_summary = pd.DataFrame([{
    "total_entries": a["total_entries"], "flagged_count": a["flagged_count"],
    "flag_rate_pct": a["flag_rate_pct"],
    **{f"type_{k.lower().replace('-', '_').replace(' ', '_')}": v_
       for k, v_ in a["by_type"].items()},
    "risk_high": a["risk_band"]["High (>=70)"],
    "risk_medium": a["risk_band"]["Medium (40-69)"],
    "risk_low": a["risk_band"]["Low (<40)"],
}])
save_gold(an_summary, "gold_anomaly_summary")

# ---- Reconciliation ----
save_gold(pd.DataFrame(r["breaks"]), "gold_recon_breaks")

ex = r["exposure"]
rc_summary = pd.DataFrame([{
    "erp_lines": r["erp_lines"], "bank_lines": r["bank_lines"],
    "match_rate_pct": r["match_rate_pct"], "auto_cleared_pct": r["auto_cleared_pct"],
    "break_count": r["break_count"],
    **{f"count_{k}": v_ for k, v_ in r["counts"].items()},
    **{f"exposure_{k}": v_ for k, v_ in ex.items()},
}])
save_gold(rc_summary, "gold_recon_summary")

# COMMAND ----------

# MAGIC %md ### Preview

# COMMAND ----------

display(spark.table(f"{CATALOG}.{GOLD}.gold_pl_variance"))

# COMMAND ----------

display(spark.table(f"{CATALOG}.{GOLD}.gold_anomaly_flags").orderBy("risk_score", ascending=False))

# COMMAND ----------

# MAGIC %md
# MAGIC Gold layer is ready. Deploy the **Streamlit app** in `databricks/app/` as a
# MAGIC Databricks App, point it at `{catalog}.{gold_schema}`, and grant the app's
# MAGIC service principal `SELECT` on those tables.
