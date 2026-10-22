"""Append one prepared batch to its parent run with transactional ledger/watermark."""
import argparse
import json
import os
import re
import sys
from datetime import datetime,timezone
from pathlib import Path

import psycopg
from psycopg import sql
from dotenv import dotenv_values
from load_store_data import copy_csv, check_database

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src/generate'))
from generate_store_data import hashfile, stamp
from validate_incremental_data import validate

# Explicit column order used for both insert and exact source/target comparison.
TABLES={
 'customers':(['run_id','customer_id','customer_name','email','city','country','registered_at_utc','is_synthetic'],
   "SELECT %s,customer_id,customer_name,email,city,country,registered_at_utc::timestamptz,is_synthetic::boolean FROM tmp_inc_customers",'customer_id'),
 'orders':(['run_id','order_id','customer_id','ordered_at_utc','order_status','currency','items_count','subtotal','discount_amount','net_items_amount','shipping_amount','order_total','is_synthetic'],
   "SELECT %s,order_id,customer_id,ordered_at_utc::timestamptz,order_status,currency,items_count::smallint,subtotal::numeric,discount_amount::numeric,net_items_amount::numeric,shipping_amount::numeric,order_total::numeric,is_synthetic::boolean FROM tmp_inc_orders",'order_id'),
 'order_items':(['run_id','order_id','line_number','asin','quantity','unit_price','discount_percent','gross_amount','discount_amount','net_amount','currency','price_method','is_synthetic'],
   "SELECT %s,order_id,line_number::smallint,asin,quantity::smallint,unit_price::numeric,discount_percent::smallint,gross_amount::numeric,discount_amount::numeric,net_amount::numeric,currency,price_method,is_synthetic::boolean FROM tmp_inc_items",'order_id'),
 'payments':(['run_id','payment_id','order_id','currency','payment_method','payment_status','amount_due','amount_paid','attempted_at_utc','paid_at_utc','is_synthetic'],
   "SELECT %s,record->>'payment_id',record->>'order_id',record->>'currency',record->>'payment_method',record->>'payment_status',(record->>'amount_due')::numeric,(record->>'amount_paid')::numeric,(record->>'attempted_at_utc')::timestamptz,(record->>'paid_at_utc')::timestamptz,(record->>'is_synthetic')::boolean FROM tmp_inc_payments",'payment_id'),
}


def read_current_state(cur,run_id):
    counts={};offsets={}
    for table,key in [('customers','customer_id'),('orders','order_id'),('payments','payment_id')]:
        cur.execute(sql.SQL('SELECT count(*),max(substring({} FROM 2)::bigint) FROM store.{} WHERE run_id=%s').format(
                    sql.Identifier(key),sql.Identifier(table)),(run_id,))
        count,maximum=cur.fetchone();counts[table]=count;offsets[table]=int(maximum or 0)
    for table in ['products','order_items']:
        cur.execute(sql.SQL('SELECT count(*) FROM store.{} WHERE run_id=%s').format(sql.Identifier(table)),(run_id,))
        counts[table]=cur.fetchone()[0]
    cur.execute('SELECT max(ordered_at_utc) FROM store.orders WHERE run_id=%s',(run_id,))
    watermark=cur.fetchone()[0]
    if watermark is None:raise ValueError('Parent run must contain baseline orders')
    return dict(counts=counts,id_offsets=offsets,last_ordered_at_utc=stamp(watermark.astimezone(timezone.utc)))


def check_watermark_state(cur,run_id,state):
    cur.execute('SELECT last_ordered_at_utc,id_maxima,cumulative_counts FROM store.pipeline_state WHERE run_id=%s',(run_id,))
    record=cur.fetchone()
    if record and (stamp(record[0].astimezone(timezone.utc))!=state['last_ordered_at_utc'] or record[1]!=state['id_offsets'] or record[2]!=state['counts']):
        raise ValueError('Stored pipeline watermark/counts/IDs differ from current source tables')


def stage_batch(cur,folder):
    for filename,table in [('catalog_snapshot.csv','tmp_inc_catalog'),('product_price_anchors.csv','tmp_inc_anchors'),
                           ('customers.csv','tmp_inc_customers'),('orders.csv','tmp_inc_orders'),('order_items.csv','tmp_inc_items')]:
        print(f'COPY: {filename}',flush=True)
        copy_csv(cur,folder/filename,table)
    cur.execute('CREATE TEMP TABLE tmp_inc_payments(record jsonb NOT NULL) ON COMMIT DROP')
    with cur.copy('COPY tmp_inc_payments(record) FROM STDIN') as cp,(folder/'payments.jsonl').open(encoding='utf-8') as f:
        for line in f:cp.write_row((line.rstrip('\n'),))


