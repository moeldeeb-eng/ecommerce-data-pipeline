# Logical model

Fact grain: one order line, keyed by `(run_id, order_id, line_number)`. All statuses are retained; realized measures are zero unless completed.

```mermaid
erDiagram
  DIM_PRODUCT ||--o{ FACT_SALES : product_key
  DIM_CUSTOMER ||--o{ FACT_SALES : customer_key
  DIM_DATE ||--o{ FACT_SALES : date_key
  DIM_PRODUCT {
    bigint product_key PK
    text run_id
    varchar asin
    text category
  }
  DIM_CUSTOMER {
    bigint customer_key PK
    text run_id
    text customer_id
  }
  DIM_DATE {
    integer date_key PK
    date full_date
  }
  FACT_SALES {
    text run_id PK
    text order_id PK
    smallint line_number PK
    bigint product_key FK
    bigint customer_key FK
    integer date_key FK
    numeric realized_net_amount
    smallint realized_quantity
  }
```

Product/customer foreign keys are composite with run_id, preventing cross-run joins. Date is a shared dense UTC calendar. See `sql/005_warehouse_schema.sql` for all constraints, columns, indexes and reporting views.

```mermaid
erDiagram
  CUSTOMERS ||--o{ ORDERS : places
  ORDERS ||--|{ ORDER_ITEMS : contains
  PRODUCTS ||--o{ ORDER_ITEMS : identifies
  ORDERS ||--|| PAYMENTS : has
```

Store relationships are scoped by run_id. Products retain observed source metadata separately from simulation anchors. Ingestion/incremental ledgers record hashes and committed counts; pipeline_state stores the committed watermark and ID maxima.
