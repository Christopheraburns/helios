"""The tool registry: every Helios tool installed, with what a server needs to offer it.

A tool is a plain function ``tool(caller, stores, **arguments) -> dict`` marked with
``@tool(effect=..., description=...)`` and registered under the ``helios.tools`` entry
point group, its entry-point name being the tool's name:

    [project.entry-points."helios.tools"]
    run_query = "helios_serve.tools:run_query"

``load_registry()`` finds them and gives each its name, the description an agent reads,
an input schema (JSON Schema) built from its type hints, and its ``effect``. Daedalus,
helios-mcp and the tests read this one source, so a tool is described once.

Input schemas use the subset of JSON Schema that MCP and OpenAPI 3.0 share (Daedalus's
executor publishes its tools as an OpenAPI 3.0 document): an optional argument is left
out of ``required`` rather than typed as ``null``. ``Annotated[T, "text"]`` gives a
parameter its description, and a ``TypedDict`` becomes an object schema.

A tool that needs a catalog capability (``requires={"lineage"}``) registers only when an
installed catalog plugin offers it, so ``explain_lineage`` appears with helios-cloudera
(Atlas) and not on a base install.
"""
from __future__ import annotations

import inspect
import logging
import types
import typing
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

LOGGER = logging.getLogger(__name__)

METADATA = "__helios_tool__"     # the attribute @tool sets on a tool function
LEADING = ("caller", "stores")   # every tool's first two parameters; not part of its input


class Effect(str, Enum):
    """What calling a tool does. No Helios tool changes data.

    ``read``  reads Helios's own metadata: published models, their semantics, the catalog.
    ``query`` runs SQL on the warehouse as the caller (read-only SQL, but it touches
              data and costs warehouse time). ``run_query`` is the only one; a read-only
              metadata server leaves these out.
    """

    READ = "read"
    QUERY = "query"

    @property
    def executes_sql(self) -> bool:
        return self is Effect.QUERY


@dataclass(frozen=True)
class ToolMetadata:
    """What ``@tool`` records on a function."""

    effect: Effect
    description: str
    requires: frozenset[str] = frozenset()


def tool(*, effect: Effect | str, description: str | None = None,
         requires: Iterable[str] = ()) -> Callable[[Callable], Callable]:
    """Mark ``function`` as a Helios tool. ``description`` is what an agent reads (default:
    the docstring); ``requires`` names catalog capabilities the tool needs (``lineage``,
    ``glossary``). The function is returned unchanged, so it stays directly callable."""
    effect = Effect(effect)

    def mark(function: Callable) -> Callable:
        text = inspect.cleandoc(description or inspect.getdoc(function) or "")
        if not text:
            raise TypeError(f"{function.__name__}: a tool needs a description or a docstring")
        input_schema(function)  # a signature the schema cannot express fails here, at import
        setattr(function, METADATA, ToolMetadata(effect, text, frozenset(requires)))
        return function

    return mark


# ---------------------------------------------------------------------------- schemas
_SCALARS = {str: "string", int: "integer", float: "number", bool: "boolean"}


def _split_annotated(hint: Any) -> tuple[Any, str | None]:
    while typing.get_origin(hint) in (typing.Required, typing.NotRequired):   # TypedDict keys
        hint = typing.get_args(hint)[0]
    if typing.get_origin(hint) is typing.Annotated:
        base, *extras = typing.get_args(hint)
        return base, next((e for e in extras if isinstance(e, str)), None)
    return hint, None


def _strip_none(hint: Any) -> tuple[Any, bool]:
    """``T | None`` → ``(T, True)``."""
    if typing.get_origin(hint) in (typing.Union, types.UnionType):
        args = [a for a in typing.get_args(hint) if a is not type(None)]
        if len(args) != len(typing.get_args(hint)):
            if len(args) != 1:
                raise TypeError(f"unsupported union {hint!r}")
            return args[0], True
    return hint, False


def schema_for(hint: Any) -> dict[str, Any]:
    """The JSON Schema for one type hint."""
    hint, description = _split_annotated(hint)
    hint, _ = _strip_none(hint)
    schema = _schema(hint)
    if description:
        schema["description"] = description
    return schema


