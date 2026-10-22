"""Classify titles conservatively; suggestions are not verified Amazon categories.
Usage: python src/transform/review_product_catalog.py
"""
from pathlib import Path
import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from decimal import Decimal
from statistics import median

ROOT = Path(__file__).resolve().parents[2]
# Evidence here is confined to the supplied title; ambiguous models remain review-only.
EXCLUSIONS = {
 'B0GMY8QCXQ': 'Title identifies an all-in-one computer, outside peripheral scope',
 'B0BVBVPSX6': 'Title identifies an aviation headset, outside computer headset scope',
 'B097QZGRCQ': 'Title identifies an LED streaming light, outside core categories',
 'B0CCK6LFX2': 'Title identifies video conference lighting, outside core categories',
 'B0BVZLS5GZ': 'Title identifies a conference speaker/microphone, not a headset or webcam',
 'B08BJDVWZ6': 'Title identifies a mouse pad accessory',
 'B0D1T1HZCC': 'Title identifies a mouse pad accessory',
 'B0DCVVZ6CT': 'Title identifies a wrist rest support set',
 'B0DXV94T45': 'Title identifies a mouse pad/keyboard stand wrist rest set',
 'B0FWR39T8G': 'Title identifies a keyboard stand/mouse pad wrist rest set',
}
EXTRA = ['suggested_category','classification_status','classification_basis',
         'suggested_scope','review_reason','price_missing','rating_missing']


def classify(row):
    title = row['product_name'].casefold()
    normalized = re.sub(r'[-_/]', ' ', title)
    if row['asin'] in EXCLUSIONS:
        return 'Outside core scope', 'title_rule', 'specific title evidence', 'exclude', EXCLUSIONS[row['asin']]
    # Avoid identifying an accessory as a device just because its name mentions one.
    if re.search(r'^(?:.{0,55})\b(?:mouse\s*pad|wrist rest|keyboard stand)\b', normalized):
        return 'Accessory or bundle', 'needs_review', 'accessory wording near title start', 'review', 'Verify accessory versus a full device bundle'
    mouse = bool(re.search(r'\b(?:mouse|mice|trackball|rollermouse)\b', normalized))
    keyboard = bool(re.search(r'\b(?:keyboard|keyboards|keypad|keypads)\b', normalized))
    audio = bool(re.search(r'\b(?:headset|headsets|headphones|headphone|earbuds|earphones|earpiece)\b', normalized))
    webcam = bool(re.search(r'\b(?:webcam|webcams|web cam)\b', normalized))
    camera = bool(re.search(r'\b(?:camera|cameras)\b', normalized))
    conferencing = bool(re.search(r'\b(?:conference|conferencing|ptz|panacast|meetup)\b', normalized))
    video = webcam or (camera and conferencing) or bool(re.search(r'\bvideo conferencing (?:system|solution)\b', normalized))
    if sum([mouse, keyboard, audio, video]) > 1:
        return 'Device bundle or mixed title', 'needs_review', 'multiple device families in title', 'review', 'Choose bundle category or confirm which words describe the sold product'
    if keyboard:
        category = 'Keypads' if re.search(r'\bkeypads?\b', normalized) else 'Keyboards'
    elif mouse:
        category = 'Mice and trackballs'
    elif audio:
        if re.search(r'\b(?:earbuds|earphones)\b', normalized):
            return 'Earbuds or earphones', 'needs_review', 'audio title keyword', 'review', 'Confirm suitability for the computer-peripheral scope'
        category = 'Headsets' if re.search(r'\bheadsets?\b', normalized) else 'Headphones and earpieces'
    elif video:
        category = 'Conference cameras' if conferencing else 'Webcams'
    else:
        return 'Unclassified', 'needs_review', 'insufficient explicit title evidence', 'review', 'Do not infer category from search query alone'
    return category, 'title_rule', 'explicit device-family keyword in title', 'include', ''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', default='data/product_catalog_full.csv')
    parser.add_argument('--output-dir', default='data/reference/catalog_review')
    args = parser.parse_args()
    inp = Path(args.input)
    if not inp.is_absolute(): inp = ROOT / inp
    out = Path(args.output_dir)
    if not out.is_absolute(): out = ROOT / out
    with inp.open(encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f); fields = reader.fieldnames; rows = list(reader)
    if not rows or not {'asin','product_name','price','currency','rating'} <= set(fields or []):
        raise ValueError('Missing catalog rows or required columns')
    if len({r['asin'] for r in rows}) != len(rows):
        raise ValueError('Catalog must have unique ASINs')
    annotated=[]; price_groups=defaultdict(list)
    for row in rows:
        category,status,basis,scope,reason=classify(row)
        enriched={**row, **dict(zip(EXTRA,[category,status,basis,scope,reason,
                                           'yes' if not row['price'] else 'no',
                                           'yes' if not row['rating'] else 'no']))}
        annotated.append(enriched)
        if scope=='include' and row['price'] and row['currency']:
            price=Decimal(row['price'])
            if not price.is_finite() or price<=0:
                raise ValueError(f'Invalid positive price: {row["asin"]}')
            price_groups[(category,row['currency'])].append(price)
    out.mkdir(parents=True,exist_ok=True)
    for name, subset in [('catalog_classified.csv',annotated),
                          ('catalog_needs_review.csv',[r for r in annotated if r['suggested_scope']!='include'])]:
        with (out/name).open('w',encoding='utf-8-sig',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=fields+EXTRA);writer.writeheader();writer.writerows(subset)
    stats=[]
    for (category,currency),prices in sorted(price_groups.items()):
        stats.append({'suggested_category':category,'currency':currency,
            'observed_price_count':len(prices),'median_observed_price':str(median(prices)),
            'minimum_observed_price':str(min(prices)),'maximum_observed_price':str(max(prices))})
    with (out/'category_price_reference.csv').open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=['suggested_category','currency','observed_price_count',
            'median_observed_price','minimum_observed_price','maximum_observed_price'])
        writer.writeheader();writer.writerows(stats)
    report={'total_products':len(rows),'unique_asins':len({r['asin'] for r in rows}),
        'missing_prices':sum(not r['price'] for r in rows),
        'missing_ratings':sum(not r['rating'] for r in rows),
        'suggested_scope_counts':dict(Counter(r['suggested_scope'] for r in annotated)),
        'suggested_category_counts':dict(Counter(r['suggested_category'] for r in annotated)),
        'method':'Conservative title-based suggestions; not authoritative Amazon category metadata',
        'prices_modified':False,'database_modified':False}
    (out/'catalog_review_summary.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2));print(f'Outputs: {out}')


if __name__=='__main__':main()
