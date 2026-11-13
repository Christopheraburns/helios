"""A read-only viewer for Helios's graphs: the published semantic models, the ontology,
and a query's path over a model (burndown 8.6). An example of a host built on the public
library APIs; it is not a published package and changes nothing it reads.

    python examples/viewer/serve.py                       # ./helios.toml, or HELIOS_* settings
    python examples/viewer/serve.py --config helios.toml --ontology my-ontology.yaml --port 8765

Then open http://127.0.0.1:8765. The page (index.html, beside this file) asks this server
for JSON:

    GET /api/models                          the published models and their active revisions
    GET /api/models/<model>                  datasets (with fields and metrics) and relationships
    GET /api/models/<model>/compile?metric=..&dimension=..[&dialect=duckdb]
                                             the compiler's answer: SQL, fact, joins, columns
    GET /api/ontology                        the ontology graph's nodes and edges

The models come from the ArtifactStore of the settings; the ontology from ``--ontology``:
a LinkML schema (needs ``helios-cortex[ontology]``), or an OntologyGraph saved as JSON
(``OntologyGraph.to_dict()``). Without ``--ontology`` it shows the repository's example
ontology. It listens on 127.0.0.1 only and answers GET only.
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import threading
from dataclasses import asdict
from functools import cached_property
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from helios_cortex.artifacts import ArtifactStore
from helios_cortex.compiler import CompileError, Compiler, SemanticRequest
from helios_cortex.ontology import OntologyGraph
from helios_cortex.ossie import SemanticModel
from helios_cortex.settings import Settings

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
EXAMPLE_ONTOLOGY = REPO / "ontology/customers/example-tenant/extension.yaml"
EXAMPLE_MAPPINGS = REPO / "ontology/mappings/ossie"


class Viewer:
    """What the page shows, read through the libraries: nothing here writes."""

    def __init__(self, settings: Settings, ontology: Path | None = None):
        self.artifacts = ArtifactStore.from_settings(settings)
        self.ontology_path = ontology
        self._lock = threading.Lock()

    def models(self) -> list[dict[str, Any]]:
        out = []
        for name in self.artifacts.published_model_ids():
            active = self.artifacts.active_revision(name)
            out.append({"name": name, "revision_id": active.revision_id if active else None})
        return out

    def model(self, name: str) -> SemanticModel:
        if name not in self.artifacts.published_model_ids():
            raise LookupError(f"no published model {name!r}")
        return SemanticModel.load_published(name, self.artifacts)

    def model_graph(self, name: str) -> dict[str, Any]:
        model = self.model(name)
        metrics: dict[str, list[dict[str, str]]] = {}
        for metric in model.metrics.values():
            metrics.setdefault(metric.dataset, []).append(
                {"name": metric.name, "synonyms": metric.synonyms, "description": metric.description,
                 "expression": metric.expression})
        return {
            "name": name,
            "datasets": [{
                "name": d.name, "source": d.source, "kind": d.kind, "description": d.description,
                "primary_key": d.primary_key,
                "fields": [{"name": f.name, "label": f.label, "role": f.role, "datatype": f.datatype,
                            "qualified": f"{d.source}.{f.name}"} for f in d.fields.values()],
                "metrics": metrics.get(d.name, []),
            } for d in model.datasets.values()],
            "relationships": [{
                "name": r.name, "from": r.from_dataset, "to": r.to_dataset, "from_columns": r.from_columns,
                "to_columns": r.to_columns, "default": r.is_default,
            } for r in model.relationships.values()],
        }

    def compile(self, name: str, metrics: list[str], dimensions: list[str], dialect: str) -> dict[str, Any]:
        compiled = Compiler(self.model(name)).compile(
            SemanticRequest(metrics=metrics, dimensions=dimensions), dialect=dialect)
        return asdict(compiled)

    @cached_property
    def ontology(self) -> dict[str, Any]:
        """The ontology graph, read once."""
        path = self.ontology_path or EXAMPLE_ONTOLOGY
        if path.suffix == ".json":
            return OntologyGraph.from_dict(json.loads(path.read_text())).to_dict()
        try:
            from helios_cortex.ontology import load_mappings
            from helios_cortex.ontology.parser import parse
        except ImportError as exc:
            raise LookupError("reading a LinkML ontology needs helios-cortex[ontology]") from exc
        mappings = load_mappings(EXAMPLE_MAPPINGS) if self.ontology_path is None else None
        with self._lock:
            return parse(str(path), version=path.stem, mappings=mappings).graph.to_dict()


class Handler(BaseHTTPRequestHandler):
    viewer: Viewer

    def do_GET(self) -> None:  # noqa: N802 - the http.server convention
        url = urlparse(self.path)
        parts = [unquote(p) for p in url.path.strip("/").split("/") if p]
        query = parse_qs(url.query)
        try:
            if not parts or parts == ["index.html"]:
                return self._file(HERE / "index.html")
            if parts == ["api", "models"]:
                return self._json(self.viewer.models())
            if parts[:2] == ["api", "models"] and len(parts) == 3:
                return self._json(self.viewer.model_graph(parts[2]))
            if parts[:2] == ["api", "models"] and len(parts) == 4 and parts[3] == "compile":
                metrics, dimensions = query.get("metric", []), query.get("dimension", [])
                if not metrics:
                    return self._json({"error": "name at least one metric"}, HTTPStatus.BAD_REQUEST)
                dialect = query.get("dialect", ["duckdb"])[0]
                return self._json(self.viewer.compile(parts[2], metrics, dimensions, dialect))
            if parts == ["api", "ontology"]:
                return self._json(self.viewer.ontology)
        except (CompileError, KeyError, ValueError) as exc:   # a request the model can't answer
            return self._json({"error": str(exc.args[0]) if exc.args else str(exc)}, HTTPStatus.BAD_REQUEST)
        except LookupError as exc:                            # no such model, or no ontology to show
            return self._json({"error": str(exc.args[0]) if exc.args else str(exc)}, HTTPStatus.NOT_FOUND)
        self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def _refuse(self) -> None:
        self._json({"error": "the viewer is read-only"}, HTTPStatus.METHOD_NOT_ALLOWED)

    do_POST = do_PUT = do_PATCH = do_DELETE = _refuse  # noqa: N815

    def _json(self, body: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        self._send(json.dumps(body, default=str).encode(), "application/json", status)

    def _file(self, path: Path) -> None:
        self._send(path.read_bytes(), mimetypes.guess_type(path.name)[0] or "application/octet-stream")

    def _send(self, data: bytes, content_type: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        self.send_response(status)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - http.server's signature
        pass


def make_server(viewer: Viewer, port: int = 8765, host: str = "127.0.0.1") -> ThreadingHTTPServer:
    """The viewer's HTTP server (not yet serving); port 0 picks a free one."""
    handler = type("ViewerHandler", (Handler,), {"viewer": viewer})
    return ThreadingHTTPServer((host, port), handler)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--config", type=Path, help="a helios.toml (default: the usual search)")
    parser.add_argument("--ontology", type=Path,
                        help="a LinkML schema or an OntologyGraph JSON (default: the repository's example)")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    settings = Settings.from_file(args.config) if args.config else Settings()
    server = make_server(Viewer(settings, args.ontology), args.port)
    print(f"Helios viewer on http://127.0.0.1:{server.server_address[1]} (Ctrl-C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
