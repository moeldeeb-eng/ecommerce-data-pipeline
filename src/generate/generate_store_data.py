"""Generate reproducible fictional store data from a reviewed product catalog.
No network requests, database writes, or third-party dependencies.
"""
import argparse
import csv
import hashlib
import json
import random
import re
import shutil
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from statistics import median

ROOT = Path(__file__).resolve().parents[2]
VERSION = '1.0.0'
START = datetime(2025, 1, 1, tzinfo=timezone.utc)
END = datetime(2026, 10, 21, 23, 0, tzinfo=timezone.utc)
EXPLICIT_FALLBACK = {'3D input controllers': 500000}  # 5000.00 EGP, arbitrary project assumption
CUSTOMER_FIELDS = ['customer_id','customer_name','email','city','country','registered_at_utc','is_synthetic']
ORDER_FIELDS = ['order_id','customer_id','ordered_at_utc','order_status','currency','items_count','subtotal','discount_amount','net_items_amount','shipping_amount','order_total','is_synthetic']
ITEM_FIELDS = ['order_id','line_number','asin','quantity','unit_price','discount_percent','gross_amount','discount_amount','net_amount','currency','price_method','is_synthetic']
PRICE_FIELDS = ['asin','category','source_price','source_currency','anchor_price','anchor_currency','anchor_method','is_synthetic_anchor']

def money(cents):
    return f'{cents // 100}.{cents % 100:02d}'

def cents(value):
    v = Decimal(value)
    if not v.is_finite() or v <= 0:
        raise ValueError(f'Price must be finite and positive: {value}')
    if v != v.quantize(Decimal('.01')):
        raise ValueError(f'Price has more than two decimal places: {value}')
    return int(v * 100)

