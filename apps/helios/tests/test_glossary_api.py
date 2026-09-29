from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from apps.console.main import app
from helios_core.graph import ArtifactGraphRepository, _proposal_detail


class FakeAtlas:
    def __init__(self):
        self.glossaries = {
            "customer-glossary": {
                "guid": "customer-glossary",
                "name": "Customer language",
                "shortDescription": "Governed customer vocabulary",
                "terms": [{"guid": "customer-id"}, {"guid": "lifetime-value"}],
            },
            "finance-glossary": {
                "guid": "finance-glossary",
                "name": "Finance",
                "shortDescription": "",
                "terms": [],
            },
        }
        self.terms = {
            "customer-id": {
                "guid": "customer-id",
                "name": "Customer ID",
                "shortDescription": "Stable customer identifier",
                "longDescription": "Uniquely identifies a customer.",
                "abbreviation": "CID",
                "examples": ["C-100"],
                "anchor": {"glossaryGuid": "customer-glossary"},
            },
            "lifetime-value": {
                "guid": "lifetime-value",
                "name": "Lifetime Value",
                "shortDescription": "Expected customer value",
                "anchor": {"glossaryGuid": "customer-glossary"},
            },
            "finance-term": {
                "guid": "finance-term",
                "name": "Ledger",
                "anchor": {"glossaryGuid": "finance-glossary"},
            },
        }
        self.assignments = {
            "customer-id": [
                {
                    "guid": "atlas-column-1",
                    "displayText": "warehouse.customers.customer_id@cluster",
                    "typeName": "hive_column",
                },
                {
                    "guid": "secret-column",
                    "displayText": "secret.payroll.ssn@cluster",
                    "typeName": "hive_column",
                },
            ]
        }
        self.assigned = []
        self.unassigned = []
        self.imported = []

    def get_glossary(self, guid):
        return deepcopy(self.glossaries[guid])

    def create_glossary(self, name, short_description=""):
        document = {
            "guid": "new-glossary",
            "name": name,
            "shortDescription": short_description,
            "terms": [],
        }
        self.glossaries[document["guid"]] = document
        return deepcopy(document)

    def delete_glossary(self, guid):
        self.glossaries.pop(guid)

    def list_terms(self, glossary_guid, limit=1000, offset=0):
        values = [
            deepcopy(term)
            for term in self.terms.values()
            if term.get("anchor", {}).get("glossaryGuid") == glossary_guid
        ]
        return values[offset : offset + limit]

    def get_term(self, guid):
        return deepcopy(self.terms[guid])

    def create_term(
        self,
        glossary_guid,
        name,
        short_description="",
        long_description="",
        abbreviation="",
        examples=None,
    ):
        guid = name.lower().replace(" ", "-")
        term = {
            "guid": guid,
            "name": name,
            "shortDescription": short_description,
            "longDescription": long_description,
            "abbreviation": abbreviation,
            "examples": examples or [],
            "anchor": {"glossaryGuid": glossary_guid},
        }
        self.terms[guid] = term
        return deepcopy(term)

    def update_term(self, guid, **fields):
        self.terms[guid].update(fields)
        return deepcopy(self.terms[guid])

    def delete_term(self, guid):
        self.terms.pop(guid)

    def import_csv_bytes(self, filename, content):
        self.imported.append((filename, content))
        return {"successImportInfoList": [{}, {}], "failedImportInfoList": []}

    def assigned_entities(self, term_guid):
        return deepcopy(self.assignments.get(term_guid, []))

    def find_column(self, database, table, column):
        if (database, table, column) == (
            "warehouse",
            "customers",
            "customer_id",
        ):
            return "atlas-column-1", "hive_column"
        return None

    def assign(self, term_guid, entities):
        self.assigned.append((term_guid, entities))

    def unassign(self, term_guid, entity_guid):
        self.unassigned.append((term_guid, entity_guid))


@pytest.fixture
def glossary_client(persistent_auth_stack):
    previous = dict(app.state._state)
    atlas = FakeAtlas()
    app.state.metadata_repository = persistent_auth_stack.repository
    app.state.graph_repository = ArtifactGraphRepository(
        persistent_auth_stack.artifacts
    )
    app.state.atlas_client = atlas
    app.state._state.pop("resource_store", None)
    app.state._state.pop("authorization_policy", None)
    try:
        with TestClient(app) as client:
            yield client, atlas, persistent_auth_stack
    finally:
        app.state._state.clear()
        app.state._state.update(previous)


def headers(subject):
    return {"x-forwarded-user": subject}


