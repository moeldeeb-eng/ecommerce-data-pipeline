"""Validate and atomically COPY a synthetic run into PostgreSQL. No source tables are changed."""
import argparse
import csv
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src'/'generate'))
from generate_store_data import hashfile
from validate_store_data import validate

import psycopg
from psycopg import sql
from dotenv import dotenv_values


def copy_csv(cur, path, temp_name):
    with path.open(encoding='utf-8-sig',newline='') as f:
        fields = next(csv.reader(f))
    if len(set(fields))!=len(fields) or not fields:
        raise ValueError(f'Duplicate or empty CSV header: {path.name}')
    cur.execute(sql.SQL('CREATE TEMP TABLE {} ({}) ON COMMIT DROP').format(
        sql.Identifier(temp_name),sql.SQL(',').join(sql.SQL('{} text').format(sql.Identifier(k)) for k in fields)))
    command=sql.SQL('COPY {} ({}) FROM STDIN WITH (FORMAT CSV, HEADER TRUE, ENCODING \'UTF8\')').format(
        sql.Identifier(temp_name),sql.SQL(',').join(map(sql.Identifier,fields)))
    with cur.copy(command) as cp, path.open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):
            cp.write(block)


def check_database(cur, run_id, expected):
    actual={}
    for table,key in [('products','products'),('customers','customers'),('orders','orders'),('order_items','order_items'),('payments','payments')]:
        cur.execute(sql.SQL('SELECT COUNT(*) FROM store.{} WHERE run_id=%s').format(sql.Identifier(table)),(run_id,))
        actual[key]=cur.fetchone()[0]
        if actual[key]!=expected[key]:
            raise ValueError(f'Database {table} count {actual[key]} != expected {expected[key]}')
    cur.execute('''
        WITH line_totals AS (
          SELECT order_id, count(*) AS n, sum(gross_amount) AS gross,
                 sum(discount_amount) AS discount, sum(net_amount) AS net
          FROM store.order_items WHERE run_id=%s GROUP BY order_id
        )
        SELECT count(*) FROM store.orders o
        LEFT JOIN line_totals t ON t.order_id=o.order_id
        LEFT JOIN store.payments p ON p.run_id=o.run_id AND p.order_id=o.order_id
        JOIN store.customers c ON c.run_id=o.run_id AND c.customer_id=o.customer_id
        WHERE o.run_id=%s AND (
          t.order_id IS NULL OR t.n<>o.items_count OR t.gross<>o.subtotal
          OR t.discount<>o.discount_amount OR t.net<>o.net_items_amount
          OR p.order_id IS NULL OR p.amount_due<>o.order_total
          OR o.ordered_at_utc<c.registered_at_utc OR p.attempted_at_utc<o.ordered_at_utc
          OR (o.order_status='completed' AND (p.payment_status<>'paid' OR p.amount_paid<>o.order_total))
          OR (o.order_status='cancelled' AND p.payment_status<>'cancelled')
          OR (o.order_status='payment_failed' AND p.payment_status<>'failed')
        )
    ''',(run_id,run_id))
    invalid=cur.fetchone()[0]
    if invalid:
        raise ValueError(f'{invalid} orders failed reconciliation')
    cur.execute('''SELECT count(*) FROM store.products p
                   WHERE p.run_id=%s AND NOT EXISTS (SELECT 1 FROM staging.products s WHERE s.asin=p.asin)''',(run_id,))
    if cur.fetchone()[0]:
        raise ValueError('Some catalog products are absent from staging.products; load the real catalog first')
    cur.execute('''SELECT count(*) FROM store.order_items i JOIN store.products p
                   ON p.run_id=i.run_id AND p.asin=i.asin WHERE i.run_id=%s
                   AND (i.price_method<>p.anchor_method||'_synthetic_variation'
                   OR i.unit_price<greatest(.01,round(p.anchor_price*.85,2))
                   OR i.unit_price>greatest(.01,round(p.anchor_price*1.15,2)))''',(run_id,))
    if cur.fetchone()[0]:
        raise ValueError('Database price method/range check failed')
    cur.execute('''SELECT order_status,count(*),sum(net_items_amount),sum(shipping_amount),sum(order_total)
                   FROM store.orders WHERE run_id=%s GROUP BY order_status ORDER BY order_status''',(run_id,))
    summary=[dict(order_status=r[0],orders=r[1],net_items_amount=str(r[2]),shipping_amount=str(r[3]),order_total=str(r[4])) for r in cur.fetchall()]
    return dict(counts=actual,invalid_orders=invalid,order_status_summary=summary)


