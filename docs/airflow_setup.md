# Airflow environment setup and read-only diagnostic DAG

This package adds a local Airflow 3.2.2 / Python 3.11 environment alongside the existing ecommerce project. It uses LocalExecutor with two worker slots. Airflow metadata has its own PostgreSQL service and named volume. The existing ecommerce postgres service and postgres_data volume are not replaced.

Copy compose.airflow.yaml and merge airflow, scripts, src and docs into the project root. Keep the existing compose.yaml, .env, source files and data files. The setup helper creates separate local credentials and appends three ignore entries; it does not edit the ecommerce credentials or Compose file.

Run each command from the project root. Stop and inspect any error before the next command.

```powershell
.\.venv\Scripts\python.exe scripts/prepare_airflow.py
.\.venv\Scripts\python.exe scripts/airflow_stack.py build
.\.venv\Scripts\python.exe scripts/airflow_stack.py init
.\.venv\Scripts\python.exe scripts/airflow_stack.py up
.\.venv\Scripts\python.exe scripts/airflow_stack.py ps
```

The first build downloads the official Airflow image and installs the project dependencies. It can take several minutes. The init action runs only Airflow metadata migrations. The up action starts the API server, scheduler/LocalExecutor and DAG processor, along with their database dependencies, and waits for health checks. The existing ecommerce PostgreSQL may already be running and is reused. Initializing Airflow does not create, clear or reload ecommerce tables.

## Login and run the check

Open http://localhost:8080 . Username: admin.

To view the generated local UI password in your own PowerShell terminal:

```powershell
(Get-Content .\airflow\config\simple_auth_passwords.json | ConvertFrom-Json).admin
```

This reads only the local Airflow UI password, not the ecommerce database password. Login using that value. The password file is reused on later setup runs. Do not delete local credentials to troubleshoot a container: the metadata PostgreSQL volume retains its password and needs the same credentials on restart.

After the DAG processor imports the file (normally within about a minute), locate ecommerce_connection_check. Unpause it, choose Trigger, keep project_run_id=store_v1 and run it. It is manual-only, has no automatic schedule, does not backfill, and permits one active run. Task retries: 2; delay: 30 seconds; timeout: 5 minutes.

Four tasks run sequentially:

1. check_connection opens a read-only connection to the ecommerce database.
2. read_source_counts reads current cumulative source metadata, source counts and completed-order item revenue.
3. read_warehouse_counts reads the warehouse counts, unique orders, baseline fingerprint, date coverage and realized item revenue.
4. verify_counts_and_revenue checks agreement and logs a short JSON summary.

With the currently completed project state, expect products=1623, customers=101000, orders=404000, order_items=1010000, payments=404000; warehouse products=1623, customers=101000 and facts=1010000; distinct orders=404000; date coverage=660 days; realized synthetic item revenue=3971022496.85 EGP. The DAG uses ingestion metadata and current source values rather than hardcoding those counts as future expectations.

This first DAG is a read-only environment/connection check, not the full Extract/Validate/Transform/Load pipeline. It validates counts and revenue, not every field-level business rule. All project database connections are opened with default_transaction_read_only=on and a two-minute SQL statement timeout. Only small JSON summaries enter XCom; datasets stay in project files/PostgreSQL. The Airflow metadata database necessarily records task runs/log metadata, while ecommerce business tables remain unchanged.

Keep project data stable while this diagnostic runs: its source and warehouse snapshots are separate tasks. An unrelated load between tasks can correctly cause their counts to disagree; rerun after the warehouse catches up.

## Connection and storage layout

Host Python uses 127.0.0.1:5433. Airflow containers use postgres:5432 on the existing Compose network. Container environment explicitly sets host/port and inherits only the ecommerce database name/user/password via Compose interpolation. The host .env file is not mounted into the image and not copied during the build.

The wrapper supplies both --env-file .env and --env-file airflow/local.env, then -f compose.yaml -f compose.airflow.yaml. Always use the wrapper for Airflow actions so the same project/network and overlay are used. The unmodified base compose.yaml remains suitable for existing plain docker compose operations on the ecommerce service.

