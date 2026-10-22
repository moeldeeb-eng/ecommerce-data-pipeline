# Synthetic store data contract, generator v1.0.0

## Run locally

Merge the included src folder into the project root. Python 3.11 standard library only.
Existing extraction, cleaning, loading and compose files are not changed.

Small run:

```powershell
.\.venv\Scripts\python.exe src/generate/generate_store_data.py --run-id demo_v1 --customers 1000 --orders 4000 --items 10000
```

Full run:

```powershell
.\.venv\Scripts\python.exe src/generate/generate_store_data.py --run-id store_v1
```

Separate verification:

```powershell
.\.venv\Scripts\python.exe src/generate/validate_store_data.py --run-id store_v1
```

Default input: data/reference/catalog_final/simulation_products.csv.
Outputs: data/raw/synthetic_runs/<run-id>/.
Generation automatically validates the complete dataset before publishing the run folder.
A failing run stays in <run-id>.partial. Investigate before removing that folder manually.
Existing output folders are never overwritten. Change run-id for a new run.
Do not load different run-ids into the database as independent increments: local IDs restart at 1. Incremental generation/loading will be implemented separately.

## Grain and keys

| File | Grain | Key | Relationships |
| --- | --- | --- | --- |
| customers.csv | One fictional customer | customer_id | Orders reference customer_id |
| orders.csv | One fictional order | order_id | Customer FK, monetary totals at order level |
| order_items.csv | One product line in an order | order_id + line_number | Product ASIN FK and order FK |
| payments.jsonl | One payment outcome per order | payment_id, unique order_id | One payment for each order |
| product_price_anchors.csv | One simulation price anchor per product | asin | Source values kept separate from simulation anchor |
| catalog_snapshot.csv | Exact original input snapshot | asin | Immutable source evidence |

Default counts: 100000 customers, 400000 orders, exactly 1000000 order lines, 400000 payment outcomes.
Each order contains 1–5 distinct products; quantity is 1–4 units per line. Every customer receives at least one order. Product sampling is uniform; the small demo need not contain every ASIN. No invented popularity claims.

## Fictional assumptions

All customers, orders, payments and sale prices are synthetic. Customer labels deliberately say Synthetic Customer; email addresses use example.invalid. City/country labels describe fictional customers in Egypt. No Amazon reviewer identities are used.

Dates: customer registrations during 2024; orders uniformly sampled from 2025-01-01 through 2026-10-21 23:00 UTC. Dates and product availability are fictional; current product snapshots do not establish historical availability or prices.

Order statuses have illustrative weights 90% completed, 6% cancelled, 4% payment_failed. Exact realized counts vary by seed. All payment methods/outcomes are fictional; no requests to real payment services occur. Completed orders are fully paid; other orders have amount_paid=0 and paid_at_utc=null. No refunds, returns, partial payments or tax breakdown are modeled.

The million rows are order lines across all statuses, not a million paid sales. In downstream FactSales retain the order status and use zero recognized revenue for cancelled/payment_failed orders. Report paid/completed revenue separately from attempted order value.

## Pricing

Observed product prices and missing values remain unchanged in catalog_snapshot.csv. Source rating values are never filled.

For each eligible product, use its observed EGP snapshot price if present; otherwise use the median of observed EGP prices in its final project category. If the category has no observed prices, fail unless an explicit assumption exists. The only supplied fallback is 5000.00 EGP for 3D input controllers. This is an arbitrary demonstration value, not an estimate or researched market price.

All anchors are simulation inputs, including those based on observed prices, and marked is_synthetic_anchor=true. EGP is explicitly the simulated store currency. Each sale unit price varies between 85% and 115% of its anchor; price_method records the anchor method and synthetic variation.

Integer cents avoid floating point totals. Round half up to cents. For each line: gross=unit_price*quantity; discount=round_half_up(gross*discount_percent/100); net=gross-discount. Illustrative discount choices: 0,0,0,5,10,15 percent. Shipping is 0 when net order items >=2000 EGP; otherwise 40,50 or60 EGP. These thresholds are project assumptions.

Order subtotal=sum(line gross), order discount=sum(line discounts), net_items_amount=sum(line net), order_total=net_items_amount+shipping_amount. Payment amount_due matches order_total; amount_paid matches it only for completed orders. Shipping belongs to orders and must not be summed once per line after a join.

## Reproducibility and integrity

Same seed, input snapshot, arguments and supported Python version produce identical data files. Run-id is recorded in the manifest and does not alter business IDs. SHA256 hashes cover every data file; manifest records configuration, counts and explicit assumptions. Keep manifest.json and validation_report.json with the dataset.

The validator checks exact counts, unique/sequential keys, customer/product relationships, distinct ASINs within each order, timestamps, currencies, synthetic markers, price methods/ranges, item/order/payment arithmetic, statuses and file integrity. It streams orders/items/payments; only customers and the small product catalog are retained in memory.

These raw inputs intentionally contain consistent records. Later validation examples can use a separate deliberately corrupted fixture; never silently alter the authoritative generated run.

## Next integration

This package only generates and validates files. It does not create database schemas or modify staging.products. Next stages will load transactional staging tables, then DimProduct/DimCustomer/DimDate and a line-grain FactSales, with idempotent loading and explicit revenue rules. Airflow orchestration follows after the stages work independently.
