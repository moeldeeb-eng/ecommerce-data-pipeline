# Submission changes

The staging schema now permits NULL ratings and rating counts, matching the source cleaner and loader. Existing staging.products and product_observations constraints are migrated by the normal product-loader transaction. Existing values are preserved; CHECK constraints still reject out-of-range non-NULL ratings and negative counts.

Added README, configuration example, offline tests, architecture and ERD documentation, validation boundaries, and a sanitized execution summary. Excluded local credentials, raw logs, bytecode and the obsolete root dags probe folder from this separate distribution. Included source catalog/reference decisions. Full generated transactions remain on the original machine.

Do not replace local credential files or remove existing volumes when merging this distribution.
