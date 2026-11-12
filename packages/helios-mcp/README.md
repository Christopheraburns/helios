# helios-mcp

The Helios tools over MCP on stdio, for one developer's local agent: Claude Code,
Cursor, goose, Mistral Vibe or any other MCP client.

```sh
pip install helios-mcp
helios mcp --help
```

You don't run `helios mcp` by hand: your agent launches it and talks to it over its
stdin and stdout.

**Local, single-user, unauthenticated.** helios-mcp acts with the credentials in your
own Helios settings (`helios.toml`, `HELIOS_*` variables). It serves nothing over the
network and checks no identity. Shared, governed deployments serve the same tools
through Daedalus, which runs each call as the real user.

## What it serves

Every tool in helios-serve's registry: `list_models`, `describe_model`,
`search_semantics`, `describe`, `compile_query`, `run_query`, and `explain_lineage` when
a catalog with lineage is installed (helios-cloudera's Atlas). Names, descriptions and
input schemas are the registry's; every tool is marked `readOnlyHint` (none changes
data). Arguments are checked against the schema before a tool runs, and a tool's error
(`invalid_semantic_query`, `model_not_found`, ...) comes back as an error result with a
message the agent can act on.

## Whose credentials

By default, yours: queries run with the credentials of the connection in your settings
(its `service` identity), so `helios mcp` works on a laptop with DuckDB. To have the
warehouse enforce someone's permissions instead, run it as them: `helios mcp --as USER`
(an `impersonate` connection), or set `HELIOS_CALLER_TOKEN` for a `token` connection.
A `service` connection then refuses, since it cannot run a query as anyone but itself.

Status (Phase 7 of `docs/helios-decomposition-burndown.md`): the package and the
`helios mcp` command (7.1) and the tools (7.2). Reading the same `helios.toml` as the
facade (7.3) and per-agent configuration snippets (7.5) follow.
