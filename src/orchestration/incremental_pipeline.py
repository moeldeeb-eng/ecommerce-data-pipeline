"""Five-stage prepared-file pipeline. Airflow imports these functions only at task runtime."""
import csv
import hashlib
import json
import subprocess
import sys
import tempfile
from decimal import Decimal
from pathlib import Path
from orchestration.diagnostics import validate_run_id

ROOT=Path(__file__).resolve().parents[2]
FIELDS=['order_id','line_number','asin','customer_id','date_key','order_status','quantity',
        'realized_quantity','net_amount','realized_net_amount','currency','price_method','is_synthetic']
INTEGER_FIELDS={'line_number','date_key','quantity','realized_quantity'}
AMOUNT_FIELDS={'net_amount','realized_net_amount'}


def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for data in iter(lambda:f.read(1024*1024),b''):h.update(data)
    return h.hexdigest()


def paths(run_id,batch_id):
    validate_run_id(run_id);validate_run_id(batch_id)
    return (ROOT/'data/raw/incremental_runs'/run_id/batch_id,
            ROOT/'data/processed/incremental_runs'/run_id/batch_id)


def cli(relative,*args):
    print(f'Running project stage: {relative}',flush=True)
    subprocess.run([sys.executable,str(ROOT/relative),*args],cwd=ROOT,check=True)


def raw_manifest(descriptor):
    folder,_=paths(descriptor['run_id'],descriptor['batch_id'])
    if sha(folder/'manifest.json')!=descriptor['raw_manifest_sha256']:
        raise ValueError('Raw batch manifest changed between pipeline tasks')
    result=json.loads((folder/'manifest.json').read_text(encoding='utf-8'))
    if result.get('batch_type')!='new_orders_only' or result['parent_run_id']!=descriptor['run_id'] or result['batch_id']!=descriptor['batch_id']:
        raise ValueError('Raw batch identity/type mismatch')
    return folder,result


def extract(run_id,batch_id):
    folder,_=paths(run_id,batch_id)
    if not folder.exists():
        seed=1000+int(hashlib.sha256((run_id+':'+batch_id).encode()).hexdigest()[:8],16)%1000000000
        cli('src/generate/generate_incremental_batch.py','--run-id',run_id,'--batch-id',batch_id,'--seed',str(seed))
    else:print('Reusing the immutable prepared batch; no regeneration.',flush=True)
    descriptor=dict(run_id=run_id,batch_id=batch_id,raw_manifest_sha256=sha(folder/'manifest.json'))
    folder,manifest=raw_manifest(descriptor)
    if not all((folder/name).is_file() for name in manifest['files']):raise FileNotFoundError('Batch input file missing')
    print('Extract: CSV/JSONL inputs and the previously scraped product snapshot identified.',flush=True)
    return descriptor


def validate(descriptor):
    folder,_=raw_manifest(descriptor)
    sys.path.insert(0,str(ROOT/'src/generate'))
    from validate_incremental_data import validate as validate_files
    report=validate_files(folder)
    if report['status']!='passed':raise ValueError('Raw input validation failed')
    print(json.dumps(report,indent=2),flush=True)
    return descriptor


def canonical(values):
    normalized=[]
    for key,value in zip(FIELDS,values):
        if key in INTEGER_FIELDS:value=str(int(value))
        elif key in AMOUNT_FIELDS:value=format(Decimal(str(value)),'.2f')
        elif key=='is_synthetic':value='true' if value is True or str(value).lower()=='true' else 'false'
        else:value=str(value)
        normalized.append(value)
    return (json.dumps(normalized,separators=(',',':'),ensure_ascii=True)+'\n').encode()


