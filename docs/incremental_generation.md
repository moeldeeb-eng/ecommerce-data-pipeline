# Prepare the first incremental synthetic batch

Copy src into the existing project root, merging folders. Existing files are not replaced. Requires the previously delivered generate_store_data.py and validate_store_data.py plus psycopg[binary] and python-dotenv (already used by the existing loaders).

```powershell
.\.venv\Scripts\python.exe src/generate/generate_incremental_batch.py --run-id store_v1 --batch-id inc_001
```

Defaults: 1000 new customers, 4000 new orders, exactly 10000 new lines, and 4000 payment outcomes. No product additions. Products and their simulation price anchors use the immutable baseline catalog snapshot.

Outputs:

```text
data/raw/incremental_runs/store_v1/inc_001/
  catalog_snapshot.csv
  product_price_anchors.csv
  customers.csv
  orders.csv
  order_items.csv
  payments.jsonl
  manifest.json
  validation_report.json
```

Separate validation, without connecting to PostgreSQL:

```powershell
.\.venv\Scripts\python.exe src/generate/validate_incremental_data.py --run-id store_v1 --batch-id inc_001
```

## Scope and safety

This stage only reads PostgreSQL and creates files. The read transaction is explicitly REPEATABLE READ and READ ONLY. Source and warehouse counts must match ingestion metadata; the parent warehouse must already contain all current source rows. The baseline catalog fingerprint is verified. The next loader will recheck parent state under its write lock before inserting anything; this preparation step does not reserve IDs in the database.

The generator reads maximum numeric customer, order and payment IDs from store tables for the parent run and offsets all new IDs beyond those maxima. No hardcoded production start IDs. Defaults for the already loaded parent produce:

- Customers C000100001 through C000101000.
- Orders O000400001 through O000404000.
- Payments P000400001 through P000404000.

Every incremental order references a customer created in this batch; this first demonstration covers append-only new customer/order events. It does not yet simulate existing-customer purchases, corrections, late arrivals, cancellations of existing orders, CDC or SCD Type 2 changes. Orders have 1–5 distinct products per order as before. Not every product must occur in the 10000-line batch.

The event-time lower bound is midnight UTC on the next calendar date after the latest parent order; new orders span that fictional day through 23:00 UTC. The parent event-time watermark is recorded in manifest.parent_state. The actual order maximum after loading, not the configured end bound, must be used as the committed new watermark. All new customer registration timestamps are the preceding midnight. Payment timestamps follow their corresponding orders. Dates are fictional simulation events, not claims of actual observed sales; the next simulation day may be in the future relative to the real clock.

The manifest records parent run, batch identity, parent source manifest/catalog hashes, exact parent counts/ID maxima/watermark, seed, offsets, expected incremental counts and hashes for all data files. Source catalog metadata stays unchanged. New prices remain synthetic and all source price nulls remain null.

The generator reuses the already tested baseline generation logic in a private temporary folder, maps IDs and dates, updates hashes and validates before publishing the final batch folder. Existing batch folders are refused rather than overwritten. The separate validator checks offset-aware key sequences, unique line grain, product/customer/payment relationships, dates newer than the recorded parent watermark, price rules, exact arithmetic and file integrity.

Do not prepare several batches against the same parent state for concurrent loading: they would allocate the same next ID range. Prepare/load/build each batch in sequence. If parent data changes after preparation, the upcoming loader will reject stale metadata. Do not run the existing load_store_data.py on this batch as a separate synthetic run. This batch belongs to store_v1; the incremental loader will append it there.

After successful loading and warehouse refresh, expected totals are 101000 customers, 404000 orders, 1010000 lines, 404000 payments and 1623 products. Those totals have not changed merely by running this generator.

## Validation performed

Generated and validated a 1000-customer/4000-order/10000-line fixture using the supplied 1623-product catalog and a recorded parent state with the real expected ID offsets. All keys were disjoint from baseline, dates were strictly newer, amounts/relationships reconciled, seed reproduction generated identical data file hashes, existing batch paths were refused, corrupt offset metadata was rejected, and the source catalog fingerprint stayed unchanged. Python compiled and static PostgreSQL statements parsed. The database state reader was not connected to the user's live server in the authoring environment; the command on the user's machine performs that read-only integration check.

This package is preparation and validation only. A subsequent incremental loader will add a batch ledger, stale-state checks, transactionally update cumulative counts/watermark and preserve baseline reload idempotence. It will then feed the existing missing-key warehouse builder.
