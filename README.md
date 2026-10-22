# E-commerce Data Pipeline

Real Amazon product snapshots combined with explicitly fictional store transactions, loaded into PostgreSQL and a star schema and orchestrated with Airflow.

## Verified project scale

User-provided Docker/Airflow execution evidence: 1,623 simulation products, 102,000 customers, 408,000 orders, 1,020,000 order lines and 408,000 payments after `inc_001` and `inc_002`. FactSales retains **all order statuses**, so its row count is not the count of completed sales. Realized simulated item revenue is EGP 4,011,356,993.25, excluding shipping. This describes the recorded project execution, not an independent database query from this archive.

Customers, orders, payments and sale prices are fictional. Product ratings/counts are observed aggregate metadata, not review text or real customer identities. Categories are project classifications inferred from titles, not official Amazon taxonomy. Source NULLs are preserved. Missing prices use labelled simulation anchors; the 3D controller fallback of EGP 5,000 is an arbitrary assumption.

## Contents

`src/`: collectors, transforms, generators, validators, loaders, warehouse and orchestration helpers. `sql/`: schema, model and quality checks. `airflow/dags/`: connection, incremental pipeline and retry demonstration. `data/reference/`: reviewed catalog and decisions. `docs/architecture.md` and `docs/erd.md`: diagrams. `tests/`: executable offline data-contract tests. Existing stage-specific documentation remains in `docs/`.

## Existing Windows project

Keep the original working project and its database volumes. Compare and merge this package's source, SQL, tests and documentation into that project. Keep your existing `.env`, `airflow/local.env`, Airflow password configuration and data directories. Do not change the existing Compose project directory/name or remove volumes. See `docs/CHANGES.md` for the small schema fix.

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## New installation: baseline

Python 3.11 and Docker Desktop with Linux containers are required. In a new directory:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
# Edit .env with your local password before continuing.
docker compose up -d --wait
.\.venv\Scripts\python.exe src/generate/generate_store_data.py --run-id store_v1
.\.venv\Scripts\python.exe src/load/load_store_data.py --run-id store_v1
.\.venv\Scripts\python.exe src/warehouse/build_warehouse.py --run-id store_v1
```

The included reviewed catalog makes generation independent of a fresh Amazon request. Default generation produces 100,000 customers, 400,000 orders and 1,000,000 order lines. It creates full files locally; this archive does not contain those large files. Immutable run folders must not be overwritten; use a new run ID for a separate simulation. Baseline metadata in `docs/evidence/baseline_metadata` is reference evidence, not a complete loadable batch.

For a smaller trial, supply `--run-id demo_v1 --customers 1000 --orders 4000 --items 10000` to the generator, then use `demo_v1` for loading/building. For collection, install Chrome and follow the collector documentation/source CLI; Amazon availability can change and collection is not guaranteed. No new live scraping was performed for this package.

## Incremental processing

```powershell
.\.venv\Scripts\python.exe src/generate/generate_incremental_batch.py --run-id store_v1 --batch-id inc_001
.\.venv\Scripts\python.exe src/load/load_incremental_batch.py --run-id store_v1 --batch-id inc_001
.\.venv\Scripts\python.exe src/warehouse/build_warehouse.py --run-id store_v1
```

Each default batch adds 1,000 customers, 4,000 orders and 10,000 lines. Generation reads committed parent state; stale parent snapshots are refused. Repeating an identical committed batch adds no business rows. Source loading and warehouse building use separate transactions: retrying can resume the warehouse after a successful source commit. This is append-only loading, not general CDC, late-arrival handling or order-status updates.

## Airflow

Follow `docs/airflow_setup.md` for preparing local credentials and building the stack. Entry commands:

```powershell
.\.venv\Scripts\python.exe scripts/prepare_airflow.py
.\.venv\Scripts\python.exe scripts/airflow_stack.py build
.\.venv\Scripts\python.exe scripts/airflow_stack.py init
.\.venv\Scripts\python.exe scripts/airflow_stack.py up
```

Open http://127.0.0.1:8080. All DAGs are **manual**, with no scheduled daily collection. `ecommerce_incremental_pipeline` runs Extract → Validate → Transform → Load → Quality Check. Extract generates fictional incremental files from the frozen scraped catalog. Pandas joins produce fact candidates verified against committed SQL facts. Use a new batch ID for new data; repeat the same ID to demonstrate no duplicate loading. `ecommerce_recovery_check` intentionally fails once and retries after 30 seconds; keep other writers idle during this check.

## Verification and reporting

Run `python -m unittest discover -s tests -v` for offline tests. SQL quality checks are run by the loaders/build script before commit. `warehouse.v_sales_detail` and `warehouse.v_monthly_category_sales` support reporting. Filter by run ID. Sum `realized_net_amount` for item revenue; do not add shipping once per line or add distinct category order counts across categories. Product/customer dimensions are fixed per-run snapshots, not SCD Type 2.

See `docs/VALIDATION.md` for the boundary between local automated tests and user-supplied live execution evidence. Raw logs, credentials, virtual environments and generated full datasets are intentionally absent from this distribution. A report/presentation and any additional assessment-specific requirements still need final review against the original guideline.
