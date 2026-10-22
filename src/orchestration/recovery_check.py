"""A read-only retry demonstration, separate from business ingestion."""
import json
from datetime import timezone
from orchestration.diagnostics import connect,read_store,read_warehouse,verify_snapshots,validate_run_id


def capture_snapshot(run_id):
    validate_run_id(run_id)
    source=read_store(run_id)
    warehouse=read_warehouse(source)
    summary=verify_snapshots(warehouse)
    with connect() as conn,conn.cursor() as cur:
        cur.execute('SELECT last_ordered_at_utc,id_maxima,cumulative_counts FROM store.pipeline_state WHERE run_id=%s',(run_id,))
        state=cur.fetchone()
        if state is None:raise ValueError('Expected committed incremental pipeline state')
        cur.execute('SELECT count(*) FROM store.incremental_batches WHERE run_id=%s',(run_id,))
        batch_count=cur.fetchone()[0]
    return dict(project=summary,source_manifest_sha256=source['source_manifest_sha256'],
                date_range_days=warehouse['warehouse']['date_range_days'],incremental_batches=batch_count,
                watermark_utc=state[0].astimezone(timezone.utc).isoformat(),id_maxima=state[1],cumulative_counts=state[2])


def transient_attempt(before,attempt):
    if type(attempt) is not int or attempt<1:raise ValueError('Invalid Airflow task attempt number')
    print(f'Recovery demonstration: task attempt {attempt}',flush=True)
    if attempt==1:
        raise RuntimeError('INTENTIONAL TRANSIENT FAILURE: first attempt only. Airflow should retry automatically; no business database writes were attempted.')
    print('Retry attempt reached; continuing without generating or loading data.',flush=True)
    return dict(before=before,successful_attempt=attempt)


def confirm_recovery(retry_result):
    before=retry_result['before']
    if retry_result['successful_attempt']<2:raise ValueError('A second task attempt was not observed')
    after=capture_snapshot(before['project']['run_id'])
    if after!=before:raise ValueError('Project counts, revenue, provenance or committed pipeline state changed during recovery check')
    result=dict(status='passed',run_id=before['project']['run_id'],successful_attempt=retry_result['successful_attempt'],
                counts=after['project']['counts'],realized_item_revenue_egp=after['project']['realized_item_revenue_egp'],
                watermark_utc=after['watermark_utc'],business_state_unchanged=True,verification_read_only=True)
    print(json.dumps(result,indent=2),flush=True)
    return result
