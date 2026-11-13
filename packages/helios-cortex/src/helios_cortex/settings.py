"""Helios configuration: one object that the host builds and passes in.

Values are resolved in this order, highest first:

1. arguments to ``Settings(...)``
2. ``HELIOS_*`` environment variables; sections nest with ``__``
   (``HELIOS_DATA_DIR``, ``HELIOS_CONNECTIONS__SALES__URL``, ``HELIOS_LLM__TIMEOUT_SECONDS``)
3. the conventional environment variables: ``HELIOS_ROOT`` and the LLM
   ecosystem's names (``ANTHROPIC_API_KEY``, ``MISTRAL_API_KEY``, ...); see
   ``ConventionalEnvironment``. A subclass adds a platform's variables here
   through ``profile_sources`` (helios-cloudera's ``WorkbenchSettings`` reads
   ``CDSW_*``, ``IMPALA_*``, ``WORKLOAD_*``, ``ATLAS_*`` and ``INFERENCE_*``)
4. a config file: ``HELIOS_CONFIG``, else ``./helios.toml``, else
   ``[tool.helios]`` in ``./pyproject.toml`` (relative file paths in it are relative to the file, see ``anchored``)
5. defaults; files live under the user's data directory

Importing this module reads nothing; constructing ``Settings`` reads the
environment and the config file once.
"""
from __future__ import annotations

import logging
import math
import os
import tomllib
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Literal

