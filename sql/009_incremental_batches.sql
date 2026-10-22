CREATE TABLE IF NOT EXISTS store.incremental_batches (
 run_id text NOT NULL REFERENCES store.ingestion_runs(run_id),
 batch_id text NOT NULL,
 manifest_sha256 char(64) NOT NULL,
 parent_manifest_sha256 char(64) NOT NULL,
 manifest jsonb NOT NULL,
 counts_before jsonb NOT NULL,
 counts_after jsonb NOT NULL,
 watermark_before timestamptz NOT NULL,
 watermark_after timestamptz NOT NULL,
 loaded_at_utc timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY (run_id,batch_id),
 CHECK (watermark_after>watermark_before)
);

CREATE TABLE IF NOT EXISTS store.pipeline_state (
 run_id text PRIMARY KEY REFERENCES store.ingestion_runs(run_id),
 last_ordered_at_utc timestamptz NOT NULL,
 id_maxima jsonb NOT NULL,
 cumulative_counts jsonb NOT NULL,
 updated_at_utc timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
);
