# Location model upgrade (1.1.0)

## Design and scope

The physical warehouse adds a shared location dimension (unique country + city),
and a non-null customer location foreign key. Location is customer residence,
not order delivery location. Current customer locations are immutable (SCD0).
A future address-change policy requires a separate design.

Power BI uses five `reporting` views: dim_location -> dim_customer -> fact_sales,
plus dim_product and dim_date -> fact_sales. Relationships are active, single,
one-to-many from dimensions toward sales. Never connect location directly to sales
in addition to the customer path.

Internal warehouse city/country and provenance columns remain for compatibility
with existing source reconciliation and Airflow diagnostics. They are excluded
from the clean customer/product/fact reporting interfaces. No existing columns,
keys, files or sales are deleted. No transactions are regenerated.
`catalog_price` is the frozen simulation anchor in EGP, not the actual line selling
price. `unit_price` remains the transaction price. Ratings retain source values
and NULLs; no fabricated reviews or ratings. Store transactions are simulated;
this disclosure remains necessary in the README and report subtitle.

`run_id` intentionally remains in the views for run isolation. Filter store_v1
in the three run-scoped queries before hiding run_id in Power BI. Location/date
are shared dimensions. Catalog counts do not automatically change with fact dates.

## 1. Pause and back up

Pause project DAGs and wait for running tasks to finish. Keep Postgres running.
Save the PBIX under a new filename. Use the project folder that owns the running
Compose deployment for the backup commands below. If your submission copy does
not own it, run these Docker commands from the original project folder. Do not
start a second Compose stack or remove volumes.

PowerShell (pg_dump writes inside container, avoiding PowerShell binary redirects):

```powershell
New-Item -ItemType Directory -Force backups | Out-Null
docker compose exec -T postgres pg_dump -U ecommerce_user -d ecommerce -Fc -f /tmp/before_location.dump
docker compose exec -T postgres pg_restore --list /tmp/before_location.dump
docker compose cp postgres:/tmp/before_location.dump ./backups/before_location.dump
```

Check each command succeeds and the local backup is nonempty. Listing checks the
archive header/catalog, not a full restore test. Keep backups out of Git.

## 2. Install the patch

ZIP contains only changed/new files, not a replacement project. Copy its `sql`,
`src`, `tests` and `docs` contents into the matching project folders, overwriting
only included files. Retain .env, raw data, Docker volumes and other code.

IMPORTANT: Airflow mounts ./src and ./sql from its Compose project folder. If you
use a separate submission copy, apply the patch to BOTH that copy and the original
mounted project folder before resuming DAGs. No image rebuild is needed for these
bind-mounted Python/SQL changes. No DAG code is modified.

From the patched Python project folder with the working .env:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py" -v
.\.venv\Scripts\python.exe src/warehouse/migrate_location_model.py --run-id store_v1
```

The migration uses the existing loader lock and one database transaction. It copies
existing run fact rows into a temporary table and compares every row with EXCEPT ALL,
preserves product/customer keys, runs existing source quality checks, validates
location assignments, and reconciles reporting joins. A failure rolls back the
migration transaction. Temporary copies require free disk space; run while loads
are paused. It does not replace a database backup.

Success must include:
- model_version: 1.1.0
- all quality counts zero, including invalid_locations
- existing_fact_rows_unchanged: true
- dimension_keys_preserved: true
- reporting_joins_reconciled: true
- committed: true

Repeat the ordinary builder to verify idempotence:

```powershell
.\.venv\Scripts\python.exe src/warehouse/build_warehouse.py --run-id store_v1
```

All five rows_added entries should be zero. The ordinary builder now maintains
locations for newly loaded customers as well. It accepts the known 1.0.0 model
for this additive upgrade and rejects unknown model versions. The migration may
backfill existing customer locations across all runs, without changing sales.

## 3. Update Power BI without losing sales measures

Work on the saved copy. Open Transform data. Edit existing warehouse queries to
navigate to the corresponding `reporting` views (Kind may change Table -> View).
Keep the existing Power BI query names (`warehouse fact_sales`, etc.) so sales
measure references stay valid. Inspect Applied Steps: old column-select/type steps
may refer to removed fields and must be updated.

Import reporting.dim_location, rename its query `warehouse dim_location`.
Filter run_id = store_v1 in fact_sales, dim_customer and dim_product.

Before Apply, remove or rewrite calculated columns/measures referencing removed
source fields. Location refs move from customer[city/country] to location[city/country].
Product refs map as follows:

| Old column | New reporting column |
| --- | --- |
| simulation_anchor_price | catalog_price |
| observed_rating | rating |
| observed_ratings_count | ratings_count |
| observed_currency | currency (EGP anchor currency; not observed currency) |
| observed_price | no equivalent; source-price coverage metrics leave this report |
| simulation_anchor_method | not exposed; remove source-method analysis measures |

Remove Observed Price Availability; redefine Observed Rating Band using rating
and rename to Rating Band if needed. Remove source-price/anchor-method coverage
and provenance measures from this business report, including any color measures
that depend on them. Keep provenance analysis in the internal model if needed.
Fact price_method/is_synthetic are not exposed; remove their dependent measures.
Revenue, quantity, order status, date and monetary columns retain their names.

Preserve existing customer/product/date -> fact relationships. Add:
`warehouse dim_location[location_key]` (1) ->
`warehouse dim_customer[location_key]` (*), Single.

Replace City slicers and charts with location[city]. For City Revenue Highlight FX,
replace both occurrences of customer[city] with location[city]. Country likewise.
Hide run_id and technical keys after setup; hiding does not replace filtering.
Validate total revenue/order counts match the pre-upgrade report, and city/date/
category slicers work. Store_v1 last recorded total was 1,020,000 lines and
EGP 4,011,356,993.25; use actual pre-upgrade totals if new batches were loaded.

## 4. Resume and version control

Resume the project DAGs only after confirming mounted code is patched. Run a
connection/reconciliation diagnostic first. Subsequent incremental builds maintain
locations automatically. Retest recovery DAG only with other writers idle.

```powershell
git diff --stat
git add sql/005_warehouse_schema.sql sql/006_build_warehouse.sql sql/011_location_reporting.sql src/warehouse/build_warehouse.py src/warehouse/migrate_location_model.py tests/test_location_model_contract.py docs/LOCATION_MODEL_UPGRADE.md
git commit -m "Add customer location dimension and clean reporting views"
```

Do not revert only the code to model 1.0.0 after upgrading the database: old version
checks will reject 1.1.0. Restore a tested backup in an isolated database if rollback
is necessary; do not automatically overwrite the working database.

## Validation performed on the delivered patch

Six existing offline data-contract tests passed. Python warehouse scripts compiled.
Additional static SQL/contract checks are included. This environment has no running
PostgreSQL or Docker: live SQL execution, upgrade of the million-row database,
Airflow task execution and Power BI refresh must be verified on the user's PC.