import platformdirs
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator
from pydantic_settings import (
    BaseSettings,
    InitSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

from helios_cortex.caller import IdentityMode

LOGGER = logging.getLogger(__name__)

JournalMode = Literal["DELETE", "TRUNCATE", "PERSIST", "WAL"]
LLMProvider = Literal["anthropic", "openai", "mistral"]

DEFAULT_AUDIT_RETENTION_DAYS = 30
DEFAULT_LLM_MODELS = {"anthropic": "claude-sonnet-5", "mistral": "mistral-small-latest", "openai": ""}
DEFAULT_MISTRAL_BASE_URL = "https://api.mistral.ai/v1"
DEFAULT = "default"  # the connection / catalog used when nothing names one

_config_file: ContextVar[Path | None] = ContextVar("helios_config_file", default=None)


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# Also as suffixes: ssl_token, client_secret, sas_token. account_key, connection_string and
# credential are Azure's (adlfs); a connection string carries the account key.
SECRET_OPTIONS = ("password", "token", "secret", "api_key", "account_key", "connection_string", "credential")


def _is_secret(name: str) -> bool:
    # A bare ``url`` is a database URL, which often embeds a password; base_url is not.
    return name == "url" or any(name == key or name.endswith("_" + key) for key in SECRET_OPTIONS)


class _PluginSection(BaseModel):
    """A named entry that a plugin interprets. Core checks only which plugin; the
    plugin validates the rest (``options``) when it is used, so a config that names
    a plugin which is not installed still loads. Secret-looking options (those in
    SECRET_OPTIONS, and url, which often embeds a password) are kept as SecretStr."""

    model_config = ConfigDict(extra="allow", frozen=True)

    @model_validator(mode="before")
    @classmethod
    def _hide_secrets(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        return {
            k: SecretStr(str(v)) if _is_secret(k) and v is not None and not isinstance(v, SecretStr) else v
            for k, v in data.items()
        }

    @property
    def options(self) -> dict[str, Any]:
        """Everything except the plugin name, for the plugin to validate."""
        return dict(self.model_extra or {})


class ConnectionSettings(_PluginSection):
    """A warehouse or database, referenced by ``DataSource.connection_ref``.

    ``engine`` names an engine plugin (impala, duckdb, sqlalchemy, ...). ``identity``
    is whose identity a caller's query runs as: ``service`` (the connection's own
    credentials, the default), ``impersonate`` (the caller's username) or ``token``
    (the caller's token). The engine refuses a mode it does not support."""

    engine: str
    identity: IdentityMode = IdentityMode.SERVICE


class CatalogSettings(_PluginSection):
    """A metadata catalog (glossary, lineage). ``catalog`` names a catalog plugin (atlas, ...)."""

    catalog: str


class ArtifactStorageSettings(_PluginSection):
    """Object storage for artifacts (``helios_cortex.artifact_storage``): ``location`` is a
    URL (``s3://bucket/helios``, ``abfss://…``, ``gs://…``, ``ofs://…``); every other key
    goes to the storage's filesystem (``key``, ``secret``, ``endpoint_url``,
    ``account_key``, ``httpfs_url``, ...). Secret-looking keys stay hidden in output."""

    location: str

    @property
    def storage_options(self) -> dict[str, Any]:
        """The options with secrets revealed, for the filesystem."""
        return {k: v.get_secret_value() if isinstance(v, SecretStr) else v for k, v in self.options.items()}


class LLMSettings(_Section):
    """The LLM used for proposals and the console. Unconfigured when ``provider`` is None."""

    provider: LLMProvider | None = None
    model: str | None = None
    api_key: SecretStr | None = None
    base_url: str | None = None
    timeout_seconds: float = 30.0

    @field_validator("timeout_seconds")
    @classmethod
    def _positive(cls, value: float) -> float:
        if not math.isfinite(value) or value <= 0:
            raise ValueError("timeout_seconds must be a positive number")
        return value

    @model_validator(mode="after")
    def _provider_defaults(self) -> LLMSettings:
        if self.provider is None:
            return self
        if self.model is None:
            object.__setattr__(self, "model", DEFAULT_LLM_MODELS[self.provider])  # frozen; filled once, here
        if self.provider == "mistral" and self.base_url is None:
            object.__setattr__(self, "base_url", DEFAULT_MISTRAL_BASE_URL)
        return self


class EvidenceSettings(_Section):
    """Document search over crawled passages (moves to helios-crawl)."""

    index: Path | None = None  # default: <data_dir>/state/evidence.lance
    embedding_model: str = "all-MiniLM-L6-v2"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="HELIOS_",
        env_nested_delimiter="__",
        extra="forbid",
        frozen=True,
    )

    # Where Helios keeps its files. The others default to places inside it:
    # <data_dir>/models (artifacts), <data_dir>/runs, <data_dir>/state/helios.db,
    # and the model registry at sqlite:///<data_dir>/state/registry.db.
    data_dir: Path = Field(default_factory=lambda: platformdirs.user_data_path("helios"))
    artifacts_dir: Path | None = None
    runs_dir: Path | None = None
    metadata_db: Path | None = None
    # The model registry's database (helios_cortex.registry.SqlRegistry), any SQLAlchemy URL.
    # Secret because a URL may carry a password.
    registry_url: SecretStr | None = None
    sqlite_journal_mode: JournalMode = "DELETE"
    audit_retention_days: int = DEFAULT_AUDIT_RETENTION_DAYS

    connections: dict[str, ConnectionSettings] = {}
    catalogs: dict[str, CatalogSettings] = {}
    # Object storage for artifacts; when unset they live in artifacts_dir.
    artifact_storage: ArtifactStorageSettings | None = None
    llm: LLMSettings = LLMSettings()
    evidence: EvidenceSettings = EvidenceSettings()

    def resolve_connection(self, ref: str | None = None) -> str | None:
        """The name of the connection ``ref`` (a ``DataSource.connection_ref``) means:
        the connection of that name, else ``default``, else None. Data sources recorded
        before named connections (``impala``, ``env:IMPALA_HOST``) resolve to ``default``."""
        if ref and ref in self.connections:
            return ref
        return DEFAULT if DEFAULT in self.connections else None

    def connection(self, ref: str | None = None) -> ConnectionSettings | None:
        name = self.resolve_connection(ref)
        return self.connections[name] if name else None

    def catalog(self, name: str | None = None) -> CatalogSettings | None:
        """The catalog of that name, else ``default``, else None."""
        return self.catalogs.get(name or DEFAULT) or self.catalogs.get(DEFAULT)

    @classmethod
    def from_file(cls, path: str | os.PathLike[str], **overrides: Any) -> Settings:
        """Settings with ``path`` as the config file (layer 4) instead of the usual search."""
        token = _config_file.set(Path(path))
        try:
            return cls(**overrides)
        finally:
            _config_file.reset(token)

    @field_validator("sqlite_journal_mode", mode="before")
    @classmethod
    def _upper(cls, value: Any) -> Any:
        return value.strip().upper() if isinstance(value, str) else value

    @field_validator("audit_retention_days", mode="before")
    @classmethod
    def _clamp_retention(cls, value: Any) -> int:
        try:
            return min(max(int(value), 1), 3650)
        except (TypeError, ValueError):
            LOGGER.warning("invalid audit_retention_days %r; using %d", value, DEFAULT_AUDIT_RETENTION_DAYS)
            return DEFAULT_AUDIT_RETENTION_DAYS

    @model_validator(mode="after")
    def _derived_paths(self) -> Settings:
        state = self.data_dir / "state"
        defaults = {
            "artifacts_dir": self.data_dir,
            "runs_dir": self.data_dir / "runs",
            "metadata_db": state / "helios.db",
        }
        updates: dict[str, Any] = {k: v for k, v in defaults.items() if getattr(self, k) is None}
        if self.registry_url is None:
            updates["registry_url"] = SecretStr(f"sqlite:///{state / 'registry.db'}")
        if self.evidence.index is None:
            updates["evidence"] = self.evidence.model_copy(update={"index": state / "evidence.lance"})
        for name, value in updates.items():
            object.__setattr__(self, name, value)  # frozen; filled once, here
        return self

    @classmethod
    def profile_sources(cls, settings_cls: type[BaseSettings]) -> tuple[PydanticBaseSettingsSource, ...]:
        """Sources a subclass adds for its platform, after the conventional environment
        and before the config file. Each returns plain values that Settings validates."""
        return ()

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            env_settings,
            ConventionalEnvironment(settings_cls),
            *cls.profile_sources(settings_cls),
            _config_file_source(settings_cls),
        )


