-- Read-only checks; edit run_id when inspecting another isolated run.
SELECT 'DimProduct' AS entity,count(*) AS rows FROM warehouse.dim_product WHERE run_id='store_v1'
UNION ALL SELECT 'DimCustomer',count(*) FROM warehouse.dim_customer WHERE run_id='store_v1'
UNION ALL SELECT 'FactSales',count(*) FROM warehouse.fact_sales WHERE run_id='store_v1';

SELECT order_status,count(*) AS lines,count(DISTINCT order_id) AS orders,
       sum(quantity) AS attempted_units,sum(realized_quantity) AS realized_units,
       sum(net_amount) AS attempted_net_value,sum(realized_net_amount) AS realized_item_revenue
FROM warehouse.fact_sales WHERE run_id='store_v1'
GROUP BY order_status ORDER BY order_status;

SELECT calendar_year,month_number,sum(realized_item_revenue) AS realized_item_revenue
FROM warehouse.v_monthly_category_sales WHERE run_id='store_v1'
GROUP BY calendar_year,month_number ORDER BY calendar_year,month_number;

SELECT category,sum(realized_item_revenue) AS realized_item_revenue
FROM warehouse.v_monthly_category_sales WHERE run_id='store_v1'
GROUP BY category ORDER BY realized_item_revenue DESC;
