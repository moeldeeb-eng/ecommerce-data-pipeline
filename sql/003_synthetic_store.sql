CREATE SCHEMA IF NOT EXISTS store;

CREATE TABLE IF NOT EXISTS store.ingestion_runs (
 run_id text PRIMARY KEY,
 manifest_sha256 char(64) NOT NULL,
 manifest jsonb NOT NULL,
 loaded_at_utc timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS store.products (
 run_id text NOT NULL REFERENCES store.ingestion_runs(run_id),
 asin varchar(10) NOT NULL,
 product_name text NOT NULL,
 category text NOT NULL,
 source_price numeric(14,2),
 source_currency varchar(3),
 source_rating numeric(2,1),
 source_ratings_count bigint,
 anchor_price numeric(14,2) NOT NULL CHECK (anchor_price > 0),
 anchor_currency varchar(3) NOT NULL CHECK (anchor_currency = 'EGP'),
 anchor_method text NOT NULL CHECK (anchor_method IN ('observed_snapshot_anchor','category_median_anchor','explicit_project_fallback')),
 is_synthetic_anchor boolean NOT NULL CHECK (is_synthetic_anchor),
 source_record jsonb NOT NULL,
 PRIMARY KEY (run_id, asin),
 CHECK ((source_price IS NULL AND source_currency IS NULL) OR (source_price IS NOT NULL AND source_price > 0 AND source_currency = 'EGP')),
 CHECK (source_rating IS NULL OR source_rating BETWEEN 0 AND 5),
 CHECK (source_ratings_count IS NULL OR source_ratings_count >= 0)
);

CREATE TABLE IF NOT EXISTS store.customers (
 run_id text NOT NULL REFERENCES store.ingestion_runs(run_id),
 customer_id text NOT NULL,
 customer_name text NOT NULL,
 email text NOT NULL,
 city text NOT NULL,
 country char(2) NOT NULL CHECK (country='EG'),
 registered_at_utc timestamptz NOT NULL,
 is_synthetic boolean NOT NULL CHECK (is_synthetic),
 PRIMARY KEY (run_id, customer_id),
 UNIQUE (run_id, email)
);

CREATE TABLE IF NOT EXISTS store.orders (
 run_id text NOT NULL REFERENCES store.ingestion_runs(run_id),
 order_id text NOT NULL,
 customer_id text NOT NULL,
 ordered_at_utc timestamptz NOT NULL,
 order_status text NOT NULL CHECK (order_status IN ('completed','cancelled','payment_failed')),
 currency char(3) NOT NULL CHECK (currency='EGP'),
 items_count smallint NOT NULL CHECK (items_count BETWEEN 1 AND 5),
 subtotal numeric(16,2) NOT NULL CHECK (subtotal > 0),
 discount_amount numeric(16,2) NOT NULL CHECK (discount_amount >= 0),
 net_items_amount numeric(16,2) NOT NULL CHECK (net_items_amount > 0),
 shipping_amount numeric(16,2) NOT NULL CHECK (shipping_amount >= 0),
 order_total numeric(16,2) NOT NULL CHECK (order_total > 0),
 is_synthetic boolean NOT NULL CHECK (is_synthetic),
 PRIMARY KEY (run_id, order_id),
 FOREIGN KEY (run_id, customer_id) REFERENCES store.customers(run_id, customer_id),
 CHECK (net_items_amount = subtotal-discount_amount),
 CHECK (order_total = net_items_amount+shipping_amount)
);

CREATE TABLE IF NOT EXISTS store.order_items (
 run_id text NOT NULL,
 order_id text NOT NULL,
 line_number smallint NOT NULL CHECK (line_number BETWEEN 1 AND 5),
 asin varchar(10) NOT NULL,
 quantity smallint NOT NULL CHECK (quantity BETWEEN 1 AND 4),
 unit_price numeric(14,2) NOT NULL CHECK (unit_price > 0),
 discount_percent smallint NOT NULL CHECK (discount_percent IN (0,5,10,15)),
 gross_amount numeric(16,2) NOT NULL CHECK (gross_amount > 0),
 discount_amount numeric(16,2) NOT NULL CHECK (discount_amount >= 0),
 net_amount numeric(16,2) NOT NULL CHECK (net_amount > 0),
 currency char(3) NOT NULL CHECK (currency='EGP'),
 price_method text NOT NULL,
 is_synthetic boolean NOT NULL CHECK (is_synthetic),
 PRIMARY KEY (run_id, order_id, line_number),
 UNIQUE (run_id, order_id, asin),
 FOREIGN KEY (run_id, order_id) REFERENCES store.orders(run_id, order_id),
 FOREIGN KEY (run_id, asin) REFERENCES store.products(run_id, asin),
 CHECK (gross_amount=unit_price*quantity),
 CHECK (discount_amount=round(gross_amount*discount_percent/100,2)),
 CHECK (net_amount=gross_amount-discount_amount)
);

CREATE TABLE IF NOT EXISTS store.payments (
 run_id text NOT NULL,
 payment_id text NOT NULL,
 order_id text NOT NULL,
 currency char(3) NOT NULL CHECK (currency='EGP'),
 payment_method text NOT NULL CHECK (payment_method IN ('card','mobile_wallet','bank_transfer')),
 payment_status text NOT NULL CHECK (payment_status IN ('paid','cancelled','failed')),
 amount_due numeric(16,2) NOT NULL CHECK (amount_due > 0),
 amount_paid numeric(16,2) NOT NULL CHECK (amount_paid >= 0),
 attempted_at_utc timestamptz NOT NULL,
 paid_at_utc timestamptz,
 is_synthetic boolean NOT NULL CHECK (is_synthetic),
 PRIMARY KEY (run_id, payment_id),
 UNIQUE (run_id, order_id),
 FOREIGN KEY (run_id, order_id) REFERENCES store.orders(run_id, order_id),
 CHECK ((payment_status='paid' AND amount_paid=amount_due AND paid_at_utc IS NOT NULL AND paid_at_utc>=attempted_at_utc)
     OR (payment_status IN ('cancelled','failed') AND amount_paid=0 AND paid_at_utc IS NULL))
);

CREATE INDEX IF NOT EXISTS store_orders_customer_idx ON store.orders(run_id, customer_id);
CREATE INDEX IF NOT EXISTS store_orders_date_idx ON store.orders(run_id, ordered_at_utc);
CREATE INDEX IF NOT EXISTS store_items_product_idx ON store.order_items(run_id, asin);