def config_file() -> Path | None:
    """The config file Settings would read now: ``from_file``'s, else ``HELIOS_CONFIG``,
    else ``./helios.toml``, else ``./pyproject.toml``; None when there is none. An explicit
    file is returned even when it is missing (Settings then refuses it)."""
    explicit = _config_file.get() or (Path(os.environ["HELIOS_CONFIG"]) if os.environ.get("HELIOS_CONFIG") else None)
    return explicit or next((p for p in (Path("helios.toml"), Path("pyproject.toml")) if p.is_file()), None)


def _config_file_source(settings_cls: type[BaseSettings]) -> PydanticBaseSettingsSource:
    path = config_file()
    if path is not None and not path.is_file():
        raise FileNotFoundError(f"Helios config file not found: {path}")
    values: dict[str, Any] = {}
    if path is not None:
        with path.open("rb") as handle:
            values = tomllib.load(handle)
        if path.name == "pyproject.toml":
            values = values.get("tool", {}).get("helios", {})
        values = anchored(values, path.resolve().parent)
    return InitSettingsSource(settings_cls, init_kwargs=values)


FILE_SETTINGS = ("data_dir", "artifacts_dir", "runs_dir", "metadata_db")


def anchored(values: dict[str, Any], base: Path) -> dict[str, Any]:
    """A config file's values with its relative file paths made relative to the file's own
    directory, so a file means the same wherever the process starts (an agent launching
    ``helios mcp --config /project/helios.toml`` from elsewhere, a notebook in a subfolder).
    The paths: ``data_dir``, ``artifacts_dir``, ``runs_dir``, ``metadata_db``,
    ``evidence.index``, each connection's ``path`` (DuckDB's database file), a relative
    SQLite ``registry_url`` and a local ``artifact_storage.location``. ``~`` expands.
    Environment variables and values passed in code keep meaning the working directory."""
    def file(value: Any) -> Any:
        if not isinstance(value, str) or not value or value == ":memory:":
            return value
        candidate = Path(value).expanduser()
        return str(candidate if candidate.is_absolute() else base / candidate)

    values = dict(values)
    for name in FILE_SETTINGS:
        if name in values:
            values[name] = file(values[name])
    if isinstance(values.get("evidence"), dict) and "index" in values["evidence"]:
        values["evidence"] = {**values["evidence"], "index": file(values["evidence"]["index"])}
    if isinstance(values.get("connections"), dict):
        values["connections"] = {
            name: {**entry, "path": file(entry["path"])} if isinstance(entry, dict) and "path" in entry else entry
            for name, entry in values["connections"].items()}
    url = values.get("registry_url")
    if isinstance(url, str) and url.startswith("sqlite:///") and not url.startswith("sqlite:////"):
        database = url[len("sqlite:///"):]
        if database and database != ":memory:":
            values["registry_url"] = "sqlite:///" + file(database)
    storage = values.get("artifact_storage")
    if isinstance(storage, dict) and isinstance(storage.get("location"), str) and "://" not in storage["location"]:
        values["artifact_storage"] = {**storage, "location": file(storage["location"])}
    return values


SECRET_NAME_PARTS = ("credential", "password", "secret", "token", "api_key", "apikey")


