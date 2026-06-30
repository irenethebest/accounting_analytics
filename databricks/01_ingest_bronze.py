# Databricks notebook source
# MAGIC %md
# MAGIC # 01 · Ingest — Synthetic Finance Data → Bronze Delta Tables
# MAGIC
# MAGIC Runs the from-scratch data engine (`data_engine/generate_data.py`) and lands
# MAGIC the raw datasets as **managed Delta tables** in Unity Catalog.
# MAGIC
# MAGIC **Bronze tables created** in `{catalog}.{schema}`:
# MAGIC `chart_of_accounts`, `journal_entries`, `budget`, `erp_cash_ledger`,
# MAGIC `bank_statement` (+ `_anomaly_truth`, `_recon_truth` for validation).
# MAGIC
# MAGIC No external data is read — everything is generated deterministically (seed=42).

# COMMAND ----------

# MAGIC %md ### Parameters

# COMMAND ----------

dbutils.widgets.text("catalog", "finance_portfolio", "Unity Catalog")
dbutils.widgets.text("schema", "bronze", "Schema (bronze layer)")

CATALOG = dbutils.widgets.get("catalog")
SCHEMA = dbutils.widgets.get("schema")
print(f"Target: {CATALOG}.{SCHEMA}")

# COMMAND ----------

# MAGIC %md ### Make the repo importable and load the data engine

# COMMAND ----------

import os
import sys

# Add the repo root (parent of the /databricks folder) to the path so we can
# import the shared generator used by the local app too.
REPO_ROOT = os.path.abspath(os.path.join(os.getcwd(), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)
print("Repo root:", REPO_ROOT)

from data_engine.generate_data import generate, COMPANY, YEAR  # noqa: E402

# COMMAND ----------

# MAGIC %md ### Create catalog / schema if needed

# COMMAND ----------

spark.sql(f"CREATE CATALOG IF NOT EXISTS {CATALOG}")
spark.sql(f"CREATE SCHEMA IF NOT EXISTS {CATALOG}.{SCHEMA}")
spark.sql(f"USE CATALOG {CATALOG}")
spark.sql(f"USE SCHEMA {SCHEMA}")

# COMMAND ----------

# MAGIC %md ### Generate the dataset (pandas) and write Delta tables

# COMMAND ----------

from pyspark.sql import functions as F

data = generate()  # dict[str, pandas.DataFrame]

# Tables to publish as bronze (the two _truth frames are written too, for validation)
TABLES = [
    "chart_of_accounts", "journal_entries", "budget",
    "erp_cash_ledger", "bank_statement", "_anomaly_truth", "_recon_truth",
]

# Columns to cast from string -> proper date/timestamp for nicer downstream SQL
DATE_COLS = {"date"}
TS_COLS = {"posted_ts"}

for name in TABLES:
    pdf = data[name]
    sdf = spark.createDataFrame(pdf)
    for c in sdf.columns:
        if c in DATE_COLS:
            sdf = sdf.withColumn(c, F.to_date(c))
        elif c in TS_COLS:
            sdf = sdf.withColumn(c, F.to_timestamp(c))
    table = f"{CATALOG}.{SCHEMA}.{name}"
    (sdf.write.format("delta").mode("overwrite")
        .option("overwriteSchema", "true").saveAsTable(table))
    print(f"  wrote {table:<55} {sdf.count():>6,} rows")

# COMMAND ----------

# MAGIC %md ### Integrity check — the ledger must balance

# COMMAND ----------

chk = spark.sql(f"""
  SELECT ROUND(SUM(debit), 2) AS total_debit,
         ROUND(SUM(credit), 2) AS total_credit,
         COUNT(DISTINCT entry_id) AS entries,
         COUNT(*) AS lines
  FROM {CATALOG}.{SCHEMA}.journal_entries
""")
display(chk)

row = chk.first()
assert abs(row.total_debit - row.total_credit) < 0.01, "GL does not balance!"
print(f"GL balanced ✓  ({row.entries:,} entries / {row.lines:,} lines)")

# COMMAND ----------

# MAGIC %md
# MAGIC Bronze layer is ready. Next run **`02_build_gold`** to compute the FP&A,
# MAGIC anomaly, and reconciliation result tables.
