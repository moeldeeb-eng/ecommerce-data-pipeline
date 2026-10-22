-- Exactly one result row. All comparisons are NULL-safe.
SELECT
 (SELECT count(*) FROM warehouse.dim_product p
  JOIN store.products s ON s.run_id=p.run_id AND s.asin=p.asin
  WHERE p.run_id=(SELECT run_id FROM _warehouse_context)
  AND ROW(p.product_name,p.category,p.observed_price,p.observed_currency,p.observed_rating,p.observed_ratings_count,
          p.simulation_anchor_price,p.simulation_anchor_method)
      IS DISTINCT FROM ROW(s.product_name,s.category,s.source_price,s.source_currency,s.source_rating,s.source_ratings_count,
                           s.anchor_price,s.anchor_method)) AS invalid_products,
 (SELECT count(*) FROM warehouse.dim_customer c
  JOIN store.customers s ON s.run_id=c.run_id AND s.customer_id=c.customer_id
  WHERE c.run_id=(SELECT run_id FROM _warehouse_context)
  AND ROW(c.customer_name,c.email,c.city,c.country,c.registered_at_utc,c.is_synthetic)
      IS DISTINCT FROM ROW(s.customer_name,s.email,s.city,s.country,s.registered_at_utc,s.is_synthetic)) AS invalid_customers,
 (SELECT count(*) FROM warehouse.fact_sales f
  JOIN warehouse.dim_product p ON p.run_id=f.run_id AND p.product_key=f.product_key
  JOIN warehouse.dim_customer c ON c.run_id=f.run_id AND c.customer_key=f.customer_key
  JOIN warehouse.dim_date d ON d.date_key=f.date_key
  JOIN store.order_items i ON i.run_id=f.run_id AND i.order_id=f.order_id AND i.line_number=f.line_number
  JOIN store.orders o ON o.run_id=i.run_id AND o.order_id=i.order_id
  JOIN store.payments pay ON pay.run_id=o.run_id AND pay.order_id=o.order_id
  WHERE f.run_id=(SELECT run_id FROM _warehouse_context)
  AND ROW(p.asin,c.customer_id,d.full_date,f.ordered_at_utc,f.order_status,f.payment_status,
          f.quantity,f.realized_quantity,f.unit_price,f.discount_percent,f.gross_amount,f.discount_amount,
          f.net_amount,f.realized_net_amount,f.currency,f.price_method,f.is_synthetic)
      IS DISTINCT FROM ROW(i.asin,o.customer_id,(o.ordered_at_utc AT TIME ZONE 'UTC')::date,o.ordered_at_utc,o.order_status,pay.payment_status,
                           i.quantity,CASE WHEN o.order_status='completed' THEN i.quantity ELSE 0 END,
                           i.unit_price,i.discount_percent,i.gross_amount,i.discount_amount,i.net_amount,
                           CASE WHEN o.order_status='completed' THEN i.net_amount ELSE 0 END,i.currency,i.price_method,i.is_synthetic)) AS invalid_sales,
 (SELECT count(*) FROM warehouse.dim_date d WHERE
     ROW(d.calendar_year,d.calendar_quarter,d.month_number,d.day_of_month,d.iso_day_of_week,d.iso_week,d.iso_year)
     IS DISTINCT FROM ROW(extract(year FROM d.full_date)::smallint,extract(quarter FROM d.full_date)::smallint,
                           extract(month FROM d.full_date)::smallint,extract(day FROM d.full_date)::smallint,
                           extract(isodow FROM d.full_date)::smallint,extract(week FROM d.full_date)::smallint,
                           extract(isoyear FROM d.full_date)::smallint)) AS invalid_dates;