def environment_secret_values() -> set[str]:
    """Values of environment variables whose names look secret, for redaction."""
    return {
        value
        for name, value in os.environ.items()
        if value and len(value) >= 4 and any(part in name.lower() for part in SECRET_NAME_PARTS)
    }


def _env(name: str) -> str | None:
    value = os.environ.get(name)
    return value if value not in (None, "") else None


def _compact(values: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in values.items() if v is not None}


class ConventionalEnvironment(PydanticBaseSettingsSource):
    """Environment variables Helios reads without the ``HELIOS_`` nesting: ``HELIOS_ROOT``
    (the data directory), ``HELIOS_LLM_TIMEOUT_SECONDS``, ``HELIOS_EVIDENCE_INDEX``,
    ``HELIOS_EMBEDDING_MODEL``, and the LLM ecosystem's names.

    The Cloudera and Workbench names (``CDSW_*``, ``WORKLOAD_*``, ``IMPALA_*``,
    ``ATLAS_*``, ``INFERENCE_*``) are read by helios-cloudera's Workbench profile.
    """

    def get_field_value(self, field: Any, field_name: str) -> tuple[Any, str, bool]:
        return None, field_name, False  # unused: __call__ builds the whole mapping

    def __call__(self) -> dict[str, Any]:
        values: dict[str, Any] = _compact({"data_dir": _env("HELIOS_ROOT")})
        llm = self._llm()
        if llm:
            values["llm"] = llm
        evidence = _compact({
            "index": _env("HELIOS_EVIDENCE_INDEX"),
            "embedding_model": _env("HELIOS_EMBEDDING_MODEL"),
        })
        if evidence:
            values["evidence"] = evidence
        return values

    @staticmethod
    def _llm() -> dict[str, Any]:
        """The provider is LLM_PROVIDER, else whichever API key is set (anthropic, then
        mistral). Otherwise it is an OpenAI-compatible endpoint, which a profile or the
        ``HELIOS_LLM__*`` variables configure."""
        timeout = _compact({"timeout_seconds": _env("HELIOS_LLM_TIMEOUT_SECONDS")})
        provider = conventional_llm_provider()
        if provider == "anthropic":
            key = _env("ANTHROPIC_API_KEY")
            if not key:
                return timeout
            return {**timeout, **_compact({"provider": "anthropic", "api_key": key, "model": _env("ANTHROPIC_MODEL")})}
        if provider == "mistral":
            key = _env("MISTRAL_API_KEY")
            if not key:
                return timeout
            return {**timeout, **_compact({
                "provider": "mistral", "api_key": key,
                "model": _env("MISTRAL_MODEL"), "base_url": _env("MISTRAL_BASE_URL"),
            })}
        return timeout


def conventional_llm_provider() -> str:
    """The LLM provider the conventional variables choose: LLM_PROVIDER, else
    anthropic or mistral when its key is set, else openai (an OpenAI-compatible
    endpoint). A profile reads its own endpoint variables only for openai."""
    return (_env("LLM_PROVIDER") or (
        "anthropic" if _env("ANTHROPIC_API_KEY") else "mistral" if _env("MISTRAL_API_KEY") else "openai"
    )).lower()


# ---------------------------------------------------------------- profiles and the caller
def load_settings(profile: str | None = None, config: str | os.PathLike[str] | None = None) -> Settings:
    """Settings as a command builds them. ``profile`` (else ``HELIOS_PROFILE``) names a
    settings profile registered under ``helios.profiles`` (helios-cloudera's
    ``workbench``); with none it is plain ``Settings``. A profile is never chosen from
    the environment it describes: it is named or it is not used. ``config`` replaces
    the config-file search (layer 4)."""
    name = profile or _env("HELIOS_PROFILE")
    cls: type[Settings] = Settings
    if name:
        from . import plugins

        loaded = plugins.load(plugins.PROFILES, name)
        if not (isinstance(loaded, type) and issubclass(loaded, Settings)):
            raise plugins.PluginError(f"settings profile {name!r} is not a Settings subclass: {loaded!r}")
        cls = loaded
    return cls.from_file(config) if config is not None else cls()


def environment_caller_token() -> str | None:
    """``HELIOS_CALLER_TOKEN``: the token a command passes on as its caller's, for a
    connection in ``token`` mode. Read from the environment only, so it never appears in
    a command line or a config file."""
    return _env("HELIOS_CALLER_TOKEN")
