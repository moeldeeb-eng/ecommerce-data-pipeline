-- Additive, repeatable migration. Called inside the builder's transaction/lock.
-- Internal provenance and original dimension keys are deliberately preserved.
INSERT INTO warehouse.dim_location(city,country)
SELECT DISTINCT city,country FROM warehouse.dim_customer
ORDER BY country,city
ON CONFLICT(country,city) DO NOTHING;

UPDATE warehouse.dim_customer c SET location_key=l.location_key
FROM warehouse.dim_location l
WHERE l.city=c.city AND l.country=c.country
AND c.location_key IS DISTINCT FROM l.location_key;

ALTER TABLE warehouse.dim_customer ALTER COLUMN location_key SET NOT NULL;

CREATE SCHEMA IF NOT EXISTS reporting;
COMMENT ON SCHEMA reporting IS
'Analytics for a simulated store. Product metadata is source-derived; transactions and catalog price anchors are simulated.';

CREATE OR REPLACE VIEW reporting.dim_location AS
SELECT location_key,city,country FROM warehouse.dim_location;

CREATE OR REPLACE VIEW reporting.dim_customer AS
SELECT customer_key,run_id,customer_id,customer_name,email,location_key,registered_at_utc
FROM warehouse.dim_customer;

CREATE OR REPLACE VIEW reporting.dim_product AS
SELECT product_key,run_id,asin,product_name,category,
 simulation_anchor_price AS catalog_price,'EGP'::varchar(3) AS currency,
 observed_rating AS rating,observed_ratings_count AS ratings_count
FROM warehouse.dim_product;
COMMENT ON COLUMN reporting.dim_product.catalog_price IS
'Frozen simulation catalog price anchor in EGP; transaction unit_price can differ.';
COMMENT ON COLUMN reporting.dim_product.rating IS
'Source product rating, nullable. Not generated customer review data.';

CREATE OR REPLACE VIEW reporting.dim_date AS
SELECT * FROM warehouse.dim_date;

CREATE OR REPLACE VIEW reporting.fact_sales AS
SELECT run_id,order_id,line_number,product_key,customer_key,date_key,
 ordered_at_utc,order_status,payment_status,quantity,realized_quantity,
 unit_price,discount_percent,gross_amount,discount_amount,net_amount,
 realized_net_amount,currency FROM warehouse.fact_sales;
