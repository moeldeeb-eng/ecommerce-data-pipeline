"""Apply title-review decisions without changing source values or database."""
import argparse
import csv
import json
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path
from statistics import median

ROOT = Path(__file__).resolve().parents[2]

def read(path):
    with path.open(encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f)
        return reader.fieldnames, list(reader)

def write(path, fields, rows):
    with path.open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input', default='data/reference/catalog_review/catalog_classified.csv')
    p.add_argument('--decisions', default='data/reference/catalog_review/catalog_decisions.csv')
    p.add_argument('--output-dir', default='data/reference/catalog_final')
    args = p.parse_args()
    fields, rows = read(ROOT / args.input)
    _, decisions = read(ROOT / args.decisions)
    lookup = {r['asin']: r for r in rows}
    if len(lookup) != len(rows):
        raise ValueError('Duplicate catalog ASINs')
    overrides = {r['asin']: r for r in decisions}
    if len(overrides) != len(decisions):
        raise ValueError('Duplicate decisions')
    for asin, d in overrides.items():
        if asin not in lookup or lookup[asin]['product_name'] != d['expected_product_name']:
            raise ValueError(f'Decision title does not match catalog: {asin}')
        if d['simulation_scope'] not in {'include', 'exclude', 'hold'}:
            raise ValueError(f'Invalid scope: {asin}')
    added = ['final_category', 'simulation_scope', 'decision_basis', 'decision_reason']
    if set(added) & set(fields):
        raise ValueError('Input already contains final decision columns; use catalog_classified.csv')
    result = []
    groups = defaultdict(list)
    for r in rows:
        d = overrides.get(r['asin'])
        if d:
            extra = {k:d[k] for k in added}
        elif r['suggested_scope'] == 'include':
            extra = dict(final_category=r['suggested_category'], simulation_scope='include',
                         decision_basis='automated_title_suggestion',
                         decision_reason='Accepted title rule for project scope; not verified Amazon taxonomy')
        else:
            raise ValueError(f'Unresolved review row: {r["asin"]}')
        merged = {**r, **extra}
        result.append(merged)
        if extra['simulation_scope']=='include' and r['price'] and r['currency']:
            value = Decimal(r['price'])
            if not value.is_finite() or value <= 0:
                raise ValueError(f'Invalid observed price: {r["asin"]}')
            groups[(extra['final_category'],r['currency'])].append(value)
    # Source columns must remain byte-for-byte equivalent as parsed CSV strings.
    assert all(all(a[k] == b[k] for k in fields) for a,b in zip(rows,result))
    out = ROOT / args.output_dir
    out.mkdir(parents=True,exist_ok=True)
    write(out/'catalog_final.csv', fields+added, result)
    write(out/'simulation_products.csv', fields+added,
          [r for r in result if r['simulation_scope']=='include'])
    write(out/'excluded_or_held.csv', fields+added,
          [r for r in result if r['simulation_scope']!='include'])
    refs = [dict(category=c,currency=curr,observed_price_count=len(v),
                 median_observed_price=str(median(v)),min_observed_price=str(min(v)),
                 max_observed_price=str(max(v))) for (c,curr),v in sorted(groups.items())]
    write(out/'category_price_reference.csv',
          ['category','currency','observed_price_count','median_observed_price','min_observed_price','max_observed_price'],refs)
    summary = dict(total_products=len(rows), unique_asins=len(lookup),
                   simulation_scope_counts=dict(Counter(r['simulation_scope'] for r in result)),
                   missing_source_prices=sum(not r['price'] for r in result),
                   missing_source_ratings=sum(not r['rating'] for r in result),
                   source_values_preserved=True,database_modified=False,
                   taxonomy='Project categories based on titles; not authoritative Amazon metadata')
    (out/'catalog_final_summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    print(json.dumps(summary,indent=2))
    print(f'Outputs: {out}')

if __name__=='__main__':
    main()
