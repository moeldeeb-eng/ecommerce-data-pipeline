"""Read-only database diagnostics for the first Airflow environment check."""
import json
import os
import re
from decimal import Decimal


def validate_run_id(run_id):
    if not isinstance(run_id,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}',run_id):
        raise ValueError('Invalid project run_id')
    return run_id


def connect():
    import psycopg
    values={key:os.environ.get(key) for key in ['POSTGRES_HOST','POSTGRES_PORT','POSTGRES_DB','POSTGRES_USER','POSTGRES_PASSWORD']}
    for key,value in values.items():
        if not value:raise ValueError(f'Missing container configuration: {key}')
    return psycopg.connect(host=values['POSTGRES_HOST'],port=int(values['POSTGRES_PORT']),dbname=values['POSTGRES_DB'],
                           user=values['POSTGRES_USER'],password=values['POSTGRES_PASSWORD'],connect_timeout=10,
                           options='-c default_transaction_read_only=on -c statement_timeout=120000')


def check_connection(run_id):
    validate_run_id(run_id)
    with connect() as conn,conn.cursor() as cur:
        cur.execute('SELECT current_database(), current_setting(\'transaction_read_only\')')
        database,readonly=cur.fetchone()
    if readonly!='on':raise ValueError('Diagnostic connection must be read-only')
    return dict(run_id=run_id,database=database,read_only=True)


def read_store(run_id):
    from psycopg import sql
    validate_run_id(run_id)
    with connect() as conn,conn.cursor() as cur:
        cur.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
        cur.execute('SELECT manifest_sha256,manifest FROM store.ingestion_runs WHERE run_id=%s',(run_id,))
        record=cur.fetchone()
        if record is None:raise ValueError('Project run has not been loaded')
        digest,manifest=record
        counts={}
        for table in ['products','customers','orders','order_items','payments']:
            cur.execute(sql.SQL('SELECT count(*) FROM store.{} WHERE run_id=%s').format(sql.Identifier(table)),(run_id,))
            counts[table]=cur.fetchone()[0]
        cur.execute('''SELECT coalesce(sum(net_items_amount) FILTER (WHERE order_status='completed'),0),
                      min((ordered_at_utc AT TIME ZONE 'UTC')::date),max((ordered_at_utc AT TIME ZONE 'UTC')::date)
                      FROM store.orders WHERE run_id=%s''',(run_id,))
        revenue,first,last=cur.fetchone()
    if first is None or last is None:raise ValueError('Source orders are empty')
    return dict(run_id=run_id,counts=counts,expected_counts=manifest['expected_counts'],
                source_manifest_sha256=digest.strip(),realized_item_revenue_egp=str(revenue),
                min_date=first.isoformat(),max_date=last.isoformat(),date_range_days=(last-first).days+1)


def read_warehouse(source):
    from psycopg import sql
    run_id=validate_run_id(source['run_id'])
    with connect() as conn,conn.cursor() as cur:
        cur.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
        cur.execute('SELECT source_manifest_sha256,model_version FROM warehouse.build_runs WHERE run_id=%s',(run_id,))
        record=cur.fetchone()
        if record is None:raise ValueError('Warehouse has no successful build for this run')
        digest,version=record
        counts={}
        for table in ['dim_product','dim_customer','fact_sales']:
            cur.execute(sql.SQL('SELECT count(*) FROM warehouse.{} WHERE run_id=%s').format(sql.Identifier(table)),(run_id,))
            counts[table]=cur.fetchone()[0]
        cur.execute('''SELECT count(DISTINCT order_id),coalesce(sum(realized_net_amount),0)
                       FROM warehouse.fact_sales WHERE run_id=%s''',(run_id,))
        distinct_orders,revenue=cur.fetchone()
        cur.execute('SELECT count(*) FROM warehouse.dim_date WHERE full_date BETWEEN %s::date AND %s::date',
                    (source['min_date'],source['max_date']))
        dates=cur.fetchone()[0]
    return dict(source=source,warehouse=dict(counts=counts,distinct_orders=distinct_orders,
                source_manifest_sha256=digest.strip(),model_version=version,
                realized_item_revenue_egp=str(revenue),date_range_days=dates))


def verify_snapshots(snapshot):
    source,warehouse=snapshot['source'],snapshot['warehouse']
    if source['counts']!=source['expected_counts']:raise ValueError('Source counts differ from cumulative ingestion metadata')
    for dim,entity in [('dim_product','products'),('dim_customer','customers'),('fact_sales','order_items')]:
        if warehouse['counts'][dim]!=source['counts'][entity]:raise ValueError(f'Warehouse {dim} does not match source')
    if warehouse['distinct_orders']!=source['counts']['orders']:raise ValueError('Warehouse distinct order count mismatch')
    if warehouse['source_manifest_sha256']!=source['source_manifest_sha256']:raise ValueError('Warehouse/source baseline fingerprint mismatch')
    if warehouse['date_range_days']!=source['date_range_days']:raise ValueError('Warehouse date range has missing days')
    if Decimal(warehouse['realized_item_revenue_egp'])!=Decimal(source['realized_item_revenue_egp']):
        raise ValueError('Warehouse/source realized item revenue mismatch')
    result=dict(status='passed',run_id=source['run_id'],counts=source['counts'],
                warehouse_counts=warehouse['counts'],distinct_orders=warehouse['distinct_orders'],
                realized_item_revenue_egp=warehouse['realized_item_revenue_egp'],read_only=True)
    print(json.dumps(result,indent=2))
    return result
