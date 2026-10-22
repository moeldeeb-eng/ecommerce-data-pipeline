"""Run from src/extract. Collect at most two search pages into a unique batch.
The existing cleaner/loader are intentionally not invoked.
"""
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import urlencode
import argparse
import csv
import json
import re

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, TimeoutError as BrowserTimeout

SELECTOR = '[data-component-type="s-search-result"][data-asin]'
FIELDS = ['asin', 'product_name', 'price_raw', 'rating', 'ratings_count',
          'product_url', 'source_file', 'parsed_at_utc', 'collected_at_utc',
          'source_url', 'page_number', 'batch_id']


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def text_at(card, selector):
    node = card.select_one(selector)
    return node.get_text(' ', strip=True) if node else None


def parse_products(html, metadata):
    soup = BeautifulSoup(html, 'html.parser')
    parsed_at = utc_now()
    rows = []
    seen = set()
    for card in soup.select(SELECTOR):
        asin = card.get('data-asin', '').strip().upper()
        if not re.fullmatch(r'[A-Z0-9]{10}', asin) or asin in seen:
            continue
        name = text_at(card, 'h2')
        if not name:
            continue
        seen.add(asin)
        rating = re.search(r'(\d+(?:\.\d+)?) out of 5',
                           text_at(card, '.a-icon-alt') or '')
        count_node = card.select_one('a[aria-label$=" ratings"], a[aria-label$=" rating"]')
        count = re.fullmatch(r'([\d,]+) ratings?',
                             count_node.get('aria-label', '') if count_node else '')
        rows.append({
            'asin': asin, 'product_name': name,
            'price_raw': text_at(card, '.a-price:not(.a-text-price) .a-offscreen'),
            'rating': float(rating.group(1)) if rating else None,
            'ratings_count': int(count.group(1).replace(',', '')) if count else None,
            'product_url': f'https://www.amazon.com/dp/{asin}',
            'parsed_at_utc': parsed_at, **metadata,
        })
    return rows


def save_report(folder, report):
    (folder / 'manifest.json').write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--query', default='computer mouse')
    parser.add_argument('--pages', type=int, choices=[1, 2], default=2)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    batch_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    folder = root / 'data/raw/amazon_batches' / batch_id
    folder.mkdir(parents=True, exist_ok=False)
    report = {'batch_id': batch_id, 'query': args.query,
              'requested_pages': args.pages, 'status': 'running',
              'started_at_utc': utc_now(), 'pages': []}
    save_report(folder, report)
    unique = {}
    duplicates = 0
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(channel='chrome', headless=False)
            try:
                context = browser.new_context()
                page = context.new_page()
                home = page.goto('https://www.amazon.com/',
                                 wait_until='domcontentloaded', timeout=45000)
                report['homepage_status'] = home.status if home else None
                # 202 is provisional, never proof of successful collection.
                if home is None or home.status not in (200, 202):
                    (folder / 'homepage_error.html').write_text(page.content(), encoding='utf-8')
                    raise RuntimeError('Homepage failed; see saved HTML.')
                if page.locator('input#captchacharacters, form[action*="validateCaptcha"]').count():
                    (folder / 'homepage_error.html').write_text(page.content(), encoding='utf-8')
                    raise RuntimeError('Homepage verification detected; stopping.')
                page.wait_for_timeout(5000)
                for number in range(1, args.pages + 1):
                    if number > 1:
                        page.wait_for_timeout(5000)
                    url = 'https://www.amazon.com/s?' + urlencode({'k': args.query, 'page': number})
                    response = page.goto(url, wait_until='domcontentloaded', timeout=45000)
                    status = response.status if response else None
                    if status == 200:
                        try:
                            page.locator(SELECTOR).first.wait_for(state='attached', timeout=15000)
                        except BrowserTimeout:
                            pass
                    collected_at = utc_now()
                    html = page.content()
                    filename = f'page_{number:03d}.html'
                    (folder / filename).write_text(html, encoding='utf-8')
                    info = {'page_number': number, 'requested_url': url,
                            'final_url': page.url, 'http_status': status,
                            'page_title': page.title(), 'source_file': filename,
                            'collected_at_utc': collected_at}
                    report['pages'].append(info)
                    if status != 200:
                        raise RuntimeError(f'Page {number}: HTTP {status}; stopping.')
                    if page.locator('input#captchacharacters, form[action*="validateCaptcha"]').count():
                        raise RuntimeError(f'Page {number}: verification detected; stopping.')
                    rows = parse_products(html, {
                        'source_file': f'{batch_id}/{filename}',
                        'collected_at_utc': collected_at, 'source_url': page.url,
                        'page_number': number, 'batch_id': batch_id})
                    info['products_found'] = len(rows)
                    save_report(folder, report)
                    if not rows:
                        raise RuntimeError(f'Page {number}: no valid products; stopping.')
                    for row in rows:
                        if row['asin'] in unique:
                            duplicates += 1
                            continue  # First observation wins; HTML preserves later observations.
                        unique[row['asin']] = row
                    print(f'Page {number}: {len(rows)} products')
            finally:
                browser.close()
        output_file = folder / 'products.csv'
        with output_file.open('x', newline='', encoding='utf-8-sig') as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerows(unique.values())
        report.update(status='complete', unique_products=len(unique),
                      duplicates_skipped=duplicates,
                      missing_prices=sum(not r['price_raw'] for r in unique.values()))
        print(f'Unique products: {len(unique)}')
        print(f'Duplicates skipped: {duplicates}')
        print(f'Missing prices: {report["missing_prices"]}')
        print(f'Batch CSV: {output_file}')
    except Exception as error:
        report.update(status='failed', error=f'{type(error).__name__}: {error}')
        print(f'Batch failed: {error}')
        print(f'Diagnostics: {folder}')
        raise
    finally:
        report['finished_at_utc'] = utc_now()
        save_report(folder, report)


if __name__ == '__main__':
    main()
