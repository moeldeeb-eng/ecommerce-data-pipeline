# Five-stage incremental pipeline DAG

Merge the included airflow, src and docs folders into the project root. They add a new DAG and orchestration module; existing loaders, warehouse builder, Docker configuration and diagnostic DAG remain in use. The existing image already contains the required Pandas/psycopg dependencies, so no image rebuild is needed. Bind mounts expose the new files automatically; allow the DAG processor to import them.

## Run

Open http://localhost:8080, find ecommerce_incremental_pipeline, unpause it and Trigger with:

```json
{"project_run_id":"store_v1","batch_id":"inc_002"}
```

This DAG writes new fictional business records when the batch has not already been loaded. It is separate from the earlier read-only ecommerce_connection_check. It runs manually, with catchup disabled, one active run, serial tasks, two retries per task and 30-second retry delay. Each task has a 30-minute execution timeout.

For the first new inc_002 after the currently completed inc_001, defaults add 1000 customers, 4000 orders, 10000 lines and 4000 payment outcomes, with no new products. Expected cumulative source/warehouse state: 1623 products, 102000 customers, 408000 orders, 1020000 fact rows, 408000 payments and 661 calendar dates in the run range. Actual completed-order revenue is calculated from the generated batch and reconciled, not assumed to be actual Amazon sales.

After all five tasks succeed, trigger again with the SAME run and batch IDs. It must reuse the original prepared batch, the incremental loader must report zero added rows, the warehouse builder must add zero rows, and the final totals must stay unchanged. A different new batch ID intentionally prepares a different increment and may add more business records.

## Stages

| Task | Work |
| --- | --- |
| extract | Locate the prepared immutable CSV/JSONL batch; if absent, call the existing generator using current parent DB state. Identify customer/order/item/payment inputs and the frozen, previously scraped product snapshot. |
| validate | Run the existing offset-aware input validator: hashes, keys, dates, quantities, currency, statuses and order/item/payment arithmetic. |
| transform | Pandas joins lines to orders, customers and product snapshot with many-to-one checks. Derive UTC date keys, realized units and completed-only item revenue. Publish candidate line rows and a signed transformation report. |
| load | Check that the transformation report/artifact still match the original batch; invoke the transaction-safe incremental source loader, then the existing warehouse builder. |
| quality_check | Verify batch ledger fingerprint and compare every committed batch fact row to the Pandas candidate contract using a canonical ordered row signature. Then reconcile cumulative source/warehouse counts, unique orders, date coverage, baseline fingerprint and realized item revenue. |

Amazon scraping has already been performed upstream in the local browser collector. This DAG does not run a fresh browser scrape or bypass Amazon access restrictions. The extract stage consumes that frozen product snapshot and prepares/reads fictional transactional files. This is file-source orchestration with a separately collected product source, not a claim that Airflow freshly scraped Amazon.

Pandas produces an independent fact-row contract. Surrogate dimension key mapping and final table inserts still occur in the existing SQL warehouse builder inside the load task. The candidate artifact gates loading and is consumed by final line-level verification; the DAG does not replace the already tested SQL model or change source prices.

## Outputs and lineage

Raw files:

```text
data/raw/incremental_runs/store_v1/inc_002/
```

Transformation/quality artifacts:

```text
data/processed/incremental_runs/store_v1/inc_002/
  fact_candidates.csv
  transform_report.json
  pipeline_quality_report.json
```

The descriptor passed between tasks contains only parent run ID, batch ID and immutable raw manifest SHA256. No datasets or database credentials enter XCom. Transformation reports retain the raw fingerprint, candidate file hash, row signature, expected line count, order ID boundaries and batch realized revenue. The row signature includes order/line, ASIN/customer ID, UTC date key, status, ordered/realized units, net/realized amount, currency, price method and synthetic marker. Amounts normalize to exact two-decimal strings; integers and booleans normalize consistently across CSV and PostgreSQL types.

The generator seed is derived deterministically from run and batch ID. New event dates are the next fictional simulation day after the committed order watermark. Source observed price/rating nulls remain unchanged. These dates can describe future simulated activity relative to the actual clock.

Artifacts are prepared in a temporary folder and replaced after successful transformation. Changed candidate bytes or raw manifest block the load stage. Re-running transformation reproduces its content for the same inputs. Raw inputs stay immutable. Failed runs retain Airflow logs and raw files for diagnosis.

## Failure/retry behavior

The existing source loader commits business rows, ledger, cumulative counts and actual watermark in one transaction. Warehouse building is a separate transaction. If the source append succeeds and the warehouse build fails, retrying the load task first sees the committed batch as already_loaded, then retries only the missing warehouse work. It does not regenerate the raw data or insert duplicate source rows.

If quality_check fails after loading, inspect the mismatch. A failed quality check does not undo already committed source/warehouse transactions; rerun the same batch after resolving the error. Never choose a new batch ID merely to retry a failure. Loader fingerprints, stale parent-state checks and immutable batch identities prevent conflicting payloads from being silently overwritten.

DAG max_active_runs=1 serializes this DAG's runs, while the loaders share a DB advisory lock with manual load/build operations. Avoid preparing several different batches concurrently against identical parent state: the stale-state guard will reject overlapping allocations.

To examine parse problems:

```powershell
.\.venv\Scripts\python.exe scripts/airflow_stack.py import-errors
```

Task logs are in the Airflow UI and airflow/logs. The full pipeline is not scheduled automatically yet. Fresh browser collection, late arrivals, order-status updates, CDC and SCD Type 2 remain outside this append-only run.

## Validation

The DAG imported successfully under Airflow 3.2.2 with exactly extract -> validate -> transform -> load -> quality_check, correct dependencies and manual-only configuration. A 10000-line generated increment was validated and transformed; every derived customer key, product link, UTC date and completed-only amount was compared independently to raw rows. Transform output reproduced byte-for-byte. Loader command ordering was checked. A typed warehouse-result test double matched all 10000 candidate rows; a one-cent changed measure was rejected. A changed candidate artifact blocked loading before any subprocess call.

The existing source and warehouse loaders have already run successfully on the user's million-row database and passed repeat checks. This new DAG has not yet been executed end-to-end inside the user's Airflow environment. Its first UI trigger is the live integration check. The quality SQL stream and cumulative helper connection will be exercised against the real PostgreSQL there.
