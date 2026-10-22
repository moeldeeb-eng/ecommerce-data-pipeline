"""Build a checked, run-scoped line-grain sales star from the loaded store tables."""
import argparse
import json
import os
import re
import sys
from pathlib import Path

import psycopg
from psycopg import sql
from dotenv import dotenv_values

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src'/'load'))
from load_store_data import check_database
MODEL_VERSION='1.0.0'


def build(cur,run_id):
    # Same lock as store loader: no overlapping project load/build operations.
    cur.execute("SELECT pg_advisory_xact_lock(hashtextextended('ecommerce_store_loader_v1',0))")
    cur.execute('SELECT manifest_sha256,manifest FROM store.ingestion_runs WHERE run_id=%s FOR SHARE',(run_id,))
    source=cur.fetchone()
    if source is None:
        raise ValueError('Run has not been loaded into store.ingestion_runs')
    source_hash,manifest=source
    print('Checking loaded source tables...',flush=True)
    source_result=check_database(cur,run_id,manifest['expected_counts'])
    cur.execute((ROOT/'sql/005_warehouse_schema.sql').read_text(encoding='utf-8'))
    cur.execute('SELECT source_manifest_sha256,model_version FROM warehouse.build_runs WHERE run_id=%s',(run_id,))
    previous=cur.fetchone()
    if previous and (previous[0].strip()!=source_hash.strip() or previous[1]!=MODEL_VERSION):
        raise ValueError('Existing build uses a different source manifest or model version; migration required')
    cur.execute('CREATE TEMP TABLE _warehouse_context(run_id text PRIMARY KEY) ON COMMIT DROP')
    cur.execute('INSERT INTO _warehouse_context VALUES (%s)',(run_id,))
    print('Building dimensions and FactSales...',flush=True)
    cur.execute((ROOT/'sql/006_build_warehouse.sql').read_text(encoding='utf-8'))
    inserted=[]
    while True:
        inserted.append(cur.rowcount)
        if not cur.nextset():
            break
    if len(inserted)!=4 or any(n<0 for n in inserted):
        raise ValueError('Unexpected transformation SQL result sets')
    added=dict(zip(['dim_date','dim_product','dim_customer','fact_sales'],inserted))
    counts={}
    for table,expected_key in [('dim_product','products'),('dim_customer','customers'),('fact_sales','order_items')]:
        cur.execute(sql.SQL('SELECT count(*) FROM warehouse.{} WHERE run_id=%s').format(sql.Identifier(table)),(run_id,))
        counts[table]=cur.fetchone()[0]
        if counts[table]!=manifest['expected_counts'][expected_key]:
            raise ValueError(f'{table}: source/warehouse row counts differ')
    cur.execute('''WITH bounds AS (
        SELECT min((ordered_at_utc AT TIME ZONE 'UTC')::date) lo,
               max((ordered_at_utc AT TIME ZONE 'UTC')::date) hi
        FROM store.orders WHERE run_id=%s
    ) SELECT (SELECT count(*) FROM warehouse.dim_date,bounds WHERE full_date BETWEEN lo AND hi),
             hi-lo+1 FROM bounds''',(run_id,))
    date_count,expected_dates=cur.fetchone()
    if date_count!=expected_dates:
        raise ValueError('Date dimension is not continuous over order date range')
    counts['dim_date_in_run_range']=date_count
    print('Checking dimensions, every fact row and revenue reconciliation...',flush=True)
    cur.execute((ROOT/'sql/007_warehouse_quality.sql').read_text(encoding='utf-8'))
    invalid=dict(zip(['invalid_products','invalid_customers','invalid_sales','invalid_dates'],cur.fetchone()))
    if any(invalid.values()):
        raise ValueError(f'Warehouse quality failure: {invalid}')
    cur.execute('''SELECT count(*),count(DISTINCT order_id),sum(realized_quantity),sum(realized_net_amount)
                   FROM warehouse.fact_sales WHERE run_id=%s''',(run_id,))
    line_count,order_count,units,revenue=cur.fetchone()
    if order_count!=manifest['expected_counts']['orders']:
        raise ValueError('Warehouse distinct order count mismatch')
    cur.execute('''SELECT coalesce(sum(net_items_amount),0) FROM store.orders
                   WHERE run_id=%s AND order_status='completed' ''',(run_id,))
    source_revenue=cur.fetchone()[0]
    if revenue!=source_revenue:
        raise ValueError('Warehouse realized revenue differs from completed store orders')
    cur.execute('''INSERT INTO warehouse.build_runs(run_id,source_manifest_sha256,model_version)
                   VALUES (%s,%s,%s) ON CONFLICT (run_id) DO UPDATE SET checked_at_utc=CURRENT_TIMESTAMP''',
                (run_id,source_hash,MODEL_VERSION))
    return dict(run_id=run_id,status='already_built' if previous and not any(added.values()) else 'built',
                rows_added=added,counts=counts,quality=invalid,distinct_orders=order_count,
                realized_units=int(units),realized_item_revenue_egp=str(revenue),
                completed_source_item_revenue_egp=str(source_revenue),revenue_reconciled=True,
                model_version=MODEL_VERSION,source_invalid_orders=source_result['invalid_orders'])


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run-id',required=True)
    args=p.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}',args.run_id):
        raise ValueError('Invalid run-id')
    config={**dotenv_values(ROOT/'.env'),**os.environ}
    for key in ['POSTGRES_DB','POSTGRES_USER','POSTGRES_PASSWORD']:
        if not config.get(key):raise ValueError(f'Missing configuration: {key}')
    with psycopg.connect(host=config.get('POSTGRES_HOST','127.0.0.1'),port=int(config.get('POSTGRES_PORT','5433')),
                         dbname=config['POSTGRES_DB'],user=config['POSTGRES_USER'],password=config['POSTGRES_PASSWORD'],
                         connect_timeout=10) as conn:
        with conn.cursor() as cur:
            result=build(cur,args.run_id)
    result['committed']=True
    print(json.dumps(result,indent=2))
    print('Warehouse build committed successfully.')

if __name__=='__main__':
    main()
