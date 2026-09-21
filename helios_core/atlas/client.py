"""
Thin client for the Apache Atlas v2 REST API, scoped to what helios needs:
glossary CRUD, term CRUD, term <-> entity assignment, entity lookup and lineage.
"""
from __future__ import annotations

import os
from typing import Any

import requests

from ..config import AtlasConfig

COLUMN_TYPES = ("hive_column", "iceberg_column")


class AtlasError(RuntimeError):
    def __init__(self, status: int, message: str):
        super().__init__(f"Atlas {status}: {message}")
        self.status = status


class AtlasClient:
    def __init__(self, cfg: AtlasConfig):
        self.base = cfg.base_url
        self.s = requests.Session()
        self.s.auth = (cfg.user, cfg.password)
        self.s.verify = cfg.verify_ssl
        self.s.headers.update({"X-XSRF-HEADER": "valid", "Accept": "application/json"})

    # ---------------------------------------------------------------- plumbing
    def _req(self, method: str, path: str, **kw) -> Any:
        r = self.s.request(method, f"{self.base}{path}", timeout=60, **kw)
        if r.status_code >= 400:
            try:
                msg = r.json().get("errorMessage", r.text)
            except ValueError:
                msg = r.text
            raise AtlasError(r.status_code, msg[:500])
        if r.status_code == 204 or not r.content:
            return None
        return r.json()

    def ping(self) -> bool:
        self._req("GET", "/types/typedefs/headers")
        return True

    # ---------------------------------------------------------------- glossaries
    def list_glossaries(self) -> list[dict]:
        return self._req("GET", "/glossary", params={"limit": 100}) or []

    def get_glossary(self, guid: str) -> dict:
        return self._req("GET", f"/glossary/{guid}")

    def create_glossary(self, name: str, short_description: str = "") -> dict:
        return self._req("POST", "/glossary", json={"name": name, "shortDescription": short_description})

    def delete_glossary(self, guid: str) -> None:
        self._req("DELETE", f"/glossary/{guid}")

    # ---------------------------------------------------------------- terms
    def list_terms(self, glossary_guid: str, limit: int = 1000) -> list[dict]:
        return self._req("GET", f"/glossary/{glossary_guid}/terms", params={"limit": limit, "offset": 0}) or []

    def get_term(self, term_guid: str) -> dict:
        return self._req("GET", f"/glossary/term/{term_guid}")

    def create_term(self, glossary_guid: str, name: str, short_description: str = "",
                    long_description: str = "", abbreviation: str = "", examples: list[str] | None = None) -> dict:
        body = {"name": name, "shortDescription": short_description, "longDescription": long_description,
                "abbreviation": abbreviation, "examples": examples or [], "anchor": {"glossaryGuid": glossary_guid}}
        return self._req("POST", "/glossary/term", json=body)

    def update_term(self, term_guid: str, **fields) -> dict:
        """Atlas PUT replaces the term, so fetch, merge the editable fields, and send back."""
        term = self.get_term(term_guid)
        for k in ("name", "shortDescription", "longDescription", "abbreviation", "examples"):
            if k in fields and fields[k] is not None:
                term[k] = fields[k]
        return self._req("PUT", f"/glossary/term/{term_guid}", json=term)

    def delete_term(self, term_guid: str) -> None:
        self._req("DELETE", f"/glossary/term/{term_guid}")

    def import_csv(self, path: str) -> dict:
        with open(path, "rb") as f:
            return self._req("POST", "/glossary/import", files={"file": (os.path.basename(path), f, "text/csv")})

    # ---------------------------------------------------------------- assignments
    def assigned_entities(self, term_guid: str) -> list[dict]:
        return self._req("GET", f"/glossary/terms/{term_guid}/assignedEntities") or []

    def assign(self, term_guid: str, entities: list[tuple[str, str]]) -> None:
        """entities: list of (guid, typeName)."""
        if entities:
            self._req("POST", f"/glossary/terms/{term_guid}/assignedEntities",
                      json=[{"guid": g, "typeName": t} for g, t in entities])

    def unassign(self, term_guid: str, entity_guid: str) -> None:
        """Atlas needs the relationship guid to disassociate; look it up from the term's assignments."""
        for e in self.assigned_entities(term_guid):
            if e.get("guid") == entity_guid:
                self._req("PUT", f"/glossary/terms/{term_guid}/assignedEntities",
                          json=[{"guid": entity_guid, "relationshipGuid": e.get("relationshipGuid")}])
                return
        raise AtlasError(404, f"entity {entity_guid} is not assigned to term {term_guid}")

    # ---------------------------------------------------------------- entities
    def dsl(self, query: str, limit: int = 25) -> list[dict]:
        return (self._req("GET", "/search/dsl", params={"query": query, "limit": limit}) or {}).get("entities") or []

    def find_column(self, db: str, table: str, column: str) -> tuple[str, str] | None:
        """Return (guid, typeName) of the ACTIVE column entity for db.table.column, checking each known column type."""
        for typ in COLUMN_TYPES:
            try:
                ents = self.dsl(f'{typ} where qualifiedName like "{db}.{table}.{column}@*"', limit=5)
            except AtlasError as e:
                if e.status == 400:      # type does not exist on this Atlas
                    continue
                raise
            ents = [x for x in ents if x.get("status", "ACTIVE") == "ACTIVE"]
            if ents:
                return ents[0]["guid"], typ
        return None

    def entity(self, guid: str) -> dict:
        return self._req("GET", f"/entity/guid/{guid}").get("entity", {})

    def lineage(self, guid: str, depth: int = 3) -> dict:
        return self._req("GET", f"/lineage/{guid}", params={"depth": depth, "direction": "BOTH"})
