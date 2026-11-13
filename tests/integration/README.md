# Integration tests

Tests that cross package boundaries, so they belong to no single package (burndown 6.7).
Each package's own tests stay in `packages/<name>/tests` and must pass with that package
installed alone; these need the workspace (`uv sync`), and read shared fixtures from
the repository.

- `test_integration_identity.py`: helios-serve's `run_query` through the default data
  policy and helios-cloudera's real Impala engine, down to the impyla `connect` call:
  whose identity each query runs as (the Phase 2 gate, moved from the old MCP server's tests).
- `test_integration_tpcds_sample.py`: the `Helios` facade (helios-semantic) and an
  evaluation (helios-serve) on a DuckDB TPC-DS sample with the repository's published
  TPC-DS model.
- `test_integration_one_config.py`: one `helios init` project read by the `Helios` facade
  (helios-semantic) and by `helios mcp` (helios-mcp) launched as an agent would, from the
  project and from elsewhere with `HELIOS_CONFIG`, while the warehouse is held open.

- `test_integration_viewer.py`: `examples/viewer` served on the same sample: the page, the
  model graph, a query's path from the compiler, the ontology graph, and GET only.

The DuckDB TPC-DS sample (`warehouse`, `settings`) is in `conftest.py`: what `helios init --sample` builds, made once per run. CI runs this directory, with the crawler's tests and the import contracts, in the
`integration` job of `.github/workflows/helios.yml`.
