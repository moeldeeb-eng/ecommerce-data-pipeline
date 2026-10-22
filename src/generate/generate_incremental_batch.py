"""Create a disjoint, synthetic append batch using read-only database state."""
import argparse
import contextlib
import csv
import io
import json
import os
import re
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import psycopg
from psycopg import sql
from dotenv import dotenv_values
import generate_store_data as generator
from validate_incremental_data import validate

ROOT=Path(__file__).resolve().parents[2]
VERSION='1.0.0'


def get_parent_state(run_id):
    config={**dotenv_values(ROOT/'.env'),**os.environ}
    for key in ['POSTGRES_DB','POSTGRES_USER','POSTGRES_PASSWORD']:
        if not config.get(key):raise ValueError(f'Missing configuration: {key}')
    with psycopg.connect(host=config.get('POSTGRES_HOST','127.0.0.1'),port=int(config.get('POSTGRES_PORT','5433')),
                         dbname=config['POSTGRES_DB'],user=config['POSTGRES_USER'],password=config['POSTGRES_PASSWORD'],
                         connect_timeout=10) as conn:
        with conn.cursor() as cur:
            cur.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
            cur.execute('SELECT manifest_sha256,manifest FROM store.ingestion_runs WHERE run_id=%s',(run_id,))
            result=cur.fetchone()
            if result is None:raise ValueError('Parent run must be loaded first')
            digest,manifest=result
            cur.execute('SELECT source_manifest_sha256 FROM warehouse.build_runs WHERE run_id=%s',(run_id,))
            built=cur.fetchone()
            if built is None or built[0].strip()!=digest.strip():
                raise ValueError('Parent warehouse must have a successful build with the same source fingerprint')
            counts={};offsets={}
            for table,column in [('customers','customer_id'),('orders','order_id'),('payments','payment_id')]:
                cur.execute(sql.SQL('SELECT count(*),max(substring({} FROM 2)::bigint) FROM store.{} WHERE run_id=%s').format(
                    sql.Identifier(column),sql.Identifier(table)),(run_id,))
                count,maximum=cur.fetchone();counts[table]=count;offsets[table]=int(maximum or 0)
            for table in ['products','order_items']:
                cur.execute(sql.SQL('SELECT count(*) FROM store.{} WHERE run_id=%s').format(sql.Identifier(table)),(run_id,))
                counts[table]=cur.fetchone()[0]
            if counts!=manifest['expected_counts']:
                raise ValueError('Parent table counts differ from ingestion metadata; refusing to allocate IDs')
            for table,source_table in [('dim_product','products'),('dim_customer','customers'),('fact_sales','order_items')]:
                cur.execute(sql.SQL('SELECT count(*) FROM warehouse.{} WHERE run_id=%s').format(sql.Identifier(table)),(run_id,))
                if cur.fetchone()[0]!=counts[source_table]:
                    raise ValueError('Warehouse is behind the parent source; build it before preparing another batch')
            cur.execute('SELECT max(ordered_at_utc) FROM store.orders WHERE run_id=%s',(run_id,))
            last=cur.fetchone()[0]
            if last is None:raise ValueError('Parent has no orders')
    return dict(source_manifest_sha256=digest.strip(),source_catalog_sha256=manifest['source_catalog_sha256'],
                counts=counts,id_offsets=offsets,last_ordered_at_utc=generator.stamp(last.astimezone(timezone.utc)))


def rewrite_csv(path,transform):
    with path.open(encoding='utf-8-sig',newline='') as f:
        rd=csv.DictReader(f);fields=rd.fieldnames;rows=list(rd)
    for row in rows:transform(row)
    with path.open('w',encoding='utf-8',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)