Airflow service image: ecommerce-airflow:3.2.2-python3.11, built from apache/airflow:3.2.2-python3.11. Dockerfile keeps the Airflow version fixed while installing project dependencies against the bundled official Python 3.11 constraints. Psycopg v3 and its binary wheel are explicitly pinned to 3.3.6. No runtime pip installations.

Airflow metadata service: airflow-db, PostgreSQL 16, separate named volume airflow_metadata_data; no host DB port is published. UI binds only 127.0.0.1:8080. Project postgres keeps its already working shm_size=1gb setting from the base file. API/scheduler/DAG processor share JWT and logging secrets. SimpleAuthManager is configured for this local graduation-project setup.

Project src/sql and DAG files are mounted read-only. Project data is mounted for the later pipeline stages; this diagnostic only reads the database. Logs persist under airflow/logs. The config directory is writable because SimpleAuthManager opens its password file in update mode even when users already exist. The preparation helper uses the Windows-compatible container UID 50000 (or the current non-root UID on Linux) and sets local folder/file access accordingly.

Generated airflow/local.env, airflow/config and airflow/logs are ignored by Git. The Docker build context includes only Dockerfile, requirements and constraints, excluding credentials, logs and project data. Preparation refuses incomplete existing service secrets instead of silently rotating them.

## Useful commands

```powershell
.\.venv\Scripts\python.exe scripts/airflow_stack.py import-errors
.\.venv\Scripts\python.exe scripts/airflow_stack.py logs
.\.venv\Scripts\python.exe scripts/airflow_stack.py stop
```

The stop action stops only Airflow components and its metadata database, leaving ecommerce PostgreSQL running. None of the wrapper actions delete volumes. Do not use docker compose down -v on this project to resolve startup errors, as it would delete persistent database volumes.

If port 8080 is already occupied, change only the host-side UI binding in compose.airflow.yaml and the AIRFLOW__API__BASE_URL value to the same port. Leave the internal execution API URL at airflow-api-server:8080/execution/.

The up command can time out while a first startup is still finishing. Inspect ps and logs before retrying; do not remove volumes. If a build step fails, preserve the error text and fix that dependency/network failure before running init/up.

## Validation performed

- Airflow 3.2.2 installed in an isolated Python 3.12 environment and imported this DAG with zero import errors; all four task edges/defaults checked.
- The official Docker tag for Python 3.11 exists for amd64 and arm64; project dependency resolution passed against official Python 3.11 constraints. Installed Python 3.12 test dependencies passed compatibility checks.
- Airflow SimpleAuthManager initialized and read the prepared password-file format successfully.
- The merged base/overlay model passed the official Compose JSON schema. The existing business PostgreSQL service definition stayed identical; dependency references resolved.
- Repeating preparation preserved credentials, baseline .env/compose bytes and ignore entries. Fernet key encoding validated. Wrapper command construction checked for all actions.
- Snapshot reconciliation accepted expected counts/revenue and rejected wrong revenue, distinct-order count, date coverage and invalid run IDs. Diagnostic connections requested read-only mode.

Docker Engine is not available in the authoring environment. No container build/start, Airflow metadata PostgreSQL migration, or connection to the user's live ecommerce database was executed here. The commands on the user's machine provide that integration check. This is environment setup plus a diagnostic DAG; the existing baseline/incremental pipeline stages will be wired into a separate DAG after the check succeeds.

## Primary references

- https://airflow.apache.org/docs/apache-airflow/3.2.2/howto/docker-compose/
- https://airflow.apache.org/docs/apache-airflow/3.2.2/core-concepts/executor/local.html
- https://airflow.apache.org/docs/apache-airflow/3.2.2/core-concepts/auth-manager/simple/index.html
- https://airflow.apache.org/docs/apache-airflow/3.2.2/configurations-ref.html
- https://airflow.apache.org/docs/apache-airflow/3.2.2/installation/prerequisites.html
- https://raw.githubusercontent.com/apache/airflow/constraints-3.2.2/constraints-3.11.txt
- https://github.com/compose-spec/compose-spec/blob/main/schema/compose-spec.json
