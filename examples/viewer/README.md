# Helios viewer

A very basic, read-only page for looking at Helios's graphs. It's an example of a host
built on the public library APIs, not a published package:

- **Semantic model:** a published model's datasets and their relationships, from the
  artifact store. Facts are on the inner ring, the datasets they join to around them.
  Select a dataset for its fields and metrics.
- **Query path:** pick metrics and dimensions, and the compiler's answer (the fact
  dataset, the relationships it joins through, the columns and the SQL) is drawn over the
  model.
- **Ontology:** the ontology graph's classes, enums, glossary terms and mapped model
  elements, with a filter by kind.

```sh
uv sync                                         # from the repository root
uv run python examples/viewer/serve.py          # ./helios.toml, or HELIOS_* settings
uv run python examples/viewer/serve.py --config path/to/helios.toml --port 8765
```

Then open http://127.0.0.1:8765. Each view has its own address, so you can link to it:
`#model`, `#ontology`, or `#query?metric=store_sales_revenue&dimension=tpcds.date_dim.d_year`,
which also compiles.

**Ontology.** `--ontology` takes a LinkML schema (parsed with `helios-cortex[ontology]`)
or an `OntologyGraph` saved as JSON (`OntologyGraph.to_dict()`). Without it, the
viewer shows the repository's example ontology (`ontology/customers/example-tenant`) with
its Ossie mappings.

**What it is made of.** `serve.py` is the Python standard library's HTTP server plus
`ArtifactStore`, `SemanticModel`, `Compiler` and `OntologyGraph`. `index.html` is one
static page with no build step; its only dependency is Cytoscape.js, loaded from cdnjs at
a pinned version with an integrity hash. The server listens on 127.0.0.1, answers GET
only, and changes nothing it reads: it never publishes or activates.

`tests/integration/test_integration_viewer.py` serves it on the DuckDB TPC-DS sample.
