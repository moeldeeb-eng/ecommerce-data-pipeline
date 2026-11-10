"""Migrate an existing run atomically, checking keys, fact rows and reporting totals."""
import argparse
import json
import os
from pathlib import Path
import psycopg
from dotenv import dotenv_values
from build_warehouse import build, ROOT


def migrate(cur, run_id):
    cur.execute("SELECT pg_advisory_xact_lock(hashtextextended('ecommerce_store_loader_v1',0))")
    cur.execute('SELECT 1 FROM warehouse.build_runs WHERE run_id=%s',(run_id,))
    if cur.fetchone() is None:
        raise ValueError('Migration requires an existing warehouse build')
    # Actual rows, not only aggregates: EXCEPT ALL below catches any changed line.
    cur.execute('CREATE TEMP TABLE _before_facts ON COMMIT DROP AS SELECT * FROM warehouse.fact_sales WHERE run_id=%s',(run_id,))
    cur.execute('CREATE TEMP TABLE _before_customers ON COMMIT DROP AS SELECT customer_key,customer_id FROM warehouse.dim_customer WHERE run_id=%s',(run_id,))
    cur.execute('CREATE TEMP TABLE _before_products ON COMMIT DROP AS SELECT product_key,asin FROM warehouse.dim_product WHERE run_id=%s',(run_id,))
    result=build(cur,run_id)
    for name,query in [
        ('facts','SELECT * FROM warehouse.fact_sales WHERE run_id=%s'),
        ('customers','SELECT customer_key,customer_id FROM warehouse.dim_customer WHERE run_id=%s'),
        ('products','SELECT product_key,asin FROM warehouse.dim_product WHERE run_id=%s')]:
        cur.execute(f'''SELECT EXISTS(
            (SELECT * FROM _before_{name} EXCEPT ALL {query})
            UNION ALL ({query} EXCEPT ALL SELECT * FROM _before_{name}))''',(run_id,run_id))
        if cur.fetchone()[0]:
            raise ValueError(f'Migration changed existing {name}; transaction will roll back')
    cur.execute('''SELECT count(*),coalesce(sum(realized_net_amount),0)
        FROM reporting.fact_sales f
        JOIN reporting.dim_customer c ON c.customer_key=f.customer_key AND c.run_id=f.run_id
        JOIN reporting.dim_location l ON l.location_key=c.location_key
        JOIN reporting.dim_product p ON p.product_key=f.product_key AND p.run_id=f.run_id
        JOIN reporting.dim_date d ON d.date_key=f.date_key
        WHERE f.run_id=%s''',(run_id,))
    count,revenue=cur.fetchone()
    if count!=result['counts']['fact_sales'] or str(revenue)!=result['realized_item_revenue_egp']:
        raise ValueError('Reporting joins changed sales count/revenue')
    result.update(existing_fact_rows_unchanged=True,dimension_keys_preserved=True,
                  reporting_joins_reconciled=True)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id',required=True)
    args=parser.parse_args()
    config={**dotenv_values(ROOT/'.env'),**os.environ}
    for key in ['POSTGRES_DB','POSTGRES_USER','POSTGRES_PASSWORD']:
        if not config.get(key): raise ValueError(f'Missing configuration: {key}')
    with psycopg.connect(host=config.get('POSTGRES_HOST','127.0.0.1'),
        port=int(config.get('POSTGRES_PORT','5433')),dbname=config['POSTGRES_DB'],
        user=config['POSTGRES_USER'],password=config['POSTGRES_PASSWORD'],connect_timeout=10) as conn:
        with conn.cursor() as cur:
            result=migrate(cur,args.run_id)
    result['committed']=True
    print(json.dumps(result,indent=2))

if __name__=='__main__':main()
