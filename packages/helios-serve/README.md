# helios-serve

The Helios tools, as plain Python functions: list the published semantic models,
describe one, search their semantics, compile a question into SQL, run it as the
caller, and explain a column's lineage. Each tool is a function
`(caller, stores, **arguments) -> dict`, listed in a registry with its name,
description, input schema and whether it executes SQL.

helios-serve depends only on helios-cortex. It has no MCP dependency and no HTTP
server: Daedalus serves the tools to shared, governed
deployments, and helios-mcp serves them over stdio to one developer's local agent.

Status: scaffold (burndown 6.1). The tools move here in 6.2, the registry in 6.3 and
`health` in 6.4 (Phase 6 of `docs/helios-decomposition-burndown.md`).
