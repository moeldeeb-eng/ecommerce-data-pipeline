-- Run in psql after loading store_v1. These are read-only queries.
SELECT 'products' AS table_name,count(*) AS rows FROM store.products WHERE run_id='store_v1'
UNION ALL SELECT 'customers',count(*) FROM store.customers WHERE run_id='store_v1'
UNION ALL SELECT 'orders',count(*) FROM store.orders WHERE run_id='store_v1'
UNION ALL SELECT 'order_items',count(*) FROM store.order_items WHERE run_id='store_v1'
UNION ALL SELECT 'payments',count(*) FROM store.payments WHERE run_id='store_v1';

SELECT order_status,count(*) AS orders,
       sum(net_items_amount) AS net_items_amount,
       sum(shipping_amount) AS shipping_amount,
       sum(order_total) AS attempted_order_value,
       sum(CASE WHEN order_status='completed' THEN net_items_amount ELSE 0 END) AS realized_simulated_item_revenue
FROM store.orders WHERE run_id='store_v1'
GROUP BY order_status ORDER BY order_status;

SELECT run_id,loaded_at_utc,manifest_sha256 FROM store.ingestion_runs ORDER BY loaded_at_utc;
