"""Clean and load all completed page batches for a single catalog run.
Use existing src/transform/clean_products.py and src/load/load_products.py.
"""
from pathlib import Path
from datetime import datetime, timezone
import argparse
import csv
import hashlib
import json
import os
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def now():
    return datetime.now(timezone.utc).isoformat()


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def atomic_json(path, data):
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding='utf-8')
    tmp.replace(path)


def read_rows(path):
    with path.open(encoding='utf-8-sig', newline='') as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or 'asin' not in reader.fieldnames:
            raise ValueError(f'Invalid CSV header: {path}')
        return list(reader)


def connect_db():
    import psycopg
    from dotenv import dotenv_values
    config = dotenv_values(ROOT / '.env')
    for key in ('POSTGRES_DB', 'POSTGRES_USER', 'POSTGRES_PASSWORD'):
        if not config.get(key):
            raise ValueError(f'Missing configuration: {key}')
    return psycopg.connect(
        host=config.get('POSTGRES_HOST') or '127.0.0.1',
        port=int(config.get('POSTGRES_PORT') or 5433),
        dbname=config['POSTGRES_DB'], user=config['POSTGRES_USER'],
        password=config['POSTGRES_PASSWORD'], connect_timeout=10)


def run_child(script, batch_id, log_path):
    env = os.environ.copy()
    env['PYTHONIOENCODING'] = 'utf-8'
    result = subprocess.run(
        [sys.executable, str(script), '--batch-id', batch_id],
        cwd=ROOT, env=env, capture_output=True, text=True,
        encoding='utf-8', errors='replace', timeout=180)
    log_path.write_text(result.stdout + '\n' + result.stderr, encoding='utf-8')
    if result.returncode:
        raise RuntimeError(f'{script.name} failed for {batch_id}; see {log_path}')
    return result.stdout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--clean-only', action='store_true')
    args = parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9_-]+', args.run_id):
        parser.error('Invalid run ID')
    run_dir = ROOT / 'data/raw/catalog_runs' / args.run_id
    state = read_json(run_dir / 'state.json')
    if state.get('run_id') != args.run_id or not state.get('completed'):
        raise ValueError('Missing or mismatched run state')
    cleaner = ROOT / 'src/transform/clean_products.py'
    loader = ROOT / 'src/load/load_products.py'
    if not cleaner.exists() or not loader.exists():
        raise FileNotFoundError('Keep the existing cleaner and loader in their project folders')
    # Never load folders by glob: the completed mapping is the authoritative list.
    jobs = []
    seen_batches = set()
    raw_union = set()
    for key, batch_id in state['completed'].items():
        if not re.fullmatch(r'[A-Za-z0-9_-]+', batch_id) or batch_id in seen_batches:
            raise ValueError(f'Unsafe or repeated batch ID: {batch_id}')
        seen_batches.add(batch_id)
        query, page_number = key.rsplit('|', 1)
        folder = ROOT / 'data/raw/amazon_batches' / batch_id
        manifest = read_json(folder / 'manifest.json')
        if (manifest.get('status') != 'complete'
                or manifest.get('batch_id') != batch_id
                or manifest.get('query') != query):
            raise ValueError(f'Invalid completed manifest: {batch_id}')
        rows = read_rows(folder / 'products.csv')
        if not rows or any(r.get('batch_id') != batch_id
                           or r.get('page_number') != page_number for r in rows):
            raise ValueError(f'Empty or mismatched raw batch: {batch_id}')
        if len({r['asin'] for r in rows}) != len(rows):
            raise ValueError(f'Duplicate ASIN within raw page: {batch_id}')
        raw_union.update(r['asin'] for r in rows)
        jobs.append((batch_id, rows, folder / 'products.csv'))
    jobs.sort(key=lambda item: item[0])
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    report_dir = ROOT / 'data/processed/catalog_runs' / args.run_id / stamp
    report_dir.mkdir(parents=True, exist_ok=False)
    report_path = report_dir / 'processing_report.json'
    baseline = set(state.get('baseline_asins', []))
    report = {'run_id': args.run_id, 'started_at_utc': now(),
              'status': 'cleaning', 'completed_page_batches': len(jobs),
              'raw_observation_rows': sum(len(rows) for _, rows, _ in jobs),
              'raw_unique_asins': len(raw_union),
              'baseline_unique_asins': len(baseline),
              'baseline_plus_raw_unique_asins': len(baseline | raw_union),
              'database_modified_by_this_invocation': False, 'batches': []}
    atomic_json(report_path, report)
    expected_pairs = set()
    accepted_union = set()
    raw_by_batch = {batch: rows for batch, rows, _ in jobs}
    try:
        # Finish ALL cleaning before starting ANY product loads.
        for index, (batch_id, raw_rows, raw_path) in enumerate(jobs, 1):
            run_child(cleaner, batch_id, report_dir / f'{batch_id}_clean.log')
            out = ROOT / 'data/processed' / batch_id
            clean = read_rows(out / 'products_clean.csv')
            rejected = read_rows(ROOT / 'data/rejected' / batch_id / 'products_rejected.csv')
            quality = read_json(out / 'quality_report.json')
            if (len(clean) + len(rejected) != len(raw_rows)
                    or quality.get('clean_rows') != len(clean)
                    or quality.get('rejected_rows') != len(rejected)):
                raise ValueError(f'Quality row counts do not reconcile: {batch_id}')
            raw_ids = {r['asin'] for r in raw_rows}
            if (any(r.get('batch_id') != batch_id or r['asin'] not in raw_ids for r in clean)
                    or len({r['asin'] for r in clean}) != len(clean)):
                raise ValueError(f'Invalid cleaned identities: {batch_id}')
            accepted_union.update(r['asin'] for r in clean)
            expected_pairs.update((batch_id, r['asin']) for r in clean)
            report['batches'].append({
                'batch_id': batch_id, 'input_rows': len(raw_rows),
                'clean_rows': len(clean), 'rejected_rows': len(rejected),
                'missing_prices': sum(r.get('price_status') == 'missing' for r in clean),
                'missing_ratings': sum(not r.get('rating', '').strip() for r in clean),
                'raw_sha256': hashlib.sha256(raw_path.read_bytes()).hexdigest(),
                'clean_sha256': hashlib.sha256((out / 'products_clean.csv').read_bytes()).hexdigest(),
                'load_status': 'pending'})
            atomic_json(report_path, report)
            print(f'Clean {index}/{len(jobs)}: {batch_id} | accepted {len(clean)}, rejected {len(rejected)}', flush=True)
        report.update(
            accepted_observation_rows=len(expected_pairs), accepted_unique_asins=len(accepted_union),
            rejected_observation_rows=sum(b['rejected_rows'] for b in report['batches']),
            baseline_plus_accepted_unique_asins=len(baseline | accepted_union))
        if args.clean_only:
            report['status'] = 'cleaned_only'
        else:
            report['status'] = 'loading'
            atomic_json(report_path, report)
            # Schema migration is repeatable; do not fabricate old timestamps.
            with connect_db() as connection:
                connection.execute('''
                    ALTER TABLE staging.products
                        ALTER COLUMN rating DROP NOT NULL,
                        ALTER COLUMN ratings_count DROP NOT NULL;
                    ALTER TABLE staging.product_observations
                        ALTER COLUMN rating DROP NOT NULL,
                        ALTER COLUMN ratings_count DROP NOT NULL;
                ''')
            report['database_modified_by_this_invocation'] = True
            for index, batch in enumerate(report['batches'], 1):
                batch_id = batch['batch_id']
                if not batch['clean_rows']:
                    batch['load_status'] = 'skipped_empty'
                    continue
                raw_path = ROOT / 'data/raw/amazon_batches' / batch_id / 'products.csv'
                clean_path = ROOT / 'data/processed' / batch_id / 'products_clean.csv'
                if (hashlib.sha256(raw_path.read_bytes()).hexdigest() != batch['raw_sha256']
                        or hashlib.sha256(clean_path.read_bytes()).hexdigest() != batch['clean_sha256']):
                    raise ValueError(f'Batch changed during processing: {batch_id}')
                run_child(loader, batch_id, report_dir / f'{batch_id}_load.log')
                batch['load_status'] = 'committed'
                atomic_json(report_path, report)
                print(f'Load {index}/{len(jobs)}: {batch_id} committed', flush=True)
            with connect_db() as connection:
                with connection.cursor() as cursor:
                    cursor.execute('''SELECT batch_id, asin FROM staging.product_observations
                                      WHERE batch_id = ANY(%s)''', (list(seen_batches),))
                    stored_pairs = set(cursor.fetchall())
                    if stored_pairs != expected_pairs:
                        raise ValueError('Stored observations differ from accepted batch/ASIN pairs')
                    cursor.execute('SELECT asin FROM staging.products')
                    db_ids = {r[0] for r in cursor.fetchall()}
                    expected_ids = baseline | accepted_union
                    if not expected_ids <= db_ids:
                        raise ValueError('Some baseline or accepted product ASINs are absent from database')
                    cursor.execute('''SELECT COUNT(*), COUNT(DISTINCT asin),
                        COUNT(*) FILTER (WHERE price IS NULL),
                        COUNT(*) FILTER (WHERE rating IS NULL)
                        FROM staging.products''')
                    total, unique, missing_price, missing_rating = cursor.fetchone()
                    report['database'] = {
                        'total_products': total, 'unique_products': unique,
                        'missing_prices': missing_price, 'missing_ratings': missing_rating,
                        'run_observations_verified': len(stored_pairs),
                        'extra_asins_outside_baseline_and_run': len(db_ids - expected_ids)}
            report['status'] = 'complete'
    except (Exception, KeyboardInterrupt) as error:
        report.update(status='failed', error=f'{type(error).__name__}: {error}')
        raise
    finally:
        report['finished_at_utc'] = now()
        atomic_json(report_path, report)
        with (report_dir / 'batch_summary.csv').open('w', encoding='utf-8-sig', newline='') as handle:
            fields = ['batch_id','input_rows','clean_rows','rejected_rows',
                      'missing_prices','missing_ratings','load_status']
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction='ignore')
            writer.writeheader()
            writer.writerows(report['batches'])
        print(f'Status: {report["status"]}')
        print(f'Report: {report_path}')
        for key in ['accepted_observation_rows', 'accepted_unique_asins',
                    'rejected_observation_rows', 'baseline_plus_accepted_unique_asins']:
            if key in report:
                print(f'{key}: {report[key]}')
        if 'database' in report:
            print(json.dumps(report['database'], indent=2))


if __name__ == '__main__':
    main()
