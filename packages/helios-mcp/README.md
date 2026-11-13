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
network and checks no identity: anything that can launch it on your machine can use your
warehouse credentials through it. It is for one developer and their own agent. Shared,
governed deployments, where many people ask and each must be checked, serve the same tools
through Daedalus, which runs each call as the real user.

## Configure your agent

From your project (where `helios.toml` is), print the configuration for your agent:

```sh
helios mcp --print-config claude-code     # or cursor, goose, vibe
```

It names this environment's own `helios` and your config file as absolute paths, so it
works whatever directory the agent starts the server in, without activating your
virtualenv. Add `--as USER` to run every query as USER (see "Whose credentials"). The
output for each agent looks like this, with your paths:

**Claude Code**: `.mcp.json` in the project (Claude Code asks you to approve a project
server once), or the same in one command:
`claude mcp add --transport stdio --scope project helios -- /path/to/.venv/bin/helios --config /path/to/project/helios.toml mcp`

```json
{
  "mcpServers": {
    "helios": {
      "type": "stdio",
      "command": "/path/to/.venv/bin/helios",
      "args": ["--config", "/path/to/project/helios.toml", "mcp"],
      "env": {}
    }
  }
}
```

**Cursor**: `.cursor/mcp.json` in the project, or `~/.cursor/mcp.json` for every project.

```json
{
  "mcpServers": {
    "helios": {
      "command": "/path/to/.venv/bin/helios",
      "args": ["--config", "/path/to/project/helios.toml", "mcp"],
      "env": {}
    }
  }
}
```

**goose**: under `extensions` in `~/.config/goose/config.yaml`
(`%APPDATA%\Block\goose\config\config.yaml` on Windows).

```yaml
extensions:
  helios:
    name: helios
    type: stdio
    enabled: true
    cmd: /path/to/.venv/bin/helios
    args: [--config, /path/to/project/helios.toml, mcp]
    envs: {}
    timeout: 300
```

**Mistral Vibe**: `.vibe/config.toml` in the project, or `~/.vibe/config.toml`. Vibe
shows the tools as `helios_list_models`, `helios_run_query` and so on.

```toml
[[mcp_servers]]
name = "helios"
transport = "stdio"
command = "/path/to/.venv/bin/helios"
args = ["--config", "/path/to/project/helios.toml", "mcp"]
```

Any other MCP client that launches a stdio server takes the same command and arguments.
A token for a `token` connection is never written into these files: set
`HELIOS_CALLER_TOKEN` in the agent's environment (its `env`, `envs`, or your shell).

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

## Which configuration

The same as the `Helios` facade and every `helios` command: `HELIOS_CONFIG`, else
`./helios.toml`, else `[tool.helios]` in `./pyproject.toml`, under the `HELIOS_*`
variables. Relative paths in the file are relative to the file, so an agent that starts
`helios mcp` in your project (Claude Code does) or anywhere with
`HELIOS_CONFIG=/path/to/project/helios.toml` sees the models your programs see. A DuckDB
warehouse is opened read-only, so the agent, a notebook and a harvest can use it at once.

Status: Phase 7 of `docs/helios-decomposition-burndown.md` is done: the package and
command (7.1), the tools (7.2), the shared configuration (7.3), the tests on the sample
model (7.4) and this guide (7.5).
