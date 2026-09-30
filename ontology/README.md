# Helios Ontology

Source-agnostic model of what exists in the enterprise (entities, assets, concepts, claims) and how those things relate. The Ossie model describes *what can be queried in the warehouse*; the ontology describes *what things are, wherever they appear*. Ossie elements map **to** ontology classes; they are not the classes.

## Layout

```
ontology/
  core/core.yaml                      fixed, shipped with Helios; all code targets this
  packs/<domain>/<domain>.yaml        optional shipped domain packs (retail from TPC-DS)
  customers/<tenant>/extension.yaml   generated per customer, reviewed, published
  mappings/mapping.schema.yaml        schema for mapping files
  mappings/ossie/<model>.yaml         Ossie model -> ontology classes + identifiers
```

Layers only add leaves: a pack imports core, a customer extension imports a pack (or core), and none may redefine what it imports. That is the extensibility guarantee. New customer, new domain, new data type: add subclasses and mappings; the trunk the crawler, resolver, planner and MCP tools depend on does not move.

## Rules

- Every entity class an asset can mention must have a mapping entry with `identifiers`. The resolver reads only `identifiers` and `resolution`.
- `external_ids` carry source keys as `<source>.<object>:<key>`; `entity_id` is Helios-assigned and never a source key.
- Glossary terms are `SemanticConcept`s, never resolved as entities.
- Every edge records `resolved_by` (tier) and `evidence` (segments). Precision over recall: below threshold is `PossiblySameAs`, not `SameAs`.
- Publish immutably: validate, snapshot to Iceberg as `ontology_version`, materialise into Memgraph. The active version is part of the index generation; changing it is a blue/green event.

## Validate

```bash
pip install linkml
linkml-lint ontology/core/core.yaml
linkml-lint ontology/packs/retail/retail.yaml
linkml-lint ontology/customers/example-tenant/extension.yaml
linkml-validate -s ontology/mappings/mapping.schema.yaml -C SourceMapping ontology/mappings/ossie/tpcds.yaml
```

## Export

```bash
gen-json-schema ontology/packs/retail/retail.yaml > build/retail.schema.json
gen-owl ontology/packs/retail/retail.yaml > build/retail.owl.ttl
```
