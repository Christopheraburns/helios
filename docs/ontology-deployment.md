# Helios Ontology: Deployment and Operations

This guide covers deploying and operating the Helios ontology system: the graph database, API endpoints, and UI browser.

## Architecture Overview

The ontology system consists of five components:

1. **O-0: Helios Graph Application** — Memgraph instance (disposable graph database)
2. **O-1: Graph Gateway** — FastAPI service for materialisation and querying
3. **O-2: Ontology Parser** — LinkML schema → OntologyGraph transformation
4. **O-3: Ontology API** — REST endpoints for publish and query
5. **O-4: Ontology UI** — React browser for exploring versions and classes

## Prerequisites

### System Requirements

- **Docker**: For Helios Graph Application (Memgraph + gateway)
- **Python 3.12+**: For Helios Console API (parser, publish endpoint)
- **Node.js 18+**: For Helios UI (already included in Helios deployment)
- **Storage**: 500 MB–2 GB for ontology versions (content-addressed cache)

### Dependencies

**Helios Console API** requires:
- `linkml-runtime>=1.8.0` — included in helios runtime 0.1.3+
- `pyyaml>=6.0.0` — already in runtime
- `pydantic>=2.9.0` — already in runtime

**Helios Graph Application** requires:
- Memgraph 2.x
- Python 3.11+ (included in helios-graph runtime)

## Deployment Steps

### Step 1: Update Runtime

Ensure all applications use the latest Helios runtime with LinkML support:

```bash
# Helios Console/API: use helios runtime 0.1.3+
# This includes linkml-runtime for schema parsing

# Helios Graph Application: use helios-graph runtime 3.11+
# (Memgraph-specific, separate image)
```

**In Cloudera AI Workbench:**
1. Navigate to **Runtimes** → **Manage Runtimes**
2. Verify **helios 0.1.3+** is registered (includes LinkML)
3. For the Helios Graph Application, verify **helios-graph** runtime exists

### Step 2: Deploy Helios Graph Application

Create the Helios Graph Application in Workbench:

**Create Application:**
- **Name**: Helios Graph Application
- **Script**: `helios/apps/helios/graph/app.py` (relative to project root)
- **Runtime**: helios-graph (Python 3.11 with Memgraph)
- **Subdomain**: helios-graph (or any)

**Expected behavior:**
- On startup, the gateway:
  1. Launches Memgraph process (disposable)
  2. Probes Bolt connectivity on loopback:27687
  3. Exposes `/health`, `/ready`, `/v1/probe`, `/v1/diagnostics`
  4. Waits for rebuild trigger from Helios Console API

**Verify deployment:**
```bash
curl http://helios-graph:8080/health
# Expected: {"status": "ok"}

curl http://helios-graph:8080/ready
# Expected: {"status": "ok"} or 503 Service Unavailable if Memgraph fails
```

### Step 3: Deploy Helios Console API

Ensure Helios Console API is configured with the graph gateway URL:

**Create Application:**
- **Name**: Helios Console
- **Script**: `helios/apps/helios/console/app.py`
- **Runtime**: helios (Python 3.12, includes LinkML)
- **Subdomain**: helios (or any)

**Environment variables (if needed):**
```bash
HELIOS_GRAPH_GATEWAY_URL=http://helios-graph:8080
```

The console serves the ontology API at `/api/v1/ontology/*`.

### Step 4: Access Ontology Features

**Publish an ontology version:**
```bash
curl -X POST http://helios:8080/api/v1/ontology:publish \
  -H "Content-Type: application/json" \
  -d '{
    "version": "0.1.0",
    "schema_path": "ontology/customers/example-tenant/extension.yaml"
  }'

# Expected response:
# {
#   "version": "0.1.0",
#   "content_hash": "4bf8575e...",
#   "node_count": 42,
#   "edge_count": 296,
#   "enum_count": 3,
#   "broken_mappings": []
# }
```

**Query published versions:**
```bash
curl http://helios:8080/api/v1/ontology/versions
# Lists all published versions with metadata
```

**Retrieve full ontology graph:**
```bash
curl http://helios:8080/api/v1/ontology/0.1.0/graph
# Returns OntologyGraph JSON payload (nodes, edges, hash)
```

**Get class detail:**
```bash
curl http://helios:8080/api/v1/ontology/0.1.0/classes/Organization
# Returns class node, parents (is_a), attributes, attribute ranges
```