def _schema(hint: Any) -> dict[str, Any]:
    if hint is Any:
        return {}
    if hint in _SCALARS:
        return {"type": _SCALARS[hint]}
    origin, args = typing.get_origin(hint), typing.get_args(hint)
    if origin is typing.Literal:
        kinds = {type(a) for a in args}
        if len(kinds) != 1 or next(iter(kinds)) not in _SCALARS:
            raise TypeError(f"a Literal must hold values of one scalar type: {hint!r}")
        return {"type": _SCALARS[next(iter(kinds))], "enum": list(args)}
    if origin in (list, tuple, set, frozenset) or hint in (list, tuple):
        if origin is tuple and not (len(args) == 2 and args[1] is Ellipsis):
            raise TypeError(f"use list[T] or tuple[T, ...], not {hint!r}")
        return {"type": "array", "items": schema_for(args[0]) if args else {}}
    if typing.is_typeddict(hint):
        hints = typing.get_type_hints(hint, include_extras=True)
        # Required/NotRequired read from the resolved hints: under ``from __future__ import
        # annotations``, Python before 3.13 leaves them out of __required_keys__.
        required = [name for name, h in hints.items()
                    if typing.get_origin(h) is typing.Required
                    or (hint.__total__ and typing.get_origin(h) is not typing.NotRequired)]
        return {"type": "object", "properties": {name: schema_for(h) for name, h in hints.items()},
                "required": required, "additionalProperties": False}
    if origin is dict or hint is dict:
        return {"type": "object"}
    raise TypeError(f"no JSON Schema for {hint!r}")


def input_schema(function: Callable) -> dict[str, Any]:
    """The JSON Schema of ``function``'s arguments after ``caller`` and ``stores``: an
    argument with no default is required; defaults other than None are stated."""
    target = inspect.unwrap(function)
    parameters = list(inspect.signature(target).parameters.values())
    name = getattr(function, "__name__", repr(function))
    if tuple(p.name for p in parameters[:2]) != LEADING:
        raise TypeError(f"{name}: a tool's first parameters are (caller, stores)")
    hints = typing.get_type_hints(target, include_extras=True)
    properties: dict[str, Any] = {}
    required: list[str] = []
    for parameter in parameters[2:]:
        if parameter.kind not in (parameter.KEYWORD_ONLY, parameter.POSITIONAL_OR_KEYWORD):
            raise TypeError(f"{name}: parameter {parameter.name} must be a named argument")
        if parameter.name not in hints:
            raise TypeError(f"{name}: parameter {parameter.name} needs a type hint")
        try:
            schema = schema_for(hints[parameter.name])
        except TypeError as exc:
            raise TypeError(f"{name}: parameter {parameter.name}: {exc}") from None
        if parameter.default is parameter.empty:
            required.append(parameter.name)
        elif parameter.default is not None:
            schema["default"] = parameter.default
        properties[parameter.name] = schema
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


# ---------------------------------------------------------------------------- registry
@dataclass(frozen=True)
class Tool:
    """One registered tool. Call it like the function: ``tool(caller, stores, **arguments)``."""

    name: str
    description: str
    input_schema: dict[str, Any]
    effect: Effect
    function: Callable[..., dict[str, Any]] = field(repr=False, compare=False)
    requires: frozenset[str] = frozenset()
    distribution: str | None = None   # the package that registers it

    def __call__(self, caller, stores, **arguments: Any) -> dict[str, Any]:
        return self.function(caller, stores, **arguments)

    def as_dict(self) -> dict[str, Any]:
        """The tool's description as JSON: name, description, input_schema, effect."""
        return {"name": self.name, "description": self.description, "input_schema": self.input_schema,
                "effect": self.effect.value}


def tool_from(function: Callable, *, name: str | None = None, distribution: str | None = None) -> Tool:
    """The Tool a ``@tool``-marked function describes."""
    metadata = getattr(function, METADATA, None)
    if not isinstance(metadata, ToolMetadata):
        raise TypeError(f"{function!r} is not marked with helios_serve.tool")
    return Tool(name or function.__name__, metadata.description, input_schema(function), metadata.effect,
                function, metadata.requires, distribution)


