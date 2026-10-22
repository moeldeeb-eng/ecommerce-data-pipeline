"""Stream checks of generated store files, keys, arithmetic and file hashes."""
import argparse
import csv
import itertools
import json
from collections import Counter
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from generate_store_data import ROOT, hashfile, load_catalog

def check(condition, message):
    if not condition:
        raise ValueError(message)

def amount(text):
    value = Decimal(text)
    check(value.is_finite() and value>=0 and value==value.quantize(Decimal('.01')),f'Invalid amount: {text}')
    return int(value*100)

def time(text):
    dt = datetime.fromisoformat(text.replace('Z','+00:00'))
    check(dt.tzinfo is not None,'Timestamp requires timezone')
    return dt

def validate(folder):
    manifest = json.loads((folder/'manifest.json').read_text(encoding='utf-8'))
    check(manifest['status']=='complete','Incomplete manifest')
    expected_files = {'catalog_snapshot.csv','product_price_anchors.csv','customers.csv','orders.csv','order_items.csv','payments.jsonl'}
    check(set(manifest['files'])==expected_files,'Manifest file list mismatch')
    for name, info in manifest['files'].items():
        path = folder/name
        check(path.stat().st_size==info['bytes'] and hashfile(path)==info['sha256'],f'File integrity mismatch: {name}')
    check(hashfile(folder/'catalog_snapshot.csv')==manifest['source_catalog_sha256'],'Catalog snapshot hash mismatch')
    expected = manifest['expected_counts']
    with (folder/'product_price_anchors.csv').open(encoding='utf-8',newline='') as f:
        anchors_list = list(csv.DictReader(f))
    check(anchors_list==load_catalog(folder/'catalog_snapshot.csv'),'Price anchors differ from catalog rules')
    anchors = {r['asin']:r for r in anchors_list}
    check(len(anchors)==expected['products'],'Product count mismatch')
    customers = {}
    with (folder/'customers.csv').open(encoding='utf-8',newline='') as f:
        for i,r in enumerate(csv.DictReader(f),1):
            check(r['customer_id']==f'C{i:09d}','Customer key sequence mismatch')
            check(r['is_synthetic']=='true' and r['email']==f'customer{i:09d}@example.invalid','Invalid synthetic customer')
            customers[r['customer_id']]=time(r['registered_at_utc'])
    check(len(customers)==expected['customers'],'Customer count mismatch')
    counts = Counter()
    used_customers, used_products = set(), set()
    start,end = time(manifest['start_utc']),time(manifest['end_utc'])
    with (folder/'orders.csv').open(encoding='utf-8',newline='') as of, \
         (folder/'order_items.csv').open(encoding='utf-8',newline='') as itf, \
         (folder/'payments.jsonl').open(encoding='utf-8') as pf:
        orders = csv.DictReader(of)
        item_groups = itertools.groupby(csv.DictReader(itf),key=lambda r:r['order_id'])
        for i,triple in enumerate(itertools.zip_longest(orders,item_groups,pf),1):
            order,group,payment_line = triple
            check(order is not None and group is not None and payment_line is not None,'Order/item/payment groups have different lengths')
            order_id = f'O{i:09d}'
            check(order['order_id']==order_id and group[0]==order_id,'Order/item key or sort mismatch')
            check(order['is_synthetic']=='true' and order['currency']=='EGP','Invalid order provenance/currency')
            cust=order['customer_id']
            check(cust in customers,'Unknown customer')
            ordered=time(order['ordered_at_utc'])
            check(customers[cust]<=ordered and start<=ordered<=end,'Invalid order/customer date')
            used_customers.add(cust)
            items=list(group[1])
            check(1<=len(items)<=5 and len(items)==int(order['items_count']),'Invalid item count')
            seen=set()
            subtotal=discount_total=0
            for line,r in enumerate(items,1):
                asin=r['asin']
                check(asin in anchors and asin not in seen,'Unknown or repeated ASIN within order')
                check(int(r['line_number'])==line,'Duplicate/missing line number')
                check(r['is_synthetic']=='true' and r['currency']=='EGP','Invalid item provenance/currency')
                check(r['price_method']==anchors[asin]['anchor_method']+'_synthetic_variation','Price method mismatch')
                seen.add(asin);used_products.add(asin)
                qty,unit,discount=int(r['quantity']),amount(r['unit_price']),int(r['discount_percent'])
                check(1<=qty<=4 and unit>0 and discount in {0,5,10,15},'Invalid quantity/price/discount')
                anchor=amount(anchors[asin]['anchor_price'])
                check(max(1,(anchor*85+50)//100)<=unit<=max(1,(anchor*115+50)//100),'Unit price outside simulated range')
                gross=unit*qty
                reduction=(gross*discount+50)//100
                check(amount(r['gross_amount'])==gross and amount(r['discount_amount'])==reduction and amount(r['net_amount'])==gross-reduction,'Item arithmetic mismatch')
                subtotal+=gross;discount_total+=reduction;counts['order_items']+=1
            net=subtotal-discount_total
            shipping=amount(order['shipping_amount'])
            check(shipping==0 if net>=200000 else shipping in {4000,5000,6000},'Shipping rule mismatch')
            total=net+shipping
            for field,wanted in [('subtotal',subtotal),('discount_amount',discount_total),('net_items_amount',net),('order_total',total)]:
                check(amount(order[field])==wanted,f'Order {field} mismatch')
            payment=json.loads(payment_line)
            status=order['order_status']
            check(status in {'completed','cancelled','payment_failed'},'Invalid order status')
            check(payment['order_id']==order_id and payment['payment_id']==f'P{i:09d}','Payment key mismatch')
            check(payment['is_synthetic'] is True and payment['currency']=='EGP','Invalid payment provenance/currency')
            check(payment['payment_method'] in {'card','mobile_wallet','bank_transfer'},'Invalid payment method')
            check(payment['payment_status']=={'completed':'paid','cancelled':'cancelled','payment_failed':'failed'}[status],'Payment/order status mismatch')
            check(amount(payment['amount_due'])==total and amount(payment['amount_paid'])==(total if status=='completed' else 0),'Payment amount mismatch')
            check(time(payment['attempted_at_utc'])>=ordered,'Payment precedes order')
            if status=='completed':
                check(payment['paid_at_utc'] is not None and time(payment['paid_at_utc'])>=time(payment['attempted_at_utc']),'Invalid paid timestamp')
            else:
                check(payment['paid_at_utc'] is None,'Unpaid order has paid timestamp')
            counts['orders']+=1;counts['payments']+=1;counts[status]+=1
    for field in ['orders','payments','order_items']:
        check(counts[field]==expected[field],f'{field} count mismatch')
    check(len(used_customers)==expected['customers'],'Not all generated customers are referenced')
    return dict(status='passed',customers=len(customers),orders=counts['orders'],
                order_items=counts['order_items'],payments=counts['payments'],
                order_status_counts={k:counts[k] for k in ['completed','cancelled','payment_failed']},
                catalog_products=len(anchors),products_used=len(used_products),
                foreign_keys_valid=True,amounts_reconciled=True,hashes_verified=True,
                source_snapshot_preserved=True,database_modified=False)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run-id',required=True)
    args=p.parse_args()
    import re
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}',args.run_id):
        raise ValueError('Invalid run-id')
    report=validate(ROOT/'data'/'raw'/'synthetic_runs'/args.run_id)
    print(json.dumps(report,indent=2))

if __name__=='__main__':
    main()
