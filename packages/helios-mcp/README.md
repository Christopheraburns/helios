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

Status (Phase 7 of `docs/helios-decomposition-burndown.md`): the package and the
`helios mcp` command (7.1). Serving the registry's tools (7.2), reading the same
`helios.toml` as the facade (7.3), and per-agent configuration snippets (7.5) follow.
