# Helios-DS-Evaluation

The **Helios-DS-Evaluation** project is a separate Cloudera AI Workbench application in this monorepo. It scores synthetic corpora and query agents against governed ground truth without sharing credentials with the data generator or the Helios query runtime.

## Role in the monorepo

| Cloudera AI project | Directory | Runtime | Purpose |
|---------------------|-----------|---------|---------|
| **helios-query** | `apps/helios/` | helios-runtime | Query execution, console, MCP |
| **helios-ds-generation** | `apps/helios-ds-generation/` | helios-ds-runtime | Deterministic synthetic asset generation |
| **helios-ds-evaluation** | `apps/helios-ds-evaluation/` | helios-ds-eval-runtime | Benchmarking and scoring (isolated project) |

Evaluation runs in its **own** Workbench project so ground-truth tables and answer keys stay isolated from generation and from interactive query credentials.

## What gets evaluated

Helios Query evaluation (TPC-DS reference environment) scores each governed layer:

| Layer | Reference | Measure |
|-------|-----------|---------|
| Glossary | `TPCDS Retail` full glossary | Terms recovered, definitions correct, column links correct |
| Semantic layer | Ossie TPC-DS example model | Relationships, metrics, naming quality |
| Question answering | TPC-DS query set | Share of questions expressible as semantic requests and compiled to correct SQL |

Helios-DS evaluation consumes **ground truth** produced by the generator: Iceberg tables in `helios_ground_truth.*`, including `expected_queries` and `expected_results`, plus threshold profiles defined for the harness.

## Handoff from Helios-DS-Generation

The generator must publish datasets in `READY` state and expose:

- `helios_ground_truth.*` entity, claim, and evidence rows
- `expected_queries` / `expected_results` for benchmark questions
- Threshold profiles used by the evaluator

The evaluator does **not** run inside the generation project; it reads the same lakehouse namespaces from its own Workbench identity.

## Development setup

```bash
cd /home/cdsw/helios

pip install -e ./shared/helios_core
pip install -e ./apps/helios-ds-evaluation[dev]

pytest apps/helios-ds-evaluation/tests -v
```

## Related documentation

- [Helios Query evaluation (README)](../README.md#evaluation) — glossary, semantic layer, and TPC-DS QA scoring for the query runtime
- [Monorepo structure](../MONOREPO.md) — deployment and CI layout
- [Helios-DS-Generation burndown](./helios-ds-burndown.md) — generator milestones and handoffs
