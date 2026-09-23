"""
Load a published Ossie document into an indexed, queryable model.

The Ossie spec is a serialization format; this is the in-memory shape everything downstream (compiler, MCP server,
console) works with. helios provenance is read back from `custom_extensions` where vendor_name == HELIOS.
"""
from __future__ import annotations

import json
from collections import defaultdict, deque
from dataclasses import dataclass, field

import yaml

from helios_core.artifacts import ArtifactStore

VENDOR = "HELIOS"


def _ext(obj: dict) -> dict:
    for e in obj.get("custom_extensions") or []:
        if e.get("vendor_name") == VENDOR:
            try:
                return json.loads(e.get("data") or "{}")
            except json.JSONDecodeError:
                return {}
    return {}


def _expr(obj: dict, dialect: str = "ANSI_SQL") -> str:
    dialects = (obj.get("expression") or {}).get("dialects") or []
    for d in dialects:
        if d.get("dialect") == dialect:
            return d["expression"]
    return dialects[0]["expression"] if dialects else ""


@dataclass
class Field:
    dataset: str
    name: str
    expression: str
    label: str
    description: str
    datatype: str
    is_dimension: bool
    is_time: bool
    role: str            # helios role: identifier / foreign_key / time / measure / dimension / attribute
    synonyms: list[str] = field(default_factory=list)

    @property
    def qualified(self) -> str:
        return f"{self.dataset}.{self.name}"

    @property
    def is_measure(self) -> bool:
        return not self.is_dimension


@dataclass
class Dataset:
    name: str
    source: str
    description: str
    kind: str            # helios kind: fact / dimension / bridge / lookup / other
    primary_key: list[str]
    fields: dict[str, Field]
    synonyms: list[str] = field(default_factory=list)


@dataclass
class Relationship:
    name: str
    from_dataset: str
    to_dataset: str
    from_columns: list[str]
    to_columns: list[str]
    is_default: bool
    source: str
    confidence: float | None

    def other(self, dataset: str) -> str:
        return self.to_dataset if dataset == self.from_dataset else self.from_dataset


@dataclass
class Metric:
    name: str
    expression: str      # dataset-qualified ANSI SQL, e.g. SUM(store_sales.ss_net_paid)
    description: str
    datatype: str
    dataset: str         # the fact dataset it is computed over
    synonyms: list[str] = field(default_factory=list)