def create_batch(args,state):
    dest=ROOT/'data/raw/incremental_runs'/args.run_id/args.batch_id
    if dest.exists():raise FileExistsError(f'Batch exists: {dest}. Validate it instead of regenerating.')
    catalog=ROOT/'data/raw/synthetic_runs'/args.run_id/'catalog_snapshot.csv'
    if generator.hashfile(catalog)!=state['source_catalog_sha256']:
        raise ValueError('Baseline catalog file differs from loaded source catalog fingerprint')
    for field,count in [('customers',args.customers),('orders',args.orders),('payments',args.orders)]:
        if state['id_offsets'][field]+count>999999999:raise ValueError('ID exceeds nine-digit project range')
    last=datetime.fromisoformat(state['last_ordered_at_utc'].replace('Z','+00:00'))
    start=(last+timedelta(days=1)).replace(hour=0,minute=0,second=0,microsecond=0)
    end=start+timedelta(hours=23)
    offsets=state['id_offsets']
    dest.parent.mkdir(parents=True,exist_ok=True)
    # Reuse the tested baseline generator privately; final output appears only after validation.
    with tempfile.TemporaryDirectory(prefix='.increment-',dir=dest.parent) as tmp:
        previous_root=generator.ROOT
        try:
            generator.ROOT=Path(tmp)
            opts=SimpleNamespace(run_id='template',catalog=str(catalog),customers=args.customers,
                                 orders=args.orders,items=args.items,seed=args.seed)
            with contextlib.redirect_stdout(io.StringIO()):generator.generate(opts)
        finally:
            generator.ROOT=previous_root
        folder=Path(tmp)/'data/raw/synthetic_runs/template'
        def id_map(value,kind):return value[0]+f'{int(value[1:])+offsets[kind]:09d}'
        def customer(row):
            row['customer_id']=id_map(row['customer_id'],'customers')
            number=int(row['customer_id'][1:])
            row['customer_name']=f'Synthetic Customer {number:09d}'
            row['email']=f'customer{number:09d}@example.invalid'
            row['registered_at_utc']=generator.stamp(start-timedelta(days=1))
        def order(row):
            row['order_id']=id_map(row['order_id'],'orders')
            row['customer_id']=id_map(row['customer_id'],'customers')
            old=datetime.fromisoformat(row['ordered_at_utc'].replace('Z','+00:00'))
            fraction=(old-generator.START).total_seconds()/(generator.END-generator.START).total_seconds()
            row['ordered_at_utc']=generator.stamp(start+timedelta(seconds=int(fraction*(end-start).total_seconds())))
        rewrite_csv(folder/'customers.csv',customer)
        rewrite_csv(folder/'orders.csv',order)
        rewrite_csv(folder/'order_items.csv',lambda r:r.update(order_id=id_map(r['order_id'],'orders')))
        with (folder/'orders.csv').open(newline='') as f:
            order_times={r['order_id']:datetime.fromisoformat(r['ordered_at_utc'].replace('Z','+00:00')) for r in csv.DictReader(f)}
        payments=[]
        with (folder/'payments.jsonl').open(encoding='utf-8') as f:
            for line in f:
                p=json.loads(line);p['order_id']=id_map(p['order_id'],'orders');p['payment_id']=id_map(p['payment_id'],'payments')
                p['attempted_at_utc']=generator.stamp(order_times[p['order_id']]+timedelta(minutes=2))
                p['paid_at_utc']=generator.stamp(order_times[p['order_id']]+timedelta(minutes=3)) if p['payment_status']=='paid' else None
                payments.append(p)
        with (folder/'payments.jsonl').open('w',encoding='utf-8') as f:
            for p in payments:f.write(json.dumps(p,separators=(',',':'))+'\n')
        manifest=json.loads((folder/'manifest.json').read_text())
        manifest.update(run_id=args.run_id,parent_run_id=args.run_id,batch_id=args.batch_id,
                        batch_type='new_orders_only',incremental_generator_version=VERSION,
                        parent_state=state,id_offsets=offsets,start_utc=generator.stamp(start),end_utc=generator.stamp(end))
        manifest['assumptions'] += ['Append-only synthetic batch: only new customers and new orders; no changes to existing order statuses.',
                                    'Order dates represent the next fictional simulation day after the parent watermark; no claim of real observed sales.']
        manifest['files']={name:dict(sha256=generator.hashfile(folder/name),bytes=(folder/name).stat().st_size) for name in manifest['files']}
        (folder/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
        report=validate(folder)
        (folder/'validation_report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
        # Reject a competing writer rather than replacing a prepared batch.
        if dest.exists():raise FileExistsError('Another process has already created this batch')
        folder.rename(dest)
    summary=dict(parent_run_id=args.run_id,batch_id=args.batch_id,**report,
                 new_customer_range=[f'C{offsets["customers"]+1:09d}',f'C{offsets["customers"]+args.customers:09d}'],
                 new_order_range=[f'O{offsets["orders"]+1:09d}',f'O{offsets["orders"]+args.orders:09d}'],
                 new_payment_range=[f'P{offsets["payments"]+1:09d}',f'P{offsets["payments"]+args.orders:09d}'],
                 parent_watermark=state['last_ordered_at_utc'],new_start_utc=generator.stamp(start),new_end_utc=generator.stamp(end))
    print(json.dumps(summary,indent=2))
    print(f'Output: {dest}')
    print('Files prepared only. Database has not been modified.')
    return dest


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run-id',required=True)
    p.add_argument('--batch-id',required=True)
    p.add_argument('--customers',type=int,default=1000)
    p.add_argument('--orders',type=int,default=4000)
    p.add_argument('--items',type=int,default=10000)
    p.add_argument('--seed',type=int,default=43)
    args=p.parse_args()
    for value in [args.run_id,args.batch_id]:
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}',value):raise ValueError('Invalid run/batch ID')
    if not 1<=args.customers<=args.orders<=100000 or not args.orders<=args.items<=5*args.orders:
        raise ValueError('Require 1<=customers<=orders<=100000 and orders<=items<=5*orders')
    dest=ROOT/'data/raw/incremental_runs'/args.run_id/args.batch_id
    if dest.exists():raise FileExistsError('Batch exists; use validate_incremental_data.py to check it')
    print('Reading parent IDs, counts and event-time watermark (read-only)...',flush=True)
    state=get_parent_state(args.run_id)
    print('Generating and validating the incremental batch...',flush=True)
    create_batch(args,state)

if __name__=='__main__':
    main()
