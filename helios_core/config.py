"""
Runtime configuration, read from environment variables.

In Cloudera AI Workbench set these under Project Settings -> Advanced -> Environment Variables
so that Sessions, Jobs and Applications all see them. Never commit values.
"""
import os
from dataclasses import dataclass


def _env(name: str, default: str | None = None) -> str | None:
    v = os.environ.get(name)
    return v if v not in (None, "") else default


@dataclass(frozen=True)
class AtlasConfig:
    base_url: str          # e.g. https://<datalake-host>/<datalake-name>/cdp-proxy-api/atlas/api/atlas/v2
    user: str
    password: str
    verify_ssl: bool = True


@dataclass(frozen=True)
class ImpalaConfig:
    host: str              # Impala VW hostname from "Copy JDBC URL" in Cloudera Data Warehouse
    port: int
    user: str
    password: str
    database: str = "default"
    http_path: str = "cliservice"


@dataclass(frozen=True)
class InferenceConfig:
    base_url: str | None   # OpenAI-compatible base URL of the Cloudera AI Inference endpoint
    api_key: str | None
    model: str | None


def atlas_config() -> AtlasConfig | None:
    base, user, pw = _env("ATLAS_BASE"), _env("ATLAS_USER"), _env("ATLAS_PASS")
    if not (base and user and pw):
        return None
    return AtlasConfig(base.rstrip("/"), user, pw, _env("ATLAS_VERIFY_SSL", "true").lower() != "false")


def impala_config() -> ImpalaConfig | None:
    host, user, pw = _env("IMPALA_HOST"), _env("IMPALA_USER") or _env("ATLAS_USER"), _env("IMPALA_PASS") or _env("ATLAS_PASS")
    if not (host and user and pw):
        return None
    return ImpalaConfig(host, int(_env("IMPALA_PORT", "443")), user, pw,
                        _env("IMPALA_DATABASE", "default"), _env("IMPALA_HTTP_PATH", "cliservice"))


def inference_config() -> InferenceConfig:
    return InferenceConfig(_env("INFERENCE_BASE_URL"), _env("INFERENCE_API_KEY"), _env("INFERENCE_MODEL"))