def check_products(cur,run_id):
    cur.execute('''SELECT count(*) FROM tmp_inc_catalog c JOIN tmp_inc_anchors a ON a.asin=c.asin
      LEFT JOIN store.products p ON p.run_id=%s AND p.asin=c.asin
      WHERE p.asin IS NULL OR ROW(p.product_name,p.category,p.source_price,p.source_currency,p.source_rating,
        p.source_ratings_count,p.anchor_price,p.anchor_currency,p.anchor_method,p.is_synthetic_anchor,p.source_record)
      IS DISTINCT FROM ROW(c.product_name,c.final_category,NULLIF(c.price,'')::numeric,NULLIF(c.currency,''),
        NULLIF(c.rating,'')::numeric,NULLIF(c.ratings_count,'')::bigint,a.anchor_price::numeric,a.anchor_currency,
        a.anchor_method,a.is_synthetic_anchor::boolean,to_jsonb(c))''',(run_id,))
    if cur.fetchone()[0]:raise ValueError('Batch catalog/anchors differ from the parent product snapshot')


def check_exact_batch(cur,run_id):
    for table,(columns,select,key) in TABLES.items():
        # Compare every supplied typed value, including dates, statuses and flags.
        names=sql.SQL(',').join(map(sql.Identifier,columns))
        query=sql.SQL('''WITH expected ({cols}) AS ({source}),actual AS (
            SELECT {cols} FROM store.{table} t WHERE t.run_id=%s
              AND t.{key} IN (SELECT {key} FROM expected)
        ),missing AS (SELECT * FROM expected EXCEPT ALL SELECT * FROM actual),
          extra AS (SELECT * FROM actual EXCEPT ALL SELECT * FROM expected)
        SELECT (SELECT count(*) FROM missing)+(SELECT count(*) FROM extra)''').format(
            cols=names,source=sql.SQL(select),table=sql.Identifier(table),key=sql.Identifier(key))
        cur.execute(query,(run_id,run_id))
        if cur.fetchone()[0]:raise ValueError(f'Exact batch/source mismatch in {table}')


