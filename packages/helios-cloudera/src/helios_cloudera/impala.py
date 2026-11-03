"""Impala adapter: connects to a Cloudera Data Warehouse Impala Virtual Warehouse over HTTPS.

Identity modes: ``service`` authenticates the workload user over LDAP; ``impersonate``
does the same and adds ``doAs=<caller.username>``, then checks ``EFFECTIVE_USER()``;
``token`` authenticates the caller's JWT and needs no workload user."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlencode

from pydantic import BaseModel, ConfigDict, SecretStr, ValidationInfo, model_validator

from ..caller import Caller, IdentityError, IdentityMode
from ..settings import ConnectionSettings, Settings
from .base import Engine, QueryResult

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class ImpalaConfig:
    host: str              # Impala VW hostname from "Copy JDBC URL" in Cloudera Data Warehouse
    port: int
    user: str | None       # the workload (service) user; optional only in token mode
    password: str | None = field(default=None, repr=False)
    database: str = "default"
    http_path: str = "cliservice"
    identity: IdentityMode = IdentityMode.SERVICE


def parse_impala_host(value: str) -> tuple[str, int | None, str | None]:
    """Accept a bare hostname or a full CDW JDBC URL
    (jdbc:impala://host:443/default;AuthMech=12;transportMode=http;httpPath=cliservice;ssl=1)
    and return (host, port, http_path). Only the host is required."""
    v = value.strip()
    http_path = None
    if v.lower().startswith("jdbc:impala://"):
        v = v[len("jdbc:impala://"):]
    if ";" in v:
        v, _, props = v.partition(";")
        for kv in props.split(";"):
            k, _, val = kv.partition("=")
            if k.strip().lower() == "httppath" and val:
                http_path = val.strip()
    v = v.split("/", 1)[0]
    port = None
    if ":" in v:
        v, _, p = v.rpartition(":")
        if p.isdigit():
            port = int(p)
    return v, port, http_path



class ImpalaOptions(BaseModel):
    """Options of an ``engine = "impala"`` connection. ``host`` may be a full CDW
    JDBC URL; its port and httpPath apply unless ``port`` / ``http_path`` are set."""

    model_config = ConfigDict(extra="forbid")

    host: str
    user: str | None = None
    password: SecretStr | None = None
    port: int | None = None
    database: str = "default"
    http_path: str | None = None

    @model_validator(mode="after")
    def _credentials(self, info: ValidationInfo) -> ImpalaOptions:
        if (self.user is None) != (self.password is None):
            raise ValueError("set both user and password, or neither")
        identity = (info.context or {}).get("identity", IdentityMode.SERVICE)
        if self.user is None and identity is not IdentityMode.TOKEN:
            raise ValueError(f"an Impala connection in {identity.value} mode needs user and password")
        return self


def impala_config_for(connection: ConnectionSettings) -> ImpalaConfig:
    if connection.engine != "impala":
        raise ValueError(f"not an Impala connection: engine is {connection.engine!r}")
    options = ImpalaOptions.model_validate(connection.options, context={"identity": connection.identity})
    host, port, http_path = parse_impala_host(options.host)
    return ImpalaConfig(
        host,
        options.port or port or 443,
        options.user,
        options.password.get_secret_value() if options.password else None,
        options.database,
        options.http_path or http_path or "cliservice",
        connection.identity,
    )


def impala_config(settings: Settings, ref: str | None = None) -> ImpalaConfig | None:
    """The Impala connection ``ref`` resolves to (see ``Settings.resolve_connection``),
    or None when there is no connection or it is not an Impala one."""
    connection = settings.connection(ref)
    if connection is None or connection.engine != "impala":
        return None
    return impala_config_for(connection)


class ImpalaAuthenticationError(PermissionError):
    """The CDW HTTP endpoint rejected the credential: the workload user's, or in
    token mode the caller's token."""


class ImpalaProxyDelegationError(PermissionError):
    """Impala did not accept or enforce the impersonated effective user."""


def _short_name(user: str) -> str:
    """Kerberos-style short name: text before the first '/' or '@', lower-cased."""
    return user.strip().split("/", 1)[0].split("@", 1)[0].lower()


def _same_user(a: str, b: str) -> bool:
    return bool(a and b) and _short_name(a) == _short_name(b)


class ImpalaEngine(Engine):
    name = "impala"
    sqlglot_dialect = "hive"   # SQLGlot has no dedicated Impala dialect; Hive is the closest and is post-processed by the compiler
    identity_modes = frozenset(IdentityMode)

    def __init__(self, cfg: ImpalaConfig):
        super().__init__(cfg.identity)
        self.cfg = cfg

    def _connect(self, caller: Caller | None = None):
        from impala.dbapi import connect
        self.check_caller(caller)
        common = dict(host=self.cfg.host, port=self.cfg.port, database=self.cfg.database,
                      use_ssl=True, use_http_transport=True)
        if caller is not None and self.identity_mode is IdentityMode.TOKEN:
            return connect(**common, auth_mechanism="JWT", jwt=caller.token, http_path=self.cfg.http_path)
        if self.cfg.user is None:
            raise IdentityError("this Impala connection has no workload user; pass a caller with a token")
        http_path = self.cfg.http_path
        run_as = self._impersonated(caller)
        if run_as and not _same_user(run_as, self.cfg.user):
            # Impala rejects doAs-to-self unless the Virtual Warehouse has a proxy config, so
            # it is only added for someone else; query() verifies EFFECTIVE_USER() either way.
            path, separator, query = http_path.partition("?")
            parameters = dict(parse_qsl(query, keep_blank_values=True))
            parameters["doAs"] = run_as
            http_path = f"{path}?{urlencode(parameters)}"
        return connect(**common, auth_mechanism="LDAP", user=self.cfg.user, password=self.cfg.password,
                       http_path=http_path)

    def _impersonated(self, caller: Caller | None) -> str | None:
        """The username Impala must run as, when impersonating."""
        if caller is not None and self.identity_mode is IdentityMode.IMPERSONATE:
            return caller.username
        return None

    def query(self, sql: str, caller: Caller | None = None, *, limit: int | None = 1000) -> QueryResult:
        conn = self._connect(caller)
        run_as = self._impersonated(caller)
        try:
            cur = conn.cursor()
            if run_as:
                cur.execute("SELECT EFFECTIVE_USER()")
                effective = cur.fetchone()
                if not effective or not _same_user(str(effective[0]), run_as):
                    raise ImpalaProxyDelegationError(
                        "Impala did not enforce the delegated SSO identity"
                    )
            cur.execute(sql)
            if cur.description is None:
                result = QueryResult([], [])
            else:
                cols = [d[0] for d in cur.description]
                rows = cur.fetchmany(limit) if limit else cur.fetchall()
                result = QueryResult(cols, [tuple(r) for r in rows])
        except BaseException as exc:
            try:
                conn.close()
            except Exception as close_error:
                LOGGER.warning(
                    "Impala connection cleanup failed after %s: %s",
                    type(exc).__name__,
                    type(close_error).__name__,
                )
            if getattr(exc, "code", None) == 401:
                whose = "the caller's token" if caller is not None and self.identity_mode is IdentityMode.TOKEN else "the configured workload authentication"
                raise ImpalaAuthenticationError(f"CDW rejected {whose}") from exc
            raise
        else:
            conn.close()
            return result

    def connect(self, caller: Caller | None = None):
        """A DB-API connection, as the service identity unless a caller is given. Unlike
        query(), it does not check EFFECTIVE_USER(); a caller impersonating must."""
        return self._connect(caller)

    def ping(self) -> bool:
        return self.query("SELECT 1").rows == [(1,)]

    def query_history(self, limit: int = 500) -> QueryResult:
        """Recent statements from Impala's query log, when the VW exposes it via the sys database."""
        return self.query(f"SELECT * FROM sys.impala_query_log ORDER BY start_time_utc DESC LIMIT {limit}", limit=limit)
