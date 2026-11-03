"""The Cloudera AI Workbench settings profile: the environment variables Workbench
projects have always set, read into Helios Settings.

A host on Workbench builds ``WorkbenchSettings()`` where it would build
``Settings()``. On top of helios-core's sources it reads:

* ``CDSW_PROJECT_DIR`` (data directory ``<project>/helios``); on Workbench without
  it (``CDSW_PROJECT_ID``, ``CDSW_ENGINE_ID`` or ``CDSW_DOMAIN`` set),
  ``/home/cdsw/helios``. ``HELIOS_ROOT`` still wins.
* ``IMPALA_HOST`` with ``WORKLOAD_USER`` / ``WORKLOAD_PASSWORD`` (and ``IMPALA_PORT``,
  ``IMPALA_DATABASE``, ``IMPALA_HTTP_PATH``, ``IMPALA_PROXY_DELEGATION``): the
  ``default`` connection, only when host, user and password are all set.
* ``ATLAS_BASE`` / ``ATLAS_USER`` / ``ATLAS_PASS`` (and ``ATLAS_VERIFY_SSL``): the
  ``default`` catalog, likewise only when complete.
* ``INFERENCE_BASE_URL`` / ``INFERENCE_MODEL`` / ``INFERENCE_TOKEN`` (or
  ``INFERENCE_API_KEY``): a Cloudera AI Inference endpoint as the OpenAI-compatible
  LLM, used when no LLM_PROVIDER or Anthropic / Mistral key chooses another.

These are the values Helios resolved before Settings existed, so existing
deployments keep their variables.
"""
from __future__ import annotations

import os
from typing import Any

from pydantic_settings import BaseSettings, PydanticBaseSettingsSource

from helios_core.settings import DEFAULT, Settings, conventional_llm_provider

WORKBENCH_MARKERS = ("CDSW_PROJECT_ID", "CDSW_ENGINE_ID", "CDSW_DOMAIN")
WORKBENCH_HOME = "/home/cdsw/helios"  # Workbench's project directory when CDSW_PROJECT_DIR is unset


def _env(name: str) -> str | None:
    value = os.environ.get(name)
    return value if value not in (None, "") else None


def _compact(values: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in values.items() if v is not None}


class WorkbenchEnvironment(PydanticBaseSettingsSource):
    """The Workbench and Cloudera environment variables, as Settings values."""

    def get_field_value(self, field: Any, field_name: str) -> tuple[Any, str, bool]:
        return None, field_name, False  # unused: __call__ builds the whole mapping

    def __call__(self) -> dict[str, Any]:
        values: dict[str, Any] = _compact({"data_dir": self._data_dir()})
        impala, atlas, llm = self._impala(), self._atlas(), self._inference()
        if impala:
            values["connections"] = {DEFAULT: impala}
        if atlas:
            values["catalogs"] = {DEFAULT: atlas}
        if llm:
            values["llm"] = llm
        return values

    @staticmethod
    def _data_dir() -> str | None:
        if _env("CDSW_PROJECT_DIR"):
            return os.path.join(_env("CDSW_PROJECT_DIR"), "helios")
        if any(_env(name) for name in WORKBENCH_MARKERS):
            return WORKBENCH_HOME
        return None

    @staticmethod
    def _impala() -> dict[str, Any] | None:
        host, user, password = _env("IMPALA_HOST"), _env("WORKLOAD_USER"), _env("WORKLOAD_PASSWORD")
        if not (host and user and password):
            return None
        return _compact({
            "engine": "impala",
            "host": host,
            "user": user,
            "password": password,
            "port": _env("IMPALA_PORT"),
            "database": _env("IMPALA_DATABASE"),
            "http_path": _env("IMPALA_HTTP_PATH"),
            "identity": "impersonate" if (_env("IMPALA_PROXY_DELEGATION") or "").lower() == "true" else None,
        })

    @staticmethod
    def _atlas() -> dict[str, Any] | None:
        base, user, password = _env("ATLAS_BASE"), _env("ATLAS_USER"), _env("ATLAS_PASS")
        if not (base and user and password):
            return None
        return {
            "catalog": "atlas",
            "base_url": base,
            "user": user,
            "password": password,
            "verify_ssl": (_env("ATLAS_VERIFY_SSL") or "true").lower() != "false",
        }

    @staticmethod
    def _inference() -> dict[str, Any] | None:
        base = _env("INFERENCE_BASE_URL")
        if not base or conventional_llm_provider() != "openai":
            return None
        return _compact({
            "provider": "openai",
            "base_url": base,
            "model": _env("INFERENCE_MODEL"),
            "api_key": _env("INFERENCE_TOKEN") or _env("INFERENCE_API_KEY"),
        })


class WorkbenchSettings(Settings):
    """Settings that also read the Workbench and Cloudera environment variables."""

    @classmethod
    def profile_sources(cls, settings_cls: type[BaseSettings]) -> tuple[PydanticBaseSettingsSource, ...]:
        return (WorkbenchEnvironment(settings_cls),)
