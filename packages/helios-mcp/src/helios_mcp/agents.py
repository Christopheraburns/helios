"""Configuration snippets that make a local agent launch ``helios mcp``
(``helios mcp --print-config AGENT``).

Each snippet names this environment's own ``helios`` (an absolute path, since an agent
does not activate your virtualenv) and an absolute ``--config``, so it works whatever
directory the agent starts the server in. Nothing secret is written: a caller token
stays in ``HELIOS_CALLER_TOKEN``, which you set in the agent's environment yourself.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

SERVER = "helios"
AGENTS = {
    "claude-code": "Claude Code: .mcp.json in the project (or `claude mcp add`)",
    "cursor": "Cursor: .cursor/mcp.json in the project, or ~/.cursor/mcp.json",
    "goose": "goose: under extensions in ~/.config/goose/config.yaml",
    "vibe": "Mistral Vibe: .vibe/config.toml in the project, or ~/.vibe/config.toml",
}


def launch(config: Path, caller: str | None = None) -> tuple[str, list[str]]:
    """The command and arguments that start ``helios mcp`` with ``config``: the ``helios``
    script beside this interpreter when there is one, else this interpreter running it."""
    script = Path(sys.executable).with_name("helios")
    command, prefix = (str(script), []) if script.is_file() else (sys.executable, ["-m", "helios_cortex.cli"])
    args = [*prefix, "--config", str(config), "mcp"]
    if caller:
        args += ["--as", caller]
    return command, args


def snippet(agent: str, command: str, args: list[str]) -> str:
    """The configuration ``agent`` reads, for one stdio server named ``helios``."""
    if agent == "claude-code":
        return json.dumps({"mcpServers": {SERVER: {"type": "stdio", "command": command, "args": args, "env": {}}}},
                          indent=2)
    if agent == "cursor":
        return json.dumps({"mcpServers": {SERVER: {"command": command, "args": args, "env": {}}}}, indent=2)
    if agent == "goose":
        import yaml

        return yaml.safe_dump({"extensions": {SERVER: {
            "name": SERVER, "type": "stdio", "enabled": True, "cmd": command, "args": args, "envs": {},
            "timeout": 300}}}, sort_keys=False)
    if agent == "vibe":
        # TOML basic strings take JSON's escapes, so json.dumps quotes paths safely.
        return "\n".join(["[[mcp_servers]]", f"name = {json.dumps(SERVER)}", 'transport = "stdio"',
                          f"command = {json.dumps(command)}", f"args = {json.dumps(args)}", ""])
    raise ValueError(f"unknown agent {agent!r}; one of {', '.join(AGENTS)}")


def server_entry(agent: str, text: str) -> tuple[str, list[str], dict[str, str]]:
    """Read a snippet back as an agent would: the command, arguments and environment it
    launches the ``helios`` server with."""
    if agent in ("claude-code", "cursor"):
        entry = json.loads(text)["mcpServers"][SERVER]
        return entry["command"], entry["args"], entry.get("env") or {}
    if agent == "goose":
        import yaml

        entry = yaml.safe_load(text)["extensions"][SERVER]
        assert entry["type"] == "stdio" and entry["enabled"]
        return entry["cmd"], entry["args"], entry.get("envs") or {}
    if agent == "vibe":
        import tomllib

        [entry] = [e for e in tomllib.loads(text)["mcp_servers"] if e["name"] == SERVER]
        assert entry["transport"] == "stdio"
        return entry["command"], entry["args"], entry.get("env") or {}
    raise ValueError(f"unknown agent {agent!r}")
