# Architecture

```mermaid
flowchart TD
  A[Amazon product pages] --> B[Saved HTML and product batches]
  B --> C[Clean and validate products]
  C --> D[PostgreSQL staging]
  D --> E[Reviewed catalog snapshot]
  E --> F[Fictional CSV and JSON generator]
  F --> G[Validate hashes and business rules]
  G --> H[Pandas fact candidates]
  G --> I[Transactional store loader]
  I --> J[SQL star schema build]
  H --> K[Compare committed facts and revenue]
  J --> K
  J --> L[Reporting views]
```

Live collection and catalog review precede the store pipeline. The manual incremental Airflow DAG orchestrates generation, validation, transformation, source/warehouse loading and read-only verification. Its Extract stage does not request fresh Amazon pages. Separate Airflow metadata PostgreSQL stores orchestration state; the project database stores staging, store and warehouse schemas.

Raw files carry batch/run identity and manifests. Loader transactions protect source integrity; warehouse transactions protect facts/dimensions. A committed source batch can be followed by a resumed warehouse build. Read-only recovery checks compare counts, revenue, provenance and pipeline state and do not constitute a byte comparison of every business row.