class Registry(Mapping[str, Tool]):
    """The installed tools by name."""

    def __init__(self, tools: Iterable[Tool] = ()):
        self._tools: dict[str, Tool] = {}
        for entry in tools:
            if entry.name in self._tools:
                raise ValueError(f"tool {entry.name!r} is registered twice")
            self._tools[entry.name] = entry

    def __getitem__(self, name: str) -> Tool:
        return self._tools[name]

    def __iter__(self) -> Iterator[str]:
        return iter(self._tools)

    def __len__(self) -> int:
        return len(self._tools)

    def __repr__(self) -> str:
        return f"Registry({', '.join(self._tools)})"

    def tools(self, *, effects: Iterable[Effect | str] | None = None) -> tuple[Tool, ...]:
        """Every tool, or those with one of ``effects`` (``effects={"read"}`` is the
        read-only metadata server: no SQL runs)."""
        wanted = None if effects is None else {Effect(e) for e in effects}
        return tuple(t for t in self._tools.values() if wanted is None or t.effect in wanted)

    def call(self, name: str, caller, stores, **arguments: Any) -> dict[str, Any]:
        """Run the tool ``name``; KeyError for a tool that is not registered."""
        return self._tools[name](caller, stores, **arguments)

    def as_dicts(self) -> list[dict[str, Any]]:
        return [t.as_dict() for t in self._tools.values()]


def _catalog_capabilities() -> frozenset[str]:
    """What the installed catalog plugins offer between them (``lineage``, ``glossary``),
    judged from each plugin class's methods; a plugin that fails to import offers nothing."""
    from helios_cortex import plugins
    from helios_cortex.catalog import GLOSSARY, LINEAGE, GlossaryCatalog, LineageCatalog

    methods = {capability: [n for n, v in vars(protocol).items() if not n.startswith("_") and callable(v)]
               for capability, protocol in ((GLOSSARY, GlossaryCatalog), (LINEAGE, LineageCatalog))}
    found: set[str] = set()
    for name in plugins.names(plugins.CATALOGS):
        try:
            plugin = plugins.load(plugins.CATALOGS, name)
        except plugins.PluginError as exc:
            LOGGER.warning("catalog plugin %s is not usable: %s", name, exc)
            continue
        found |= {c for c, needed in methods.items() if all(callable(getattr(plugin, m, None)) for m in needed)}
    return frozenset(found)


def load_registry(*, catalog_capabilities: Iterable[str] | None = None) -> Registry:
    """Every tool registered under ``helios.tools`` by an installed package. A tool that
    requires catalog capabilities is left out unless an installed catalog plugin offers
    them (``catalog_capabilities`` overrides what is installed). Raises
    ``helios_cortex.plugins.PluginError`` for a tool that is registered twice, fails to
    import, is not marked with ``@tool`` or is registered under another name."""
    from helios_cortex import plugins

    available = frozenset(catalog_capabilities) if catalog_capabilities is not None else None
    found: list[Tool] = []
    for info in plugins.installed(plugins.TOOLS):   # by name
        function = plugins.load(plugins.TOOLS, info.name)
        metadata = getattr(function, METADATA, None)
        if not isinstance(metadata, ToolMetadata):
            raise plugins.PluginError(f"tool {info.name!r} ({info.target}) is not marked with helios_serve.tool")
        if getattr(function, "__name__", info.name) != info.name:
            raise plugins.PluginError(f"tool {info.name!r} is registered as {info.target}, a function of another name")
        if metadata.requires:
            if available is None:
                available = _catalog_capabilities()
            if not metadata.requires <= available:
                LOGGER.info("tool %s is not offered: no installed catalog provides %s", info.name,
                            ", ".join(sorted(metadata.requires - available)))
                continue
        found.append(tool_from(function, name=info.name, distribution=info.distribution))
    return Registry(found)
