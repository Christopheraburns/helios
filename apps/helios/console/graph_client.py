"""The Helios API's client for the Helios Graph gateway (CR-7).

Configured by ``HELIOS_GRAPH_GATEWAY_URL`` and ``HELIOS_GRAPH_TOKEN``; the
token never reaches a browser. Mirrors the gateway's ``/v1/index`` endpoints,
which the crawler API uses to push one crawl run's helios_index rows.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

DEFAULT_TIMEOUT = 60.0


class GraphGatewayUnavailable(RuntimeError):
    """The gateway is not configured, or could not be reached."""


class GraphGatewayError(RuntimeError):
    """The gateway answered with an error status."""

    def __init__(self, status: int, detail: Any) -> None:
        super().__init__(f"graph gateway returned {status}: {detail}")
        self.status = status
        self.detail = detail


class GraphGatewayClient:
    def __init__(
        self,
        base_url: str | None = None,
        token: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        base_url = base_url or os.environ.get("HELIOS_GRAPH_GATEWAY_URL") or ""
        token = token or os.environ.get("HELIOS_GRAPH_TOKEN") or ""
        if not base_url or not token:
            raise GraphGatewayUnavailable(
                "graph gateway is not configured: set HELIOS_GRAPH_GATEWAY_URL and HELIOS_GRAPH_TOKEN"
            )
        self._http = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout,
            transport=transport,
        )

    def close(self) -> None:
        self._http.close()

    def _request(self, method: str, path: str, json: Any = None) -> dict[str, Any]:
        try:
            response = self._http.request(method, path, json=json)
        except httpx.HTTPError as exc:
            raise GraphGatewayUnavailable(f"graph gateway unreachable: {exc}") from exc
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail")
            except ValueError:
                detail = response.text
            raise GraphGatewayError(response.status_code, detail)
        return response.json()

    # --- /v1/index ------------------------------------------------------------------

    def begin_run(self, crawl_run_id: str, ontology_version: str, force: bool = False) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/v1/index/{crawl_run_id}:begin",
            {"ontology_version": ontology_version, "force": force},
        )

    def load_rows(self, crawl_run_id: str, table: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
        return self._request("POST", f"/v1/index/{crawl_run_id}/{table}", {"rows": rows})

    def finish_run(self, crawl_run_id: str) -> dict[str, Any]:
        return self._request("POST", f"/v1/index/{crawl_run_id}:finish")

    def run_state(self, crawl_run_id: str) -> dict[str, Any] | None:
        """The run's load state, or None when the gateway has not loaded it."""
        try:
            return self._request("GET", f"/v1/index/{crawl_run_id}")
        except GraphGatewayError as exc:
            if exc.status == 404:
                return None
            raise

    def drop_run(self, crawl_run_id: str) -> dict[str, Any]:
        return self._request("DELETE", f"/v1/index/{crawl_run_id}")

    def list_runs(self) -> list[dict[str, Any]]:
        return list(self._request("GET", "/v1/index").get("runs", []))