def test_glossary_collection_search_sort_and_safe_detail(glossary_client):
    client, _, _ = glossary_client
    summary = client.get(
        "/api/v1/models/customer360/glossary",
        headers=headers("viewer"),
    )
    collection = client.get(
        "/api/v1/models/customer360/glossary/terms"
        "?query=customer&sort=definition&direction=desc",
        headers=headers("viewer"),
    )
    detail = client.get(
        "/api/v1/models/customer360/glossary/terms/customer-id",
        headers=headers("viewer"),
    )

    assert summary.status_code == 200
    assert summary.json()["glossary"]["name"] == "Customer language"
    assert collection.status_code == 200
    assert collection.json()["total"] == 2
    assert detail.status_code == 200
    assignments = detail.json()["term"]["assignments"]
    assert [item["id"] for item in assignments] == ["atlas-column-1"]
    assert assignments[0]["canvas_element_id"] == "attribute:customers:customer_id"
    assert "secret-column" not in detail.text


def test_glossary_mutations_require_edit_and_reject_cross_glossary_term(
    glossary_client,
):
    client, atlas, _ = glossary_client
    denied = client.post(
        "/api/v1/models/customer360/glossary/terms",
        headers=headers("viewer"),
        json={"name": "Denied"},
    )
    cross_glossary = client.get(
        "/api/v1/models/customer360/glossary/terms/finance-term",
        headers=headers("owner"),
    )
    created = client.post(
        "/api/v1/models/customer360/glossary/terms",
        headers=headers("owner"),
        json={
            "name": "Customer Segment",
            "definition": "A governed segment.",
            "long_description": "",
            "abbreviation": "",
            "examples": [],
        },
    )
    updated = client.patch(
        "/api/v1/models/customer360/glossary/terms/customer-segment",
        headers=headers("owner"),
        json={
            "name": "Customer Cohort",
            "definition": "A governed cohort.",
            "long_description": "",
            "abbreviation": "",
            "examples": [],
        },
    )
    deleted = client.delete(
        "/api/v1/models/customer360/glossary/terms/customer-segment",
        headers=headers("owner"),
    )

    assert denied.status_code == 403
    assert cross_glossary.status_code == 404
    assert created.status_code == 201
    assert updated.json()["term"]["name"] == "Customer Cohort"
    assert deleted.status_code == 200
    assert "customer-segment" not in atlas.terms


def test_glossary_import_and_authorized_assignment(glossary_client):
    client, atlas, _ = glossary_client
    invalid_import = client.post(
        "/api/v1/models/customer360/glossary/import",
        headers=headers("owner"),
        files={
            "file": (
                "terms.csv",
                b"GlossaryName,TermName\nOther,Term\n",
                "text/csv",
            )
        },
    )
    valid_import = client.post(
        "/api/v1/models/customer360/glossary/import",
        headers=headers("owner"),
        files={
            "file": (
                "terms.csv",
                b"GlossaryName,TermName\nCustomer language,Term\n",
                "text/csv",
            )
        },
    )
    assets = client.get(
        "/api/v1/models/customer360/glossary/assignable-assets",
        headers=headers("owner"),
    )
    assigned = client.post(
        "/api/v1/models/customer360/glossary/terms/customer-id/assignments",
        headers=headers("owner"),
        json={"canvas_element_id": "attribute:customers:customer_id"},
    )
    unassigned = client.delete(
        "/api/v1/models/customer360/glossary/terms/customer-id"
        "/assignments/atlas-column-1",
        headers=headers("owner"),
    )

    assert invalid_import.status_code == 422
    assert valid_import.json()["imported"] == 2
    assert assets.json()["items"][0]["id"] == "attribute:customers:customer_id"
    assert assigned.status_code == 201
    assert atlas.assigned == [
        ("customer-id", [("atlas-column-1", "hive_column")])
    ]
    assert unassigned.status_code == 200
    assert atlas.unassigned == [("customer-id", "atlas-column-1")]


def test_create_and_delete_glossary_updates_model_binding(glossary_client):
    client, _, stack = glossary_client
    created = client.post(
        "/api/v1/models/other-model/glossary",
        headers=headers("other-owner"),
        json={"name": "Other vocabulary", "description": ""},
    )
    assert created.status_code == 201
    assert stack.repository.model("other-model").glossary_id == "new-glossary"

    confirmation = client.delete(
        "/api/v1/models/other-model/glossary",
        headers=headers("other-owner"),
    )
    deleted = client.delete(
        "/api/v1/models/other-model/glossary?confirm=true",
        headers=headers("other-owner"),
    )

    assert confirmation.status_code == 409
    assert deleted.status_code == 200
    assert stack.repository.model("other-model").glossary_id is None


def test_glossary_proposal_canvas_detail_exposes_available_evidence():
    detail = _proposal_detail(
        {
            "glossary_terms": [
                {
                    "name": "Order lifecycle",
                    "definition": "The stages of an order.",
                    "columns": ["sales.orders.status"],
                    "confidence": 0.91,
                    "source": "discovery",
                }
            ]
        },
        "concept:Order lifecycle",
    )

    assert detail == {
        "description": "The stages of an order.",
        "business_term": "Order lifecycle",
        "mapped_attributes": ["sales.orders.status"],
        "confidence": 0.91,
        "source": "discovery",
    }
