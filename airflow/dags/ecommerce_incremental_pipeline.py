"""Full manual pipeline for fictional incremental orders and an existing scraped catalog."""
from datetime import datetime,timedelta,timezone
from airflow.sdk import DAG,Param,task,get_current_context

with DAG(
    dag_id='ecommerce_incremental_pipeline',
    description='Prepare/extract CSV-JSON batch, validate, Pandas transform, append/build, and reconcile.',
    start_date=datetime(2026,10,1,tzinfo=timezone.utc),schedule=None,catchup=False,
    max_active_runs=1,max_active_tasks=1,
    default_args=dict(owner='ecommerce-project',retries=2,retry_delay=timedelta(seconds=30),execution_timeout=timedelta(minutes=30)),
    params={
        'project_run_id':Param('store_v1',type='string',pattern=r'^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$'),
        'batch_id':Param('inc_002',type='string',pattern=r'^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$'),
    },tags=['ecommerce','incremental','pipeline','synthetic-transactions'],
) as dag:
    @task(task_id='extract')
    def extract_task():
        from orchestration.incremental_pipeline import extract
        p=get_current_context()['params']
        return extract(p['project_run_id'],p['batch_id'])

    @task(task_id='validate')
    def validate_task(batch):
        from orchestration.incremental_pipeline import validate
        return validate(batch)

    @task(task_id='transform')
    def transform_task(batch):
        from orchestration.incremental_pipeline import transform
        return transform(batch)

    @task(task_id='load')
    def load_task(batch):
        from orchestration.incremental_pipeline import load
        return load(batch)

    @task(task_id='quality_check')
    def quality_task(batch):
        from orchestration.incremental_pipeline import quality_check
        return quality_check(batch)

    quality_task(load_task(transform_task(validate_task(extract_task()))))
