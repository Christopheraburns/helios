# helios-cloudera

Helios plugins for Cloudera Data Platform. Install it next to helios-core and
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
| `helios_cloudera.index` | `impala_index_store`; `python -m helios_cloudera.index init-tables` |
| `helios_cloudera.impala_diagnose` | `python -m helios_cloudera.impala_diagnose`: checks workload and proxy authentication |

The Impala engine supports all three identity modes: `service` (LDAP as the
workload user), `impersonate` (adds `doAs` and checks `EFFECTIVE_USER()`) and
`token` (the caller's JWT, no workload user).