def transform(descriptor):
    import pandas as pd
    folder,manifest=raw_manifest(descriptor)
    _,output=paths(descriptor['run_id'],descriptor['batch_id'])
    options=dict(dtype=str,keep_default_na=False)
    orders=pd.read_csv(folder/'orders.csv',**options)[['order_id','customer_id','ordered_at_utc','order_status','currency']]
    customers=pd.read_csv(folder/'customers.csv',**options)[['customer_id']]
    products=pd.read_csv(folder/'catalog_snapshot.csv',**options)[['asin','final_category']]
    def join(left,right,key):
        merged=left.merge(right,on=key,how='left',validate='many_to_one',indicator=True,sort=False)
        if not merged['_merge'].eq('both').all():raise ValueError(f'Unmatched transform relationship: {key}')
        return merged.drop(columns=['_merge'])
    orders=join(orders,customers,'customer_id').rename(columns={'currency':'order_currency'})
    output.mkdir(parents=True,exist_ok=True)
    signature=hashlib.sha256();rows=0;realized_cents=0;first_order=None;last_order=None;last_key=None
    with tempfile.TemporaryDirectory(prefix='.transform-',dir=output) as tmp:
        candidate=Path(tmp)/'fact_candidates.csv'
        with candidate.open('w',encoding='utf-8',newline='') as f:
            writer=csv.writer(f);writer.writerow(FIELDS)
            for items in pd.read_csv(folder/'order_items.csv',chunksize=100000,**options):
                merged=join(join(items,orders,'order_id'),products,'asin')
                if not merged['currency'].eq(merged['order_currency']).all():raise ValueError('Transform currency mismatch')
                merged['date_key']=pd.to_datetime(merged['ordered_at_utc'],utc=True,errors='raise').dt.strftime('%Y%m%d')
                merged['realized_quantity']=merged['quantity'].where(merged['order_status'].eq('completed'),'0')
                merged['realized_net_amount']=merged['net_amount'].where(merged['order_status'].eq('completed'),'0.00')
                for values in merged[FIELDS].itertuples(index=False,name=None):
                    key=(str(values[0]),int(values[1]))
                    if last_key is not None and key<=last_key:raise ValueError('Transform input is not ordered at unique line grain')
                    last_key=key;first_order=first_order or key[0];last_order=key[0]
                    writer.writerow(values);signature.update(canonical(values));rows+=1
                    realized_cents+=int(Decimal(str(values[9]))*100)
        if rows!=manifest['expected_counts']['order_items']:raise ValueError('Transform row count mismatch')
        report=dict(status='passed',run_id=descriptor['run_id'],batch_id=descriptor['batch_id'],
                    raw_manifest_sha256=descriptor['raw_manifest_sha256'],fact_rows=rows,
                    first_order_id=first_order,last_order_id=last_order,
                    realized_item_revenue_egp=format(Decimal(realized_cents)/100,'.2f'),
                    fact_row_signature=signature.hexdigest(),candidate_sha256=sha(candidate),
                    method='Pandas many-to-one joins with orders, customers and product snapshot; UTC date key and completed-only measures')
        report_path=Path(tmp)/'transform_report.json';report_path.write_text(json.dumps(report,indent=2),encoding='utf-8')
        candidate.replace(output/'fact_candidates.csv');report_path.replace(output/'transform_report.json')
    print(json.dumps(report,indent=2),flush=True)
    return descriptor


def transformation(descriptor):
    raw_manifest(descriptor)
    _,output=paths(descriptor['run_id'],descriptor['batch_id'])
    report=json.loads((output/'transform_report.json').read_text(encoding='utf-8'))
    if (report['run_id'],report['batch_id'],report['raw_manifest_sha256'])!=(descriptor['run_id'],descriptor['batch_id'],descriptor['raw_manifest_sha256']):
        raise ValueError('Transformation report belongs to different inputs')
    if sha(output/'fact_candidates.csv')!=report['candidate_sha256']:raise ValueError('Transformed candidate artifact changed')
    return report


def load(descriptor):
    transformation(descriptor)
    # Retry-safe: store append and warehouse build each have their own commit/checkpoint.
    cli('src/load/load_incremental_batch.py','--run-id',descriptor['run_id'],'--batch-id',descriptor['batch_id'])
    cli('src/warehouse/build_warehouse.py','--run-id',descriptor['run_id'])
    return descriptor


def quality_check(descriptor):
    from orchestration.diagnostics import connect,read_store,read_warehouse,verify_snapshots
    report=transformation(descriptor)
    run_id=descriptor['run_id']
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT manifest_sha256 FROM store.incremental_batches WHERE run_id=%s AND batch_id=%s',(run_id,descriptor['batch_id']))
            ledger=cur.fetchone()
            if ledger is None or ledger[0].strip()!=descriptor['raw_manifest_sha256']:raise ValueError('Batch ledger fingerprint mismatch')
        signature=hashlib.sha256();rows=0
        with conn.cursor(name='verify_pipeline_fact_rows') as cur:
            cur.execute('''SELECT f.order_id,f.line_number,p.asin,c.customer_id,f.date_key,f.order_status,
                f.quantity,f.realized_quantity,f.net_amount,f.realized_net_amount,f.currency,f.price_method,f.is_synthetic
                FROM warehouse.fact_sales f
                JOIN warehouse.dim_product p ON p.run_id=f.run_id AND p.product_key=f.product_key
                JOIN warehouse.dim_customer c ON c.run_id=f.run_id AND c.customer_key=f.customer_key
                WHERE f.run_id=%s AND f.order_id BETWEEN %s AND %s ORDER BY f.order_id,f.line_number''',
                (run_id,report['first_order_id'],report['last_order_id']))
            while True:
                records=cur.fetchmany(10000)
                if not records:break
                for record in records:signature.update(canonical(record));rows+=1
    if rows!=report['fact_rows'] or signature.hexdigest()!=report['fact_row_signature']:
        raise ValueError('Warehouse line-level values differ from the Pandas transformed candidate rows')
    cumulative=verify_snapshots(read_warehouse(read_store(run_id)))
    result=dict(status='passed',run_id=run_id,batch_id=descriptor['batch_id'],
                pandas_candidate_rows_verified=rows,batch_realized_item_revenue_egp=report['realized_item_revenue_egp'],
                cumulative_counts=cumulative['counts'],cumulative_realized_item_revenue_egp=cumulative['realized_item_revenue_egp'],
                verification_read_only=True)
    _,output=paths(run_id,descriptor['batch_id'])
    (output/'pipeline_quality_report.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result,indent=2),flush=True)
    return result
