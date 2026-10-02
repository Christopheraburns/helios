# Q-03: Pin and Record Reproducibility Dependencies

## Completion Summary

Q-03 implements reproducibility dependency pinning for the helios-ds generator. All text corpus datasets now record:

1. **Python version** — from `platform.python_version()`
2. **Lockfile SHA256** — from `requirements.lock` 
3. **Runtime image digest** — container_digest field (populated when runtime is built)
4. **ReportLab version** — from installed package
5. **pypdf version** — from installed package
6. **Fonts** — from Dockerfile (fonts-dejavu-core)
7. **Template bundle** — already recorded as template_bundle_hash
8. **Manifest schema version** — MANIFEST_SCHEMA_VERSION = "1.0"
9. **Source fingerprint** — already recorded as source_fingerprint_hash
10. **Generator version** — already recorded as generator_version
11. **Architecture** — from `platform.machine()` (e.g., x86_64)

## Changes Made

### 1. Schema Updates (schemas.py)

Added to `DatasetRecord`:
- `lockfile`: SHA256 of requirements.lock
- `reportlab_version`: Pinned ReportLab version
- `pypdf_version`: Pinned pypdf version
- `fonts`: System fonts string
- `manifest_schema_version`: Manifest format version
- `platform_architecture`: CPU architecture
- `runtime_version`: helios-ds-runtime version

Added to `GenerationRunRecord`:
- `platform_architecture`: CPU architecture
- `runtime_version`: helios-ds-runtime version

### 2. Runtime Image (helios-ds runtime v0.1.1)

Updated `/runtimes/helios-ds/`:

- **Dockerfile**: Changed to use `requirements.lock` instead of `requirements.txt`
- **Version**: Updated to 0.1.1 (was 0.1.0)
- **requirements.lock**: Pinned all dependencies and transitive dependencies

Key pinned versions:
- reportlab==5.0.1 (Q-03 requirement: PDF rendering is version-sensitive)
- pypdf==6.19.0 (Q-03 requirement: PDF validation pinned)
- pillow==12.3.0
- duckdb==1.5.5
- pyiceberg==0.12.0
- All other dependencies frozen

### 3. Pipeline Updates (pipeline.py)

- Imported `MANIFEST_SCHEMA_VERSION` from manifests
- Added `RUNTIME_VERSION = "0.1.1"` constant
- Added `_get_lockfile_hash()` to read requirements.lock SHA256
- Added `_get_package_version()` to query installed package versions
- Added `_get_reproducibility_deps()` to gather all dependencies
- Updated `_publish_rows()` to populate all new fields on DatasetRecord
- Updated `_record_run()` to populate architecture and runtime version

### 4. Database Schema

- **lakehouse_impala.sql**: Updated with new columns for helios_ds.datasets and helios_ds.generation_runs
- **init-tables-q03.sql**: Migration script to add columns to existing tables (Iceberg-compatible)

## Runtime Build Instructions

To build and push the new runtime image:

```bash
cd apps/helios-ds-generation/runtimes
./build.sh docker.io/christopheraburns 0.1.1
```

This will:
1. Build helios-ds-runtime:0.1.1 with pinned dependencies
2. Build helios-ds-media-runtime:0.1.1
3. Print the image digests
4. Output instructions to register in Workbench Runtime Catalog

Example output:
```
docker.io/christopheraburns/helios-ds-runtime:0.1.1 -> sha256:...
docker.io/christopheraburns/helios-ds-media-runtime:0.1.1 -> sha256:...
```

## Database Migration

To add the new columns to existing tables in Workbench Impala:

1. In Hue or impala-shell, execute `schemas/init-tables-q03.sql`
2. Verify: `DESCRIBE helios_ds.datasets;` and `DESCRIBE helios_ds.generation_runs;`

New columns are optional (NULL) and won't affect existing datasets.

## Future Dataset Generations

All new datasets generated with helios-ds-runtime 0.1.1 will automatically record:

```json
{
  "python_version": "3.12.X",
  "lockfile": "7fc2ad429fe35c76f81b5694aa2a425ecc9d01dcb713c0fae8d40d5453bfaaf9",
  "reportlab_version": "5.0.1",
  "pypdf_version": "6.19.0",
  "fonts": "fonts-dejavu-core",
  "manifest_schema_version": "1.0",
  "platform_architecture": "x86_64",
  "runtime_version": "0.1.1",
  "generator_version": "0.3.1",
  "generator_schema_version": "2024.09"
}
```

## C-09 (Development Corpus) Update

The frozen development corpus `1ca99f86-e6a0-57fc-8311-cadcac4c8302` was generated before Q-03 changes. To record its dependencies:

Option A (Recommended): Regenerate with the new runtime
- Delete and regenerate the dataset with helios-ds-runtime 0.1.1
- All dependencies will be automatically recorded

Option B (Manual): Update the record directly
- Requires SQL UPDATE with the environment's actual values at generation time
- Less reliable as it must be done manually

## Verification

Test that dependencies are recorded:

```python
from helios_ds.catalog import DatasetCatalog
from helios_ds.lakehouse import sql_catalog

catalog = DatasetCatalog(sql_catalog(...))
dataset_info = catalog.get("1ca99f86-e6a0-57fc-8311-cadcac4c8302")
record = dataset_info.record

# Verify all fields are populated
assert record.lockfile is not None
assert record.reportlab_version == "5.0.1"
assert record.pypdf_version == "6.19.0"
assert record.fonts == "fonts-dejavu-core"
assert record.manifest_schema_version == "1.0"
assert record.platform_architecture is not None
assert record.runtime_version == "0.1.1"
```

## Status

- [x] Schema updated with all required fields
- [x] Pipeline updated to populate all fields
- [x] Lockfile created and pinned
- [x] Dockerfile updated to v0.1.1 with lockfile
- [x] Database schema updated
- [x] Migration script created
- [ ] Runtime image built and pushed (manual: `./build.sh`)
- [ ] Runtime registered in Workbench Runtime Catalog (manual: Cloudera AI console)
- [ ] Database migration run (manual: `init-tables-q03.sql` in Impala)
- [ ] C-09 dataset regenerated or updated with new dependency records (optional)

## Related Tasks

- F-11: Runtime images (already done 0.1.0)
- C-09: Development corpus (already approved, can regenerate with new runtime)
- Q-04: Clean-room reproducibility test (will verify Q-03 pinning works)