**Browse in Helios UI:**
Navigate to **Build → Ontology** in the Helios console at http://helios:8080

## Configuration

### Cache Location

Ontology versions are cached in the project storage at:
```
$CDSW_PROJECT_DIR/.helios-graph/ontology/
```

Structure:
```
.helios-graph/
├── ontology/
│   ├── active.json              # Active pointer
│   └── versions/
│       ├── 0.1.0/
│       │   └── {content_hash}.json
│       └── 0.2.0/
│           └── {content_hash}.json
```

**Cache behavior:**
- Content-addressed: files named by SHA-256 hash
- Write-once: republishing the same schema is idempotent
- Project-scoped: survives pod restarts
- Manual activation: `active.json` pointer controls which version is loaded

### Environment Variables

**Helios Console API:**
```bash
# Optional: override default ontology cache location
HELIOS_GRAPH_STORE_DIR=/custom/path/to/cache

# Optional: graph gateway URL (if running separately)
HELIOS_GRAPH_GATEWAY_URL=http://helios-graph:8080
```

**Helios Graph Application:**
```bash
# Optional: Memgraph configuration
# (Most settings passed explicitly to avoid root permission issues)
MEMGRAPH_CONFIG=                # Set to empty (avoids /etc/memgraph/memgraph.conf)
```

## Schema Requirements

Ontology schemas must be LinkML YAML files with specific structure:

**Minimum schema:**
```yaml
id: https://helios.dev/ontology/my-schema
name: my_ontology
title: My Ontology
version: 0.1.0

prefixes:
  linkml: https://w3id.org/linkml/
  default_prefix: helios

imports:
  - linkml:types

classes:
  MyClass:
    description: A concrete class
    attributes:
      my_attribute:
        range: string
```

**Multi-layer import example:**
```yaml
# ontology/customers/example-tenant/extension.yaml
id: https://helios.dev/ontology/customer/example
name: example_tenant_extension
imports:
  - ../../../packs/retail/retail.yaml
  - ../../../core/core.yaml

classes:
  FranchisePartner:
    is_a: Organization
    description: A franchise partner (maps to Ossie entities.franchise_partner)
    attributes:
      franchise_id:
        range: string
```

**Mapping to Ossie elements** (for broken mapping detection):
```yaml
classes:
  Customer:
    description: |
      A retail customer entity. This class is materialized from
      the TPC-DS schema via Ossie.
      [Ossie: entities.customer]
```

Mappings use `[Ossie: path.to.element]` in the class description.

## Operations

### Publish an Ontology Version

**Via API (recommended):**
```bash
curl -X POST http://helios:8080/api/v1/ontology:publish \
  -H "Content-Type: application/json" \
  -d '{"version": "0.2.0", "schema_path": "ontology/your/schema.yaml"}'
```

**Constraints:**
- `schema_path` must be relative to repository root
- File must exist and be readable
- LinkML must parse successfully (imports resolved, no errors)
- Mappings are validated (broken mappings returned in response)

**Idempotency:**
Publishing the same schema twice produces the same `content_hash`, so repeated publishes are safe (no duplicate cache entries).

### List Published Versions

```bash
curl http://helios:8080/api/v1/ontology/versions
```

Response:
```json
[
  {
    "version": "0.1.0",
    "content_hash": "4bf8575e3f9f...",
    "node_count": 42,
    "edge_count": 296,
    "enum_count": 3,
    "is_active": false
  }
]
```

### Query the Graph

**Full graph (for visualization):**
```bash
curl http://helios:8080/api/v1/ontology/{version}/graph
```

**Class detail (for inspection):**
```bash
curl http://helios:8080/api/v1/ontology/{version}/classes/{class_name}
```

Returns:
```json
{
  "class": {"name": "Customer", "properties": {...}},
  "parents": ["Person"],
  "attributes": [
    {"name": "Customer#loyalty_tier", "properties": {...}}
  ],
  "ranges": [
    {"attribute": "Customer#affiliated_with", "range_class": "Organization"}
  ]
}
```

### Monitor Graph Gateway Health

```bash
# Liveness probe (pod is running)
curl http://helios-graph:8080/health

# Readiness probe (Memgraph is available)
curl http://helios-graph:8080/ready

# Detailed diagnostics
curl http://helios-graph:8080/v1/diagnostics
```

Diagnostics response includes:
- Memgraph startup stderr/stdout (for troubleshooting failed starts)
- Pod memory constraints (used to tune Memgraph `--memory-limit`)
- Startup duration
- Last rebuild status