class SemanticModel:
    def __init__(self, doc: dict):
        self.doc = doc
        self.name: str = doc["name"]
        self.version: str = doc.get("version", "")
        self.description: str = doc.get("description", "")
        self.datasets: dict[str, Dataset] = {}
        self.relationships: dict[str, Relationship] = {}
        self.metrics: dict[str, Metric] = {}
        self._edges: dict[str, list[Relationship]] = defaultdict(list)
        self._by_label: dict[str, list[Field]] = defaultdict(list)
        self._by_name: dict[str, list[Field]] = defaultdict(list)

        for d in doc.get("datasets", []):
            dx = _ext(d)
            fields = {}
            for f in d.get("fields", []):
                fx = _ext(f)
                fld = Field(dataset=d["name"], name=f["name"], expression=_expr(f) or f["name"],
                            label=f.get("label") or f["name"], description=f.get("description") or "",
                            datatype=f.get("datatype") or "Opaque", is_dimension="dimension" in f,
                            is_time=bool((f.get("dimension") or {}).get("is_time")), role=fx.get("role", ""),
                            synonyms=list(((f.get("ai_context") or {}).get("synonyms") or []) if isinstance(f.get("ai_context"), dict) else []))
                fields[f["name"]] = fld
                self._by_name[f["name"].lower()].append(fld)
                for key in {fld.label.lower(), *(s.lower() for s in fld.synonyms)}:
                    if fld not in self._by_label[key]:
                        self._by_label[key].append(fld)
            self.datasets[d["name"]] = Dataset(
                name=d["name"], source=d["source"], description=d.get("description") or "", kind=dx.get("kind", "other"),
                primary_key=list(d.get("primary_key") or []), fields=fields,
                synonyms=list(((d.get("ai_context") or {}).get("synonyms") or []) if isinstance(d.get("ai_context"), dict) else []))

        for r in doc.get("relationships", []):
            rx = _ext(r)
            rel = Relationship(name=r["name"], from_dataset=r["from"], to_dataset=r["to"],
                               from_columns=list(r["from_columns"]), to_columns=list(r["to_columns"]),
                               is_default=bool(rx.get("is_default")), source=rx.get("source", ""), confidence=rx.get("confidence"))
            self.relationships[rel.name] = rel
            self._edges[rel.from_dataset].append(rel)
            self._edges[rel.to_dataset].append(rel)

        for m in doc.get("metrics", []):
            mx = _ext(m)
            ds = mx.get("dataset", "")
            ds = ds.split(".")[-1] if ds else self._infer_metric_dataset(_expr(m))
            self.metrics[m["name"]] = Metric(name=m["name"], expression=_expr(m), description=m.get("description") or "",
                                             datatype=m.get("datatype") or "Decimal", dataset=ds,
                                             synonyms=list(((m.get("ai_context") or {}).get("synonyms") or []) if isinstance(m.get("ai_context"), dict) else []))

    # ------------------------------------------------------------ loading
    @classmethod
    def load(cls, path: str) -> "SemanticModel":
        with open(path) as f:
            doc = yaml.safe_load(f) if path.endswith((".yaml", ".yml")) else json.load(f)
        return cls(doc)

    @classmethod
    def load_published(cls, model_id: str, root: str | None = None) -> "SemanticModel":
        """Load by stable Helios Model ID, with legacy flat-layout fallback."""
        return cls.load(str(ArtifactStore(root).published_ossie_path(model_id)))

    @staticmethod
    def list_published(root: str | None = None) -> list[str]:
        """Return stable Model IDs with published semantic artifacts."""
        return ArtifactStore(root).published_model_ids()

    # ------------------------------------------------------------ lookups
    def _infer_metric_dataset(self, expression: str) -> str:
        for name in self.datasets:
            if f"{name}." in expression:
                return name
        return ""

    def field(self, ref: str) -> Field:
        """Resolve 'dataset.field', a unique physical field name, or a unique label / synonym (case-insensitive)."""
        if "." in ref:
            ds, col = ref.rsplit(".", 1)
            d = self.datasets.get(ds)
            if d and col in d.fields:
                return d.fields[col]
            raise KeyError(f"unknown field {ref}")
        for index in (self._by_name, self._by_label):
            hits = index.get(ref.lower(), [])
            if len(hits) == 1:
                return hits[0]
            if len(hits) > 1:
                raise KeyError(f"ambiguous field {ref!r}: " + ", ".join(sorted(h.qualified for h in hits)))
        raise KeyError(f"unknown field {ref!r}")

    def metric(self, ref: str) -> Metric:
        if ref in self.metrics:
            return self.metrics[ref]
        low = ref.lower()
        hits = [m for m in self.metrics.values() if m.name.lower() == low or low in (s.lower() for s in m.synonyms)]
        if len(hits) == 1:
            return hits[0]
        raise KeyError(f"unknown metric {ref!r}" if not hits else f"ambiguous metric {ref!r}")

    def edges(self, dataset: str) -> list[Relationship]:
        return self._edges.get(dataset, [])

    def relationships_between(self, a: str, b: str) -> list[Relationship]:
        return [r for r in self._edges.get(a, []) if {r.from_dataset, r.to_dataset} == {a, b}]

    def default_relationship(self, a: str, b: str) -> Relationship | None:
        rels = self.relationships_between(a, b)
        if not rels:
            return None
        return next((r for r in rels if r.is_default), rels[0])

    def join_path(self, start: str, target: str, via: dict[str, str] | None = None) -> list[Relationship]:
        """Shortest chain of relationships from start to target. `via` pins {dataset: relationship_name} choices."""
        if start == target:
            return []
        via = via or {}
        prev: dict[str, tuple[str, Relationship]] = {}
        seen = {start}
        q = deque([start])
        while q:
            cur = q.popleft()
            for rel in self.edges(cur):
                nxt = rel.other(cur)
                if nxt in seen:
                    continue
                pinned = via.get(nxt)
                if pinned and pinned != rel.name:
                    continue
                if not pinned and not rel.is_default and any(r.is_default for r in self.relationships_between(cur, nxt)):
                    continue  # a default exists for this pair; do not take the non-default one unless pinned
                seen.add(nxt)
                prev[nxt] = (cur, rel)
                if nxt == target:
                    path, node = [], nxt
                    while node != start:
                        node, rel_ = prev[node][0], prev[node][1]
                        path.append(rel_)
                    return list(reversed(path))
                q.append(nxt)
        raise KeyError(f"no join path from {start} to {target}")

    # ------------------------------------------------------------ description (for agents)
    def describe(self) -> dict:
        return {
            "name": self.name, "version": self.version, "description": self.description,
            "datasets": [{"name": d.name, "kind": d.kind, "description": d.description,
                          "fields": [{"name": f.name, "label": f.label, "role": f.role, "datatype": f.datatype,
                                      "description": f.description} for f in d.fields.values()]}
                         for d in self.datasets.values()],
            "metrics": [{"name": m.name, "dataset": m.dataset, "description": m.description, "expression": m.expression}
                        for m in self.metrics.values()],
            "relationships": [{"name": r.name, "from": r.from_dataset, "to": r.to_dataset, "on": list(zip(r.from_columns, r.to_columns)),
                               "default": r.is_default} for r in self.relationships.values()],
        }