def load_run(cur, folder, manifest, manifest_hash):
    run_id=manifest['run_id']
    # One loader at a time also avoids concurrent CREATE TABLE IF NOT EXISTS races.
    cur.execute("SELECT pg_advisory_xact_lock(hashtextextended('ecommerce_store_loader_v1',0))")
    cur.execute((ROOT/'sql'/'003_synthetic_store.sql').read_text(encoding='utf-8'))
    cur.execute('SELECT manifest_sha256,manifest FROM store.ingestion_runs WHERE run_id=%s',(run_id,))
    existing=cur.fetchone()
    if existing:
        if existing[0].strip()!=manifest_hash:
            raise ValueError('This run-id is already loaded with a different manifest; refusing overwrite')
        # DB metadata contains cumulative counts after committed incremental batches.
        result=check_database(cur,run_id,existing[1]['expected_counts'])
        return dict(status='already_loaded',rows_added=0,**result)
    cur.execute('INSERT INTO store.ingestion_runs(run_id,manifest_sha256,manifest) VALUES (%s,%s,%s::jsonb)',
                (run_id,manifest_hash,json.dumps(manifest)))
    for filename,name in [('catalog_snapshot.csv','tmp_catalog'),('product_price_anchors.csv','tmp_anchors'),
                          ('customers.csv','tmp_customers'),('orders.csv','tmp_orders'),('order_items.csv','tmp_items')]:
        print(f'COPY: {filename}',flush=True)
        copy_csv(cur,folder/filename,name)
    cur.execute('CREATE TEMP TABLE tmp_payments (record jsonb NOT NULL) ON COMMIT DROP')
    print('COPY: payments.jsonl',flush=True)
    with cur.copy('COPY tmp_payments(record) FROM STDIN') as cp, (folder/'payments.jsonl').open(encoding='utf-8') as f:
        for line in f:
            cp.write_row((line.rstrip('\n'),))
    cur.execute('''INSERT INTO store.products (run_id,asin,product_name,category,source_price,source_currency,source_rating,source_ratings_count,anchor_price,anchor_currency,anchor_method,is_synthetic_anchor,source_record)
        SELECT %s,c.asin,c.product_name,c.final_category,NULLIF(c.price,'')::numeric,
               NULLIF(c.currency,''),NULLIF(c.rating,'')::numeric,NULLIF(c.ratings_count,'')::bigint,
               a.anchor_price::numeric,a.anchor_currency,a.anchor_method,a.is_synthetic_anchor::boolean,to_jsonb(c)
        FROM tmp_catalog c JOIN tmp_anchors a ON a.asin=c.asin''',(run_id,))
    cur.execute('''INSERT INTO store.customers (run_id,customer_id,customer_name,email,city,country,registered_at_utc,is_synthetic)
        SELECT %s,customer_id,customer_name,email,city,country,registered_at_utc::timestamptz,is_synthetic::boolean
        FROM tmp_customers''',(run_id,))
    cur.execute('''INSERT INTO store.orders (run_id,order_id,customer_id,ordered_at_utc,order_status,currency,items_count,subtotal,discount_amount,net_items_amount,shipping_amount,order_total,is_synthetic)
        SELECT %s,order_id,customer_id,ordered_at_utc::timestamptz,order_status,currency,items_count::smallint,
               subtotal::numeric,discount_amount::numeric,net_items_amount::numeric,shipping_amount::numeric,
               order_total::numeric,is_synthetic::boolean FROM tmp_orders''',(run_id,))
    cur.execute('''INSERT INTO store.order_items (run_id,order_id,line_number,asin,quantity,unit_price,discount_percent,gross_amount,discount_amount,net_amount,currency,price_method,is_synthetic)
        SELECT %s,order_id,line_number::smallint,asin,quantity::smallint,unit_price::numeric,
               discount_percent::smallint,gross_amount::numeric,discount_amount::numeric,
               net_amount::numeric,currency,price_method,is_synthetic::boolean FROM tmp_items''',(run_id,))
    cur.execute('''INSERT INTO store.payments (run_id,payment_id,order_id,currency,payment_method,payment_status,amount_due,amount_paid,attempted_at_utc,paid_at_utc,is_synthetic)
        SELECT %s,record->>'payment_id',record->>'order_id',record->>'currency',record->>'payment_method',
               record->>'payment_status',(record->>'amount_due')::numeric,(record->>'amount_paid')::numeric,
               (record->>'attempted_at_utc')::timestamptz,(record->>'paid_at_utc')::timestamptz,
               (record->>'is_synthetic')::boolean FROM tmp_payments''',(run_id,))
    print('Checking database counts, keys and amounts...',flush=True)
    result=check_database(cur,run_id,manifest['expected_counts'])
    return dict(status='loaded',rows_added=sum(result['counts'].values()),**result)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run-id',required=True)
    args=p.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}',args.run_id):
        raise ValueError('Invalid run-id')
    folder=ROOT/'data'/'raw'/'synthetic_runs'/args.run_id
    print('Validating source files before opening database transaction...',flush=True)
    validation=validate(folder)
    manifest=json.loads((folder/'manifest.json').read_text(encoding='utf-8'))
    if manifest['run_id']!=args.run_id:
        raise ValueError('Manifest run-id differs from folder/argument')
    manifest_hash=hashfile(folder/'manifest.json')
    config={**dotenv_values(ROOT/'.env'),**os.environ}
    for key in ['POSTGRES_DB','POSTGRES_USER','POSTGRES_PASSWORD']:
        if not config.get(key):
            raise ValueError(f'Missing configuration: {key}')
    with psycopg.connect(host=config.get('POSTGRES_HOST','127.0.0.1'),port=int(config.get('POSTGRES_PORT','5433')),
                         dbname=config['POSTGRES_DB'],user=config['POSTGRES_USER'],password=config['POSTGRES_PASSWORD'],
                         connect_timeout=10) as conn:
        with conn.cursor() as cur:
            result=load_run(cur,folder,manifest,manifest_hash)
            # Refuse to commit if source files changed during loading.
            for name,info in manifest['files'].items():
                if hashfile(folder/name)!=info['sha256']:
                    raise ValueError(f'Source changed during loading: {name}')
            if hashfile(folder/'manifest.json')!=manifest_hash:
                raise ValueError('Manifest changed during loading')
    # Connection context commits only after every check has passed.
    report=dict(run_id=args.run_id,source_validation=validation['status'],committed=True,**result)
    print(json.dumps(report,indent=2))
    print('Load committed successfully.')

if __name__=='__main__':
    main()