## Troubleshooting

### Schema Parse Fails

**Error:** `HTTP 400: schema parse failed: LinkMLError: ...`

**Causes:**
- Invalid YAML syntax — check schema file directly with `yamllint`
- Missing import — relative paths must resolve (e.g., `../../../core/core.yaml`)
- Unknown class referenced in `is_a` or ranges
- Circular imports

**Resolution:**
1. Validate YAML: `yamllint ontology/your/schema.yaml`
2. Check relative imports from schema location
3. Verify all `is_a` parent classes exist
4. Check for circular imports with `linkml-lint` (if available)

### Memgraph Fails to Start

**Error:** `/health` returns 503, `/diagnostics` shows startup error

**Causes:**
- Memory limit too high (exceeds cgroup ceiling)
- Disk full in `/tmp/helios-graph/`
- Port 27687 already in use
- Missing system libraries

**Resolution:**
1. Check `/v1/diagnostics` stderr for error message
2. Verify pod memory allocation (graph gateway needs ≥2 GB)
3. Clear `/tmp/helios-graph/` if full
4. Ensure port 27687 is not in use: `netstat -tulpn | grep 27687`

### UI Shows No Versions

**Cause:** Ontology API not accessible from browser

**Resolution:**
1. Check Helios Console is running: `curl http://helios:8080/health`
2. Check CORS headers if UI is on different domain
3. Verify `/api/v1/ontology/versions` returns 200: `curl http://helios:8080/api/v1/ontology/versions`

### Broken Mappings Reported

**Response includes:**
```json
"broken_mappings": [
  {
    "class": "Customer",
    "mapped_to": "entities.nonexistent_table",
    "status": "not_found"
  }
]
```

**Resolution:**
1. Verify the Ossie element path in the schema (e.g., `entities.customer` vs `dim_customer`)
2. Remove `[Ossie: ...]` annotation if not intended
3. Use correct Ossie model path format: `{table_type}.{table_name}`

## Performance Considerations

### Cache Size

Ontology versions are small:
- Core ontology: ~40 KB
- Full extension: ~50 KB
- Disk storage: <10 MB for typical deployments (even with 100+ versions)

### Query Performance

- **List versions**: O(n) where n = number of versions (typically <10)
- **Get graph**: O(1) retrieval + O(n) JSON serialization where n = node count (~300 nodes)
- **Get class detail**: O(n) where n = edges from the class (~50 edges)

All queries are sub-second on typical hardware.

### Memgraph Memory

Memory limit is set to 60% of pod cgroup ceiling, leaving 40% for gateway + system overhead.

- Pod with 4 GB allocation → Memgraph gets ~2.4 GB
- Pod with 2 GB allocation → Memgraph gets ~1.2 GB

Minimum recommended: 2 GB pod allocation.

## Maintenance

### Backup Ontology Versions

Ontology cache is stored in project storage:
```bash
# Backup cache
tar czf ontology-backup.tar.gz $CDSW_PROJECT_DIR/.helios-graph/ontology/

# Restore
tar xzf ontology-backup.tar.gz -C $CDSW_PROJECT_DIR/
```

### Clean Old Versions

Versions are content-addressed, so deleting unused payloads is safe:
```bash
# List all versions
curl http://helios:8080/api/v1/ontology/versions | jq '.[] | .version'

# Remove version 0.1.0 (all hashes)
rm $CDSW_PROJECT_DIR/.helios-graph/ontology/versions/0.1.0/*
```

### Validate Ontology Integrity

Run the verification probe in any Helios session:
```bash
cd /home/cdsw/helios
PYTHONPATH=shared:. python scripts/verify_linkml.py
```

This confirms:
- LinkML parser works in runtime
- Multi-layer imports resolve
- Inherited slots are detected
- Content hash is deterministic

## Next Steps

1. **Publish a schema**: Use the API or `/api/v1/ontology:publish` endpoint
2. **Materialize in graph**: Call graph gateway rebuild (O-1)
3. **Query and browse**: Use API endpoints or UI at `/ontology`
4. **Integrate with Helios**: Link ontology classes to Ossie elements for data lineage

---

**Questions or issues?** Check logs:
- **Helios Console**: Workbench logs for parser errors
- **Graph Gateway**: Workbench logs for Memgraph startup/probe failures
- **UI**: Browser console for fetch/network errors