def apply_batch(cur,folder,manifest,batch_hash):
    run_id,batch_id=manifest['parent_run_id'],manifest['batch_id']
    cur.execute("SELECT pg_advisory_xact_lock(hashtextextended('ecommerce_store_loader_v1',0))")
    cur.execute((ROOT/'sql/009_incremental_batches.sql').read_text(encoding='utf-8'))
    cur.execute('SELECT manifest_sha256,manifest FROM store.ingestion_runs WHERE run_id=%s FOR UPDATE',(run_id,))
    source=cur.fetchone()
    if source is None:raise ValueError('Parent ingestion run does not exist')
    parent_hash,parent_manifest=source
    parent_hash=parent_hash.strip()
    if parent_hash!=manifest['parent_state']['source_manifest_sha256']:
        raise ValueError('Parent baseline fingerprint differs from prepared batch')
    if parent_manifest['source_catalog_sha256']!=manifest['source_catalog_sha256'] or manifest['source_catalog_sha256']!=manifest['parent_state']['source_catalog_sha256']:
        raise ValueError('Source catalog fingerprint differs from parent')
    cur.execute('SELECT manifest_sha256 FROM store.incremental_batches WHERE run_id=%s AND batch_id=%s',(run_id,batch_id))
    prior=cur.fetchone()
    if prior and prior[0].strip()!=batch_hash:raise ValueError('This batch-id was already loaded with different contents')
    state=read_current_state(cur,run_id)
    if state['counts']!=parent_manifest['expected_counts']:raise ValueError('Current source counts differ from ingestion metadata')
    check_watermark_state(cur,run_id,state)
    if not prior:
        captured=manifest['parent_state']
        if any(state[k]!=captured[k] for k in ['counts','id_offsets','last_ordered_at_utc']):
            raise ValueError('Stale batch: parent counts, ID maxima or watermark changed after preparation')
    stage_batch(cur,folder)
    check_products(cur,run_id)
    if prior:
        check_exact_batch(cur,run_id)
        result=check_database(cur,run_id,parent_manifest['expected_counts'])
        return dict(status='already_loaded',rows_added=dict(customers=0,orders=0,order_items=0,payments=0),
                    watermark_utc=state['last_ordered_at_utc'],**result)
    cur.execute('SELECT min(ordered_at_utc::timestamptz) FROM tmp_inc_orders')
    first_new=cur.fetchone()[0]
    if first_new<=datetime.fromisoformat(state['last_ordered_at_utc'].replace('Z','+00:00')):
        raise ValueError('New order dates overlap the committed event-time watermark')
    added={}
    for table,(columns,select,key) in TABLES.items():
        command=sql.SQL('INSERT INTO store.{} ({}) {}').format(sql.Identifier(table),sql.SQL(',').join(map(sql.Identifier,columns)),sql.SQL(select))
        cur.execute(command,(run_id,));added[table]=cur.rowcount
        if added[table]!=manifest['expected_counts'][table]:raise ValueError(f'{table} insert count mismatch')
    expected=dict(state['counts'])
    for key,value in added.items():expected[key]+=value
    check_exact_batch(cur,run_id)
    print('Reconciling cumulative counts and amounts...',flush=True)
    result=check_database(cur,run_id,expected)
    after=read_current_state(cur,run_id)
    if after['counts']!=expected:raise ValueError('Cumulative counts changed unexpectedly')
    for key,offset in manifest['id_offsets'].items():
        if after['id_offsets'][key]!=offset+added[key]:raise ValueError(f'{key} maximum ID did not advance as expected')
    updated=dict(parent_manifest)
    updated.setdefault('baseline_expected_counts',dict(parent_manifest['expected_counts']))
    updated['expected_counts']=expected
    batches=list(updated.get('incremental_batches',[]))
    batches.append(dict(batch_id=batch_id,manifest_sha256=batch_hash,added_counts=added,watermark_after=after['last_ordered_at_utc']))
    updated['incremental_batches']=batches
    cur.execute('UPDATE store.ingestion_runs SET manifest=%s::jsonb WHERE run_id=%s',(json.dumps(updated),run_id))
    cur.execute('''INSERT INTO store.incremental_batches
      (run_id,batch_id,manifest_sha256,parent_manifest_sha256,manifest,counts_before,counts_after,watermark_before,watermark_after)
      VALUES (%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,%s,%s)''',
      (run_id,batch_id,batch_hash,parent_hash,json.dumps(manifest),json.dumps(state['counts']),json.dumps(expected),
       state['last_ordered_at_utc'],after['last_ordered_at_utc']))
    cur.execute('''INSERT INTO store.pipeline_state(run_id,last_ordered_at_utc,id_maxima,cumulative_counts)
      VALUES (%s,%s,%s::jsonb,%s::jsonb) ON CONFLICT (run_id) DO UPDATE SET
      last_ordered_at_utc=EXCLUDED.last_ordered_at_utc,id_maxima=EXCLUDED.id_maxima,
      cumulative_counts=EXCLUDED.cumulative_counts,updated_at_utc=CURRENT_TIMESTAMP''',
      (run_id,after['last_ordered_at_utc'],json.dumps(after['id_offsets']),json.dumps(expected)))
    return dict(status='loaded',rows_added=added,watermark_before=state['last_ordered_at_utc'],
                watermark_utc=after['last_ordered_at_utc'],**result)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run-id',required=True);p.add_argument('--batch-id',required=True)
    args=p.parse_args()
    for value in [args.run_id,args.batch_id]:
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}',value):raise ValueError('Invalid run/batch ID')
    folder=ROOT/'data/raw/incremental_runs'/args.run_id/args.batch_id
    print('Validating prepared incremental files...',flush=True)
    validate(folder)
    manifest=json.loads((folder/'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('batch_type')!='new_orders_only' or manifest['parent_run_id']!=args.run_id or manifest['batch_id']!=args.batch_id:
        raise ValueError('Batch identity/type mismatch')
    digest=hashfile(folder/'manifest.json')
    config={**dotenv_values(ROOT/'.env'),**os.environ}
    for key in ['POSTGRES_DB','POSTGRES_USER','POSTGRES_PASSWORD']:
        if not config.get(key):raise ValueError(f'Missing configuration: {key}')
    with psycopg.connect(host=config.get('POSTGRES_HOST','127.0.0.1'),port=int(config.get('POSTGRES_PORT','5433')),
                         dbname=config['POSTGRES_DB'],user=config['POSTGRES_USER'],password=config['POSTGRES_PASSWORD'],
                         connect_timeout=10) as conn:
        with conn.cursor() as cur:
            result=apply_batch(cur,folder,manifest,digest)
            for name,info in manifest['files'].items():
                if hashfile(folder/name)!=info['sha256']:raise ValueError(f'Source file changed during load: {name}')
            if hashfile(folder/'manifest.json')!=digest:raise ValueError('Batch manifest changed during load')
    print(json.dumps(dict(run_id=args.run_id,batch_id=args.batch_id,committed=True,**result),indent=2))
    print('Incremental load committed successfully.')

if __name__=='__main__':
    main()
