"""Bounded, sequential catalog collection with page-level batch checkpoints.
Place beside collect_amazon_batch.py in src/extract. Each completed page is
compatible with the existing batch-aware cleaner and loader.
"""
import argparse
import csv
import json
import re
from pathlib import Path
from urllib.parse import urlencode
from datetime import datetime, timezone

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, TimeoutError as BrowserTimeout
from collect_amazon_batch import FIELDS, SELECTOR, parse_products, utc_now

ROOT = Path(__file__).resolve().parents[2]
QUERIES = [
    'computer mouse', 'wireless computer mouse', 'gaming mouse', 'ergonomic mouse',
    'computer keyboard', 'mechanical keyboard', 'wireless keyboard', 'ergonomic keyboard',
    'computer headset', 'usb headset microphone', 'gaming headset', 'wireless computer headset',
    'computer webcam', '1080p webcam', '4k webcam', 'conference webcam',
]


def atomic_json(path, data):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding='utf-8')
    temp.replace(path)


def csv_asins(path):
    with path.open(encoding='utf-8-sig', newline='') as handle:
        reader = csv.DictReader(handle)
        if 'asin' not in (reader.fieldnames or []):
            raise ValueError(f'Missing asin column: {path}')
        return {row['asin'] for row in reader if re.fullmatch(r'[A-Z0-9]{10}', row['asin'])}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--target', type=int, default=1200)
    parser.add_argument('--pages-per-query', type=int, default=10)
    parser.add_argument('--max-pages', type=int, default=20,
                        help='Maximum search requests in this invocation, not total run')
    args = parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9_-]+', args.run_id):
        parser.error('run-id must contain only letters, digits, underscores or hyphens')
    if not (1 <= args.max_pages <= 160 and 1 <= args.pages_per_query <= 10 and 1 <= args.target <= 3000):
        parser.error('Limits: max-pages 1..160; pages-per-query 1..10; target 1..3000')
    run_dir = ROOT / 'data/raw/catalog_runs' / args.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    state_path = run_dir / 'state.json'
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding='utf-8'))
        if state['queries'] != QUERIES or state['pages_per_query'] != args.pages_per_query:
            raise ValueError('Existing run plan differs; use a new run-id')
    else:
        baseline = ROOT / 'data/product_catalog_review.csv'
        if not baseline.exists():
            raise FileNotFoundError(f'Export the existing catalog first: {baseline}')
        state = {'run_id': args.run_id, 'queries': QUERIES,
                 'pages_per_query': args.pages_per_query,
                 'baseline_asins': sorted(csv_asins(baseline)),
                 'completed': {}, 'ended_queries': [], 'status': 'created'}
        atomic_json(state_path, state)

    known = set(state['baseline_asins'])
    for batch_id in state['completed'].values():
        folder = ROOT / 'data/raw/amazon_batches' / batch_id
        manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
        if manifest['status'] != 'complete':
            raise ValueError(f'Invalid completed checkpoint: {batch_id}')
        known.update(csv_asins(folder / 'products.csv'))

    # Recover a committed page if interruption happened just before state write.
    pending = state.get('pending')
    if pending:
        folder = ROOT / 'data/raw/amazon_batches' / pending['batch_id']
        mp = folder / 'manifest.json'
        manifest = json.loads(mp.read_text(encoding='utf-8')) if mp.exists() else {}
        if manifest.get('status') == 'complete' and (folder / 'products.csv').exists():
            state['completed'][pending['key']] = pending['batch_id']
            known.update(csv_asins(folder / 'products.csv'))
            if manifest.get('end_of_query'):
                state['ended_queries'].append(pending['query'])
        state.pop('pending', None)
        atomic_json(state_path, state)

    print(f'Known unique products (baseline + this run): {len(known)}')
    if len(known) >= args.target:
        print('Target already reached. No requests sent.')
        return
    requests_made = 0
    current_folder = None
    current_manifest = None
    state['status'] = 'running'
    atomic_json(state_path, state)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(channel='chrome', headless=False)
            try:
                page = browser.new_context().new_page()
                home = page.goto('https://www.amazon.com/', wait_until='domcontentloaded', timeout=45000)
                if not home or home.status not in (200, 202):
                    raise RuntimeError('Homepage failed; stopping')
                if page.locator('input#captchacharacters, form[action*="validateCaptcha"]').count():
                    raise RuntimeError('Homepage verification detected; stopping')
                for query in QUERIES:
                    if query in state['ended_queries']:
                        continue
                    for number in range(1, args.pages_per_query + 1):
                        key = f'{query}|{number}'
                        if key in state['completed']:
                            continue
                        if requests_made >= args.max_pages or len(known) >= args.target:
                            break
                        page.wait_for_timeout(7000)
                        batch_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
                        current_folder = ROOT / 'data/raw/amazon_batches' / batch_id
                        current_folder.mkdir(parents=True, exist_ok=False)
                        current_manifest = {'batch_id': batch_id, 'query': query,
                            'status': 'running', 'started_at_utc': utc_now(), 'pages': [],
                            'catalog_run_id': args.run_id}
                        atomic_json(current_folder / 'manifest.json', current_manifest)
                        state['pending'] = {'key': key, 'query': query, 'batch_id': batch_id}
                        atomic_json(state_path, state)
                        url = 'https://www.amazon.com/s?' + urlencode({'k': query, 'page': number})
                        requests_made += 1
                        response = page.goto(url, wait_until='domcontentloaded', timeout=45000)
                        status = response.status if response else None
                        if status == 200:
                            try:
                                page.locator(SELECTOR).first.wait_for(state='attached', timeout=15000)
                            except BrowserTimeout:
                                pass
                        html = page.content()
                        collected_at = utc_now()
                        filename = f'page_{number:03d}.html'
                        (current_folder / filename).write_text(html, encoding='utf-8')
                        info = {'page_number': number, 'requested_url': url,
                                'final_url': page.url, 'http_status': status,
                                'page_title': page.title(), 'source_file': filename,
                                'collected_at_utc': collected_at}
                        current_manifest['pages'].append(info)
                        if status != 200 or page.locator('input#captchacharacters, form[action*="validateCaptcha"]').count():
                            raise RuntimeError(f'HTTP {status} or verification on {query}, page {number}')
                        rows = parse_products(html, {'source_file': f'{batch_id}/{filename}',
                            'collected_at_utc': collected_at, 'source_url': page.url,
                            'page_number': number, 'batch_id': batch_id})
                        if not rows:
                            raise RuntimeError('No valid products; inspect HTML before resuming')
                        with (current_folder / 'products.csv').open('x', encoding='utf-8-sig', newline='') as handle:
                            writer = csv.DictWriter(handle, fieldnames=FIELDS)
                            writer.writeheader()
                            writer.writerows(rows)
                        new_ids = {r['asin'] for r in rows} - known
                        known.update(r['asin'] for r in rows)
                        ended = not bool(BeautifulSoup(html, 'html.parser').select_one('a.s-pagination-next[href]'))
                        info['products_found'] = len(rows)
                        current_manifest.update(status='complete', finished_at_utc=utc_now(),
                            unique_products=len(rows), missing_prices=sum(not r['price_raw'] for r in rows),
                            end_of_query=ended)
                        atomic_json(current_folder / 'manifest.json', current_manifest)
                        state['completed'][key] = batch_id
                        state.pop('pending', None)
                        if ended:
                            state['ended_queries'].append(query)
                        atomic_json(state_path, state)
                        print(f'{query} | page {number}: {len(rows)} rows, {len(new_ids)} new | total unique {len(known)} | batch {batch_id}')
                        current_folder = None
                        current_manifest = None
                        if ended:
                            break
                    if requests_made >= args.max_pages or len(known) >= args.target:
                        break
            finally:
                browser.close()
        state['status'] = 'target_reached' if len(known) >= args.target else 'paused_or_plan_exhausted'
    except (Exception, KeyboardInterrupt) as error:
        # A completed manifest is authoritative; preserve pending for crash recovery.
        if current_folder and current_manifest and current_manifest.get('status') != 'complete':
            current_manifest.update(status='failed', error=str(error), finished_at_utc=utc_now())
            atomic_json(current_folder / 'manifest.json', current_manifest)
        state.update(status='stopped_on_error', error=f'{type(error).__name__}: {error}')
        raise
    finally:
        state['unique_products'] = len(known)
        state['updated_at_utc'] = utc_now()
        atomic_json(state_path, state)
        ids_path = run_dir / 'completed_batches.txt'
        ids_path.write_text(''.join(b+'\n' for b in state['completed'].values()), encoding='utf-8')
        print(f'Unique catalog products: {len(known)}')
        print(f'Completed page batches: {len(state["completed"])}')
        print(f'State: {state_path}')
        print(f'Batch list: {ids_path}')
        print('Collection only. Database has not been modified.')


if __name__ == '__main__':
    main()
