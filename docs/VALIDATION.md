# Validation evidence

Recorded user execution on Docker PostgreSQL 16 / Airflow 3.2.2: baseline generation and load; source and warehouse incremental loads; repeated identical batch and build with zero added rows; exact revenue reconciliation; all reported invalid counts zero; recovery DAG completed with successful_attempt=2, business_state_unchanged=true and verification_read_only=true. Current recorded state: 1,623 products; 102,000 customers; 408,000 orders; 1,020,000 facts; 408,000 payments; item revenue EGP 4,011,356,993.25. Watermark 2026-10-23T22:58:37Z reflects synthetic event time, not wall-clock execution time.

These statements summarize provided logs/screenshots. Original full logs and credentials are not distributed. Existing historical *_test_results.json files describe the earlier tests identified in those reports, not a fresh full-stack test of this archive.

New offline tests execute the generator/validator with a temporary small run and the supplied reviewed catalog. They verify valid generation, deterministic file hashes, detection of byte corruption, rejection of a broken customer foreign key even when the file hash is updated, monetary validation, and duplicate catalog rejection. Temporary outputs are removed. They do not connect to PostgreSQL or run the Airflow scheduler.

The nullable staging schema change requires a fresh-database/live PostgreSQL regression check on the user's stack. No live database or Docker daemon was available in the packaging environment. Do not interpret compilation/offline tests as database integration tests.

Submission evidence to retain: DAG pipeline graph; repeat-run zero additions; retry task attempts 1 and 2; final quality logs; source/warehouse counts and revenue. A report and presentation are separate final deliverables.
