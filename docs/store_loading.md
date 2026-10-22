# Load synthetic store inputs

Merge src and sql into the existing project root. The previously delivered src/generate/generate_store_data.py and validate_store_data.py must remain present. This pack does not replace them.

Uses psycopg[binary] and python-dotenv, already required by load_products.py. If imports fail, install with the virtual environment Python:

```powershell
.\.venv\Scripts\python.exe -m pip install "psycopg[binary]" python-dotenv
```

Start PostgreSQL if needed:

```powershell
docker compose up -d --wait
```

Load:

```powershell
.\.venv\Scripts\python.exe src/load/load_store_data.py --run-id store_v1
```

Repeat the identical command to verify idempotence. Expected first status: loaded. Expected repeat status: already_loaded, rows_added: 0. Both paths check database counts and financial consistency.

Connection settings come from .env and environment variables. Required: POSTGRES_DB, POSTGRES_USER, POSTGRES_PASSWORD. Defaults: POSTGRES_HOST=127.0.0.1 and POSTGRES_PORT=5433. Never commit .env. The loader creates the store schema automatically; do not execute DDL manually first.

One complete run is processed in a single transaction. Source validation runs before connecting. CSVs stream with COPY into temporary text tables; JSONL streams into a temporary JSONB column. Typed inserts enforce keys/checks. All data, temporary-table work, run registration and schema initialization roll back on failure. Database counts, order/item/payment amounts and membership in staging.products must pass before commit. Source hashes are checked again before commit. Temporary tables are dropped automatically.

The database role must have permission to create the store schema/tables and read staging.products. Uses the existing ecommerce_user role in the existing project configuration. Extraction tables remain unchanged.

Products preserve original snapshot strings as source_record JSONB and original observed prices/ratings in separate typed columns. Simulation anchor values/methods are stored separately. Source product rows themselves are not synthetic; is_synthetic_anchor only describes their simulation price anchor. Customers/orders/items/payments are explicitly synthetic.

Database tables: store.ingestion_runs, store.products, store.customers, store.orders, store.order_items, store.payments. Business keys include run_id, allowing audit isolation. Loading two run IDs with repeated IDs is not incremental sales ingestion and would double-count simulated activity if aggregated together. Use run_id filters until a separate incremental design is implemented. A manifest conflict for the same run_id is refused rather than updated or deleted. This loader never truncates tables or overwrites existing runs.

Expected store_v1 counts: 1623 products, 100000 customers, 400000 orders, 1000000 order items, 400000 payments. rows_added counts all five entity tables: 1901623. It does not count the ingestion metadata row. PostgreSQL needs additional disk space for persistent data, indexes, WAL and temporary copies beyond the roughly 500 MiB raw files; actual usage depends on the server.

Use sql/004_verify_store.sql for optional read-only checks. Only completed orders represent realized simulated item revenue. Shipping belongs at order grain; do not repeat/sum it for each line. These are fictional transactions, not Amazon sales.

Validation performed here: PostgreSQL syntax parsed for all statements, CSV/JSON COPY plumbing exercised on the 10000-line sample, fresh-load/repeat/conflict/count-failure paths checked using a cursor test double. No live PostgreSQL server was available in the authoring environment. The first execution on your PostgreSQL instance is the live integration check; failure rolls the transaction back. The full million-row generator has already passed separately.

Next stage after successful load: construct line-grain FactSales and DimProduct/DimCustomer/DimDate with documented revenue rules, followed by incremental loading and Airflow orchestration.
