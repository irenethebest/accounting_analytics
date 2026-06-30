# Databricks Lakehouse Deployment

This folder turns the project into a Databricks Lakehouse pipeline:

```
Synthetic generator  →  Bronze Delta tables  →  Gold result tables  →  Streamlit App
(data_engine)           (01_ingest_bronze)      (02_build_gold)        (app/)
```

The notebooks reuse the **same** Python code as the local app
(`data_engine/generate_data.py` and `modules/*.py`) — nothing is duplicated.

## Prerequisites
- A Databricks workspace with **Unity Catalog** enabled
- A **SQL Warehouse** (serverless is fine) for the app to query
- Permission to create a catalog/schema (or point the notebooks at an existing one)

## 1. Get the code into Databricks
Add this Git repo as a **Databricks Git folder**
(Workspace → Repos → Add Repo → paste the GitHub URL). The notebooks add the repo
root to `sys.path`, so the shared modules import cleanly.

## 2. Run the pipeline
1. Open **`01_ingest_bronze`**, set the `catalog` / `schema` widgets
   (defaults: `finance_portfolio` / `bronze`), and **Run All**.
   → Creates the bronze Delta tables and asserts the ledger balances.
2. Open **`02_build_gold`**, set `catalog` / `bronze_schema` / `gold_schema`
   (defaults: `finance_portfolio` / `bronze` / `gold`), and **Run All**.
   → Creates the `gold_*` result tables the app reads.

(Optional) Schedule both as a **Databricks Job** with two tasks so the data
refreshes on a cadence.

## 3. Deploy the Streamlit app (Databricks Apps)
1. **Compute → Apps → Create app** (or `databricks apps create meridian-finance`).
2. Point the source at `databricks/app/` (sync via Git folder or
   `databricks sync ./databricks/app <workspace-path>`).
3. **Add a resource**: your SQL Warehouse, with resource **key `sql_warehouse`**
   (this is what `app.yaml`'s `valueFrom: sql_warehouse` injects as
   `DATABRICKS_WAREHOUSE_ID`).
4. If your catalog/schema differ from the defaults, edit the `APP_CATALOG` /
   `APP_GOLD_SCHEMA` values in `app.yaml`.
5. **Grant the app's service principal** read access:
   ```sql
   GRANT USE CATALOG ON CATALOG finance_portfolio TO `<app-service-principal>`;
   GRANT USE SCHEMA  ON SCHEMA  finance_portfolio.gold TO `<app-service-principal>`;
   GRANT SELECT      ON SCHEMA  finance_portfolio.gold TO `<app-service-principal>`;
   ```
6. **Deploy**. Open the app URL.

## Run the app locally (dev / screenshots)
The app is dual-mode. With no Databricks env vars set, it reads the local CSVs in
`../../data` and computes the same results:
```bash
cd databricks/app
pip install -r requirements.txt
streamlit run app.py        # opens with "Local CSVs (dev mode)" in the header
```

## Files
| File | Purpose |
|------|---------|
| `01_ingest_bronze.py` | Generate synthetic data → bronze Delta tables (Unity Catalog) |
| `02_build_gold.py` | Run FP&A / anomaly / recon → flat gold tables |
| `app/app.py` | Streamlit dashboard (Databricks SQL backend + local fallback) |
| `app/requirements.txt` | App Python dependencies |
| `app/app.yaml` | Databricks App config (command, warehouse resource, env) |
