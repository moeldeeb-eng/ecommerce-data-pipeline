"""Demonstrate one intentional transient failure and an Airflow retry without loading data."""
from datetime import datetime,timedelta,timezone
from airflow.sdk import DAG,Param,task,get_current_context

with DAG(
    dag_id='ecommerce_recovery_check',
    description='Read-only snapshot, intentional first-attempt failure, automatic retry, unchanged-state check.',
    start_date=datetime(2026,10,1,tzinfo=timezone.utc),schedule=None,catchup=False,max_active_runs=1,max_active_tasks=1,
    default_args=dict(owner='ecommerce-project',retries=1,retry_delay=timedelta(seconds=30),execution_timeout=timedelta(minutes=5)),
    params={'project_run_id':Param('store_v1',type='string',pattern=r'^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$')},
    tags=['ecommerce','test','retry','read-only'],
) as dag:
    @task(task_id='snapshot_before')
    def before_task():
        from orchestration.recovery_check import capture_snapshot
        return capture_snapshot(get_current_context()['params']['project_run_id'])

    @task(task_id='fail_once_then_retry')
    def retry_task(before):
        from orchestration.recovery_check import transient_attempt
        return transient_attempt(before,get_current_context()['ti'].try_number)

    @task(task_id='verify_recovery')
    def verify_task(retry_result):
        from orchestration.recovery_check import confirm_recovery
        return confirm_recovery(retry_result)

    verify_task(retry_task(before_task()))