def hashfile(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()

def stamp(dt):
    return dt.isoformat().replace('+00:00','Z')

def load_catalog(path):
    with path.open(encoding='utf-8-sig',newline='') as f:
        reader = csv.DictReader(f)
        required = {'asin','final_category','simulation_scope','price','currency'}
        if not required <= set(reader.fieldnames or []):
            raise ValueError(f'Catalog requires {sorted(required)}')
        rows = list(reader)
    if not rows or len({r['asin'] for r in rows}) != len(rows):
        raise ValueError('Catalog must be nonempty with unique ASINs')
    groups = defaultdict(list)
    for r in rows:
        if r['simulation_scope'] != 'include' or not re.fullmatch(r'[A-Z0-9]{10}',r['asin']):
            raise ValueError(f'Invalid or ineligible product: {r["asin"]}')
        if r['price']:
            if r['currency'] != 'EGP':
                raise ValueError('This simulation supports EGP only; no currency conversion is assumed')
            groups[r['final_category']].append(cents(r['price']))
        elif r['currency']:
            raise ValueError('Missing source price should have missing source currency')
    result = []
    for r in sorted(rows,key=lambda x:x['asin']):
        category = r['final_category']
        if r['price']:
            anchor, method = cents(r['price']), 'observed_snapshot_anchor'
        elif groups[category]:
            anchor = int(Decimal(str(median(groups[category]))).quantize(Decimal('1'),rounding=ROUND_HALF_UP))
            method = 'category_median_anchor'
        elif category in EXPLICIT_FALLBACK:
            anchor, method = EXPLICIT_FALLBACK[category], 'explicit_project_fallback'
        else:
            raise ValueError(f'No observed prices or explicit fallback for {category}')
        result.append(dict(asin=r['asin'],category=category,source_price=r['price'],
                           source_currency=r['currency'],anchor_price=money(anchor),anchor_currency='EGP',
                           anchor_method=method,is_synthetic_anchor='true'))
    return result

def writer(handle, fields):
    w = csv.DictWriter(handle,fieldnames=fields)
    w.writeheader()
    return w

def generate(args):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}',args.run_id):
        raise ValueError('run-id must contain letters, numbers, underscores or hyphens, max 64 chars')
    if not 1 <= args.customers <= args.orders or not args.orders <= args.items <= 5 * args.orders:
        raise ValueError('Require 1 <= customers <= orders and orders <= items <= 5*orders')
    catalog = (ROOT / args.catalog).resolve()
    products = load_catalog(catalog)
    if len(products) < 5:
        raise ValueError('At least five catalog products are required')
    dest = ROOT / 'data' / 'raw' / 'synthetic_runs' / args.run_id
    pending = dest.with_name(dest.name + '.partial')
    if dest.exists() or pending.exists():
        raise FileExistsError(f'Run already exists: {dest}. Use a new run-id; existing files are never overwritten.')
    pending.mkdir(parents=True)
    try:
        # Store the exact input snapshot so the run stays independently auditable.
        shutil.copyfile(catalog,pending/'catalog_snapshot.csv')
        with (pending/'product_price_anchors.csv').open('w',encoding='utf-8',newline='') as f:
            writer(f,PRICE_FIELDS).writerows(products)
        rng = random.Random(args.seed)
        cities = ['Cairo','Giza','Alexandria','Banha','Mansoura','Tanta','Asyut','Zagazig']
        with (pending/'customers.csv').open('w',encoding='utf-8',newline='') as f:
            w = writer(f,CUSTOMER_FIELDS)
            for i in range(1,args.customers+1):
                w.writerow(dict(customer_id=f'C{i:09d}',customer_name=f'Synthetic Customer {i:09d}',
                                email=f'customer{i:09d}@example.invalid',city=rng.choice(cities),country='EG',
                                registered_at_utc=stamp(datetime(2024,1,1,tzinfo=timezone.utc)+timedelta(days=rng.randrange(366))),
                                is_synthetic='true'))
        # Allocate exactly the requested line count, with one to five distinct ASINs per order.
        counts = [1] * args.orders
        for _ in range(args.items-args.orders):
            pos = rng.randrange(args.orders)
            while counts[pos] == 5:
                pos = rng.randrange(args.orders)
            counts[pos] += 1
        customer_ids = list(range(1,args.customers+1))
        rng.shuffle(customer_ids)
        eligible_indices = list(range(len(products)))
        with (pending/'orders.csv').open('w',encoding='utf-8',newline='') as of, \
             (pending/'order_items.csv').open('w',encoding='utf-8',newline='') as itf, \
             (pending/'payments.jsonl').open('w',encoding='utf-8',newline='') as pf:
            ow, iw = writer(of,ORDER_FIELDS), writer(itf,ITEM_FIELDS)
            for i, count in enumerate(counts,1):
                order_id = f'O{i:09d}'
                cust = customer_ids[i-1] if i <= args.customers else rng.randint(1,args.customers)
                ordered = START + timedelta(seconds=rng.randrange(int((END-START).total_seconds())+1))
                status = rng.choices(['completed','cancelled','payment_failed'],weights=[90,6,4],k=1)[0]
                selected = rng.sample(eligible_indices,count)
                subtotal = discount_total = 0
                for line, idx in enumerate(selected,1):
                    p = products[idx]
                    anchor = cents(p['anchor_price'])
                    unit = max(1,(anchor * rng.randint(85,115)+50)//100)
                    qty = rng.choices([1,2,3,4],weights=[75,17,6,2],k=1)[0]
                    discount = rng.choice([0,0,0,5,10,15])
                    gross = unit * qty
                    reduction = (gross * discount + 50)//100
                    subtotal += gross
                    discount_total += reduction
                    iw.writerow(dict(order_id=order_id,line_number=line,asin=p['asin'],quantity=qty,
                                     unit_price=money(unit),discount_percent=discount,gross_amount=money(gross),
                                     discount_amount=money(reduction),net_amount=money(gross-reduction),
                                     currency='EGP',price_method=p['anchor_method']+'_synthetic_variation',is_synthetic='true'))
                net = subtotal-discount_total
                shipping = 0 if net >= 200000 else rng.choice([4000,5000,6000])
                total = net+shipping
                ow.writerow(dict(order_id=order_id,customer_id=f'C{cust:09d}',ordered_at_utc=stamp(ordered),
                                 order_status=status,currency='EGP',items_count=count,subtotal=money(subtotal),
                                 discount_amount=money(discount_total),net_items_amount=money(net),
                                 shipping_amount=money(shipping),order_total=money(total),is_synthetic='true'))
                payment = dict(payment_id=f'P{i:09d}',order_id=order_id,currency='EGP',
                               payment_method=rng.choice(['card','mobile_wallet','bank_transfer']),
                               payment_status={'completed':'paid','cancelled':'cancelled','payment_failed':'failed'}[status],
                               amount_due=money(total),amount_paid=money(total if status=='completed' else 0),
                               attempted_at_utc=stamp(ordered+timedelta(minutes=2)),
                               paid_at_utc=stamp(ordered+timedelta(minutes=3)) if status=='completed' else None,
                               is_synthetic=True)
                pf.write(json.dumps(payment,separators=(',',':'))+'\n')
                if i % 50000 == 0:
                    print(f'Generated orders: {i:,}/{args.orders:,}',flush=True)
        files = sorted(p.name for p in pending.iterdir() if p.is_file())
        manifest = dict(status='complete',generator_version=VERSION,seed=args.seed,run_id=args.run_id,
                        expected_counts=dict(customers=args.customers,orders=args.orders,order_items=args.items,payments=args.orders,products=len(products)),
                        source_catalog_sha256=hashfile(catalog),start_utc=stamp(START),end_utc=stamp(END),
                        anchor_method_counts=dict(Counter(p['anchor_method'] for p in products)),
                        explicit_fallback_egp={k:money(v) for k,v in EXPLICIT_FALLBACK.items()},
                        assumptions=['All customers, orders, payments and sale prices are fictional.',
                         'Snapshot catalog prices are simulation anchors, not historical Amazon prices.',
                         'Uniform product and date sampling; proportions are arbitrary project assumptions.',
                         'Completed orders count as realized simulated sales; cancelled/failed orders do not.',
                         'No returns, partial payments, tax breakdown or real payment processing modeled.',
                         'EGP amounts use integer cents, half-up rounding; shipping is order-level only.'],
                        files={name:dict(sha256=hashfile(pending/name),bytes=(pending/name).stat().st_size) for name in files})
        (pending/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
        from validate_store_data import validate
        report = validate(pending)
        (pending/'validation_report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
        pending.rename(dest)
        print(json.dumps(report,indent=2))
        print(f'Output: {dest}')
    except Exception:
        print(f'Incomplete run retained for diagnosis: {pending}',file=sys.stderr)
        raise

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run-id',required=True)
    p.add_argument('--catalog',default='data/reference/catalog_final/simulation_products.csv')
    p.add_argument('--customers',type=int,default=100000)
    p.add_argument('--orders',type=int,default=400000)
    p.add_argument('--items',type=int,default=1000000)
    p.add_argument('--seed',type=int,default=42)
    generate(p.parse_args())

if __name__=='__main__':
    main()
