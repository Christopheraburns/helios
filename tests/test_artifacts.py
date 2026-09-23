import json

import pytest

from helios_core.artifacts import ArtifactStore, model_id_for_run
from helios_core.domain import DataSourceReference, Model
from helios_core.ossie import SemanticModel


def model(model_id: str) -> Model:
    return Model(
        id=model_id,
        organization_id="acme",
        name=model_id,
        data_sources=(DataSourceReference("shared-warehouse"),),
    )


def ossie_document(name: str, description: str) -> dict:
    return {
        "version": "0.2.0.dev0",
        "name": name,
        "description": description,
        "datasets": [],
        "relationships": [],
        "metrics": [],
    }


def test_models_sharing_data_source_have_independent_artifacts(tmp_path):
    customer = model("customer360")
    finance = model("finance")
    assert customer.data_sources == finance.data_sources

    store = ArtifactStore(tmp_path)
    customer_proposal = {
        "model_id": customer.id,
        "datasets": [],
        "metrics": [{"name": "Customer lifetime value"}],
        "glossary_terms": [{"name": "Customer"}],
        "ontology": {"concepts": ["Customer"]},
    }
    finance_proposal = {
        "model_id": finance.id,
        "datasets": [],
        "metrics": [{"name": "Operating margin"}],
        "glossary_terms": [{"name": "Ledger"}],
        "ontology": {"concepts": ["Account"]},
    }

    customer_path = store.write_proposal(
        customer.id, "run-customer", customer_proposal
    )
    finance_path = store.write_proposal(
        finance.id, "run-finance", finance_proposal
    )
    store.write_published_ossie(
        customer.id,
        ossie_document("Customer model", "customer semantics"),
        "name: Customer model\n",
        {"run_id": "run-customer"},
    )
    store.write_published_ossie(
        finance.id,
        ossie_document("Finance model", "finance semantics"),
        "name: Finance model\n",
        {"run_id": "run-finance"},
    )

    assert customer_path != finance_path
    assert customer.id in customer_path.parts
    assert finance.id in finance_path.parts
    assert store.read_json(
        customer.id, "proposed", "run-customer.proposal.json"
    )["metrics"] != store.read_json(
        finance.id, "proposed", "run-finance.proposal.json"
    )["metrics"]

    customer_json = json.loads(
        store.published_ossie_path(customer.id, "json").read_text()
    )
    finance_json = json.loads(
        store.published_ossie_path(finance.id, "json").read_text()
    )
    assert customer_json["description"] == "customer semantics"
    assert finance_json["description"] == "finance semantics"

    store.write_json(
        customer.id,
        "published",
        "glossary.json",
        {"terms": ["Updated customer"]},
    )
    assert not store.path(
        finance.id, "published", "glossary.json"
    ).exists()
    assert finance_json == json.loads(
        store.published_ossie_path(finance.id, "json").read_text()
    )


def test_semantic_model_lookup_uses_model_id(tmp_path):
    store = ArtifactStore(tmp_path)
    document = ossie_document("Customer model", "by stable identity")
    store.write_published_ossie(
        "model-123", document, "name: Customer model\n", {"run_id": "run-1"}
    )

    loaded = SemanticModel.load_published("model-123", str(tmp_path))

    assert loaded.name == "Customer model"
    assert SemanticModel.list_published(str(tmp_path)) == ["model-123"]


def test_legacy_flat_published_artifact_remains_readable(tmp_path):
    legacy = tmp_path / "models" / "published" / "legacy.ossie.yaml"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("name: Legacy\nversion: legacy\n")

    store = ArtifactStore(tmp_path)

    assert store.published_ossie_path("legacy") == legacy
    assert "legacy" in store.published_model_ids()


def test_artifact_model_identity_cannot_be_reassigned(tmp_path):
    store = ArtifactStore(tmp_path)

    with pytest.raises(ValueError, match="belongs to model"):
        store.write_proposal(
            "finance",
            "run-1",
            {"model_id": "customer360", "datasets": []},
        )


def test_explicit_model_id_replaces_only_legacy_default():
    assert model_id_for_run(
        explicit="customer360", legacy_default="physical_database"
    ) == "customer360"
    with pytest.raises(ValueError, match="conflicting"):
        model_id_for_run(
            {"model_id": "finance"}, explicit="customer360"
        )
