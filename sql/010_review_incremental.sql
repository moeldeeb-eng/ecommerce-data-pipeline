-- Read-only audit of the incremental run.
SELECT run_id,batch_id,counts_before,counts_after,watermark_before,watermark_after,loaded_at_utc
FROM store.incremental_batches WHERE run_id='store_v1' ORDER BY loaded_at_utc;

SELECT run_id,last_ordered_at_utc,id_maxima,cumulative_counts,updated_at_utc
FROM store.pipeline_state WHERE run_id='store_v1';

SELECT 'store.order_items' AS entity,count(*) AS rows FROM store.order_items WHERE run_id='store_v1'
UNION ALL SELECT 'warehouse.fact_sales',count(*) FROM warehouse.fact_sales WHERE run_id='store_v1';
