# Helios Monorepo Structure

This repository is a uv workspace. Helios is being split into libraries that a host project
installs like any other package (`helios-core`, `helios-model`, `helios-serve`, and later
`helios-crawl`); Daedalus serves the tools over MCP. See
`docs/helios-decomposition-burndown.md` for progress. Today `helios-core` is a real
package and the console, crawler, graph gateway and MCP server still live under
`apps/helios/`, installed by the root project.

## Directory Layout

```
helios/
├── pyproject.toml            # workspace root; also installs apps/ (until Phase 8)
├── uv.lock                   # pins the whole tree
├── packages/
│   └── helios-core/          # pyproject.toml, src/helios_core/
│
├── apps/
│   ├── helios/               # Query runtime (console, crawler, graph, mcp_server, ui, tests)
│   ├── helios-ds-generation/ # Synthetic data generator (outside the workspace)
│   └── helios-ds-evaluation/ # Evaluation harness (outside the workspace)
│
├── jobs/                      # Workbench Job entry points
├── scripts/                   # Utility scripts
├── ontology/  models/  eval/  # data, versioned, not Python
├── runtime/                   # Workbench runtime image
└── docs/
```

## Development Setup

### Query runtime and helios-core

```bash
uv sync                      # creates .venv, installs helios-core and apps/ editable
uv sync --extra evidence     # also lancedb + sentence-transformers (pulls PyTorch)

uv run pytest                # apps/helios/tests
uv run ruff check packages apps/helios jobs scripts
```

No `PYTHONPATH` or `sys.path` changes are needed locally.

### Data generator and evaluator

These apps are not workspace members yet and keep their own setup:

```bash
cd apps/helios-ds-generation
PYTHONPATH=src:../../packages/helios-core/src pytest -q
```

## Cloudera AI Project Deployment

### Three Independent Projects

Each Cloudera AI project pulls from this single Git repo:

| Project | Directory | Runtime | Purpose |
|---------|-----------|---------|---------|
| **helios-query** | `apps/helios/` | helios-runtime | Query execution & console |
| **helios-ds-generation** | `apps/helios-ds-generation/` | helios-ds-runtime | Synthetic data generation |
| **helios-ds-evaluation** | `apps/helios-ds-evaluation/` | helios-ds-eval-runtime | Evaluation (separate project!) |

### Deployment Notes

- **helios-core** (`packages/helios-core/src/helios_core/`): used by all 3 projects
- **`apps/helios` is scaffolding, not a deployment target.** Its tests protect the logic while
  it moves into the packages; it is deleted in burndown Phase 8. Helios reaches Workbench through
  a separate host application that pip-installs the libraries.
- **Generation Project**: Separate credentials and runtime from query runtime
- **Evaluation Project**: Separate Cloudera project entirely (ground-truth isolation)
- **Git**: All projects pull from `main` branch; no project-specific branches needed

## CI/CD Workflows

Located in `.github/workflows/`:

- `helios.yml`: `uv sync`, ruff and pytest for helios-core and `apps/helios/`
- `helios-ds-gen.yml`: Tests only `apps/helios-ds-generation/`

## Python Imports

All code imports from `helios_core` directly:

```python
from helios_core import __version__
from helios_core.compiler import compile_query
from helios_core.metadata import SQLiteMetadataRepository
```

Workspace members depend on `helios-core` by name; the root `pyproject.toml` resolves it
to the workspace copy:

```toml
[tool.uv.sources]
helios-core = { workspace = true }
```

## Dependency Management

- **`packages/helios-core/pyproject.toml`**: Core dependencies; `evidence` extra for document search
- **`pyproject.toml`** (root): console, crawler, graph and MCP server dependencies + core
- **`apps/helios/console/requirements.txt` / `.lock`**: what Workbench installs into `.helios-python`;
  keep in step with the root `pyproject.toml`
- **`apps/helios-ds-generation/pyproject.toml`**: Generator deps (reportlab, pillow) + core
- **`apps/helios-ds-evaluation/pyproject.toml`**: Evaluator deps (minimal) + core

## State Directories (Not Code)

These are runtime state and artifacts; not part of the codebase:

- `state/` - Current runtime state (ignored)
- `runs/` - Execution history (ignored)
- `run_archive/` - Archived runs (ignored)
- `models/` - Model artifacts
- `jobs/` - Job definitions

## Documentation

- `docs/` - Architecture, design docs
- `README.md` - Project overview
- This file - Monorepo structure and workflow
