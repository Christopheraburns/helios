# Model-aware artifacts

Generated artifacts belong to a Helios Model, not to a warehouse or database.
New files use stable Model IDs:

```text
models/
  <model-id>/
    proposed/
      <run-id>.proposal.json
    published/
      semantic.ossie.yaml
      semantic.ossie.json
      manifest.json
```

The proposal remains at `runs/<run-id>/propose.json` as well, preserving the
existing staged discovery/review workflow. New harvest, profile, and proposal
documents carry `model_id`; the model-scoped proposal copy makes ownership
unambiguous outside the run directory. Glossary proposals and future ontology
proposals remain content within the proposal until their existing publishing
systems are implemented. Published glossary content remains in Atlas and is
associated through the Model's `glossary_id`.

Set `HELIOS_MODEL_ID` for discovery and publish jobs. `HELIOS_MODEL_NAME` remains
the human-readable Ossie document name and is not used as storage identity.

## Legacy compatibility

If `HELIOS_MODEL_ID` is absent, jobs temporarily use the first harvested database
as the explicit Model ID, matching the old single-model naming behavior. Existing
flat files such as `models/published/tpcds.ossie.yaml` remain readable by calling
artifact lookup with model ID `tpcds`. They are not rewritten automatically.

To migrate an existing model:

1. Create or identify its stable Helios Model ID.
2. Set `HELIOS_MODEL_ID` for subsequent discovery/publish runs.
3. Publish once; new artifacts will be written under that Model ID.
4. Keep or remove the legacy flat files after all consumers use the stable ID.

All code should use `helios_core.artifacts.ArtifactStore` or
`SemanticModel.load_published(model_id)` rather than constructing paths or
deriving artifact names from a DataSource.
