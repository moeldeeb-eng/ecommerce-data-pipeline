# Automatic retry demonstration

Merge this pack into the existing project root. It uses the installed diagnostics helper and the existing Airflow bind mounts; no image rebuild is required.

1. Keep other data-writing pipeline runs idle during this short check.
2. Open Airflow and choose `ecommerce_recovery_check`. Unpause it, then trigger with `project_run_id=store_v1`.
3. `snapshot_before` reads the current validated source and warehouse state.
4. `fail_once_then_retry` intentionally raises an error on its first attempt. Airflow has one retry configured, with a 30-second delay. Allow the scheduler to retry automatically; do not clear the task or mark it successful.
5. The second attempt succeeds. `verify_recovery` checks that the captured business summary, source fingerprint, incremental ledger count and watermark remain unchanged.

Expected final result: `status=passed`, `successful_attempt=2`, `business_state_unchanged=true`, `verification_read_only=true`. Capture the successful DAG graph and the retry task logs showing both attempts for the project evidence.

All database reads use the existing read-only diagnostics connections. Airflow writes its own task metadata and logs. No orders, customers or sales are generated or loaded by this DAG. The comparison is a state-summary check, not a cryptographic comparison of every business row. Concurrent business-data changes may correctly make this check fail.

Local verification used the actual Airflow 3.2.2 SDK to import the DAG, inspect its dependencies, retry configuration and runtime try-number field, and exercise failure/success and changed-snapshot rejection with fixtures. Automatic scheduling and retry execution must be verified on the running Docker stack; they were not run locally here. See `recovery_test_results.json`.
