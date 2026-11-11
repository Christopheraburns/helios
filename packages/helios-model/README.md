# helios-model

Model discovery for Helios: harvest a data source's tables and columns, profile them,
propose a semantic model, review the proposal, and publish it as an Ossie revision to the
artifact store and the registry. It depends only on helios-cortex; the warehouse and catalog
come from plugins (helios-cortex's DuckDB, SQLAlchemy and Trino engines, or helios-cloudera's
Impala and Atlas).

```python
from helios_cortex import Caller
from helios_cortex.registry import SqlRegistry
from helios_cortex.settings import Settings
from helios_model import Catalog, Profiler, Proposer, Publisher

settings = Settings()                                   # helios.toml and HELIOS_* variables
source = SqlRegistry.from_settings(settings).data_source("warehouse")
caller = Caller("alice")                                # whoever the host says is asking

harvest = Catalog(settings).harvest(source, caller, databases=["sales"], model_id="sales")
profile = Profiler(settings).profile(harvest, caller)
proposals = Proposer(settings).propose(profile)         # an LLM from [llm], or Proposer(settings, llm=...)
for p in proposals.pending():                           # each with a confidence and a rationale
    print(p.section, p.id, p.confidence, p.rationale)
proposals.accept_all(min_confidence=0.8)                # or accept, edit and reject one by one
publication = Publisher(settings).publish(proposals, caller)
print(publication.revision.id)
```

Each stage keeps its document in the run store and returns a result with the run id, so
the stages can run in separate processes (`Profiler(settings).profile(run_id)`,
`Proposer(settings).load(run_id)`). Harvest and profile run their queries as the caller
under the connection's identity mode (`service`, `impersonate` or `token`); `None` is the
connection's own identity. Publish records the caller as the publisher and refuses a
model that is not in the registry.

| Module | What it provides |
|---|---|
| `helios_model.commands` | The `helios` subcommands above, over the facades |
| `helios_model.discovery` | The facades `Catalog`, `Profiler`, `Proposer`, `Publisher`, and their results `Harvest`, `Profile`, `Publication` (exported from `helios_model`) |
| `helios_model.proposals` | `Proposals`: the typed draft (`ProposedDataset`, `ProposedField`, `ProposedRelationship`, `ProposedMetric`, `ProposedTerm`, each with a confidence, a rationale and its decision) and the reviewer's accept, edit and reject |
| `helios_model.harvest` | `TableHarvester`: tables, columns and row counts from the engine, a glossary from any catalog that has one, and aggregation queries from the engine's log or a folder of `.sql` files |
| `helios_model.profile` | `TableProfiler`: column statistics, primary-key candidates, and foreign keys verified against the data |
| `helios_model.propose` | `ModelProposer`: an LLM's descriptions, roles, glossary terms, relationship decisions and metrics, each with a confidence and a rationale |
| `helios_model.review` | A reviewer's decisions over a proposal (`review.json`) and `apply` |
| `helios_model.build` | `build`: a reviewed proposal as an Ossie document; `preflight` for the review page |
| `helios_model.publication` | `publish_reviewed_proposal`: apply the review, build, validate and publish to the `ArtifactStore` |
| `helios_model.graph` | `ArtifactGraphRepository`: a model's graph (configured, proposed, published, or a run under review), with lenses and bounded navigation; no ownership or permissions |

## From the command line

helios-model adds `harvest`, `profile`, `propose` and `publish` to the `helios` command
(helios-cortex adds `init`, `source`, `model` and `registry`):

```sh
helios init                                         # a starter helios.toml (a DuckDB connection)
helios source add warehouse --connection default
helios model add sales --name Sales --source warehouse
helios harvest --source warehouse --database sales --model sales
helios profile                                      # each stage works on the latest run, or --run RUN_ID
helios propose                                      # needs an [llm]
helios publish --min-confidence 0.8                 # accept what is that sure, then publish
```

Each prints the run id and a summary on stdout and its progress on stderr. They run as
the connection's service identity unless `--as USER` names a caller (for `impersonate`
connections); a caller's token is read from `HELIOS_CALLER_TOKEN` only. On Cloudera AI
Workbench, add `--profile workbench` (helios-cloudera) to read the Workbench variables.
