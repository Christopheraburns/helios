# helios-cloudera

Helios plugins for Cloudera Data Platform. Install it next to helios-cortex and
name the plugins in your settings:

```toml
[connections.default]
engine = "impala"
host = "jdbc:impala://<vw-host>:443/default;transportMode=http;httpPath=cliservice"
identity = "impersonate"   # or "service", "token"
user = "<workload user>"
password = "<workload password>"

[catalogs.default]
catalog = "atlas"
base_url = "https://<datalake-host>/<datalake>/cdp-proxy-api/atlas/api/atlas/v2"
user = "<workload user>"
password = "<workload password>"
```

| Module | What it provides |
|---|---|
| `helios_cloudera.impala` | `ImpalaEngine` (the `impala` engine plugin), `ImpalaOptions`, `impala_config` |
| `helios_cloudera.atlas` | `AtlasClient` (the `atlas` catalog plugin), `AtlasOptions`, `atlas_config` |
| `helios_cloudera.index` | `impala_index_store`; `helios --profile workbench impala init-index` (or `python -m helios_cloudera.index init-tables`) |
| `helios_cloudera.workbench` | `WorkbenchSettings`: Settings that also read the Workbench variables (`CDSW_*`, `IMPALA_*`, `WORKLOAD_*`, `ATLAS_*`, `INFERENCE_*`) |
| `helios_cloudera.impala_diagnose` | `helios --profile workbench impala diagnose [--delegated-user USER]` (or `python -m helios_cloudera.impala_diagnose`): checks workload and proxy authentication |
| `helios_cloudera.commands` | the `helios impala` command; and `--profile workbench` (`WorkbenchSettings`, registered under `helios.profiles`) |

On Cloudera AI Workbench, build `WorkbenchSettings()` instead of `Settings()`. It reads the
variables Workbench projects already set: `IMPALA_HOST` with `WORKLOAD_USER` and
`WORKLOAD_PASSWORD` become the `default` connection, `ATLAS_BASE` / `ATLAS_USER` / `ATLAS_PASS`
the `default` catalog, `INFERENCE_*` a Cloudera AI Inference LLM, and `CDSW_PROJECT_DIR` the
data directory. `HELIOS_*` variables and the config file still apply.

The Impala engine supports all three identity modes: `service` (LDAP as the
workload user), `impersonate` (adds `doAs` and checks `EFFECTIVE_USER()`) and
`token` (the caller's JWT, no workload user).
