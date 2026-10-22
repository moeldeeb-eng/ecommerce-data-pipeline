-- The Python entry point creates _warehouse_context with one run_id.
INSERT INTO warehouse.dim_date
 (date_key,full_date,calendar_year,calendar_quarter,month_number,month_name,
  day_of_month,iso_day_of_week,day_name,iso_week,iso_year)
SELECT extract(year FROM d)::integer*10000+extract(month FROM d)::integer*100+extract(day FROM d)::integer,
       d::date,extract(year FROM d)::smallint,extract(quarter FROM d)::smallint,extract(month FROM d)::smallint,
       (ARRAY['January','February','March','April','May','June','July','August','September','October','November','December'])[extract(month FROM d)::integer],
       extract(day FROM d)::smallint,extract(isodow FROM d)::smallint,
       (ARRAY['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday'])[extract(isodow FROM d)::integer],
       extract(week FROM d)::smallint,extract(isoyear FROM d)::smallint
FROM generate_series(
 (SELECT min((ordered_at_utc AT TIME ZONE 'UTC')::date)::timestamp FROM store.orders WHERE run_id=(SELECT run_id FROM _warehouse_context)),
 (SELECT max((ordered_at_utc AT TIME ZONE 'UTC')::date)::timestamp FROM store.orders WHERE run_id=(SELECT run_id FROM _warehouse_context)),
 interval '1 day') AS days(d)
WHERE NOT EXISTS (SELECT 1 FROM warehouse.dim_date x WHERE x.full_date=d::date)
ON CONFLICT (full_date) DO NOTHING;

INSERT INTO warehouse.dim_product
 (run_id,asin,product_name,category,observed_price,observed_currency,observed_rating,observed_ratings_count,
  simulation_anchor_price,simulation_anchor_method)
SELECT p.run_id,p.asin,p.product_name,p.category,p.source_price,p.source_currency,p.source_rating,p.source_ratings_count,
       p.anchor_price,p.anchor_method
FROM store.products p
WHERE p.run_id=(SELECT run_id FROM _warehouse_context)
AND NOT EXISTS (SELECT 1 FROM warehouse.dim_product d WHERE d.run_id=p.run_id AND d.asin=p.asin)
ON CONFLICT (run_id,asin) DO NOTHING;

INSERT INTO warehouse.dim_customer
 (run_id,customer_id,customer_name,email,city,country,registered_at_utc,is_synthetic)
SELECT c.run_id,c.customer_id,c.customer_name,c.email,c.city,c.country,c.registered_at_utc,c.is_synthetic
FROM store.customers c
WHERE c.run_id=(SELECT run_id FROM _warehouse_context)
AND NOT EXISTS (SELECT 1 FROM warehouse.dim_customer d WHERE d.run_id=c.run_id AND d.customer_id=c.customer_id)
ON CONFLICT (run_id,customer_id) DO NOTHING;

INSERT INTO warehouse.fact_sales
 (run_id,order_id,line_number,product_key,customer_key,date_key,ordered_at_utc,order_status,payment_status,
  quantity,realized_quantity,unit_price,discount_percent,gross_amount,discount_amount,net_amount,
  realized_net_amount,currency,price_method,is_synthetic)
SELECT i.run_id,i.order_id,i.line_number,p.product_key,c.customer_key,
       extract(year FROM o.ordered_at_utc AT TIME ZONE 'UTC')::integer*10000+
       extract(month FROM o.ordered_at_utc AT TIME ZONE 'UTC')::integer*100+
       extract(day FROM o.ordered_at_utc AT TIME ZONE 'UTC')::integer,
       o.ordered_at_utc,o.order_status,pay.payment_status,i.quantity,
       CASE WHEN o.order_status='completed' THEN i.quantity ELSE 0 END,
       i.unit_price,i.discount_percent,i.gross_amount,i.discount_amount,i.net_amount,
       CASE WHEN o.order_status='completed' THEN i.net_amount ELSE 0 END,
       i.currency,i.price_method,i.is_synthetic
FROM store.order_items i
JOIN store.orders o ON o.run_id=i.run_id AND o.order_id=i.order_id
JOIN store.payments pay ON pay.run_id=o.run_id AND pay.order_id=o.order_id
JOIN warehouse.dim_product p ON p.run_id=i.run_id AND p.asin=i.asin
JOIN warehouse.dim_customer c ON c.run_id=o.run_id AND c.customer_id=o.customer_id
WHERE i.run_id=(SELECT run_id FROM _warehouse_context)
AND NOT EXISTS (SELECT 1 FROM warehouse.fact_sales f WHERE f.run_id=i.run_id AND f.order_id=i.order_id AND f.line_number=i.line_number)
ON CONFLICT (run_id,order_id,line_number) DO NOTHING;
