# Load inc_001 into its existing parent store_v1

Merge src and sql into the project root. Replace src/load/load_store_data.py when prompted: this updated copy changes the baseline repeat path to use cumulative database counts after committed incremental batches. All other baseline loading behavior stays compatible. Keep all previously delivered generator/validation and warehouse modules. No new Python libraries are required.

## Commands

Load the prepared batch:

```powershell
.\.venv\Scripts\python.exe src/load/load_incremental_batch.py --run-id store_v1 --batch-id inc_001
```

Expected first status: loaded; rows_added customers=1000, orders=4000, order_items=10000, payments=4000. Total new business rows=19000. No new product rows are inserted. Expected cumulative source counts: products=1623, customers=101000, orders=404000, order_items=1010000, payments=404000. The warehouse still has 1000000 fact rows until its builder is run.

Repeat the identical incremental load command. Expected status: already_loaded and all rows_added values zero. It checks the saved manifest hash, every typed value in the historical batch, current cumulative counts/financial consistency and the persisted watermark. Repeating an old batch after later batches is also a no-op.

Refresh the existing warehouse:

```powershell
.\.venv\Scripts\python.exe src/warehouse/build_warehouse.py --run-id store_v1
```

Expected new rows for the first inc_001: dim_product=0, dim_customer=1000, fact_sales=10000 and dim_date=1. Expected totals: 1623 product rows, 101000 customer rows, 1010000 fact rows, 404000 distinct order IDs and 660 dates within the run range. Realized revenue now includes completed incremental orders and must reconcile to the new cumulative source revenue; the earlier 3932789464.25 EGP amount was for the baseline only.

Repeat the warehouse build to verify all added rows return to zero.

Optional baseline idempotence check after appending:

```powershell
.\.venv\Scripts\python.exe src/load/load_store_data.py --run-id store_v1
```

With the included updated file, expected status is already_loaded, rows_added=0, and cumulative counts of 1010000 lines. It validates the immutable original files and checks current cumulative database counts without re-inserting them. Do not replace or regenerate baseline raw files.

## Transaction and audit design

The incremental loader validates files before connecting, then uses the same transaction-scoped advisory lock as the existing loaders and warehouse builder. It re-reads the parent baseline/catalog fingerprints, current cumulative counts, maximum customer/order/payment IDs and latest event timestamp. A fresh batch must match its captured parent state exactly; stale preparation is refused. Two prepared batches with overlapping ID ranges cannot both be accepted. Existing batch IDs with changed manifest contents are refused.

CSV and JSONL inputs stage in temporary tables. Product source snapshots, NULL prices/ratings, simulation anchors and original source_record values must exactly match the parent catalog. No product updates or inserts occur. Only the four new transactional entity tables are inserted. Database PK/FK/check constraints enforce key integrity and arithmetic. Exact typed comparisons verify all new supplied fields against inserted rows, followed by cumulative counts, order/item/payment reconciliation and price-method checks. Source hashes are verified again before commit.

One transaction commits all business rows plus metadata:

- store.incremental_batches: immutable batch manifest hash, baseline hash, complete batch manifest, counts before/after, actual watermark before/after and loaded timestamp.
- store.pipeline_state: committed latest order timestamp, maximum IDs and cumulative counts for the parent run.
- store.ingestion_runs.manifest.expected_counts: cumulative counts for downstream source/warehouse quality checks. baseline_expected_counts preserves original counts; incremental_batches records accepted batch identities and fingerprints.

The store.ingestion_runs.manifest_sha256 column remains the hash of the immutable BASELINE manifest file. Its meaning is baseline provenance, not a hash of the now-enriched database JSON metadata. Each incremental manifest has a separate immutable hash in the ledger. Original data files and baseline source hash are never changed. The existing warehouse build source hash remains compatible and its exact source-vs-target checks validate the appended data.

The committed event watermark is SELECT MAX(ordered_at_utc) after insertion, not the generation manifest's configured end time. Next batch generation reads actual maxima again. Missing watermark state is initialized by the first successful incremental append; before that, the baseline has no pipeline_state row. A failed transaction leaves no partial batch, advanced counts, ledger row or advanced watermark.

Raw-to-store append and store-to-warehouse build are separate transactions. If the build fails after a successful store append, repeat the builder; do not regenerate/reload the business data. The next generator checks that warehouse counts catch up to store counts before preparing more data.

## Scope

This is an append-only new-customer/new-order increment within the same parent run. It demonstrates batch idempotence, disjoint IDs, newer event dates, committed event-time watermark, audit ledger and incremental insertion of missing warehouse keys. It does not implement late arrivals, CDC updates, deletion propagation, source order-status updates, or SCD Type 2. Do not combine unrelated synthetic run IDs as increments. Continue applying the completed-only realized item revenue rule and keep order shipping out of the line fact.

The connection uses the existing .env and environment variables. Keep the working Docker shm_size setting. Schema 009 is executed by the loader automatically; no manual DDL steps are required. sql/010_review_incremental.sql contains optional read-only audit queries.

## Validation performed

Executed the actual Python load/build functions and supplied SQL against a PGlite PostgreSQL engine through a local test adapter. The adapter emulated the psycopg cursor/result interface; CSV/JSON COPY payloads were inserted into equivalent temporary tables for this test. Production COPY reuses the previously working loader helper.

The fixture loaded 10000 baseline lines and two increments of 100 lines each. Passed: first append; repeat adds zero; baseline reload after append; warehouse adds only missing rows and repeats with zero; stale parent snapshot refusal; same batch ID with changed manifest refusal; forced rollback after apply_batch returned (business rows, metadata, ledger and watermark all rolled back); second append; repeat of an older batch after the second append. Schema and emitted statements were PostgreSQL-parsed.

This is a focused integration fixture, not a full million-row benchmark or a live test against the user's Docker PostgreSQL. The command on the user's machine is the full-size integration check.
