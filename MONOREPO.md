# Helios Monorepo Structure

This repository contains three distinct Cloudera AI projects managed as a single monorepo.

## Directory Layout

```
helios/
├── shared/
│   └── helios_core/          # Shared core package (used by all 3 projects)
│       ├── artifacts.py
│       ├── compiler/
│       ├── metadata/
│       ├── ossie/
│       ├── __init__.py
│       └── pyproject.toml
│
├── apps/
│   ├── helios/               # Query runtime (primary Helios project)
│   │   ├── console/          # FastAPI console
│   │   ├── mcp/              # MCP server
│   │   ├── ui/               # React frontend
│   │   ├── tests/            # Query runtime tests
│   │   ├── runtimes/         # Docker configs
│   │   └── pyproject.toml
│   │
│   ├── helios-ds-generation/ # Synthetic data generator (new project)
│   │   ├── src/helios_ds/    # Generator source code (development)
│   │   ├── templates/        # Artifact generation templates
│   │   ├── runtimes/         # Docker configs
│   │   └── pyproject.toml
│   │
│   └── helios-ds-evaluation/ # Evaluation harness (new project)
│       ├── src/helios_ds_eval/  # Evaluator source code (development)
│       ├── runtimes/         # Docker configs
│       └── pyproject.toml
│
├── docs/                      # Documentation
├── scripts/                   # Utility scripts
├── .github/workflows/         # CI/CD workflows (per-project)
│
├── pyproject.toml            # Monorepo workspace config
└── README.md
```

## Development Setup

### For Query Runtime (apps/helios)

```bash
cd /home/cdsw/helios

# Install shared core + query runtime
pip install -e ./shared/helios_core
pip install -e ./apps/helios[dev]

# Run tests
pytest apps/helios/tests -v

# Start console
python -m apps.helios.console.api
```

### For Data Generator (apps/helios-ds-generation) - Future

```bash
cd /home/cdsw/helios

# Install shared core + generator
pip install -e ./shared/helios_core
pip install -e ./apps/helios-ds-generation[dev,media]

# Run tests
pytest apps/helios-ds-generation/tests -v
```

### For Evaluator (apps/helios-ds-evaluation) - Future

```bash
cd /home/cdsw/helios

# Install shared core + evaluator
pip install -e ./shared/helios_core
pip install -e ./apps/helios-ds-evaluation[dev]

# Run tests
pytest apps/helios-ds-evaluation/tests -v
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

- **Shared Core** (`shared/helios_core/`): Used by all 3 projects
- **Generation Project**: Separate credentials and runtime from query runtime
- **Evaluation Project**: Separate Cloudera project entirely (ground-truth isolation)
- **Git**: All projects pull from `main` branch; no project-specific branches needed

## CI/CD Workflows

Located in `.github/workflows/`:

- `helios-query.yml`: Tests only `apps/helios/` and `shared/helios_core/`
- `helios-ds-gen.yml`: Tests only `apps/helios-ds-generation/` (future)
- `helios-ds-eval.yml`: Tests only `apps/helios-ds-evaluation/` (future)
- `integration.yml`: End-to-end tests (runs only on `main`)

Each project's tests run independently. A failure in one project does not block another.

## Python Imports

All code imports from `helios_core` directly:

```python
from helios_core import __version__
from helios_core.compiler import compile_query
from helios_core.metadata import SQLiteMetadataRepository
```

The `pyproject.toml` files use path dependencies to ensure all projects use the same `helios_core`:

```toml
[project]
dependencies = [
    "helios-core @ {path=../../shared/helios_core}",
    # ... other dependencies
]
```

## Dependency Management

- **`shared/helios_core/pyproject.toml`**: Core dependencies (pydantic, pyarrow, sqlalchemy)
- **`apps/helios/pyproject.toml`**: Query runtime deps (fastapi, uvicorn) + core
- **`apps/helios-ds-generation/pyproject.toml`**: Generator deps (reportlab, pillow) + core
- **`apps/helios-ds-evaluation/pyproject.toml`**: Evaluator deps (minimal) + core

## Git History

All refactoring commits preserve full git history:

```bash
git log --oneline | head -10
# Shows:
# b82c2a2 refactor: add pyproject.toml files for monorepo structure
# fadfb21 refactor: move securityHelper.py to scripts/ directory
# f16db51 refactor: create skeleton directories for helios-ds projects
# dc139e0 refactor: move tests to apps/helios/tests
# fc8e3ed refactor: move helios query runtime to apps/helios/
# 32a62b4 refactor: create shared/ and move helios_core
```

- `git blame` still works correctly
- `git log -- apps/helios/` filters to query runtime changes only
- `git log -- shared/helios_core/` shows core package changes

## State Directories (Not Code)

These are runtime state and artifacts; not part of the codebase:

- `state/` - Current runtime state  
- `runs/` - Execution history
- `run_archive/` - Archived runs
- `models/` - Model artifacts
- `jobs/` - Job definitions

These can be excluded from code reviews and CI/CD checks.

## Next Steps

1. **Verify imports work** in each project (Python packaging setup)
2. **Set up CI/CD workflows** per-project in GitHub Actions
3. **Create Cloudera AI projects** for each component
4. **Build custom runtimes** and register in Workbench
5. **Deploy skeleton applications** and test git pulls

## Documentation

- `docs/` - Architecture, design docs
- `README.md` - Project overview (existing)
- This file - Monorepo structure and workflow

## Questions?

Refer to the engineering spec: `Helios-DS_Asset_Generator_Engineering_Development_Specification.pdf`
