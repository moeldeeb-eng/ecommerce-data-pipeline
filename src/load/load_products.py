"""Load one completed and cleaned batch, preserving collection provenance."""
from pathlib import Path
from decimal import Decimal
from datetime import datetime
import argparse
import csv
import json
import re

import psycopg
from psycopg import sql
from dotenv import dotenv_values

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def aware_datetime(value):
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError('Timestamp requires a timezone')
    return parsed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--batch-id', required=True)
    args = parser.parse_args()
    batch_id = args.batch_id
    if not re.fullmatch(r'[A-Za-z0-9_-]+', batch_id):
        raise ValueError('Invalid batch ID')
    raw_dir = PROJECT_ROOT / 'data/raw/amazon_batches' / batch_id
    manifest = json.loads((raw_dir / 'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('status') != 'complete' or manifest.get('batch_id') != batch_id:
        raise ValueError('Raw batch is not complete or does not match')
    config = dotenv_values(PROJECT_ROOT / '.env')
    for key in ('POSTGRES_DB', 'POSTGRES_USER', 'POSTGRES_PASSWORD'):
        if not config.get(key):
            raise ValueError(f'Missing configuration: {key}')
    input_file = PROJECT_ROOT / 'data/processed' / batch_id / 'products_clean.csv'
    with input_file.open(encoding='utf-8-sig', newline='') as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ValueError('No accepted products to load')
    if any(row['batch_id'] != batch_id for row in rows):
        raise ValueError('Input batch ID mismatch')
    if len({row['asin'] for row in rows}) != len(rows):
        raise ValueError('Duplicate ASIN in cleaned batch')

    columns = ['asin', 'product_name', 'price_raw', 'price', 'currency',
               'price_status', 'rating', 'ratings_count', 'product_url',
               'source_file', 'parsed_at_utc', 'collected_at_utc',
               'source_url', 'page_number', 'batch_id']
    records = []
    for row in rows:
        if not re.fullmatch(r'[A-Z0-9]{10}', row['asin']):
            raise ValueError('Invalid ASIN')
        if int(row['page_number']) < 1:
            raise ValueError('Invalid source page')
        records.append((
            row['asin'], row['product_name'], row['price_raw'] or None,
            Decimal(row['price']) if row['price'] else None,
            row['currency'] or None, row['price_status'], Decimal(row['rating']) if row['rating'].strip() else None,
            int(row['ratings_count']) if row['ratings_count'].strip() else None, row['product_url'], row['source_file'],
            aware_datetime(row['parsed_at_utc']), aware_datetime(row['collected_at_utc']),
            row['source_url'], int(row['page_number']), row['batch_id']))

    identifiers = sql.SQL(', ').join(map(sql.Identifier, columns))
    placeholders = sql.SQL(', ').join(sql.Placeholder() for _ in columns)
    assignments = sql.SQL(', ').join(
        sql.SQL('{} = EXCLUDED.{}').format(sql.Identifier(c), sql.Identifier(c))
        for c in columns if c != 'asin')
    upsert = sql.SQL('''
        INSERT INTO staging.products ({columns}) VALUES ({values})
        ON CONFLICT (asin) DO UPDATE SET {assignments},
            loaded_at_utc = CURRENT_TIMESTAMP
        WHERE staging.products.collected_at_utc IS NULL
           OR EXCLUDED.collected_at_utc > staging.products.collected_at_utc
    ''').format(columns=identifiers, values=placeholders, assignments=assignments)
    history_insert = sql.SQL('''
        INSERT INTO staging.product_observations ({columns}) VALUES ({values})
        ON CONFLICT (batch_id, asin) DO NOTHING
    ''').format(columns=identifiers, values=placeholders)

    with psycopg.connect(
        host=config.get('POSTGRES_HOST') or '127.0.0.1',
        port=int(config.get('POSTGRES_PORT') or 5433),
        dbname=config['POSTGRES_DB'], user=config['POSTGRES_USER'],
        password=config['POSTGRES_PASSWORD'], connect_timeout=10,
    ) as connection:
        with connection.cursor() as cursor:
            # Existing project schema remains the source of the original constraints.
            cursor.execute((PROJECT_ROOT / 'sql/001_products.sql').read_text(encoding='utf-8-sig'))
            cursor.execute('''
                ALTER TABLE staging.products
                  ADD COLUMN IF NOT EXISTS collected_at_utc TIMESTAMPTZ,
                  ADD COLUMN IF NOT EXISTS source_url TEXT,
                  ADD COLUMN IF NOT EXISTS page_number INTEGER,
                  ADD COLUMN IF NOT EXISTS batch_id TEXT;
            ''')
            # No historical collection times are fabricated for old sample rows.
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS staging.product_observations (
                  LIKE staging.products INCLUDING DEFAULTS INCLUDING CONSTRAINTS,
                  PRIMARY KEY (batch_id, asin)
                );
                ALTER TABLE staging.product_observations
                  ALTER COLUMN batch_id SET NOT NULL,
                  ALTER COLUMN collected_at_utc SET NOT NULL;
            ''')
            cursor.execute('ALTER TABLE staging.product_observations ALTER COLUMN rating DROP NOT NULL, ALTER COLUMN ratings_count DROP NOT NULL')
            cursor.executemany(history_insert, records)
            observations_added = cursor.rowcount
            cursor.executemany(upsert, records)
            products_changed = cursor.rowcount
            cursor.execute('''SELECT COUNT(*), COUNT(DISTINCT asin),
                COUNT(*) FILTER (WHERE price IS NULL) FROM staging.products''')
            total, unique, missing = cursor.fetchone()
            cursor.execute('SELECT COUNT(*) FROM staging.product_observations WHERE batch_id = %s', (batch_id,))
            batch_observations = cursor.fetchone()[0]
    print(f'Batch: {batch_id}')
    print(f'Input products: {len(records)}')
    print(f'Observations added: {observations_added}')
    print(f'Products inserted/updated: {products_changed}')
    print(f'Observations stored for this batch: {batch_observations}')
    print(f'Total products: {total}; unique: {unique}; missing prices: {missing}')
    print('Load committed successfully.')


if __name__ == '__main__':
    main()
