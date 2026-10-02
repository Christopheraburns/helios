-- Q-03: Add reproducibility dependency columns to existing datasets and generation_runs tables
-- Run this script in Impala to migrate existing tables:
--   impala-shell -i <host> -q "SHOW DATABASES;" < init-tables-q03.sql
-- Or in Hue: execute each statement individually

ALTER TABLE helios_ds.datasets ADD COLUMNS (
  lockfile STRING,
  reportlab_version STRING,
  pypdf_version STRING,
  fonts STRING,
  manifest_schema_version STRING,
  platform_architecture STRING,
  runtime_version STRING
);

ALTER TABLE helios_ds.generation_runs ADD COLUMNS (
  platform_architecture STRING,
  runtime_version STRING
);

-- Verify the new columns exist:
-- DESCRIBE helios_ds.datasets;
-- DESCRIBE helios_ds.generation_runs;
