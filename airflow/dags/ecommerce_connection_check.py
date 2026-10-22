"""Manual, read-only Airflow environment check for the existing ecommerce project."""
from datetime import datetime,timedelta,timezone
from airflow.sdk import DAG,Param,task,get_current_context


def project_run():
    from orchestration.diagnostics import validate_run_id
    return validate_run_id(get_current_context()['params']['project_run_id'])


with DAG(
    dag_id='ecommerce_connection_check',
    description='Read-only check of Docker networking, project source tables and warehouse reconciliation.',
    start_date=datetime(2026,10,1,tzinfo=timezone.utc),
    schedule=None,
    catchup=False,
    max_active_runs=1,
    max_active_tasks=1,
    default_args=dict(owner='ecommerce-project',retries=2,retry_delay=timedelta(seconds=30),execution_timeout=timedelta(minutes=5)),
    params={'project_run_id':Param('store_v1',type='string',pattern=r'^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$')},
    tags=['ecommerce','setup','read-only'],
) as dag:
    @task(task_id='check_connection')
    def connection():
        from orchestration.diagnostics import check_connection
        return check_connection(project_run())

    @task(task_id='read_source_counts')
    def source_counts(connection_info):
        from orchestration.diagnostics import read_store
        return read_store(connection_info['run_id'])

    @task(task_id='read_warehouse_counts')
    def warehouse_counts(source):
        from orchestration.diagnostics import read_warehouse
        return read_warehouse(source)

    @task(task_id='verify_counts_and_revenue')
    def verify(snapshot):
        from orchestration.diagnostics import verify_snapshots
        return verify_snapshots(snapshot)

    verify(warehouse_counts(source_counts(connection())))
