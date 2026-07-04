# Databricks notebook source
# MAGIC %md
# MAGIC # 03 · Email Anomaly Digest (CSV attachment via SendGrid)
# MAGIC
# MAGIC Reads the gold anomaly table, writes a CSV, and emails it as an attachment
# MAGIC using the **SendGrid API** (HTTPS — works on serverless where SMTP is blocked).
# MAGIC
# MAGIC Runs as the third task of the pipeline, after `build_gold`.
# MAGIC
# MAGIC **Prerequisites**
# MAGIC - A verified SendGrid sender and an API key (Mail Send).
# MAGIC - The API key stored in a Databricks secret scope:
# MAGIC   `databricks secrets put-secret <scope> sendgrid_api_key --string-value "SG.xxx"`
# MAGIC - The job's run-as identity (the CI service principal) has **READ** on that scope.

# COMMAND ----------

dbutils.widgets.text("catalog", "accounting_analytics", "Catalog")
dbutils.widgets.text("gold_schema", "dev", "Gold schema")
dbutils.widgets.text("email_to", "jchoi867@gatech.edu", "Recipient(s), comma-separated")
dbutils.widgets.text("email_from", "jchoi867@gatech.edu", "From (must be a verified SendGrid sender)")
dbutils.widgets.text("secret_scope", "email", "Secret scope holding the API key")
dbutils.widgets.text("secret_key", "sendgrid_api_key", "Secret key name")
dbutils.widgets.text("high_risk_only", "false", "Attach only high-risk (score>=70)? true/false")

catalog = dbutils.widgets.get("catalog")
gold = dbutils.widgets.get("gold_schema")
to_list = [e.strip() for e in dbutils.widgets.get("email_to").split(",") if e.strip()]
sender = dbutils.widgets.get("email_from")
scope = dbutils.widgets.get("secret_scope")
key = dbutils.widgets.get("secret_key")
high_only = dbutils.widgets.get("high_risk_only").lower() == "true"

# COMMAND ----------

# MAGIC %md ### Read the anomaly table and build the CSV + summary

# COMMAND ----------

import base64
import json

import requests

pdf = spark.table(f"{catalog}.{gold}.gold_anomaly_flags").toPandas()
if "risk_score" in pdf.columns:
    pdf = pdf.sort_values("risk_score", ascending=False)

if high_only:
    pdf = pdf[pdf["risk_score"] >= 70]

n_total = len(pdf)
n_high = int((pdf["risk_score"] >= 70).sum()) if "risk_score" in pdf.columns else 0
top_amt = float(pdf["amount"].abs().max()) if n_total and "amount" in pdf.columns else 0.0

csv_str = pdf.to_csv(index=False)

# A small HTML summary for the email body
rows = "".join(
    f"<tr><td>{r.risk_score}</td><td>{r.entry_id}</td><td>{r.account_name}</td>"
    f"<td style='text-align:right'>${r.amount:,.0f}</td><td>{r.flags}</td></tr>"
    for r in pdf.head(10).itertuples()
)
summary_html = f"""
<div style="font-family:Arial,sans-serif">
  <h2 style="color:#0f2742;margin-bottom:4px">Accounting Anomaly Digest</h2>
  <p style="color:#555">{catalog}.{gold} &middot; generated automatically by the pipeline</p>
  <p><b>{n_total}</b> flagged journal entries this run &mdash; <b>{n_high}</b> high-risk (score &ge; 70).
     Largest flagged amount: <b>${top_amt:,.0f}</b>.</p>
  <p>Top 10 by risk (full detail attached as CSV):</p>
  <table cellpadding="6" style="border-collapse:collapse;font-size:13px">
    <tr style="background:#0f2742;color:#fff"><th>Risk</th><th>Entry</th><th>Account</th><th>Amount</th><th>Flags</th></tr>
    {rows}
  </table>
</div>
"""

print(f"Prepared digest: {n_total} rows, {n_high} high-risk")

# COMMAND ----------

# MAGIC %md ### Send via SendGrid

# COMMAND ----------

api_key = dbutils.secrets.get(scope=scope, key=key)

payload = {
    "personalizations": [{"to": [{"email": e} for e in to_list]}],
    "from": {"email": sender},
    "subject": f"[Accounting Analytics] {n_total} anomaly flags ({n_high} high-risk)",
    "content": [{"type": "text/html", "value": summary_html}],
    "attachments": [{
        "content": base64.b64encode(csv_str.encode("utf-8")).decode("ascii"),
        "type": "text/csv",
        "filename": f"anomaly_flags_{gold}.csv",
        "disposition": "attachment",
    }],
}

resp = requests.post(
    "https://api.sendgrid.com/v3/mail/send",
    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    data=json.dumps(payload),
    timeout=30,
)

print("SendGrid response:", resp.status_code, resp.text[:400])
assert resp.status_code in (200, 202), f"SendGrid send failed: {resp.status_code} {resp.text}"
print(f"Emailed anomaly digest ({n_total} rows) to {', '.join(to_list)}")
