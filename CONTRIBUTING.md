# Developing Helios

This repository is a [uv workspace](https://docs.astral.sh/uv/concepts/projects/workspaces/) of libraries. The workspace root is not a package and installs nothing of its own: each library under `packages/` is built, tested and released on its own, and a host installs the ones it needs (see [README.md](README.md)).

## Layout

```
helios/
├── pyproject.toml          workspace root: members, dependency groups, pytest, ruff and import-linter settings
├── uv.lock                 pins the whole workspace
├── packages/
│   ├── helios-cortex/      the kernel        src/helios_cortex/, tests/, CONTRACTS.md
│   ├── helios-model/       model discovery   src/helios_model/, tests/
│   ├── helios-serve/       the tools         src/helios_serve/, tests/
│   ├── helios-semantic/    the umbrella      src/helios_semantic/, tests/
│   ├── helios-mcp/         stdio MCP         src/helios_mcp/, tests/
│   └── helios-cloudera/    Cloudera plugins  src/helios_cloudera/, tests/
├── tests/integration/      tests that cross packages
├── examples/viewer/        a read-only page for the graphs (not a package)
└── apps/
    ├── helios/crawler/     the document crawler and its tests (apps/helios/tests), until helios-crawl (Phase 10)
    ├── helios-ds-generation/
    └── helios-ds-evaluation/   Helios-DS: outside the workspace, with their own setup and CI
```

## Setup

```bash
uv sync                       # .venv with every package editable, all extras, the crawler's dependencies and the tools
uv sync --group evidence      # also document search (lancedb, sentence-transformers; pulls PyTorch)
```

The root's dependency groups define the development environment:
- `dev`: every workspace package with its extras, plus pytest, ruff, import-linter and moto.
- `crawler`: pypdf, rapidfuzz and reportlab.
- `evidence`: document search.

`dev` and `crawler` are installed by default. A package's own dependencies belong in its own `pyproject.toml`, never the root's. No `PYTHONPATH` changes are needed. The crawler isn't installed: its tests find `apps.helios.crawler` through pytest's `pythonpath`, and `python -m apps.helios.crawler` runs from the repository root.

## Tests and checks

```bash
uv run pytest                                      # every package, tests/integration and the crawler
uv run pytest packages/helios-serve/tests          # one package
uv run ruff check packages apps/helios examples scripts tests
uv run mypy packages/helios-serve/src              # types, one package at a time
uv run lint-imports                                # the import contracts
```

mypy uses the settings in the root `pyproject.toml`: pydantic's plugin, and missing imports ignored only for the libraries that ship no types (fsspec, gcsfs, impyla, lancedb, LinkML, sentence-transformers). Every package's source passes it, and CI keeps it that way.

**Package boundaries.** The import-linter contracts in `pyproject.toml` keep each package to its own dependencies:
- helios-cortex imports no other Helios package and no app code.
- helios-model, helios-serve and helios-cloudera import only helios-cortex.
- helios-serve imports no MCP SDK or web framework.
- helios-semantic and helios-mcp import only what they wrap.

**Wheel-only installs.** The workspace installs every package, so a test can pass only because a sibling happens to be installed. CI therefore also builds each package's wheel and runs that package's tests in a virtualenv with only its declared dependencies (the second half of each `package` job). To do the same locally:

```bash
uv build --package helios-cortex --wheel --out-dir /tmp/dist
uv build --package helios-serve --wheel --out-dir /tmp/dist
uv venv /tmp/serve && uv pip install --refresh --python /tmp/serve/bin/python /tmp/dist/*.whl pytest
/tmp/serve/bin/python -m pytest -q packages/helios-serve/tests
```

Pass `--refresh`: uv caches wheels by version, so a rebuilt wheel with an unchanged version is otherwise ignored. A test that needs an optional extra skips and names the extra (`pytest.importorskip(..., reason="needs helios-cortex[llm]")`).

**Test file names** must be unique across the workspace, because pytest imports test modules by basename. Prefix them with the package's topic (`test_serve_health.py`, not `test_health.py`).

## CI

`.github/workflows/helios.yml` runs on pull requests, on `main`, nightly and on release tags:

| Job | What it checks |
|---|---|
| `package` (one per package) | in the workspace: ruff, mypy and the package's tests; then from wheels, beside only the Helios packages it declares: its tests again and `helios --help`, and once more with its extra where it has one (helios-serve with `helios-cortex[llm]`, helios-semantic with `[all]`) |
| `integration` | import-linter, ruff on the rest, `tests/integration` (with the viewer's smoke test) and the crawler's tests |
| `trino`, `storage` | the Trino engine against a real coordinator; the registry on Postgres and artifacts on S3 (moto) |
| `cloudera-live` | nightly (and on demand): helios-cloudera's Impala engine against a real warehouse holding TPC-DS, from the `HELIOS_TEST_IMPALA*` secrets (it fails if they are missing). Like `trino` for the Trino engine, it is one plugin's live test; Helios itself is tested on DuckDB, and a new warehouse plugin adds its own live job |
| `publish` | on a `helios-<package>/vX.Y.Z` tag, once the package and integration jobs pass: checks the version against the package's `pyproject.toml`, builds that package and uploads it to the internal index |

`helios-ds-gen.yml` tests the Helios-DS generator. [`.github/CODEOWNERS`](.github/CODEOWNERS) names who reviews each package.

## Releasing a package

Each package is versioned and released on its own. Bump `version` in its `pyproject.toml`, merge, then tag the merge commit with the package and version:

```bash
git tag helios-serve/v0.2.0 && git push origin helios-serve/v0.2.0
```

A tag whose version doesn't match the package's `pyproject.toml`, or that names no package, fails the `publish` job and uploads nothing.

## Adding a package

1. Create `packages/helios-<name>/` with `pyproject.toml` (hatchling, `src/` layout), `README.md` and `tests/`. The workspace picks it up through `members = ["packages/*"]`.
2. Depend on `helios-cortex` (and siblings) by name, and add `helios-<name> = { workspace = true }` to the root's `[tool.uv.sources]`.
3. Register its plugins as entry points: `helios.engines`, `helios.catalogs`, `helios.profiles`, `helios.commands` or `helios.tools`.
4. Add it to `testpaths`, the `dev` dependency group, an import-linter contract, the `package` matrix in `helios.yml` (with the Helios wheels it needs) and `CODEOWNERS`.

## Where things go

- **Library code** goes in a package; anything Cloudera-specific goes in helios-cloudera. importing helios-cortex reads no environment variables and touches no files (tested); only building `Settings` does, and its stable surface is listed in [CONTRACTS.md](packages/helios-cortex/CONTRACTS.md).
- **Tests** go in their package's `tests/`. Only a test that needs several packages together goes in `tests/integration`.
- **Data** isn't Python and isn't installed: the published TPC-DS model (`models/`), the ontology (`ontology/`), the sample warehouse scripts (`setup/`) and the Atlas glossary (`Atlas_Glossary/`).
- **Runtime state** goes under each deployment's `data_dir`, never in the repository.
