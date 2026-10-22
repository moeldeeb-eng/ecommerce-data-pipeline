CREATE SCHEMA IF NOT EXISTS staging;

CREATE TABLE IF NOT EXISTS staging.products (
    asin VARCHAR(10) PRIMARY KEY,
    product_name TEXT NOT NULL,
    price_raw TEXT,
    price NUMERIC(14, 2),
    currency VARCHAR(3),
    price_status TEXT NOT NULL,
    rating NUMERIC(2, 1),
    ratings_count BIGINT,
    product_url TEXT NOT NULL,
    source_file TEXT NOT NULL,
    parsed_at_utc TIMESTAMPTZ NOT NULL,
    loaded_at_utc TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CHECK (asin ~ '^[A-Z0-9]{10}$'),
    CHECK (LENGTH(TRIM(product_name)) > 0),
    CHECK (rating BETWEEN 1 AND 5),
    CHECK (ratings_count >= 0),
    CHECK (
        (
            price_status = 'valid'
            AND price IS NOT NULL
            AND price > 0
            AND currency IS NOT NULL
            AND currency ~ '^[A-Z]{3}$'
        )
        OR
        (
            price_status = 'missing'
            AND price IS NULL
            AND currency IS NULL
        )
    )
);
-- Also migrate existing sample tables without replacing their data.
ALTER TABLE staging.products ALTER COLUMN rating DROP NOT NULL, ALTER COLUMN ratings_count DROP NOT NULL;
